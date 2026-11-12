"""Project persistence, atomic saving and resource-path handling."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .model import StudyProject
from .source_resources import (
    GOENKA_URL,
    canonical_recording_relative,
    text_resource_relative,
)

PROJECT_FILENAME = "suttara.json"


class ProjectError(Exception):
    """Base class for project-file failures with a user-presentable message."""


class ProjectLoadError(ProjectError):
    """The project file exists but could not be interpreted safely."""


class ProjectSaveError(ProjectError):
    """The project could not be written."""


def find_project_root(start: Path | None = None) -> Path:
    """Locate the repository root used for the fixed project file.

    ``SUTTARA_PROJECT_ROOT`` takes precedence.  Otherwise the nearest ancestor
    containing both ``sources.txt`` and ``pyproject.toml`` is used, starting
    from ``start`` (or the package's own source location).  A normal working
    directory is the final fallback.
    """

    override = os.environ.get("SUTTARA_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    candidates: list[Path] = []
    if start is not None:
        candidates.append(Path(start).expanduser().resolve())
    candidates.append(Path.cwd().resolve())
    candidates.append(Path(__file__).resolve().parent)

    for candidate in candidates:
        for directory in (candidate, *candidate.parents):
            if (directory / "sources.txt").exists() and (
                directory / "pyproject.toml"
            ).exists():
                return directory
        # A directory merely named like the repository is still a useful root.
    return candidates[0]


def project_file_for(root: Path) -> Path:
    return Path(root).expanduser().resolve() / PROJECT_FILENAME


def load_project(path: Path, project_root: Path | None = None) -> StudyProject:
    """Read a project without ever modifying the file.

    Raises :class:`ProjectLoadError` for unreadable, malformed, or future
    schemas.  The caller is responsible for keeping the damaged file intact.
    """

    path = Path(path)
    if not path.exists():
        return StudyProject()
    if not path.is_file():
        raise ProjectLoadError(f"{path} is not a regular file")
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ProjectLoadError(f"Could not read {path}: {error}") from error
    except UnicodeDecodeError as error:
        raise ProjectLoadError(f"{path} is not valid UTF-8: {error}") from error
    if not raw.strip():
        raise ProjectLoadError(f"{path} is empty rather than a JSON project")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ProjectLoadError(
            f"{path} is not valid JSON (line {error.lineno}, column "
            f"{error.colno}): {error.msg}"
        ) from error
    try:
        project = StudyProject.from_dict(data)
        normalise_project_resources(
            project,
            Path(project_root).expanduser().resolve()
            if project_root is not None
            else path.parent.resolve(),
        )
        return project
    except (OSError, ValueError) as error:
        raise ProjectLoadError(
            f"{path} has an unsupported project structure: {error}"
        ) from error


def save_project(
    project: StudyProject, path: Path, project_root: Path | None = None
) -> None:
    """Atomically write ``project`` as human-readable UTF-8 JSON.

    A temporary file in the same directory is flushed and fsynced before it
    replaces the destination, so an interrupted write cannot truncate the
    saved study collection.
    """

    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ProjectSaveError(f"Could not create {path.parent}: {error}") from error

    try:
        normalise_project_resources(
            project,
            Path(project_root).expanduser().resolve()
            if project_root is not None
            else path.parent.resolve(),
        )
    except (OSError, ValueError) as error:
        raise ProjectSaveError(
            f"Could not prepare resource references for saving: {error}"
        ) from error
    serialised = json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n"
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialised)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as error:
        raise ProjectSaveError(f"Could not save {path}: {error}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def backup_file(path: Path) -> Path | None:
    """Copy an unreadable project aside before a deliberate overwrite."""

    path = Path(path)
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = path.with_name(f"{path.name}.corrupt-{stamp}.bak")
    counter = 1
    while destination.exists():
        destination = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}.bak")
        counter += 1
    try:
        shutil.copy2(path, destination)
    except OSError as error:
        raise ProjectSaveError(
            f"Could not back up the unreadable project file to {destination}: {error}"
        ) from error
    return destination


def normalise_project_resources(project: StudyProject, root: Path) -> StudyProject:
    """Safely migrate known resource references to the fixed conventions.

    Text passages always use the fixed Pāli/English working texts in the
    Section Editor.  Existing references whose filename already matches a
    known text are rewritten to the canonical relative path; unrelated paths
    are left untouched.  Audio recordings found in the expected language
    directory are likewise canonicalised, while an unknown configured file is
    preserved.  Timestamps, coordinates, notes and unknown JSON fields are not
    touched.
    """

    for section in project.sections:
        for language, reference in (
            ("pali", section.pali_text),
            ("english", section.english_text),
        ):
            if reference is None:
                continue
            canonical = text_resource_relative(language)
            if Path(reference.path).name == Path(canonical).name:
                reference.path = canonical
        for language, reference in (
            ("pali", section.spoken_pali),
            ("english", section.spoken_english),
        ):
            if reference is None:
                continue
            canonical_audio = canonical_recording_relative(
                root, language, reference.path
            )
            if canonical_audio:
                reference.path = canonical_audio
        if section.goenka is not None:
            section.goenka.url = GOENKA_URL
    return project


def stored_path(path_value: str | Path, root: Path) -> str:
    """Return the canonical path representation stored in the project.

    Files beneath the repository root are stored as POSIX-style relative
    paths.  External material stays absolute.  Empty values stay empty.
    """

    text = str(path_value).strip()
    if not text:
        return ""
    root = Path(root).expanduser().resolve()
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        resolved = candidate.absolute()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def resolved_path(stored: str | None, root: Path) -> Path | None:
    """Resolve a stored path against the project root."""

    if not stored:
        return None
    candidate = Path(stored).expanduser()
    if candidate.is_absolute():
        return candidate
    return Path(root).expanduser().resolve() / candidate


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory sync so the rename itself is durable."""

    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
