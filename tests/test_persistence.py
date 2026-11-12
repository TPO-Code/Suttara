"""Atomic persistence, malformed-file handling and path resolution tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from suttara.model import (
    AudioReference,
    StudyProject,
    TextReference,
    VideoReference,
)
from suttara.persistence import (
    ProjectLoadError,
    backup_file,
    load_project,
    normalise_project_resources,
    project_file_for,
    resolved_path,
    save_project,
    stored_path,
)
from suttara.source_resources import GOENKA_URL


def test_save_and_load_round_trip_in_utf8(tmp_path: Path):
    project = StudyProject()
    section = project.add_section("Dhammānupassanā")
    section.notes = "Pāli diacritics: ā ī ū ṅ ñ ṭ ḍ ṇ ṁ"
    section.pali_text = TextReference("texts/pali.txt", 2, 3, 4, 5)
    section.english_text = TextReference("/external/english.txt", 1, 1, 1, 9)
    section.spoken_pali = AudioReference("audio/pali.mp3", 1.25, 30.5, True)
    section.spoken_english = AudioReference("/external/english.mp3", 0, 0, False)
    section.goenka = VideoReference("BrRDmiw0jvA", 10, 20, True)

    path = tmp_path / "suttara.json"
    save_project(project, path)
    raw = path.read_text(encoding="utf-8")
    assert "Dhammānupassanā" in raw
    assert "Pāli diacritics" in raw
    assert "schema_version" in raw
    assert not list(tmp_path.glob("*.tmp"))

    loaded = load_project(path)
    assert loaded.sections == project.sections


def test_missing_project_file_starts_empty(tmp_path: Path):
    assert load_project(tmp_path / "suttara.json").sections == []


def test_malformed_json_is_reported_and_file_is_unchanged(tmp_path: Path):
    path = tmp_path / "suttara.json"
    original = '{"schema_version": 1, "sections": [ this is broken'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ProjectLoadError) as caught:
        load_project(path)
    assert "not valid JSON" in str(caught.value)
    assert path.read_text(encoding="utf-8") == original


def test_future_schema_is_rejected(tmp_path: Path):
    path = tmp_path / "suttara.json"
    path.write_text('{"schema_version": 99, "sections": []}', encoding="utf-8")
    with pytest.raises(ProjectLoadError) as caught:
        load_project(path)
    assert "schema version 99" in str(caught.value)


def test_bad_reference_shapes_are_rejected(tmp_path: Path):
    path = tmp_path / "suttara.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sections": [
                    {
                        "title": "Bad",
                        "pali_text": {
                            "path": "x.txt",
                            "start_line": 0,
                            "start_column": 1,
                            "end_line": 1,
                            "end_column": 2,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProjectLoadError):
        load_project(path)


def test_backup_creates_a_distinct_copy(tmp_path: Path):
    path = tmp_path / "suttara.json"
    path.write_text("not json", encoding="utf-8")
    backup = backup_file(path)
    assert backup is not None
    assert backup.exists()
    assert backup != path
    assert backup.read_text(encoding="utf-8") == "not json"
    second = backup_file(path)
    assert second is not None and second != backup


def test_paths_inside_repository_are_stored_relative(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    inside = root / "suttara_sources" / "texts" / "pali.htm"
    outside = tmp_path / "elsewhere" / "audio.mp3"
    assert stored_path(inside, root) == "suttara_sources/texts/pali.htm"
    assert resolved_path("suttara_sources/texts/pali.htm", root) == inside
    assert stored_path(outside, root) == str(outside.resolve())
    assert resolved_path(str(outside.resolve()), root) == outside.resolve()
    assert stored_path("", root) == ""
    assert resolved_path("", root) is None


def test_project_file_name_is_fixed(tmp_path: Path):
    assert project_file_for(tmp_path) == tmp_path.resolve() / "suttara.json"


def test_unrecognised_fields_are_preserved(tmp_path: Path):
    path = tmp_path / "suttara.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin_settings": {"irrelevant": True},
                "sections": [
                    {
                        "title": "Fine",
                        "unknown": 42,
                        "pali_text": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    project = load_project(path)
    assert project.sections[0].title == "Fine"
    assert project.extra["plugin_settings"] == {"irrelevant": True}
    assert project.sections[0].extra["unknown"] == 42


def test_existing_resources_are_migrated_to_fixed_conventions(tmp_path: Path):
    root = tmp_path / "repository"
    text_dir = root / "suttara_sources" / "texts"
    audio_dir = root / "suttara_sources" / "audio" / "spoken_pali"
    text_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    pali_text = text_dir / "mahasatipatthana_pali.txt"
    pali_text.write_text("source\n", encoding="utf-8")
    audio = audio_dir / "01-Uddeso.mp3"
    audio.write_bytes(b"")

    project = StudyProject()
    section = project.add_section("Migration")
    section.pali_text = TextReference(str(pali_text), 1, 1, 1, 2)
    section.english_text = TextReference(
        "suttara_sources/texts/mahasatipatthana_english.txt", 1, 1, 1, 2
    )
    section.spoken_pali = AudioReference(str(audio), 3.0, 8.0, True)
    section.goenka = VideoReference("https://example.invalid/old", 10.0, 20.0, False)

    normalise_project_resources(project, root)
    assert section.pali_text.path == "suttara_sources/texts/mahasatipatthana_pali.txt"
    assert section.english_text.path == (
        "suttara_sources/texts/mahasatipatthana_english.txt"
    )
    assert section.spoken_pali.path == (
        "suttara_sources/audio/spoken_pali/01-Uddeso.mp3"
    )
    assert section.goenka.url == GOENKA_URL
    assert section.goenka.start_seconds == 10.0
    assert section.goenka.end_seconds == 20.0
    assert "url" not in section.goenka.to_dict()
