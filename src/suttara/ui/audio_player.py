"""Playback-only local spoken-audio excerpt player."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..media import ExcerptError, format_seconds, validate_audio_excerpt
from ..model import AudioReference
from ..persistence import resolved_path
from ..timeline import TimelineWidget, clamp_seek
from ..waveform import WaveformData, WaveformLoader


class AudioExcerptPlayer(QGroupBox):
    """One section's assigned recording with playback and loop controls.

    The recording, start and end are configuration data shown in the Section
    Editor.  This widget intentionally exposes only listening controls; the
    only preference it can change is the loop flag.
    """

    changed = Signal()
    loop_changed = Signal(bool)

    def __init__(
        self,
        title: str,
        project_root: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.project_root = project_root
        self._reference: AudioReference | None = None
        self._recording_name = ""
        self._duration_ms = 0
        self._enforcing = False
        self._user_seeking = False
        self._loading = False
        self._has_media = False
        self._waveform_generation = 0

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.9)
        self.player.setAudioOutput(self.audio_output)
        self.waveform_loader = WaveformLoader(project_root, self)

        layout = QVBoxLayout(self)
        layout.setSpacing(7)

        header = QHBoxLayout()
        self.recording_label = QLabel("No recording assigned")
        self.recording_label.setStyleSheet("font-weight: 600; color: #4e493f;")
        self.excerpt_label = QLabel("")
        self.excerpt_label.setStyleSheet("color: #6a655c;")
        header.addWidget(self.recording_label, 1)
        header.addWidget(self.excerpt_label)
        layout.addLayout(header)

        self.timeline = TimelineWidget(authoring=False)
        self.timeline.setToolTip(
            "This waveform shows only this section's excerpt. Click to seek within it."
        )
        self.timeline.seek_requested.connect(self._seek_from_waveform)
        layout.addWidget(self.timeline)

        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.stop_button = QPushButton("Stop")
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 1000)
        self.position_slider.setEnabled(False)
        self.time_label = QLabel("0:00.0 / 0:00.0")
        self.time_label.setMinimumWidth(130)
        self.time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.position_slider, 1)
        controls.addWidget(self.time_label)
        layout.addLayout(controls)

        preference_row = QHBoxLayout()
        self.loop_check = QCheckBox("Loop excerpt")
        preference_row.addWidget(self.loop_check)
        preference_row.addStretch(1)
        layout.addLayout(preference_row)

        self.status_label = QLabel("No recording assigned for this section.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #6a655c;")
        layout.addWidget(self.status_label)

        self.play_button.clicked.connect(self.toggle_play)
        self.stop_button.clicked.connect(self.stop)
        self.loop_check.toggled.connect(self._loop_toggled)
        self.position_slider.sliderPressed.connect(self._slider_pressed)
        self.position_slider.sliderReleased.connect(self._slider_released)
        self.position_slider.sliderMoved.connect(self._slider_moved)

        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_state_changed)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(self._media_error)

        self._guard_timer = QTimer(self)
        self._guard_timer.setInterval(200)
        self._guard_timer.timeout.connect(self._enforce_boundary)
        self._sync_controls()

    # -- public API ---------------------------------------------------------
    def set_reference(
        self,
        reference: AudioReference | None,
        recording_name: str | None = None,
    ) -> None:
        self._loading = True
        self._guard_timer.stop()
        self.player.stop()
        self._reference = reference
        self._recording_name = recording_name or (
            Path(reference.path).stem if reference and reference.path else ""
        )
        try:
            blocker = QSignalBlocker(self.loop_check)
            self.loop_check.setChecked(bool(reference and reference.loop))
            del blocker
        finally:
            self._loading = False
        self._update_labels()
        self._load_media()
        self._load_waveform()
        self._sync_controls()

    # Compatibility with the previous editor widget naming.
    def set_value(self, reference: AudioReference | None) -> None:
        self.set_reference(reference)

    def value(self) -> AudioReference | None:
        return self._reference

    # -- labels and loading -------------------------------------------------
    def _update_labels(self) -> None:
        reference = self._reference
        if reference is None:
            self.recording_label.setText("No recording assigned")
            self.excerpt_label.setText("")
            self.time_label.setText("0:00.0 / 0:00.0")
            return
        self.recording_label.setText(self._recording_name or "Recording")
        start = format_seconds(reference.start_seconds)
        end = (
            format_seconds(reference.end_seconds)
            if reference.end_seconds > 0
            else "end"
        )
        self.excerpt_label.setText(f"Excerpt {start} – {end}")

    def _load_media(self) -> None:
        self._guard_timer.stop()
        self.player.stop()
        self._duration_ms = 0
        self._has_media = False
        reference = self._reference
        if reference is None:
            self.player.setSource(QUrl())
            self.status_label.setText("No recording assigned for this section.")
            self.status_label.setStyleSheet("color: #6a655c;")
            self._sync_controls()
            return
        path = resolved_path(reference.path, self.project_root)
        if path is None or not path.exists() or not path.is_file():
            self.player.setSource(QUrl())
            self.status_label.setText(
                f"Recording not found: {Path(reference.path).name}. "
                "Choose an available recording in Edit Section."
            )
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            self._sync_controls()
            return
        self._has_media = True
        self.status_label.setText(f"Loading {path.name}…")
        self.status_label.setStyleSheet("color: #6a655c;")
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self._sync_controls()

    def _load_waveform(self) -> None:
        self._waveform_generation += 1
        generation = self._waveform_generation
        self.timeline.set_waveform(None)
        reference = self._reference
        if reference is None:
            self.timeline.set_duration(0.0, reset_view=True)
            self.timeline.set_excerpt_view(0.0, 1.0)
            return
        path = resolved_path(reference.path, self.project_root)
        end = reference.end_seconds or max(reference.start_seconds + 1.0, 1.0)
        self.timeline.set_excerpt_view(reference.start_seconds, end)
        if path is None or not path.is_file():
            return
        if path.stat().st_size == 0:
            return

        def loaded(data: WaveformData) -> None:
            if generation != self._waveform_generation:
                return
            self.timeline.set_waveform(data)
            if not self._duration_ms:
                self._duration_ms = round(data.duration * 1000)
            self._sync_timeline_range()
            self._sync_controls()

        def failed(_message: str) -> None:
            # Playback remains available when optional visual extraction fails.
            return

        self.waveform_loader.load(path, loaded, failed)

    def _sync_timeline_range(self) -> None:
        reference = self._reference
        if reference is None:
            return
        end = reference.end_seconds or self._duration_ms / 1000.0
        if end <= reference.start_seconds:
            end = reference.start_seconds + 1.0
        self.timeline.set_duration(max(end, self._duration_ms / 1000.0), reset_view=False)
        self.timeline.set_excerpt_view(reference.start_seconds, end)

    def _sync_controls(self) -> None:
        assigned = self._reference is not None
        self.play_button.setEnabled(assigned and self._has_media)
        self.stop_button.setEnabled(assigned and self._has_media)
        self.loop_check.setEnabled(assigned)
        self.position_slider.setEnabled(assigned and self._duration_ms > 0)
        self._update_time_label(self.player.position())

    def _update_time_label(self, position_ms: int) -> None:
        end = self._effective_end_ms()
        end_text = format_seconds(end / 1000) if end else "--:--"
        self.time_label.setText(
            f"{format_seconds(max(0, position_ms) / 1000)} / {end_text}"
        )

    # -- excerpt boundaries -------------------------------------------------
    def _start_ms(self) -> int:
        if self._reference is None:
            return 0
        return max(0, int(round(self._reference.start_seconds * 1000)))

    def _configured_end_ms(self) -> int:
        if self._reference is None:
            return 0
        value = int(round(self._reference.end_seconds * 1000))
        return value if value > 0 else 0

    def _effective_end_ms(self) -> int:
        configured = self._configured_end_ms()
        if configured:
            return configured
        return self._duration_ms if self._duration_ms > 0 else 0

    def _excerpt(self):
        if self._reference is None:
            raise ExcerptError("No recording is assigned")
        duration = self._duration_ms / 1000 if self._duration_ms > 0 else None
        return validate_audio_excerpt(
            self._reference.start_seconds,
            self._reference.end_seconds,
            duration,
        )

    def _warn_if_invalid(self) -> bool:
        try:
            self._excerpt()
        except ExcerptError as error:
            self.status_label.setText(f"Excerpt problem — {error}")
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return False
        if (
            self._duration_ms > 0
            and self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
        ):
            self.status_label.setText(
                f"Recording ready · duration {format_seconds(self._duration_ms / 1000)}"
            )
            self.status_label.setStyleSheet("color: #4c674d;")
        return True

    # -- media signals ------------------------------------------------------
    def _duration_changed(self, duration: int) -> None:
        self._duration_ms = max(0, duration)
        self._sync_timeline_range()
        if (
            self._duration_ms
            and self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
        ):
            self._warn_if_invalid()
        self._sync_controls()

    def _media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.status_label.setText(
                f"The recording could not be decoded: {self._recording_name}."
            )
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
        elif status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            if not self._duration_ms:
                self._duration_ms = int(self.player.duration())
            self._warn_if_invalid()
            self._sync_controls()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._handle_end()

    def _media_error(self, error: QMediaPlayer.Error, message: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self.status_label.setText(
            f"Audio playback error for {self._recording_name}: {message}"
        )
        self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
        self._guard_timer.stop()

    # -- playback -----------------------------------------------------------
    def toggle_play(self) -> None:
        if self._reference is None or not self._has_media:
            self.status_label.setText(
                "No playable recording is assigned. Choose one in Edit Section."
            )
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.status_label.setText("Paused.")
            self.status_label.setStyleSheet("color: #6a655c;")
            return
        try:
            excerpt = self._excerpt()
        except ExcerptError as error:
            self.status_label.setText(f"Cannot play excerpt — {error}")
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return
        start_ms = int(round(excerpt.start * 1000))
        end_ms = (
            int(round(excerpt.end * 1000))
            if excerpt.end is not None
            else self._effective_end_ms()
        )
        position = self.player.position()
        if position < start_ms or (end_ms and position >= end_ms - 50):
            self.player.setPosition(start_ms)
        self.player.play()
        self._guard_timer.start()
        self.status_label.setText(
            f"Playing excerpt {format_seconds(excerpt.start)} – "
            f"{format_seconds(excerpt.end)}"
        )
        self.status_label.setStyleSheet("color: #4c674d;")

    def stop(self) -> None:
        self._guard_timer.stop()
        self.player.pause()
        self.player.setPosition(self._start_ms())
        self._update_time_label(self._start_ms())
        self.status_label.setText("Stopped at excerpt start.")
        self.status_label.setStyleSheet("color: #6a655c;")

    def _position_changed(self, position: int) -> None:
        self.timeline.set_playback_position(position / 1000.0)
        if not self._user_seeking:
            self._update_time_label(position)
            start = self._start_ms()
            end = self._effective_end_ms()
            if end and end > start:
                fraction = (position - start) / (end - start)
                self.position_slider.setValue(
                    max(0, min(1000, int(round(fraction * 1000))))
                )
        self._enforce_boundary()

    def _enforce_boundary(self) -> None:
        if self._enforcing:
            return
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        self._enforcing = True
        try:
            position = self.player.position()
            start = self._start_ms()
            end = self._effective_end_ms()
            if position < start - 100:
                self.player.setPosition(start)
                return
            if end and position >= end:
                if self.loop_check.isChecked():
                    self.player.setPosition(start)
                    if (
                        self.player.playbackState()
                        != QMediaPlayer.PlaybackState.PlayingState
                    ):
                        self.player.play()
                    self.status_label.setText("Looping excerpt.")
                    self.status_label.setStyleSheet("color: #4c674d;")
                else:
                    self._guard_timer.stop()
                    self.player.pause()
                    self.player.setPosition(end)
                    self.status_label.setText("Excerpt finished.")
                    self.status_label.setStyleSheet("color: #4c674d;")
        finally:
            self._enforcing = False

    def _handle_end(self) -> None:
        if self.loop_check.isChecked():
            self.player.setPosition(self._start_ms())
            self.player.play()
            self._guard_timer.start()
        else:
            self._guard_timer.stop()
            self.player.pause()
            self.status_label.setText("Excerpt finished.")
            self.status_label.setStyleSheet("color: #4c674d;")

    def _playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("Pause")
            self._guard_timer.start()
        else:
            self.play_button.setText("Play")
            if state == QMediaPlayer.PlaybackState.StoppedState:
                self._guard_timer.stop()

    # -- seeking and loop preference ---------------------------------------
    def _slider_pressed(self) -> None:
        self._user_seeking = True

    def _slider_moved(self, value: int) -> None:
        start = self._start_ms()
        end = self._effective_end_ms()
        if end <= start:
            return
        target = start + int(round((end - start) * value / 1000))
        self._update_time_label(target)

    def _slider_released(self) -> None:
        self._user_seeking = False
        self._seek_to_slider(self.position_slider.value())

    def _seek_to_slider(self, value: int) -> None:
        start = self._start_ms()
        end = self._effective_end_ms()
        if end <= start:
            return
        target = start + int(round((end - start) * value / 1000))
        self.player.setPosition(target)
        self._update_time_label(target)

    def _seek_from_waveform(self, target_seconds: float) -> None:
        start = self._start_ms() / 1000.0
        end = self._effective_end_ms() / 1000.0
        if end <= start:
            return
        target = clamp_seek(target_seconds, start, end)
        self.player.setPosition(round(target * 1000))
        self.timeline.set_playback_position(target)
        self._update_time_label(round(target * 1000))

    def _loop_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        if self._reference is not None:
            self._reference.loop = checked
        self.loop_changed.emit(checked)
        self.changed.emit()
