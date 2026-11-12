"""Precision timeline, recording identity and real waveform cache tests."""

from __future__ import annotations

import math
import os
import wave
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from suttara.media import playback_boundary_decision
from suttara.model import AudioReference, Section, VideoReference
from suttara.timeline import (
    TimelineTransform,
    TimelineWidget,
    adjacent_audio_boundary,
    excerpt_seek_from_fraction,
    grouped_marker_tooltip,
    goenka_markers,
    markers_for_recording,
    snap_timestamp,
    validate_strict_range,
)
from suttara.waveform import WaveformCache, decode_waveform


def test_time_coordinate_mapping_is_reversible_and_precise():
    transform = TimelineTransform(130.125, 155.875, 10.0, 1030.0)
    for timestamp in (130.125, 131.001, 142.5, 155.875):
        assert transform.x_to_time(transform.time_to_x(timestamp)) == pytest.approx(
            timestamp, abs=1e-12
        )


def test_authoring_handles_repaint_and_drag_safely(qapp):
    timeline = TimelineWidget(authoring=True)
    timeline.resize(900, 140)
    timeline.set_duration(100.0, reset_view=True)
    timeline.set_selection(10.0, 30.0)
    timeline.show()
    qapp.processEvents()
    try:
        for offset in range(40):
            timeline.set_selection(10.0 + offset / 100, 30.0 + offset / 100)
            qapp.processEvents()
            assert not timeline.grab().isNull()

        transform = timeline.transform()
        start_x = round(transform.time_to_x(timeline.selection_start))
        target_x = round(transform.time_to_x(15.0))
        QTest.mousePress(
            timeline, Qt.MouseButton.LeftButton, pos=QPoint(start_x, 45)
        )
        QTest.mouseMove(timeline, QPoint(target_x, 45))
        QTest.mouseRelease(
            timeline, Qt.MouseButton.LeftButton, pos=QPoint(target_x, 45)
        )
        qapp.processEvents()
        assert timeline.selection_start == pytest.approx(15.0, abs=0.2)
        assert not timeline.grab().isNull()
    finally:
        timeline.close()


def test_excerpt_relative_seeking_and_strict_range_validation():
    assert excerpt_seek_from_fraction(-1, 130, 155) == 130
    assert excerpt_seek_from_fraction(0.4, 130, 155) == 140
    assert excerpt_seek_from_fraction(2, 130, 155) == 155
    assert validate_strict_range(1, 2, 3) == ""
    assert "earlier" in validate_strict_range(2, 2, 3)
    assert "within" in validate_strict_range(1, 4, 3)


def test_snapping_uses_exact_stored_value_and_grouped_tooltips(tmp_path: Path):
    exact = 12.345678901
    assert snap_timestamp(12.34, [exact, 20.0], 0.01) == exact
    root = tmp_path
    recording = root / "suttara_sources/audio/spoken_pali/read.wav"
    recording.parent.mkdir(parents=True)
    recording.touch()
    stored = "suttara_sources/audio/spoken_pali/read.wav"
    first = Section(
        id="one", title="First", spoken_pali=AudioReference(stored, 0, exact)
    )
    second = Section(
        id="two", title="Second", spoken_pali=AudioReference(stored, exact, 20)
    )
    markers = markers_for_recording([first, second], "editing", "pali", stored, root)
    shared = [marker for marker in markers if marker.time == exact]
    assert {(marker.section_name, marker.kind) for marker in shared} == {
        ("First", "end"),
        ("Second", "start"),
    }
    tooltip = grouped_marker_tooltip(shared)
    assert "First (end)" in tooltip
    assert "Second (start)" in tooltip

    video_sections = [
        Section(id="v1", title="Chant one", goenka=VideoReference(end_seconds=exact)),
        Section(
            id="v2", title="Chant two",
            goenka=VideoReference(start_seconds=exact, end_seconds=20),
        ),
    ]
    video_shared = [
        marker for marker in goenka_markers(video_sections, "editing")
        if marker.time == exact
    ]
    assert {(marker.section_name, marker.kind) for marker in video_shared} == {
        ("Chant one", "end"),
        ("Chant two", "start"),
    }


def test_markers_are_filtered_by_recording_identity_and_current_section(tmp_path: Path):
    directory = tmp_path / "suttara_sources/audio/spoken_english"
    directory.mkdir(parents=True)
    for name in ("same.wav", "other.wav"):
        (directory / name).touch()
    same = "suttara_sources/audio/spoken_english/same.wav"
    sections = [
        Section(id="current", spoken_english=AudioReference(same, 1, 2)),
        Section(id="match", title="Match", spoken_english=AudioReference(same, 3, 4)),
        Section(
            id="other",
            title="Other",
            spoken_english=AudioReference(
                "suttara_sources/audio/spoken_english/other.wav", 5, 6
            ),
        ),
    ]
    markers = markers_for_recording(
        sections, "current", "english", same, tmp_path
    )
    assert [(marker.section_name, marker.time) for marker in markers] == [
        ("Match", 3),
        ("Match", 4),
    ]
    assert adjacent_audio_boundary(
        sections, "match", "english", same, tmp_path, "previous"
    ) == 2
    assert adjacent_audio_boundary(
        sections, "current", "english", same, tmp_path, "next"
    ) == 3


def _write_tone(path: Path, frequency: float) -> None:
    sample_rate = 8_000
    frames = bytearray()
    for index in range(sample_rate // 4):
        value = round(math.sin(2 * math.pi * frequency * index / sample_rate) * 20_000)
        frames.extend(int(value).to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)


def test_waveform_cache_generation_multiresolution_and_invalidation(tmp_path: Path):
    source = tmp_path / "tone.wav"
    _write_tone(source, 440)
    data = decode_waveform(source)
    assert data.duration == pytest.approx(0.25, abs=0.002)
    assert len(data.levels) > 2
    assert min(data.levels[0].minimum) < -0.5
    assert max(data.levels[0].maximum) > 0.5
    assert all(
        later.samples_per_peak == earlier.samples_per_peak * 2
        for earlier, later in zip(data.levels, data.levels[1:])
    )

    cache = WaveformCache(tmp_path)
    cache_path = cache.store(data)
    assert cache_path.is_file()
    assert cache.load(source) is not None

    old_mtime = source.stat().st_mtime_ns
    _write_tone(source, 880)
    os.utime(source, ns=(old_mtime + 10_000_000, old_mtime + 10_000_000))
    assert cache.load(source) is None


def test_playback_boundary_and_looping_logic():
    inside = playback_boundary_decision(14.9, 10, 15, False)
    assert inside.reached is False
    stopped = playback_boundary_decision(15, 10, 15, False)
    assert (stopped.target, stopped.continue_playing) == (15, False)
    looped = playback_boundary_decision(15.1, 10, 15, True)
    assert (looped.target, looped.continue_playing) == (10, True)
