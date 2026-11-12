"""Playback-only embedded player for the fixed Goenka chanting video."""

from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..media import (
    ExcerptError,
    format_seconds,
    validate_video_excerpt,
    youtube_watch_url,
)
from ..model import VideoReference
from ..source_resources import GOENKA_URL, GOENKA_VIDEO_ID
from ..timeline import TimelineWidget, clamp_seek


class YouTubeExcerptPlayer(QGroupBox):
    """Embedded Goenka chanting with section-specific excerpt timestamps."""

    changed = Signal()
    loop_changed = Signal(bool)
    playback_position_changed = Signal(float)
    duration_available = Signal(float)

    def __init__(
        self,
        title: str,
        project_root: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.project_root = project_root
        self.video_url = GOENKA_URL
        self.video_id = GOENKA_VIDEO_ID
        self._reference: VideoReference | None = None
        self._loading = False
        self._view = None
        self._ready = False
        self._duration = 0.0
        self._current_video_id: str | None = None
        self._embed_error = ""
        self._user_seeking = False
        self._pending_play = False
        self._embed_generation = 0
        self._webengine_checked = False
        self._webengine_available = False

        layout = QVBoxLayout(self)
        layout.setSpacing(7)

        header = QHBoxLayout()
        self.recording_label = QLabel("S. N. Goenka — Pāli chanting")
        self.recording_label.setStyleSheet("font-weight: 600; color: #4e493f;")
        self.excerpt_label = QLabel("")
        self.excerpt_label.setStyleSheet("color: #6a655c;")
        header.addWidget(self.recording_label, 1)
        header.addWidget(self.excerpt_label)
        layout.addLayout(header)

        self.timeline = TimelineWidget(authoring=False)
        self.timeline.setToolTip(
            "This timeline shows only this section's chanting excerpt. Click to seek."
        )
        self.timeline.seek_requested.connect(self.seek_absolute)
        layout.addWidget(self.timeline)

        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.stop_button = QPushButton("Stop")
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 1000)
        self.position_slider.setEnabled(False)
        self.time_label = QLabel("0:00.0 / --:--")
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

        embed_row = QHBoxLayout()
        self.reload_button = QPushButton("Reload embedded player")
        self.browser_button = QPushButton("Open in browser")
        self.browser_button.setToolTip(
            "Always available when YouTube embedding refuses the video."
        )
        embed_row.addWidget(self.reload_button)
        embed_row.addWidget(self.browser_button)
        embed_row.addStretch(1)
        layout.addLayout(embed_row)

        self.embed_stack = QStackedWidget()
        self.embed_stack.setMinimumHeight(130)
        self.placeholder = QLabel(
            "No chanting excerpt is configured for this section.\n"
            "Use Edit Section to set its start and end times."
        )
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet(
            "background: #202020; color: #e8e3d8; border-radius: 4px; padding: 18px;"
        )
        self.embed_stack.addWidget(self.placeholder)
        layout.addWidget(self.embed_stack)

        self.status_label = QLabel(
            "Network access is required. YouTube may refuse embedding for some "
            "videos; Open in browser is the supported fallback."
        )
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #6a655c;")
        layout.addWidget(self.status_label)

        self.play_button.clicked.connect(self.toggle_play)
        self.stop_button.clicked.connect(self.stop)
        self.loop_check.toggled.connect(self._loop_toggled)
        self.position_slider.sliderPressed.connect(self._slider_pressed)
        self.position_slider.sliderReleased.connect(self._slider_released)
        self.position_slider.sliderMoved.connect(self._slider_moved)
        self.reload_button.clicked.connect(self._reload_embed)
        self.browser_button.clicked.connect(self.open_in_browser)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_embed)
        self._sync_controls()

    # -- public API ---------------------------------------------------------
    def set_reference(
        self, reference: VideoReference | None, *, load_embed: bool = True
    ) -> None:
        self._loading = True
        self.stop()
        self._reference = reference
        try:
            blocker = QSignalBlocker(self.loop_check)
            self.loop_check.setChecked(bool(reference and reference.loop))
            del blocker
        finally:
            self._loading = False
        self._update_labels()
        self._sync_timeline_range()
        if reference is None:
            self._unload_embed()
        elif not load_embed:
            self._show_placeholder(
                "Press Play or Reload embedded player to load the full video."
            )
        elif self._view is None or self._current_video_id != GOENKA_VIDEO_ID:
            self._reload_embed()
        else:
            self._apply_range_to_embed()
            self.stop()
        self._sync_controls()

    # Compatibility with the previous editor widget naming.
    def set_value(self, reference: VideoReference | None) -> None:
        self.set_reference(reference)

    def value(self) -> VideoReference | None:
        return self._reference

    def has_embedded_player(self) -> bool:
        """Whether the WebEngine view has already been created."""

        return self._view is not None

    # -- labels -------------------------------------------------------------
    def _update_labels(self) -> None:
        reference = self._reference
        if reference is None:
            self.excerpt_label.setText("No excerpt configured")
            self.time_label.setText("0:00.0 / --:--")
            return
        start = format_seconds(reference.start_seconds)
        end = (
            format_seconds(reference.end_seconds)
            if reference.end_seconds > 0
            else "end"
        )
        self.excerpt_label.setText(f"Excerpt {start} – {end}")

    def _sync_timeline_range(self) -> None:
        reference = self._reference
        if reference is None:
            self.timeline.set_duration(0.0, reset_view=True)
            self.timeline.set_excerpt_view(0.0, 1.0)
            return
        end = reference.end_seconds or self._duration
        if end <= reference.start_seconds:
            end = reference.start_seconds + 1.0
        self.timeline.set_duration(max(self._duration, end), reset_view=False)
        self.timeline.set_excerpt_view(reference.start_seconds, end)

    def _sync_controls(self) -> None:
        assigned = self._reference is not None
        self.play_button.setEnabled(assigned)
        self.stop_button.setEnabled(assigned)
        self.loop_check.setEnabled(assigned)
        self.reload_button.setEnabled(assigned)
        self.browser_button.setEnabled(assigned)
        self.position_slider.setEnabled(assigned and self._duration > 0)

    # -- embedding ----------------------------------------------------------
    def _ensure_webengine(self) -> bool:
        if self._webengine_checked:
            return self._webengine_available
        self._webengine_checked = True
        if os.environ.get("QT_QPA_PLATFORM", "").lower() in {
            "offscreen",
            "minimal",
        }:
            self._embed_error = (
                "Qt WebEngine is disabled on the headless Qt platform. "
                "Use a desktop session or Open in browser."
            )
            self._webengine_available = False
            return False
        flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        if "--disable-dev-shm-usage" not in flags.split():
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
                f"{flags} --disable-dev-shm-usage".strip()
            )
        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError as error:
            self._embed_error = (
                "Qt WebEngine is not installed, so YouTube cannot be embedded.\n"
                f"({error})"
            )
            self._webengine_available = False
            return False
        try:
            view = QWebEngineView(self)
            settings = view.settings()
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled, True
            )
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
            )
        except Exception as error:  # noqa: BLE001 - Qt may fail many ways
            self._embed_error = (
                f"Qt WebEngine could not create an embedded browser view.\n({error})"
            )
            self._webengine_available = False
            return False
        view.loadFinished.connect(self._load_finished)
        self._view = view
        self.embed_stack.addWidget(view)
        self._webengine_available = True
        return True

    def _reload_embed(self) -> None:
        if self._reference is None:
            self._unload_embed()
            return
        if not self._ensure_webengine():
            self._show_placeholder(
                self._embed_error
                + "\n\nUse Open in browser to play the recording externally."
            )
            self.status_label.setText(
                "Embedded playback is unavailable in this installation. "
                "Open in browser works without Qt WebEngine."
            )
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return
        assert self._view is not None
        try:
            template = (
                resources.files("suttara.resources")
                .joinpath("youtube.html")
                .read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError, ModuleNotFoundError) as error:
            self._show_placeholder(
                f"The embedded player resource is missing: {error}\n"
                "Use Open in browser as a fallback."
            )
            self.status_label.setText(
                f"The embedded player resource is missing: {error}. "
                "Use Open in browser as a fallback."
            )
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return
        start = self._reference.start_seconds if self._reference else 0.0
        end = self._reference.end_seconds if self._reference else 0.0
        loop = bool(self._reference and self._reference.loop)
        html = (
            template.replace("__START__", json.dumps(float(start)))
            .replace("__END__", json.dumps(float(end)))
            .replace("__LOOP__", "true" if loop else "false")
            .replace("__VIDEO_ID__", json.dumps(GOENKA_VIDEO_ID))
        )
        self._current_video_id = GOENKA_VIDEO_ID
        self._ready = False
        self._duration = 0.0
        self._embed_error = ""
        self._embed_generation += 1
        generation = self._embed_generation
        QTimer.singleShot(15000, lambda: self._embed_timed_out(generation))
        self.embed_stack.setCurrentWidget(self._view)
        self.status_label.setText(
            "Loading the embedded YouTube player. Network access is required."
        )
        self.status_label.setStyleSheet("color: #6a655c;")
        self._view.setHtml(html, QUrl("http://localhost/"))
        self._poll_timer.start()
        self._sync_controls()

    def _apply_range_to_embed(self) -> None:
        if self._view is None or self._reference is None:
            return
        script = (
            "window.suttara && window.suttara.setRange("
            f"{json.dumps(float(self._reference.start_seconds))}, "
            f"{json.dumps(float(self._reference.end_seconds))}, "
            f"{'true' if self._reference.loop else 'false'});"
        )
        self._view.page().runJavaScript(script)

    def _unload_embed(self) -> None:
        self._poll_timer.stop()
        self._embed_generation += 1
        self._ready = False
        self._pending_play = False
        self._current_video_id = None
        self._duration = 0.0
        self.position_slider.setValue(0)
        if self._view is not None:
            self._view.page().runJavaScript("window.suttara && window.suttara.pause();")
        self._show_placeholder(
            "No chanting excerpt is configured for this section.\n"
            "Use Edit Section to set its start and end times."
        )
        self.status_label.setText(
            "No chanting excerpt is configured for this section. "
            "Open in browser is available when an excerpt is assigned."
        )
        self.status_label.setStyleSheet("color: #6a655c;")

    def _show_placeholder(self, message: str) -> None:
        self.placeholder.setText(message)
        self.embed_stack.setCurrentWidget(self.placeholder)

    def _embed_timed_out(self, generation: int) -> None:
        if generation != self._embed_generation or self._ready:
            return
        self._embed_error = (
            "The embedded YouTube player has not become ready after 15 seconds. "
            "Check the network connection or use Open in browser."
        )
        self.status_label.setText(
            self._embed_error + " Open in browser is the supported fallback."
        )
        self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")

    def _load_finished(self, ok: bool) -> None:
        if not ok:
            self.status_label.setText(
                "The YouTube embed page could not be loaded. Check the network "
                "connection or use Open in browser."
            )
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")

    def _poll_embed(self) -> None:
        if self._view is None:
            return
        self._view.page().runJavaScript(
            "window.suttara ? JSON.stringify(window.suttara.state()) : ''",
            self._handle_state,
        )

    def _handle_state(self, result) -> None:
        if isinstance(result, str):
            if not result:
                return
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return
        if not isinstance(result, dict):
            return
        error = str(result.get("error") or "")
        if error and error != self._embed_error:
            self._embed_error = error
            self.status_label.setText(
                f"{error}\nOpen in browser is the supported fallback."
            )
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
        ready = bool(result.get("ready"))
        if ready and not self._ready and not error:
            self.status_label.setText(
                "Embedded YouTube player ready. Excerpt start, end and loop apply."
            )
            self.status_label.setStyleSheet("color: #4c674d;")
            self._apply_range_to_embed()
            if self._pending_play and self._view is not None:
                self._pending_play = False
                self._view.page().runJavaScript(
                    "window.suttara && window.suttara.play();"
                )
                self.status_label.setText("Starting the embedded player…")
        self._ready = ready
        time_value = float(result.get("time") or 0.0)
        duration = float(result.get("duration") or 0.0)
        state = int(result.get("state", -1))
        self._duration = duration
        self._sync_timeline_range()
        if duration > 0:
            self.duration_available.emit(duration)
        if not self._user_seeking:
            self._update_position(time_value, duration)
        if state == 1:
            self.play_button.setText("Pause")
        elif state in (0, 2, 5):
            self.play_button.setText("Play")
        self.position_slider.setEnabled(duration > 0)

    # -- playback -----------------------------------------------------------
    def _excerpt(self):
        if self._reference is None:
            raise ExcerptError("No chanting excerpt is configured")
        return validate_video_excerpt(
            self._reference.start_seconds,
            self._reference.end_seconds,
        )

    def toggle_play(self) -> None:
        if self._reference is None:
            self.status_label.setText(
                "No chanting excerpt is configured. Use Edit Section to set one."
            )
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return
        if self._view is None or self._current_video_id != GOENKA_VIDEO_ID:
            self._reload_embed()
        try:
            excerpt = self._excerpt()
        except ExcerptError as error:
            self.status_label.setText(f"Cannot play excerpt — {error}")
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return
        if self._duration > 0:
            if excerpt.start >= self._duration:
                self.status_label.setText(
                    f"Start time {format_seconds(excerpt.start)} is at or beyond "
                    f"the video duration {format_seconds(self._duration)}."
                )
                self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
                return
            if excerpt.end is not None and excerpt.end > self._duration:
                self.status_label.setText(
                    f"End time {format_seconds(excerpt.end)} is beyond the video "
                    f"duration {format_seconds(self._duration)}."
                )
                self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
                return
        if self._view is None:
            return
        if not self._ready:
            self._pending_play = True
            self._apply_range_to_embed()
            self._view.page().runJavaScript("window.suttara && window.suttara.play();")
            self.status_label.setText(
                "Waiting for the embedded YouTube player before starting…"
            )
            self._poll_timer.start()
            return
        if self.play_button.text() == "Pause":
            self._view.page().runJavaScript("window.suttara && window.suttara.pause();")
            self.play_button.setText("Play")
            self.status_label.setText("Paused.")
            return
        self._apply_range_to_embed()
        self._view.page().runJavaScript("window.suttara && window.suttara.play();")
        self.status_label.setText(
            "Play requested; YouTube may take a moment, or may refuse embedding."
        )
        self._poll_timer.start()

    def stop(self) -> None:
        self._pending_play = False
        if self._view is not None:
            self._view.page().runJavaScript("window.suttara && window.suttara.stop();")
        self.play_button.setText("Play")
        start = self._reference.start_seconds if self._reference else 0.0
        self._update_position(start, self._duration)
        self.status_label.setText("Stopped at excerpt start.")
        self.status_label.setStyleSheet("color: #6a655c;")

    def _update_position(self, time_value: float, duration: float) -> None:
        reference = self._reference
        start = reference.start_seconds if reference else 0.0
        end = (reference.end_seconds if reference else 0.0) or duration
        if end > start:
            fraction = (time_value - start) / (end - start)
            self.position_slider.setValue(
                max(0, min(1000, int(round(fraction * 1000))))
            )
        self.time_label.setText(
            f"{format_seconds(max(0.0, time_value))} / "
            f"{format_seconds(end) if end > 0 else '--:--'}"
        )
        self.timeline.set_playback_position(time_value)
        self.playback_position_changed.emit(time_value)

    def seek_absolute(self, target: float) -> None:
        """Seek through the official player, constrained to the active excerpt."""

        reference = self._reference
        if reference is None or self._view is None:
            return
        end = reference.end_seconds or self._duration
        if end <= reference.start_seconds:
            return
        target = clamp_seek(target, reference.start_seconds, end)
        self._view.page().runJavaScript(
            f"window.suttara && window.suttara.seek({json.dumps(target)});"
        )
        self._update_position(target, self._duration)

    def _slider_pressed(self) -> None:
        self._user_seeking = True

    def _slider_moved(self, value: int) -> None:
        reference = self._reference
        start = reference.start_seconds if reference else 0.0
        end = (reference.end_seconds if reference else 0.0) or self._duration
        if end <= start:
            return
        target = start + (end - start) * value / 1000
        self.time_label.setText(f"{format_seconds(target)} / {format_seconds(end)}")

    def _slider_released(self) -> None:
        self._user_seeking = False
        reference = self._reference
        start = reference.start_seconds if reference else 0.0
        end = (reference.end_seconds if reference else 0.0) or self._duration
        if end <= start or self._view is None:
            return
        target = start + (end - start) * self.position_slider.value() / 1000
        self._view.page().runJavaScript(
            f"window.suttara && window.suttara.seek({json.dumps(target)});"
        )

    def _loop_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        if self._reference is not None:
            self._reference.loop = checked
        self._apply_range_to_embed()
        self.loop_changed.emit(checked)
        self.changed.emit()

    def open_in_browser(self) -> None:
        if self._reference is None:
            QMessageBox.information(
                self,
                "Open in browser",
                "Configure a chanting excerpt in Edit Section first.",
            )
            return
        url = QUrl(youtube_watch_url(GOENKA_VIDEO_ID, self._reference.start_seconds))
        if not url.isValid() or not QDesktopServices.openUrl(url):
            QMessageBox.warning(
                self,
                "Open in browser",
                f"The system could not open this URL:\n{url.toString()}\n\n"
                "You can copy it into a browser manually.",
            )
