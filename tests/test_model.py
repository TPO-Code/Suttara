"""Model, ordering and serialisation-shape tests."""

from __future__ import annotations

from suttara.model import (
    AudioReference,
    Section,
    StudyProject,
    TextReference,
    VideoReference,
    next_section_title,
)
from suttara.source_resources import GOENKA_URL


def test_section_fields_are_optional_and_defaults_are_independent():
    first = Section()
    second = Section()
    assert first.pali_text is None
    assert first.english_text is None
    assert first.spoken_pali is None
    assert first.spoken_english is None
    assert first.goenka is None
    assert first.notes == ""
    assert first.id != second.id


def test_reference_round_trips_keep_unicode_paths_and_timestamps():
    section = Section(
        title="Ānāpāna",
        notes="Ṭīkā notes — ünïcode",
        pali_text=TextReference(
            path="suttara_sources/texts/pali/Mahasatipatthana-02.htm",
            start_line=5,
            start_column=2,
            end_line=8,
            end_column=17,
        ),
        english_text=TextReference("english.htm", 1, 1, 2, 1),
        spoken_pali=AudioReference("pali.mp3", 4.5, 20.25, True),
        spoken_english=AudioReference("english.mp3", 0.0, 0.0, False),
        goenka=VideoReference(
            "https://www.youtube.com/watch?v=BrRDmiw0jvA", 60.0, 120.0, True
        ),
    )
    restored = Section.from_dict(section.to_dict())
    assert restored.title == "Ānāpāna"
    assert restored.notes == "Ṭīkā notes — ünïcode"
    assert restored.pali_text == section.pali_text
    assert restored.english_text == section.english_text
    assert restored.spoken_pali == section.spoken_pali
    assert restored.spoken_english == section.spoken_english
    assert restored.goenka == section.goenka
    assert restored.id == section.id


def test_empty_reference_paths_are_normalised_to_absent():
    section = Section.from_dict(
        {
            "title": "Only notes",
            "notes": "hello",
            "pali_text": {
                "path": "",
                "start_line": 1,
                "start_column": 1,
                "end_line": 1,
                "end_column": 2,
            },
            "spoken_pali": {"path": "", "start_seconds": 0, "end_seconds": 0},
            "goenka": {"url": ""},
        }
    )
    assert section.pali_text is None
    assert section.spoken_pali is None
    assert section.goenka is None


def test_project_ordering_operations():
    project = StudyProject()
    project.add_section("First")
    second = project.add_section("Second")
    inserted = project.add_section("Inserted", index=1)
    assert [s.title for s in project.sections] == ["First", "Inserted", "Second"]
    assert project.index_of(inserted.id) == 1

    assert project.move_section(1, -1) is True
    assert [s.title for s in project.sections] == ["Inserted", "First", "Second"]
    assert project.move_section(0, -1) is False
    assert project.move_section(2, 1) is False
    assert project.move_section(0, 1) is True
    assert [s.title for s in project.sections] == ["First", "Inserted", "Second"]

    removed = project.remove_section(1)
    assert removed is inserted
    assert [s.title for s in project.sections] == ["First", "Second"]
    assert project.section_by_id(second.id) is second
    assert project.section_by_id("missing") is None


def test_next_section_title_avoids_existing_titles():
    sections = [Section("Section 1"), Section("section 2")]
    assert next_section_title(sections) == "Section 3"
    assert next_section_title([]) == "Section 1"


def test_duplicate_section_ids_are_rejected():
    data = {
        "schema_version": 1,
        "sections": [
            {"id": "same", "title": "A"},
            {"id": "same", "title": "B"},
        ],
    }
    try:
        StudyProject.from_dict(data)
    except ValueError as error:
        assert "unique" in str(error)
    else:
        raise AssertionError("duplicate ids should be rejected")


def test_serialised_project_does_not_hold_passage_content():
    project = StudyProject()
    section = project.add_section("Text")
    section.pali_text = TextReference("pali.htm", 1, 1, 1, 10)
    payload = project.to_dict()
    assert payload["schema_version"] == 1
    assert payload["sections"][0]["pali_text"]["path"] == "pali.htm"
    assert "excerpt" not in payload["sections"][0]
    assert "text" not in payload["sections"][0]


def test_video_reference_uses_fixed_url_and_omits_it_from_json():
    reference = VideoReference(start_seconds=10, end_seconds=20, loop=True)
    assert reference.url == GOENKA_URL
    assert reference.to_dict() == {
        "start_seconds": 10.0,
        "end_seconds": 20.0,
        "loop": True,
    }
    restored = VideoReference.from_dict(
        {
            "url": "https://example.invalid/old",
            "start_seconds": 10,
            "end_seconds": 20,
            "loop": True,
        }
    )
    assert restored is not None
    assert restored == reference
    assert restored.url == GOENKA_URL


def test_unrecognised_project_and_section_fields_are_preserved():
    project = StudyProject.from_dict(
        {
            "schema_version": 1,
            "future_project_setting": {"enabled": True},
            "sections": [
                {
                    "id": "one",
                    "title": "One",
                    "future_section_setting": [1, 2, 3],
                }
            ],
        }
    )
    assert project.extra["future_project_setting"] == {"enabled": True}
    assert project.sections[0].extra["future_section_setting"] == [1, 2, 3]
    payload = project.to_dict()
    assert payload["future_project_setting"] == {"enabled": True}
    assert payload["sections"][0]["future_section_setting"] == [1, 2, 3]
