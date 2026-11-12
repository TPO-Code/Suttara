"""Fixed study resources and discovery of the bundled local recordings.

Suttara has one Pāli text, one English text and one Goenka chanting video.
Only the local spoken recordings are selected per section, from the two known
audio directories.  Keeping these conventions in one module prevents the UI
from inventing or silently changing resource paths.
"""

from __future__ import annotations

import re
from pathlib import Path

GOENKA_URL = "https://www.youtube.com/watch?v=BrRDmiw0jvA"
GOENKA_VIDEO_ID = "BrRDmiw0jvA"

TEXT_RESOURCES = {
    "pali": "suttara_sources/texts/mahasatipatthana_pali.txt",
    "english": "suttara_sources/texts/mahasatipatthana_english.txt",
}
AUDIO_DIRECTORIES = {
    "pali": "suttara_sources/audio/spoken_pali",
    "english": "suttara_sources/audio/spoken_english",
}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".oga", ".m4a", ".aac", ".flac"}


def text_resource_relative(language: str) -> str:
    """Return the fixed repository-relative text path for a language."""

    try:
        return TEXT_RESOURCES[language]
    except KeyError as error:
        raise ValueError(f"unknown text language: {language}") from error


def text_resource_path(project_root: Path, language: str) -> Path:
    return Path(project_root).expanduser().resolve() / text_resource_relative(language)


def audio_directory(project_root: Path, language: str) -> Path:
    try:
        relative = AUDIO_DIRECTORIES[language]
    except KeyError as error:
        raise ValueError(f"unknown audio language: {language}") from error
    return Path(project_root).expanduser().resolve() / relative


def audio_directory_relative(language: str) -> str:
    try:
        return AUDIO_DIRECTORIES[language]
    except KeyError as error:
        raise ValueError(f"unknown audio language: {language}") from error


def discover_recordings(project_root: Path, language: str) -> list[Path]:
    """Return the recordings available for one language, sorted naturally."""

    directory = audio_directory(project_root, language)
    try:
        recordings = [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
        ]
    except OSError:
        return []
    return sorted(recordings, key=lambda path: _natural_key(path.name))


def recording_label(path: Path) -> str:
    """A friendly label for a discovered audio file."""

    stem = Path(path).stem
    prefix, separator, rest = stem.partition("-")
    if separator and prefix.isdigit() and rest:
        return f"{prefix} · {rest.replace('-', ' ').replace('_', ' ')}"
    return stem.replace("_", " ").replace("-", " ")


def recording_relative(project_root: Path, language: str, path: Path) -> str:
    """Store a discovered recording as a repository-relative path."""

    root = Path(project_root).expanduser().resolve()
    resolved = Path(path).expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def canonical_recording_relative(
    project_root: Path, language: str, stored_or_path: str | Path | None
) -> str | None:
    """Map any existing reference to the canonical recording in its directory.

    Old projects may contain absolute paths or paths from a previous download
    layout.  If the file's name exists in the fixed directory, its canonical
    repository-relative path is returned.  Unknown files are preserved rather
    than discarded.
    """

    if not stored_or_path:
        return None
    candidate = Path(stored_or_path).expanduser()
    directory = audio_directory(project_root, language)
    search = candidate if candidate.is_absolute() else directory / candidate.name
    if not search.exists():
        search = directory / candidate.name
    if search.exists() and search.is_file():
        if search.parent.resolve() == directory.resolve():
            return recording_relative(project_root, language, search)
    if candidate.is_absolute():
        return recording_relative(project_root, language, candidate)
    return str(candidate)


def _natural_key(name: str) -> list[object]:
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", name)
    ]
