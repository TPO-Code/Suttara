"""Focused tests for the study players and the Section Editor widgets."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QTextCursor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QSizePolicy

from suttara.model import AudioReference, Section, TextReference, VideoReference
from suttara.source_resources import GOENKA_URL
from suttara.ui.audio_config import AudioConfigEditor
from suttara.ui.audio_player import AudioExcerptPlayer
from suttara.ui.section_editor import SectionEditorDialog
from suttara.ui.source_range_selector import SourceRangeSelector
from suttara.ui.youtube_player import YouTubeExcerptPlayer

PALI_TEXT = "Dīgha Nikāya 22\n\nMahāsatipaṭṭhānasutta\n\nEvaṁ me sutaṁ.\n"
ENGLISH_TEXT = (
    "Long Discourses 22\n\n"
    "The Longer Discourse on Mindfulness Meditation\n\n"
    "So I have heard.\n"
)


def make_resource_root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    text_dir = root / "suttara_sources" / "texts"
    text_dir.mkdir(parents=True)
    (text_dir / "mahasatipatthana_pali.txt").write_text(PALI_TEXT, encoding="utf-8")
    (text_dir / "mahasatipatthana_english.txt").write_text(
        ENGLISH_TEXT, encoding="utf-8"
    )
    pali_audio = root / "suttara_sources" / "audio" / "spoken_pali"
    english_audio = root / "suttara_sources" / "audio" / "spoken_english"
    pali_audio.mkdir(parents=True)
    english_audio.mkdir(parents=True)
    (pali_audio / "01-Uddeso.mp3").write_bytes(b"")
    (pali_audio / "02-Anapanapabbam.mp3").write_bytes(b"")
    (english_audio / "01-Mindfulness.mp3").write_bytes(b"")
    return root


def test_source_range_selector_uses_exclusive_selection_coordinates(
    qapp, tmp_path: Path
):
    root = make_resource_root(tmp_path)
    selector = SourceRangeSelector("Pāli text", "pali", root)
    try:
        selector.show()
        qapp.processEvents()
        document = selector.editor.document()
        block = document.findBlockByNumber(2)
        cursor = QTextCursor(document)
        cursor.setPosition(block.position())
        cursor.setPosition(
            block.position() + len(block.text()),
            QTextCursor.MoveMode.KeepAnchor,
        )
        selector.editor.setTextCursor(cursor)
        selector._selection_changed()
        assert selector.use_button.isEnabled()
        assert "Selection: line 3" in selector.status_label.text()
        selector._use_selection()
        reference = selector.reference()
        assert reference == TextReference(
            "suttara_sources/texts/mahasatipatthana_pali.txt",
            3,
            1,
            3,
            len(block.text()) + 1,
        )
        assert len(selector.editor.extraSelections()) == 1
        selector._clear()
        assert selector.reference() is None
        assert selector.editor.extraSelections() == []
    finally:
        selector.close()


def test_audio_config_discovers_recording_files_and_values(qapp, tmp_path: Path):
    root = make_resource_root(tmp_path)
    editor = AudioConfigEditor("Spoken Pāli", "pali", root)
    try:
        assert editor.recording_combo.itemText(0) == "No recording assigned"
        assert editor.recording_combo.count() == 3
        editor.recording_combo.setCurrentIndex(1)
        editor.start_spin.setValue(12.5)
        editor.end_spin.setValue(30.25)
        editor.loop_check.setChecked(True)
        value = editor.reference()
        assert value is not None
        assert value.path == "suttara_sources/audio/spoken_pali/01-Uddeso.mp3"
        assert value.start_seconds == 12.5
        assert value.end_seconds == 30.25
        assert value.loop is True
        assert editor.validation_error() == ""
    finally:
        editor.close()


def test_audio_editor_has_free_and_excerpt_playback_modes(qapp, tmp_path: Path):
    root = make_resource_root(tmp_path)
    editor = AudioConfigEditor("Spoken English", "english", root)

    class PlayerSpy:
        def __init__(self):
            self.state = QMediaPlayer.PlaybackState.StoppedState
            self.position_ms = 25_000

        def playbackState(self):
            return self.state

        def position(self):
            return self.position_ms

        def setPosition(self, value):
            self.position_ms = value

        def play(self):
            self.state = QMediaPlayer.PlaybackState.PlayingState

        def pause(self):
            self.state = QMediaPlayer.PlaybackState.PausedState

    try:
        editor.recording_combo.setCurrentIndex(1)
        editor._apply_duration(100.0)
        editor.start_spin.setValue(10.0)
        editor.end_spin.setValue(20.0)
        player = PlayerSpy()
        editor.player = player  # type: ignore[assignment]

        # Full mode continues from the cursor even when it is beyond the excerpt.
        editor.toggle_full_play()
        assert editor._playback_mode == "full"
        assert player.position_ms == 25_000
        editor._enforce_boundary()
        assert player.state == QMediaPlayer.PlaybackState.PlayingState
        assert player.position_ms == 25_000
        editor.toggle_full_play()
        assert player.state == QMediaPlayer.PlaybackState.PausedState

        # Excerpt mode starts at S and clamps all seeking to S/E.
        editor.toggle_excerpt_play()
        assert editor._playback_mode == "excerpt"
        assert player.position_ms == 10_000
        editor._seek(5.0)
        assert player.position_ms == 10_000
        editor._seek(30.0)
        assert player.position_ms == 20_000
        editor._enforce_boundary()
        assert player.state == QMediaPlayer.PlaybackState.PausedState

        # From E, full playback can immediately continue into material after E.
        editor.toggle_full_play()
        assert player.position_ms == 20_000
        assert player.state == QMediaPlayer.PlaybackState.PlayingState

        assert editor.play_button.text() == "Play full recording"
        assert editor.excerpt_play_button.text() == "Play selected excerpt"
        assert (
            editor.set_start_button.sizePolicy().horizontalPolicy()
            == QSizePolicy.Policy.Fixed
        )
        assert (
            editor.set_end_button.sizePolicy().horizontalPolicy()
            == QSizePolicy.Policy.Fixed
        )
    finally:
        editor._guard.stop()
        editor.player = None
        editor.close()


def test_playback_widgets_expose_no_resource_configuration_controls(
    qapp, tmp_path: Path
):
    root = make_resource_root(tmp_path)
    audio = AudioExcerptPlayer("Spoken Pāli", root)
    video = YouTubeExcerptPlayer("Goenka chanting", root)
    try:
        for widget in (
            audio,
            video,
        ):
            assert not hasattr(widget, "path_edit")
            assert not hasattr(widget, "choose_button")
            assert not hasattr(widget, "url_edit")
            assert not hasattr(widget, "start_spin")
            assert not hasattr(widget, "end_spin")
        assert hasattr(audio, "play_button")
        assert hasattr(audio, "position_slider")
        assert hasattr(audio, "loop_check")
        assert hasattr(video, "play_button")
        assert hasattr(video, "position_slider")
        assert hasattr(video, "loop_check")
        assert video._view is None
        video.set_reference(None)
        assert video.play_button.isEnabled() is False
    finally:
        audio.close()
        video.close()


def test_section_editor_combines_sources_audio_and_goenka(qapp, tmp_path: Path):
    root = make_resource_root(tmp_path)
    original = Section(title="Original")
    dialog = SectionEditorDialog(original, root, is_new=True)
    try:
        dialog.show()
        qapp.processEvents()
        assert dialog.pali_text.editor.blockCount() == len(PALI_TEXT.splitlines())
        assert dialog.english_text.editor.blockCount() == len(ENGLISH_TEXT.splitlines())
        dialog.title_edit.setText("Combined")
        dialog.pali_text.start_line.setValue(3)
        dialog.pali_text.start_column.setValue(1)
        dialog.pali_text.end_line.setValue(3)
        dialog.pali_text.end_column.setValue(len("Mahāsatipaṭṭhānasutta") + 1)
        dialog.english_text.start_line.setValue(3)
        dialog.english_text.start_column.setValue(1)
        dialog.english_text.end_line.setValue(3)
        dialog.english_text.end_column.setValue(
            len("The Longer Discourse on Mindfulness Meditation") + 1
        )
        dialog.pali_audio.recording_combo.setCurrentIndex(1)
        dialog.pali_audio.start_spin.setValue(4)
        dialog.pali_audio.end_spin.setValue(20)
        dialog.english_audio.recording_combo.setCurrentIndex(1)
        dialog.english_audio.start_spin.setValue(2)
        dialog.english_audio.end_spin.setValue(10)
        dialog.goenka.enabled_check.setChecked(True)
        dialog.goenka.start_spin.setValue(30)
        dialog.goenka.end_spin.setValue(60)
        dialog.goenka.loop_check.setChecked(True)
        dialog.accept()
        edited = dialog.edited_section()
        assert edited is not None
        assert edited.title == "Combined"
        assert edited.pali_text is not None
        assert edited.pali_text.start_line == 3
        assert edited.english_text is not None
        assert edited.english_text.start_line == 3
        assert edited.spoken_pali is not None
        assert edited.spoken_pali.path.endswith("01-Uddeso.mp3")
        assert edited.spoken_english is not None
        assert edited.goenka is not None
        assert edited.goenka.url == GOENKA_URL
        assert "url" not in edited.goenka.to_dict()
        # Cancel/edit is on a copy.
        assert original.title == "Original"
        assert original.pali_text is None
        assert original.spoken_pali is None
    finally:
        dialog.close()


def test_section_editor_cancel_leaves_section_unchanged(qapp, tmp_path: Path):
    root = make_resource_root(tmp_path)
    original = Section(
        title="Keep me",
        notes="notes",
        pali_text=TextReference("old", 1, 1, 2, 1),
        spoken_pali=AudioReference("old.mp3", 1, 2, False),
    )
    dialog = SectionEditorDialog(original, root)
    try:
        dialog.title_edit.setText("Do not save")
        dialog.reject()
        assert dialog.edited_section() is None
        assert original.title == "Keep me"
        assert original.notes == "notes"
        assert original.pali_text is not None
        assert original.pali_text.path == "old"
        assert original.spoken_pali is not None
        assert original.spoken_pali.path == "old.mp3"
    finally:
        dialog.close()


def test_cancel_discards_waveform_handle_and_timestamp_changes(qapp, tmp_path: Path):
    root = make_resource_root(tmp_path)
    stored = "suttara_sources/audio/spoken_pali/01-Uddeso.mp3"
    original = Section(
        title="Keep authored range",
        spoken_pali=AudioReference(stored, 1.125, 2.875, True),
    )
    dialog = SectionEditorDialog(original, root, sections=[original])
    try:
        dialog.pali_audio._apply_duration(10.0)
        dialog.pali_audio._timeline_range_changed(3.25, 7.75)
        assert dialog.pali_audio.start_spin.value() == 3.25
        assert dialog.pali_audio.end_spin.value() == 7.75
        dialog.reject()
        assert original.spoken_pali is not None
        assert original.spoken_pali.start_seconds == 1.125
        assert original.spoken_pali.end_seconds == 2.875
        assert original.spoken_pali.loop is True
    finally:
        dialog.close()


def test_video_reference_always_uses_the_fixed_url():
    reference = VideoReference(start_seconds=1, end_seconds=2, loop=True)
    assert reference.url == GOENKA_URL
    assert "url" not in reference.to_dict()


def test_goenka_zoom_survives_repeated_duration_polls(qapp, tmp_path: Path):
    root = make_resource_root(tmp_path)
    dialog = SectionEditorDialog(
        Section(
            title="Chanting",
            goenka=VideoReference(start_seconds=10, end_seconds=30),
        ),
        root,
    )
    try:
        editor = dialog.goenka
        editor._duration_changed(200.0)
        editor.timeline.zoom(4.0, anchor=50.0)
        before = (editor.timeline.visible_start, editor.timeline.visible_end)
        assert before != (0.0, 200.0)

        # The IFrame state poll reports the same duration repeatedly.
        editor._duration_changed(200.0)
        editor._duration_changed(200.0)
        assert (editor.timeline.visible_start, editor.timeline.visible_end) == before
    finally:
        dialog.close()


def test_goenka_play_selected_excerpt_uses_authored_range(qapp, tmp_path: Path):
    root = make_resource_root(tmp_path)
    dialog = SectionEditorDialog(
        Section(
            title="Chanting",
            goenka=VideoReference(start_seconds=12.25, end_seconds=18.75, loop=True),
        ),
        root,
    )

    class PreviewSpy:
        def __init__(self):
            self.reference = None
            self.play_count = 0

        def has_embedded_player(self):
            return False

        def set_reference(self, reference, *, load_embed=True):
            self.reference = reference

        def toggle_play(self):
            self.play_count += 1

    try:
        spy = PreviewSpy()
        dialog.goenka.preview = spy  # type: ignore[assignment]
        dialog.goenka._play_excerpt()
        assert spy.reference is not None
        assert spy.reference.start_seconds == 12.25
        assert spy.reference.end_seconds == 18.75
        assert spy.reference.loop is True
        assert spy.play_count == 1
        assert dialog.goenka.play_excerpt_button.text() == "Play selected excerpt"
    finally:
        dialog.close()
