"""Fixed resource constants and recording discovery tests."""

from __future__ import annotations

from pathlib import Path

from suttara.source_resources import (
    GOENKA_URL,
    audio_directory_relative,
    canonical_recording_relative,
    discover_recordings,
    recording_label,
    text_resource_relative,
)


def make_audio_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    pali = root / "suttara_sources" / "audio" / "spoken_pali"
    english = root / "suttara_sources" / "audio" / "spoken_english"
    pali.mkdir(parents=True)
    english.mkdir(parents=True)
    (pali / "10-Tenth.mp3").write_bytes(b"")
    (pali / "02-Second.mp3").write_bytes(b"")
    (pali / "01-First.wav").write_bytes(b"")
    (pali / "notes.txt").write_text("not audio", encoding="utf-8")
    (english / "01-Mindfulness.mp3").write_bytes(b"")
    return root


def test_fixed_resource_conventions():
    assert text_resource_relative("pali").endswith("mahasatipatthana_pali.txt")
    assert text_resource_relative("english").endswith("mahasatipatthana_english.txt")
    assert GOENKA_URL == "https://www.youtube.com/watch?v=BrRDmiw0jvA"
    assert audio_directory_relative("pali").endswith("spoken_pali")


def test_discovery_is_limited_to_known_audio_extensions_and_sorted(tmp_path: Path):
    root = make_audio_tree(tmp_path)
    names = [path.name for path in discover_recordings(root, "pali")]
    assert names == ["01-First.wav", "02-Second.mp3", "10-Tenth.mp3"]
    assert discover_recordings(root, "english")[0].name == "01-Mindfulness.mp3"


def test_recording_labels_and_canonical_paths(tmp_path: Path):
    root = make_audio_tree(tmp_path)
    path = root / "suttara_sources/audio/spoken_pali/02-Second.mp3"
    assert recording_label(path) == "02 · Second"
    assert (
        canonical_recording_relative(
            root, "pali", "suttara_sources/audio/spoken_pali/02-Second.mp3"
        )
        == "suttara_sources/audio/spoken_pali/02-Second.mp3"
    )
    assert canonical_recording_relative(root, "pali", str(path)) == (
        "suttara_sources/audio/spoken_pali/02-Second.mp3"
    )
    assert canonical_recording_relative(root, "pali", "unknown.mp3") == "unknown.mp3"
