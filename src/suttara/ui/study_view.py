"""Study-focused main view: passages, playback and notes."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..model import Section
from ..source_resources import recording_label
from .audio_player import AudioExcerptPlayer
from .passage_view import PassageViewer
from .youtube_player import YouTubeExcerptPlayer


def _scrollable(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


class StudyView(QWidget):
    """The normal reading/listening view; resource configuration lives elsewhere."""

    changed = Signal()
    edit_requested = Signal()

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.section: Section | None = None
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(9)

        header = QHBoxLayout()
        title_column = QVBoxLayout()
        self.title_label = QLabel("No section selected")
        self.title_label.setStyleSheet(
            "font-size: 21px; font-weight: 700; color: #3e3a33;"
        )
        self.title_label.setWordWrap(True)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #6a655c; font-size: 11px;")
        title_column.addWidget(self.title_label)
        title_column.addWidget(self.summary_label)
        header.addLayout(title_column, 1)
        self.edit_button = QPushButton("Edit Section…")
        self.edit_button.setToolTip("Configure passages, recordings and chanting")
        self.edit_button.clicked.connect(lambda: self.edit_requested.emit())
        header.addWidget(self.edit_button, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        self.pali = PassageViewer("Pāli passage", project_root, "pali")
        self.english = PassageViewer("English passage", project_root, "english")
        self.text_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.text_splitter.setChildrenCollapsible(False)
        self.text_splitter.addWidget(self.pali)
        self.text_splitter.addWidget(self.english)
        self.text_splitter.setSizes([600, 600])

        self.media_tabs = QTabWidget()
        self.spoken_pali = AudioExcerptPlayer("Spoken Pāli", project_root)
        self.spoken_english = AudioExcerptPlayer("Spoken English", project_root)
        self.goenka = YouTubeExcerptPlayer("Goenka chanting", project_root)
        self.media_tabs.addTab(_scrollable(self.spoken_pali), "Spoken Pāli")
        self.media_tabs.addTab(_scrollable(self.spoken_english), "Spoken English")
        self.media_tabs.addTab(_scrollable(self.goenka), "Goenka chanting")
        self.media_tabs.setMinimumHeight(170)

        self.notes_group = QGroupBox("Study notes")
        notes_layout = QVBoxLayout(self.notes_group)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText(
            "Your own notes for this section. Saved in suttara.json."
        )
        self.notes_edit.setMinimumHeight(80)
        notes_layout.addWidget(self.notes_edit, 1)

        self.body_splitter = QSplitter(Qt.Orientation.Vertical)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.addWidget(self.text_splitter)
        self.body_splitter.addWidget(self.media_tabs)
        self.body_splitter.addWidget(self.notes_group)
        self.body_splitter.setStretchFactor(0, 3)
        self.body_splitter.setStretchFactor(1, 4)
        self.body_splitter.setStretchFactor(2, 2)
        self.body_splitter.setSizes([250, 340, 127])
        outer.addWidget(self.body_splitter, 1)

        self.notes_edit.textChanged.connect(self._notes_changed)
        self.spoken_pali.loop_changed.connect(self._pali_loop_changed)
        self.spoken_english.loop_changed.connect(self._english_loop_changed)
        self.goenka.loop_changed.connect(self._video_loop_changed)

    # -- content ------------------------------------------------------------
    def set_section(self, section: Section | None) -> None:
        self._loading = True
        self.section = section
        self.goenka.stop()
        try:
            if section is None:
                self.title_label.setText("No section selected")
                self.summary_label.setText("")
                self.pali.set_reference(None)
                self.english.set_reference(None)
                self.spoken_pali.set_reference(None)
                self.spoken_english.set_reference(None)
                self.goenka.set_reference(None)
                self.notes_edit.clear()
                self.setEnabled(False)
                return
            self.setEnabled(True)
            self.refresh_section()
            self.pali.set_reference(section.pali_text)
            self.english.set_reference(section.english_text)
            self.spoken_pali.set_reference(
                section.spoken_pali,
                recording_label(Path(section.spoken_pali.path))
                if section.spoken_pali
                else None,
            )
            self.spoken_english.set_reference(
                section.spoken_english,
                recording_label(Path(section.spoken_english.path))
                if section.spoken_english
                else None,
            )
            self.goenka.set_reference(section.goenka)
            self.notes_edit.setPlainText(section.notes)
        finally:
            self._loading = False

    def refresh_section(self) -> None:
        """Refresh the title/summary without reloading media."""

        section = self.section
        if section is None:
            return
        self.title_label.setText(section.display_title)
        self.summary_label.setText(section.resource_summary())

    # -- changes ------------------------------------------------------------
    def _notes_changed(self) -> None:
        if self._loading or self.section is None:
            return
        self.section.notes = self.notes_edit.toPlainText()
        self.changed.emit()

    def _pali_loop_changed(self, checked: bool) -> None:
        if self._loading or self.section is None or self.section.spoken_pali is None:
            return
        self.section.spoken_pali.loop = checked
        self.changed.emit()

    def _english_loop_changed(self, checked: bool) -> None:
        if self._loading or self.section is None or self.section.spoken_english is None:
            return
        self.section.spoken_english.loop = checked
        self.changed.emit()

    def _video_loop_changed(self, checked: bool) -> None:
        if self._loading or self.section is None or self.section.goenka is None:
            return
        self.section.goenka.loop = checked
        self.changed.emit()

    # -- layout persistence -------------------------------------------------
    def splitter_states(self) -> tuple[QByteArray, QByteArray]:
        return self.body_splitter.saveState(), self.text_splitter.saveState()

    def restore_splitter_states(
        self, body_state: QByteArray | None, text_state: QByteArray | None
    ) -> None:
        if body_state is not None:
            self.body_splitter.restoreState(body_state)
        if text_state is not None:
            self.text_splitter.restoreState(text_state)

    def media_tab_index(self) -> int:
        return self.media_tabs.currentIndex()

    def set_media_tab_index(self, index: int) -> None:
        if 0 <= index < self.media_tabs.count():
            self.media_tabs.setCurrentIndex(index)
