"""Full-recording waveform authoring for the Section Editor."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QSignalBlocker, QTimer, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QScrollBar, QSizePolicy, QVBoxLayout, QWidget,
)

from ..media import format_seconds
from ..model import AudioReference, Section
from ..persistence import resolved_path
from ..source_resources import (
    audio_directory_relative, canonical_recording_relative, discover_recordings,
    recording_label, recording_relative,
)
from ..timeline import (
    TimelineWidget, adjacent_audio_boundary, markers_for_recording,
    validate_strict_range,
)
from ..waveform import WaveformData, WaveformLoader

MAX_SECONDS = 1_000_000.0


class AudioConfigEditor(QGroupBox):
    """Known recording selector plus an interactive full-file waveform."""

    changed = Signal()

    def __init__(
        self, title: str, language: str, project_root: Path,
        reference: AudioReference | None = None, parent: QWidget | None = None,
        *, sections: Sequence[Section] = (), current_section_id: str = "",
    ) -> None:
        super().__init__(title, parent)
        self.language = language
        self.project_root = Path(project_root)
        self.sections = sections
        self.current_section_id = current_section_id
        self._recordings = discover_recordings(project_root, language)
        self._loading = False
        self._duration = 0.0
        self._waveform_generation = 0
        self._open_end = bool(reference and reference.end_seconds == 0)
        self._end_touched = False
        self._playback_mode: str | None = None

        self.player: QMediaPlayer | None = None
        self.audio_output: QAudioOutput | None = None
        self.waveform_loader = WaveformLoader(project_root, self)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self.recording_combo = QComboBox()
        self.recording_combo.setToolTip(
            f"Recordings in {audio_directory_relative(language)}"
        )
        top.addWidget(self.recording_combo, 1)
        self.duration_label = QLabel("Duration --:--")
        top.addWidget(self.duration_label)
        layout.addLayout(top)

        self.timeline = TimelineWidget(authoring=True)
        layout.addWidget(self.timeline)
        self.timeline_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.timeline_scroll.setRange(0, 10_000)
        layout.addWidget(self.timeline_scroll)

        navigation = QHBoxLayout()
        self.play_button = QPushButton("Play full recording")
        self.play_button.setToolTip(
            "Play or pause freely from the current cursor without excerpt boundaries"
        )
        self.excerpt_play_button = QPushButton("Play selected excerpt")
        self.excerpt_play_button.setToolTip(
            "Play or pause between the S and E handles"
        )
        self.stop_button = QPushButton("Stop")
        self.zoom_in_button = QPushButton("Zoom in")
        self.zoom_out_button = QPushButton("Zoom out")
        self.zoom_all_button = QPushButton("Show all")
        self.time_label = QLabel("0:00.0 / --:--")
        for widget in (
            self.play_button, self.excerpt_play_button, self.stop_button,
            self.zoom_in_button,
            self.zoom_out_button, self.zoom_all_button,
        ):
            navigation.addWidget(widget)
        navigation.addStretch(1)
        navigation.addWidget(self.time_label)
        layout.addLayout(navigation)

        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Start"))
        self.start_spin = self._time_spin()
        range_row.addWidget(self.start_spin)
        self.set_start_button = QPushButton("Set start at cursor")
        self._make_compact(self.set_start_button)
        range_row.addWidget(self.set_start_button)
        range_row.addWidget(QLabel("End"))
        self.end_spin = self._time_spin()
        range_row.addWidget(self.end_spin)
        self.set_end_button = QPushButton("Set end at cursor")
        self._make_compact(self.set_end_button)
        range_row.addWidget(self.set_end_button)
        self.loop_check = QCheckBox("Loop excerpt")
        range_row.addWidget(self.loop_check)
        layout.addLayout(range_row)

        reuse_row = QHBoxLayout()
        self.previous_button = QPushButton("Use preceding end")
        self.next_button = QPushButton("Use following start")
        self.previous_button.setToolTip(
            "Use the exact nearest earlier end marker for this recording"
        )
        self.next_button.setToolTip(
            "Use the exact nearest later start marker for this recording"
        )
        reuse_row.addWidget(self.previous_button)
        reuse_row.addWidget(self.next_button)
        reuse_row.addStretch(1)
        layout.addLayout(reuse_row)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self._populate_combo(reference)
        self.set_reference(reference)
        self.recording_combo.currentIndexChanged.connect(self._recording_changed)
        self.start_spin.valueChanged.connect(self._spins_changed)
        self.end_spin.valueChanged.connect(self._end_spin_changed)
        self.loop_check.toggled.connect(self._value_changed)
        self.timeline.seek_requested.connect(self._seek)
        self.timeline.range_changed.connect(self._timeline_range_changed)
        self.timeline.view_changed.connect(self._timeline_view_changed)
        self.timeline_scroll.valueChanged.connect(self._scroll_timeline)
        self.play_button.clicked.connect(self.toggle_full_play)
        self.excerpt_play_button.clicked.connect(self.toggle_excerpt_play)
        self.stop_button.clicked.connect(self.stop)
        self.zoom_in_button.clicked.connect(lambda: self.timeline.zoom(2.0))
        self.zoom_out_button.clicked.connect(lambda: self.timeline.zoom(0.5))
        self.zoom_all_button.clicked.connect(self._show_all)
        self.set_start_button.clicked.connect(self._set_start_at_cursor)
        self.set_end_button.clicked.connect(self._set_end_at_cursor)
        self.previous_button.clicked.connect(self._use_preceding_end)
        self.next_button.clicked.connect(self._use_following_start)
        self._guard = QTimer(self)
        self._guard.setInterval(50)
        self._guard.timeout.connect(self._enforce_boundary)

    @staticmethod
    def _time_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, MAX_SECONDS)
        spin.setDecimals(3)
        spin.setSingleStep(0.1)
        spin.setSuffix(" s")
        spin.setMinimumWidth(105)
        return spin

    @staticmethod
    def _make_compact(button: QPushButton) -> None:
        button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        button.setMaximumWidth(button.sizeHint().width())

    def reference(self) -> AudioReference | None:
        stored = self.recording_combo.currentData()
        if not stored:
            return None
        end = float(self.end_spin.value())
        if (
            self._open_end and not self._end_touched and self._duration > 0
            and abs(end - self._duration) < 0.001
        ):
            end = 0.0
        return AudioReference(
            str(stored), float(self.start_spin.value()), end,
            self.loop_check.isChecked(),
        )

    def set_reference(self, reference: AudioReference | None) -> None:
        self._loading = True
        try:
            self._select_recording(reference.path if reference else None)
            self.start_spin.setValue(reference.start_seconds if reference else 0.0)
            self.end_spin.setValue(reference.end_seconds if reference else 0.0)
            self.loop_check.setChecked(bool(reference and reference.loop))
            self._open_end = bool(reference and reference.end_seconds == 0)
            self._end_touched = False
        finally:
            self._loading = False
        self._load_recording()

    def validation_error(self) -> str:
        if not self.recording_combo.currentData():
            return ""
        if self._duration <= 0:
            start, end = self.start_spin.value(), self.end_spin.value()
            return "End time must be later than start time" if end > 0 and end <= start else ""
        return validate_strict_range(
            float(self.start_spin.value()), self._effective_end(), self._duration
        )

    def _populate_combo(self, reference: AudioReference | None) -> None:
        self.recording_combo.addItem("No recording assigned", None)
        discovered: set[str] = set()
        for path in self._recordings:
            stored = recording_relative(self.project_root, self.language, path)
            discovered.add(stored)
            self.recording_combo.addItem(recording_label(path), stored)
        if reference and reference.path:
            canonical = canonical_recording_relative(
                self.project_root, self.language, reference.path
            )
            if canonical not in discovered:
                self.recording_combo.addItem(
                    f"Existing: {Path(reference.path).name}", reference.path
                )

    def _select_recording(self, stored: str | None) -> None:
        if not stored:
            self.recording_combo.setCurrentIndex(0)
            return
        canonical = canonical_recording_relative(
            self.project_root, self.language, stored
        )
        for index in range(self.recording_combo.count()):
            if self.recording_combo.itemData(index) in {canonical, stored}:
                self.recording_combo.setCurrentIndex(index)
                return

    def _recording_changed(self, _index: int) -> None:
        if self._loading:
            return
        self._open_end = True
        self._end_touched = False
        self._loading = True
        self.start_spin.setValue(0.0)
        self.end_spin.setValue(0.0)
        self._loading = False
        self._load_recording()
        self.changed.emit()

    def _load_recording(self) -> None:
        self.stop()
        self._duration = 0.0
        self.duration_label.setText("Duration --:--")
        self.timeline.set_waveform(None)
        self.timeline.set_duration(0.0, reset_view=True)
        stored = self.recording_combo.currentData()
        assigned = bool(stored)
        for widget in (
            self.start_spin, self.end_spin, self.loop_check, self.play_button,
            self.excerpt_play_button,
            self.stop_button, self.set_start_button, self.set_end_button,
            self.zoom_in_button, self.zoom_out_button, self.zoom_all_button,
        ):
            widget.setEnabled(assigned)
        if not assigned:
            if self.player is not None:
                self.player.setSource(QUrl())
            self.timeline.set_markers([])
            self.status_label.setText(
                "No recording assigned. Choose one from the dropdown."
            )
            self._update_reuse_buttons()
            return
        self.timeline.set_markers(markers_for_recording(
            self.sections, self.current_section_id, self.language,
            str(stored), self.project_root,
        ))
        self._update_reuse_buttons()
        path = resolved_path(str(stored), self.project_root)
        if path is None or not path.is_file():
            if self.player is not None:
                self.player.setSource(QUrl())
            self.status_label.setText(
                f"Recording not found: {Path(str(stored)).name}"
            )
            return
        if path.stat().st_size == 0:
            if self.player is not None:
                self.player.setSource(QUrl())
            self.status_label.setText(
                f"Waveform unavailable: {path.name} is empty. Playback may still work."
            )
            return
        self._ensure_player().setSource(QUrl.fromLocalFile(str(path)))
        self.status_label.setText(f"Loading waveform for {path.name}…")
        self._waveform_generation += 1
        generation = self._waveform_generation

        def loaded(data: WaveformData) -> None:
            if generation != self._waveform_generation:
                return
            self.timeline.set_waveform(data)
            self._apply_duration(data.duration)
            self.status_label.setText(
                "Waveform ready. Drag S/E handles, click to seek, and wheel to zoom."
            )

        def failed(message: str) -> None:
            if generation == self._waveform_generation:
                self.status_label.setText(
                    f"Waveform unavailable: {message}. Playback may still work."
                )

        self.waveform_loader.load(path, loaded, failed)
        self._sync_timeline_from_spins()

    def _apply_duration(self, duration: float) -> None:
        if duration <= 0:
            return
        first = self._duration <= 0
        self._duration = duration
        self.duration_label.setText(f"Duration {format_seconds(duration)}")
        self.timeline.set_duration(duration, reset_view=first)
        if self._open_end and not self._end_touched:
            blocker = QSignalBlocker(self.end_spin)
            self.end_spin.setValue(duration)
            del blocker
        self._sync_timeline_from_spins()

    def _duration_changed(self, milliseconds: int) -> None:
        if milliseconds > 0:
            self._apply_duration(milliseconds / 1000.0)

    def _effective_end(self) -> float:
        value = float(self.end_spin.value())
        return self._duration if value == 0 and self._duration > 0 else value

    def _sync_timeline_from_spins(self) -> None:
        self.timeline.set_selection(
            float(self.start_spin.value()), self._effective_end()
        )
        self._timeline_view_changed(
            self.timeline.visible_start, self.timeline.visible_end
        )

    def _timeline_view_changed(self, start: float, end: float) -> None:
        span = end - start
        available = max(0.0, self._duration - span)
        blocker = QSignalBlocker(self.timeline_scroll)
        self.timeline_scroll.setPageStep(
            10_000 if self._duration <= 0 else max(1, round(span / self._duration * 10_000))
        )
        self.timeline_scroll.setValue(
            0 if available <= 0 else round(start / available * 10_000)
        )
        del blocker

    def _scroll_timeline(self, value: int) -> None:
        span = self.timeline.visible_end - self.timeline.visible_start
        available = max(0.0, self._duration - span)
        self.timeline.visible_start = available * value / 10_000
        self.timeline.visible_end = self.timeline.visible_start + span
        self.timeline.update()

    def _spins_changed(self, *_args) -> None:
        if self._loading:
            return
        self._sync_timeline_from_spins()
        self._update_status()
        self.changed.emit()

    def _end_spin_changed(self, *_args) -> None:
        if not self._loading:
            self._end_touched = True
        self._spins_changed()

    def _timeline_range_changed(self, start: float, end: float) -> None:
        self._loading = True
        self.start_spin.setValue(start)
        self.end_spin.setValue(end)
        self._loading = False
        self._end_touched = True
        self._update_status()
        self.changed.emit()

    def _value_changed(self, *_args) -> None:
        if not self._loading:
            self._update_status()
            self.changed.emit()

    def _update_status(self) -> None:
        error = self.validation_error()
        if error:
            self.status_label.setText(error)
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
        elif self.recording_combo.currentData():
            self.status_label.setText(
                "Full recording waveform; the shaded region is this section's excerpt."
            )
            self.status_label.setStyleSheet("color: #4c674d;")

    def toggle_full_play(self) -> None:
        player = self.player
        if player is None:
            return
        if (
            self._playback_mode == "full"
            and player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            player.pause()
            return
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        self._playback_mode = "full"
        self._guard.stop()
        if self._duration > 0 and player.position() >= round(self._duration * 1000):
            player.setPosition(0)
        player.play()
        self.status_label.setText(
            "Playing the full recording from the current cursor."
        )
        self.status_label.setStyleSheet("color: #4c674d;")

    def toggle_excerpt_play(self) -> None:
        player = self.player
        if player is None:
            return
        if (
            self._playback_mode == "excerpt"
            and player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            player.pause()
            return
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        start_ms = round(self.start_spin.value() * 1000)
        end_ms = round(self._effective_end() * 1000)
        if self._playback_mode != "excerpt" or player.position() < start_ms or (
            end_ms > start_ms and player.position() >= end_ms
        ):
            player.setPosition(start_ms)
        self._playback_mode = "excerpt"
        player.play()
        self._guard.start()
        self.status_label.setText(
            "Playing only the selected excerpt between the S and E handles."
        )
        self.status_label.setStyleSheet("color: #4c674d;")

    # Compatibility with the former single editor play action.
    def toggle_play(self) -> None:
        self.toggle_full_play()

    def stop(self) -> None:
        if hasattr(self, "_guard"):
            self._guard.stop()
        self._playback_mode = None
        if self.player is not None:
            self.player.pause()
            self.player.setPosition(round(self.start_spin.value() * 1000))
        self._sync_play_button_text()

    def _seek(self, seconds: float) -> None:
        if self.player is None:
            return
        if self._playback_mode == "excerpt":
            seconds = max(
                float(self.start_spin.value()),
                min(self._effective_end(), seconds),
            )
        self.player.setPosition(round(seconds * 1000))
        self.timeline.set_playback_position(seconds)

    def _position_changed(self, milliseconds: int) -> None:
        seconds = milliseconds / 1000.0
        self.timeline.set_playback_position(seconds)
        self.time_label.setText(
            f"{format_seconds(seconds)} / {format_seconds(self._duration)}"
        )

    def _state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self._sync_play_button_text(state)

    def _sync_play_button_text(
        self, state: QMediaPlayer.PlaybackState | None = None
    ) -> None:
        if state is None:
            state = (
                self.player.playbackState()
                if self.player is not None
                else QMediaPlayer.PlaybackState.StoppedState
            )
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText(
            "Pause full recording"
            if playing and self._playback_mode == "full"
            else "Play full recording"
        )
        self.excerpt_play_button.setText(
            "Pause selected excerpt"
            if playing and self._playback_mode == "excerpt"
            else "Play selected excerpt"
        )

    def _media_error(self, _error, message: str) -> None:
        if message:
            self.status_label.setText(f"Audio playback error: {message}")

    def _enforce_boundary(self) -> None:
        if (
            self._playback_mode != "excerpt"
            or self.player is None
            or self.player.playbackState()
            != QMediaPlayer.PlaybackState.PlayingState
        ):
            return
        start_ms = round(self.start_spin.value() * 1000)
        if self.player.position() < start_ms:
            self.player.setPosition(start_ms)
            return
        if self.player.position() >= round(self._effective_end() * 1000):
            if self.loop_check.isChecked():
                self.player.setPosition(round(self.start_spin.value() * 1000))
            else:
                self.player.pause()
                self.player.setPosition(round(self._effective_end() * 1000))

    def _set_start_at_cursor(self) -> None:
        value = self.player.position() / 1000.0 if self.player is not None else 0.0
        if value < self._effective_end():
            self.start_spin.setValue(value)

    def _set_end_at_cursor(self) -> None:
        value = self.player.position() / 1000.0 if self.player is not None else 0.0
        if value > self.start_spin.value():
            self.end_spin.setValue(value)

    def _show_all(self) -> None:
        if self._duration > 0:
            self.timeline.visible_start = 0.0
            self.timeline.visible_end = self._duration
            self.timeline.update()

    def _use_preceding_end(self) -> None:
        stored = self.recording_combo.currentData()
        value = adjacent_audio_boundary(
            self.sections, self.current_section_id, self.language, str(stored),
            self.project_root, "previous",
        ) if stored else None
        if value is None:
            values = [m.time for m in self.timeline.markers if m.kind == "end"]
            value = max(values) if values else None
        if value is not None and value < self._effective_end():
            self.start_spin.setValue(value)

    def _use_following_start(self) -> None:
        stored = self.recording_combo.currentData()
        value = adjacent_audio_boundary(
            self.sections, self.current_section_id, self.language, str(stored),
            self.project_root, "next",
        ) if stored else None
        if value is None:
            values = [
                m.time for m in self.timeline.markers
                if m.kind == "start" and m.time > self.start_spin.value()
            ]
            value = min(values) if values else None
        if value is not None and value > self.start_spin.value():
            self.end_spin.setValue(value)

    def _update_reuse_buttons(self) -> None:
        self.previous_button.setEnabled(
            any(marker.kind == "end" for marker in self.timeline.markers)
        )
        self.next_button.setEnabled(
            any(marker.kind == "start" for marker in self.timeline.markers)
        )

    def _ensure_player(self) -> QMediaPlayer:
        if self.player is not None:
            return self.player
        self.player = QMediaPlayer(self)
        if os.environ.get("QT_QPA_PLATFORM", "").lower() not in {
            "offscreen", "minimal",
        }:
            self.audio_output = QAudioOutput(self)
            self.audio_output.setVolume(0.9)
            self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.errorOccurred.connect(self._media_error)
        return self.player

    def release(self) -> None:
        self._guard.stop()
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
