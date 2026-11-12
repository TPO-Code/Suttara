"""Modal Section Editor for titles, passages, recordings and chanting."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..media import ExcerptError, format_seconds, validate_video_excerpt
from ..model import Section, VideoReference
from ..source_resources import GOENKA_URL
from ..timeline import (
    TimelineWidget, adjacent_goenka_boundary, goenka_markers,
    validate_strict_range,
)
from .audio_config import AudioConfigEditor
from .source_range_selector import SourceRangeSelector
from .youtube_player import YouTubeExcerptPlayer

MAX_SECONDS = 1_000_000.0


def _scrollable(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


class GoenkaConfigEditor(QGroupBox):
    """Section-specific timestamps for the fixed Goenka chanting video."""

    changed = Signal()

    def __init__(
        self,
        title: str,
        reference: VideoReference | None = None,
        parent: QWidget | None = None,
        *,
        project_root: Path | None = None,
        sections: tuple[Section, ...] | list[Section] = (),
        current_section_id: str = "",
    ) -> None:
        super().__init__(title, parent)
        self._loading = False
        self._duration = 0.0
        self._position = 0.0
        self._view_interacted = False
        self.sections = sections
        self.current_section_id = current_section_id

        layout = QVBoxLayout(self)
        layout.setSpacing(7)

        self.enabled_check = QCheckBox("Use the Goenka chanting excerpt")
        layout.addWidget(self.enabled_check)

        self.url_label = QLabel(GOENKA_URL)
        self.url_label.setWordWrap(True)
        self.url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.url_label.setStyleSheet("color: #6a655c; font-size: 11px;")
        layout.addWidget(self.url_label)

        self.timeline = TimelineWidget(authoring=True)
        self.timeline.set_markers(goenka_markers(sections, current_section_id))
        layout.addWidget(self.timeline)
        self.timeline_scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.timeline_scroll.setRange(0, 10_000)
        layout.addWidget(self.timeline_scroll)

        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Start"))
        self.start_spin = self._time_spin()
        range_row.addWidget(self.start_spin)
        range_row.addWidget(QLabel("End"))
        self.end_spin = self._time_spin()
        self.end_spin.setToolTip(
            "End of the excerpt in seconds. 0 means no end boundary."
        )
        range_row.addWidget(self.end_spin)
        range_row.addWidget(QLabel("(0 = no end)"))
        self.loop_check = QCheckBox("Loop excerpt")
        range_row.addWidget(self.loop_check)
        range_row.addStretch(1)
        layout.addLayout(range_row)

        action_row = QHBoxLayout()
        self.set_start_button = QPushButton("Set start at cursor")
        self.set_end_button = QPushButton("Set end at cursor")
        for button in (self.set_start_button, self.set_end_button):
            button.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
            button.setMaximumWidth(button.sizeHint().width())
        self.play_excerpt_button = QPushButton("Play selected excerpt")
        self.play_excerpt_button.setToolTip(
            "Start playback at the S handle and stop or loop at the E handle"
        )
        self.previous_button = QPushButton("Use preceding end")
        self.next_button = QPushButton("Use following start")
        for widget in (
            self.set_start_button,
            self.set_end_button,
            self.play_excerpt_button,
            self.previous_button,
            self.next_button,
        ):
            action_row.addWidget(widget)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.project_root = project_root or Path.cwd()
        self.preview: YouTubeExcerptPlayer | None = None
        self.load_preview_button = QPushButton("Play full recording")
        self.load_preview_button.setToolTip(
            "Play without excerpt boundaries while locating timestamps"
        )
        self.preview_layout = QVBoxLayout()
        self.preview_layout.addWidget(self.load_preview_button)
        layout.addLayout(self.preview_layout)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #6a655c; font-size: 11px;")
        layout.addWidget(self.status_label)

        self.enabled_check.toggled.connect(self._enabled_changed)
        self.start_spin.valueChanged.connect(self._value_changed)
        self.end_spin.valueChanged.connect(self._value_changed)
        self.loop_check.toggled.connect(self._value_changed)
        self.timeline.seek_requested.connect(self._seek_preview)
        self.timeline.range_changed.connect(self._timeline_range_changed)
        self.timeline.view_changed.connect(self._timeline_view_changed)
        self.timeline_scroll.valueChanged.connect(self._scroll_timeline)
        self.load_preview_button.clicked.connect(
            lambda: self._ensure_preview(play_full=True)
        )
        self.play_excerpt_button.clicked.connect(self._play_excerpt)
        self.set_start_button.clicked.connect(
            lambda: self.start_spin.setValue(self._position)
        )
        self.set_end_button.clicked.connect(
            lambda: self.end_spin.setValue(self._position)
        )
        self.previous_button.clicked.connect(self._use_preceding_end)
        self.next_button.clicked.connect(self._use_following_start)
        self.set_reference(reference)

    @staticmethod
    def _time_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, MAX_SECONDS)
        spin.setDecimals(3)
        spin.setSingleStep(1.0)
        spin.setSuffix(" s")
        spin.setMinimumWidth(105)
        return spin

    def reference(self) -> VideoReference | None:
        if not self.enabled_check.isChecked():
            return None
        return VideoReference(
            start_seconds=float(self.start_spin.value()),
            end_seconds=float(self.end_spin.value()),
            loop=self.loop_check.isChecked(),
        )

    def set_reference(self, reference: VideoReference | None) -> None:
        self._loading = True
        try:
            blocker = QSignalBlocker(self.enabled_check)
            self.enabled_check.setChecked(reference is not None)
            del blocker
            for spin, value in (
                (self.start_spin, reference.start_seconds if reference else 0.0),
                (self.end_spin, reference.end_seconds if reference else 0.0),
            ):
                blocker = QSignalBlocker(spin)
                spin.setValue(value)
                del blocker
            blocker = QSignalBlocker(self.loop_check)
            self.loop_check.setChecked(bool(reference and reference.loop))
            del blocker
        finally:
            self._loading = False
        self._update_enabled()
        self._update_status()
        self._sync_timeline()
        self._sync_preview()

    def validation_error(self) -> str:
        if not self.enabled_check.isChecked():
            return ""
        try:
            validate_video_excerpt(
                float(self.start_spin.value()), float(self.end_spin.value())
            )
        except ExcerptError as error:
            return str(error)
        if self._duration > 0:
            end = float(self.end_spin.value()) or self._duration
            return validate_strict_range(
                float(self.start_spin.value()), end, self._duration
            )
        return ""

    def _enabled_changed(self, _checked: bool) -> None:
        if self._loading:
            return
        self._update_enabled()
        self._update_status()
        self._sync_preview()
        self.changed.emit()

    def _value_changed(self, *_args) -> None:
        if self._loading:
            return
        self._update_status()
        self._sync_timeline()
        self.changed.emit()

    def _update_enabled(self) -> None:
        enabled = self.enabled_check.isChecked()
        for widget in (
            self.start_spin,
            self.end_spin,
            self.loop_check,
            self.timeline,
            self.set_start_button,
            self.set_end_button,
            self.play_excerpt_button,
            self.load_preview_button,
        ):
            widget.setEnabled(enabled)
        self.previous_button.setEnabled(
            enabled and any(marker.kind == "end" for marker in self.timeline.markers)
        )
        self.next_button.setEnabled(
            enabled and any(marker.kind == "start" for marker in self.timeline.markers)
        )

    def _update_status(self) -> None:
        if not self.enabled_check.isChecked():
            self.status_label.setText("No chanting excerpt for this section.")
            self.status_label.setStyleSheet("color: #6a655c; font-size: 11px;")
            return
        error = self.validation_error()
        if error:
            self.status_label.setText(error)
            self.status_label.setStyleSheet(
                "color: #a33a2b; font-weight: 600; font-size: 11px;"
            )
            return
        start = format_seconds(float(self.start_spin.value()))
        end = (
            format_seconds(float(self.end_spin.value()))
            if self.end_spin.value() > 0
            else "end"
        )
        self.status_label.setText(f"Excerpt {start} – {end}")
        self.status_label.setStyleSheet("color: #4c674d; font-size: 11px;")

    def _sync_preview(self) -> None:
        if self.preview is None:
            return
        reference = (
            VideoReference(start_seconds=0.0, end_seconds=0.0)
            if self.enabled_check.isChecked()
            else None
        )
        self.preview.set_reference(reference, load_embed=False)

    def _ensure_preview(self, *, play_full: bool) -> YouTubeExcerptPlayer:
        if self.preview is None:
            self.preview = YouTubeExcerptPlayer(
                "Full Goenka recording", self.project_root
            )
            self.preview_layout.addWidget(self.preview)
            self.preview.playback_position_changed.connect(self._position_changed)
            self.preview.duration_available.connect(self._duration_changed)
            self._sync_preview()
        if play_full:
            self.preview.set_reference(
                VideoReference(start_seconds=0.0, end_seconds=0.0),
                load_embed=self.preview.has_embedded_player(),
            )
            self.preview.toggle_play()
        return self.preview

    def _play_excerpt(self) -> None:
        error = self.validation_error()
        if error:
            self.status_label.setText(f"Cannot play excerpt — {error}")
            self.status_label.setStyleSheet(
                "color: #a33a2b; font-weight: 600; font-size: 11px;"
            )
            return
        reference = self.reference()
        if reference is None:
            return
        preview = self._ensure_preview(play_full=False)
        preview.set_reference(
            reference, load_embed=preview.has_embedded_player()
        )
        preview.toggle_play()

    def _seek_preview(self, value: float) -> None:
        if self.preview is not None:
            self.preview.seek_absolute(value)

    def _sync_timeline(self) -> None:
        start = float(self.start_spin.value())
        end = float(self.end_spin.value()) or self._duration
        if end <= start:
            end = start + 1.0
        self.timeline.set_duration(max(self._duration, end), reset_view=self.timeline.duration <= 0)
        self.timeline.set_selection(start, end)

    def _timeline_range_changed(self, start: float, end: float) -> None:
        self._loading = True
        self.start_spin.setValue(start)
        self.end_spin.setValue(end)
        self._loading = False
        self._update_status()
        self.changed.emit()

    def _position_changed(self, value: float) -> None:
        self._position = value
        self.timeline.set_playback_position(value)

    def _duration_changed(self, value: float) -> None:
        first_duration = self._duration <= 0
        self._duration = value
        self._sync_timeline()
        if first_duration and not self._view_interacted:
            self.timeline.visible_start = 0.0
            self.timeline.visible_end = value
            self.timeline.update()
        self._sync_timeline_scrollbar(
            self.timeline.visible_start, self.timeline.visible_end
        )

    def _timeline_view_changed(self, start: float, end: float) -> None:
        self._view_interacted = True
        self._sync_timeline_scrollbar(start, end)

    def _sync_timeline_scrollbar(self, start: float, end: float) -> None:
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
        self._view_interacted = True
        span = self.timeline.visible_end - self.timeline.visible_start
        available = max(0.0, self._duration - span)
        self.timeline.visible_start = available * value / 10_000
        self.timeline.visible_end = self.timeline.visible_start + span
        self.timeline.update()

    def _use_preceding_end(self) -> None:
        value = adjacent_goenka_boundary(
            self.sections, self.current_section_id, "previous"
        )
        if value is None:
            values = [m.time for m in self.timeline.markers if m.kind == "end"]
            value = max(values) if values else None
        if value is not None and value < (self.end_spin.value() or self._duration):
            self.start_spin.setValue(value)

    def _use_following_start(self) -> None:
        value = adjacent_goenka_boundary(
            self.sections, self.current_section_id, "next"
        )
        if value is None:
            values = [
                m.time for m in self.timeline.markers
                if m.kind == "start" and m.time > self.start_spin.value()
            ]
            value = min(values) if values else None
        if value is not None and value > self.start_spin.value():
            self.end_spin.setValue(value)


class SectionEditorDialog(QDialog):
    """Configure one section on a copy; Cancel leaves the section untouched."""

    def __init__(
        self,
        section: Section,
        project_root: Path,
        parent: QWidget | None = None,
        settings: QSettings | None = None,
        is_new: bool = False,
        sections: tuple[Section, ...] | list[Section] = (),
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).expanduser().resolve()
        self.original = section
        self._edited = deepcopy(section)
        self._result: Section | None = None
        self.settings = settings
        self.is_new = is_new
        self.sections = sections

        title = "New Section — Suttara" if is_new else "Edit Section — Suttara"
        self.setWindowTitle(title)
        self.resize(1180, 800)
        self.setMinimumSize(1000, 680)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        title_row = QHBoxLayout()
        title_label = QLabel("Section title")
        title_label.setStyleSheet("font-weight: 600; color: #514c44;")
        self.title_edit = QLineEdit(section.title)
        self.title_edit.setPlaceholderText("Name this study section")
        self.title_edit.setStyleSheet(
            "font-size: 18px; font-weight: 600; padding: 4px 6px;"
        )
        title_row.addWidget(title_label)
        title_row.addWidget(self.title_edit, 1)
        outer.addLayout(title_row)

        hint = QLabel(
            "The Pāli and English sources are fixed project texts. Select text "
            "in either source and choose “Use selected text”, or edit the "
            "coordinates directly. Recordings come from the known language "
            "folders; no file browser is used. Coordinates are 1-based; "
            "start is inclusive and end is exclusive."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6a655c; font-size: 11px;")
        outer.addWidget(hint)

        self.pali_text = SourceRangeSelector(
            "Pāli text", "pali", self.project_root, section.pali_text
        )
        self.english_text = SourceRangeSelector(
            "English text", "english", self.project_root, section.english_text
        )
        self.text_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.text_splitter.setChildrenCollapsible(False)
        self.text_splitter.addWidget(self.pali_text)
        self.text_splitter.addWidget(self.english_text)
        self.text_splitter.setSizes([600, 600])

        self.pali_audio = AudioConfigEditor(
            "Spoken Pāli recording", "pali", self.project_root, section.spoken_pali,
            sections=sections, current_section_id=section.id,
        )
        self.english_audio = AudioConfigEditor(
            "Spoken English recording",
            "english",
            self.project_root,
            section.spoken_english,
            sections=sections,
            current_section_id=section.id,
        )
        self.goenka = GoenkaConfigEditor(
            "Goenka chanting",
            section.goenka,
            project_root=self.project_root,
            sections=sections,
            current_section_id=section.id,
        )
        self.config_tabs = QTabWidget()
        self.config_tabs.addTab(_scrollable(self.pali_audio), "Spoken Pāli")
        self.config_tabs.addTab(_scrollable(self.english_audio), "Spoken English")
        self.config_tabs.addTab(_scrollable(self.goenka), "Goenka chanting")

        self.body_splitter = QSplitter(Qt.Orientation.Vertical)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.addWidget(self.text_splitter)
        self.body_splitter.addWidget(self.config_tabs)
        self.body_splitter.setStretchFactor(0, 5)
        self.body_splitter.setStretchFactor(1, 3)
        self.body_splitter.setSizes([560, 230])
        outer.addWidget(self.body_splitter, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save section")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._restore_geometry()
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    # -- result -------------------------------------------------------------
    def edited_section(self) -> Section | None:
        return self._result

    def accept(self) -> None:
        errors: list[str] = []
        for label, selector in (
            ("Pāli passage", self.pali_text),
            ("English passage", self.english_text),
        ):
            error = selector.validation_error()
            if error:
                errors.append(f"{label}: {error}")
        for label, editor in (
            ("Spoken Pāli", self.pali_audio),
            ("Spoken English", self.english_audio),
        ):
            error = editor.validation_error()
            if error:
                errors.append(f"{label}: {error}")
        video_error = self.goenka.validation_error()
        if video_error:
            errors.append(f"Goenka chanting: {video_error}")
        if errors:
            QMessageBox.warning(
                self,
                "Section needs attention",
                "\n\n".join(errors),
            )
            return

        edited = deepcopy(self._edited)
        edited.title = self.title_edit.text().strip() or "Untitled section"
        edited.pali_text = self.pali_text.reference()
        edited.english_text = self.english_text.reference()
        edited.spoken_pali = self.pali_audio.reference()
        edited.spoken_english = self.english_audio.reference()
        edited.goenka = self.goenka.reference()
        self._result = edited
        self._save_geometry()
        super().accept()

    def reject(self) -> None:
        self._save_geometry()
        super().reject()

    # -- geometry persistence ----------------------------------------------
    def _restore_geometry(self) -> None:
        if self.settings is None:
            return
        geometry = self.settings.value("section_editor/geometry")
        if geometry is not None:
            try:
                self.restoreGeometry(geometry)
            except (TypeError, ValueError):
                pass
        for key, splitter in (
            ("section_editor/body_splitter", self.body_splitter),
            ("section_editor/text_splitter", self.text_splitter),
        ):
            state = self._byte_array(self.settings.value(key))
            if state is not None:
                splitter.restoreState(state)
        tab_index = self.settings.value("section_editor/config_tab")
        if tab_index is not None:
            try:
                index = int(tab_index)
                if 0 <= index < self.config_tabs.count():
                    self.config_tabs.setCurrentIndex(index)
            except (TypeError, ValueError):
                pass

    def _save_geometry(self) -> None:
        if self.settings is None:
            return
        self.settings.setValue("section_editor/geometry", self.saveGeometry())
        self.settings.setValue(
            "section_editor/body_splitter", self.body_splitter.saveState()
        )
        self.settings.setValue(
            "section_editor/text_splitter", self.text_splitter.saveState()
        )
        self.settings.setValue(
            "section_editor/config_tab", self.config_tabs.currentIndex()
        )
        self.settings.sync()

    @staticmethod
    def _byte_array(value) -> QByteArray | None:
        if isinstance(value, QByteArray):
            return value
        if isinstance(value, bytes):
            return QByteArray(value)
        return None
