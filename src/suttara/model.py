"""Data model for a Suttara study project.

The model stores *references* to source texts and recordings, never copies of
their contents.  That keeps ``suttara.json`` small and makes the distinction
between the user's study notes and third-party material explicit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .source_resources import GOENKA_URL

SCHEMA_VERSION = 1


@dataclass
class TextReference:
    """A passage in a local UTF-8 text or HTML source.

    Lines and columns are 1-based.  The start position is inclusive and the end
    position is exclusive.  Columns count Unicode characters (code points), not
    UTF-8 bytes.  For HTML sources the coordinates refer to the deterministic
    visible-text view described in :mod:`suttara.texts`.
    """

    path: str
    start_line: int = 1
    start_column: int = 1
    end_line: int = 1
    end_column: int = 1

    @classmethod
    def from_dict(cls, data: Any) -> "TextReference | None":
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("a text reference must be a JSON object")
        path = _string(data.get("path"), "text reference path")
        if not path:
            return None
        return cls(
            path=path,
            start_line=_positive_int(data.get("start_line", 1), "start_line"),
            start_column=_positive_int(data.get("start_column", 1), "start_column"),
            end_line=_positive_int(data.get("end_line", 1), "end_line"),
            end_column=_positive_int(data.get("end_column", 1), "end_column"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
        }


@dataclass
class AudioReference:
    """A local recording and the excerpt the user studies.

    ``end_seconds`` of 0 means "the end of the recording".  Explicit positive
    values are validated against the known media duration by the player.
    """

    path: str
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    loop: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "AudioReference | None":
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("an audio reference must be a JSON object")
        path = _string(data.get("path"), "audio reference path")
        if not path:
            return None
        return cls(
            path=path,
            start_seconds=_timestamp(data.get("start_seconds", 0.0), "start_seconds"),
            end_seconds=_timestamp(data.get("end_seconds", 0.0), "end_seconds"),
            loop=_boolean(data.get("loop", False), "loop"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "loop": self.loop,
        }


@dataclass
class VideoReference:
    """The fixed Goenka chanting video with section-specific excerpt settings.

    ``url`` is retained as a compatibility field for older code and project
    files.  The application always uses :data:`GOENKA_URL`; new project files
    serialise only the timestamps and loop preference.
    """

    url: str = field(default=GOENKA_URL, compare=False, repr=False)
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    loop: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "VideoReference | None":
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("a video reference must be a JSON object")
        # Older project files stored the URL; it is no longer section data.
        if (
            "url" in data
            and not _string(data.get("url"), "video reference url").strip()
            and not data.get("start_seconds")
            and not data.get("end_seconds")
            and not data.get("loop")
        ):
            return None
        return cls(
            url=GOENKA_URL,
            start_seconds=_timestamp(data.get("start_seconds", 0.0), "start_seconds"),
            end_seconds=_timestamp(data.get("end_seconds", 0.0), "end_seconds"),
            loop=_boolean(data.get("loop", False), "loop"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "loop": self.loop,
        }


@dataclass
class Section:
    """One user-curated study section.

    Every resource field is optional.  A section may contain just a title or
    notes while the user is still collecting material.
    """

    title: str = "Untitled section"
    notes: str = ""
    pali_text: TextReference | None = None
    english_text: TextReference | None = None
    spoken_pali: AudioReference | None = None
    spoken_english: AudioReference | None = None
    goenka: VideoReference | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Any) -> "Section":
        if not isinstance(data, dict):
            raise ValueError("a section must be a JSON object")
        known_keys = {
            "id",
            "title",
            "notes",
            "pali_text",
            "english_text",
            "spoken_pali",
            "spoken_english",
            "goenka",
        }
        section_id = _string(data.get("id"), "section id") or uuid.uuid4().hex
        return cls(
            id=section_id,
            title=_string(data.get("title", ""), "section title"),
            notes=_string(data.get("notes", ""), "section notes"),
            pali_text=TextReference.from_dict(data.get("pali_text")),
            english_text=TextReference.from_dict(data.get("english_text")),
            spoken_pali=AudioReference.from_dict(data.get("spoken_pali")),
            spoken_english=AudioReference.from_dict(data.get("spoken_english")),
            goenka=VideoReference.from_dict(data.get("goenka")),
            extra={key: value for key, value in data.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.extra)
        data.update(
            {
                "id": self.id,
                "title": self.title,
                "notes": self.notes,
                "pali_text": self.pali_text.to_dict() if self.pali_text else None,
                "english_text": self.english_text.to_dict()
                if self.english_text
                else None,
                "spoken_pali": self.spoken_pali.to_dict() if self.spoken_pali else None,
                "spoken_english": (
                    self.spoken_english.to_dict() if self.spoken_english else None
                ),
                "goenka": self.goenka.to_dict() if self.goenka else None,
            }
        )
        return data

    @property
    def display_title(self) -> str:
        title = self.title.strip()
        return title or "Untitled section"

    def resource_summary(self) -> str:
        """A compact description used in list-item tooltips."""

        parts: list[str] = []
        if self.pali_text:
            parts.append("Pāli passage")
        if self.english_text:
            parts.append("English passage")
        if self.spoken_pali:
            parts.append("spoken Pāli audio")
        if self.spoken_english:
            parts.append("spoken English audio")
        if self.goenka:
            parts.append("Goenka chanting")
        if self.notes.strip():
            parts.append("notes")
        return ", ".join(parts) if parts else "No resources yet"


@dataclass
class StudyProject:
    """The complete, ordered collection of study sections."""

    sections: list[Section] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.extra)
        data.update(
            {
                "schema_version": self.schema_version,
                "sections": [section.to_dict() for section in self.sections],
            }
        )
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "StudyProject":
        if not isinstance(data, dict):
            raise ValueError("the project root must be a JSON object")
        version = data.get("schema_version", 1)
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("schema_version must be an integer")
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"the project uses schema version {version}, but this version "
                f"of Suttara supports up to {SCHEMA_VERSION}"
            )
        if version < 1:
            raise ValueError("schema_version must be at least 1")
        sections_data = data.get("sections", [])
        if not isinstance(sections_data, list):
            raise ValueError("sections must be a JSON array")
        sections = [Section.from_dict(item) for item in sections_data]
        identifiers = [section.id for section in sections]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("section ids must be unique")
        return cls(
            schema_version=version,
            sections=sections,
            extra={
                key: value
                for key, value in data.items()
                if key not in {"schema_version", "sections"}
            },
        )

    # -- section operations -------------------------------------------------
    def add_section(
        self, title: str | None = None, index: int | None = None
    ) -> Section:
        """Insert a new section at ``index`` (or append) and return it."""

        section = Section(title=title or next_section_title(self.sections))
        if index is None:
            self.sections.append(section)
        else:
            index = max(0, min(index, len(self.sections)))
            self.sections.insert(index, section)
        return section

    def index_of(self, section_id: str) -> int:
        for index, section in enumerate(self.sections):
            if section.id == section_id:
                return index
        return -1

    def section_by_id(self, section_id: str) -> Section | None:
        for section in self.sections:
            if section.id == section_id:
                return section
        return None

    def move_section(self, index: int, offset: int) -> bool:
        """Move a section by ``offset`` positions; return whether it moved."""

        target = index + offset
        if (
            index < 0
            or index >= len(self.sections)
            or target < 0
            or target >= len(self.sections)
        ):
            return False
        section = self.sections.pop(index)
        self.sections.insert(target, section)
        return True

    def remove_section(self, index: int) -> Section:
        if index < 0 or index >= len(self.sections):
            raise IndexError("section index out of range")
        return self.sections.pop(index)


def next_section_title(sections: list[Section]) -> str:
    """Return an unused, human-friendly title for a new section."""

    used = {section.title.strip().casefold() for section in sections}
    counter = 1
    while f"section {counter}" in used:
        counter += 1
    return f"Section {counter}"


def _string(value: Any, what: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a string")
    return value


def _positive_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} must be an integer")
    if value < 1:
        raise ValueError(f"{what} must be at least 1")
    return value


def _timestamp(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number")
    value = float(value)
    if value < 0:
        raise ValueError(f"{what} cannot be negative")
    return value


def _boolean(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{what} must be true or false")
    return value
