"""Combined full-source view and coordinate editor for one language."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..model import TextReference
from ..source_resources import text_resource_path, text_resource_relative
from ..texts import (
    SourceDocument,
    TextRangeError,
    TextSourceError,
    extract_range,
    read_source_document,
)
from .source_browser import SourceTextEdit

MAX_COORDINATE = 2_147_483_647


class SourceRangeSelector(QGroupBox):
    """Show a complete fixed source and let the user configure its passage."""

    changed = Signal()

    def __init__(
        self,
        title: str,
        language: str,
        project_root: Path,
        reference: TextReference | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.language = language
        self.project_root = project_root
        self.path = text_resource_path(project_root, language)
        self._source: SourceDocument | None = None
        self._source_error = ""
        self._enabled = False
        self._loading = False
        self._range_error = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self.path_label = QLabel(text_resource_relative(language))
        self.path_label.setWordWrap(True)
        self.path_label.setStyleSheet("color: #6a655c; font-size: 11px;")
        layout.addWidget(self.path_label)

        self.editor = SourceTextEdit()
        self.editor.setMinimumHeight(160)
        layout.addWidget(self.editor, 1)

        button_row = QHBoxLayout()
        self.use_button = QPushButton("Use selected text")
        self.use_button.setToolTip(
            "Use the text currently selected in the source as this passage"
        )
        self.all_button = QPushButton("Select full text")
        self.clear_button = QPushButton("Clear passage")
        button_row.addWidget(self.use_button)
        button_row.addWidget(self.all_button)
        button_row.addWidget(self.clear_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        coordinates = QHBoxLayout()
        coordinates.addWidget(QLabel("Start"))
        self.start_line = self._coordinate_spin("line")
        self.start_column = self._coordinate_spin("column")
        coordinates.addWidget(self.start_line)
        coordinates.addWidget(self.start_column)
        coordinates.addSpacing(10)
        coordinates.addWidget(QLabel("End"))
        self.end_line = self._coordinate_spin("line")
        self.end_column = self._coordinate_spin("column")
        coordinates.addWidget(self.end_line)
        coordinates.addWidget(self.end_column)
        coordinates.addStretch(1)
        layout.addLayout(coordinates)

        self.status_label = QLabel("No passage selected.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #6a655c; font-size: 11px;")
        layout.addWidget(self.status_label)

        self.use_button.clicked.connect(self._use_selection)
        self.all_button.clicked.connect(self._select_full_text)
        self.clear_button.clicked.connect(self._clear)
        self.editor.selectionChanged.connect(self._selection_changed)
        self.use_button.setEnabled(False)
        for spin in (
            self.start_line,
            self.start_column,
            self.end_line,
            self.end_column,
        ):
            spin.valueChanged.connect(self._coordinates_changed)

        self._load_source()
        self.set_reference(reference)

    @staticmethod
    def _coordinate_spin(what: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, MAX_COORDINATE)
        spin.setValue(1)
        spin.setPrefix("L" if what == "line" else "C")
        spin.setMinimumWidth(52)
        spin.setMaximumWidth(82)
        spin.setToolTip(f"1-based {what}")
        return spin

    # -- public API ---------------------------------------------------------
    def reference(self) -> TextReference | None:
        if not self._enabled or self._source is None:
            return None
        return TextReference(
            path=text_resource_relative(self.language),
            start_line=self.start_line.value(),
            start_column=self.start_column.value(),
            end_line=self.end_line.value(),
            end_column=self.end_column.value(),
        )

    def set_reference(self, reference: TextReference | None) -> None:
        self._loading = True
        try:
            if reference is None:
                self._enabled = False
                self._set_coordinates(1, 1, 1, 1)
            else:
                self._enabled = True
                self._set_coordinates(
                    reference.start_line,
                    reference.start_column,
                    reference.end_line,
                    reference.end_column,
                )
        finally:
            self._loading = False
        self._refresh_highlight(scroll_to_start=reference is not None)
        self._emit_change()

    def validation_error(self) -> str:
        if not self._enabled:
            return ""
        return self._range_error

    # -- loading ------------------------------------------------------------
    def _load_source(self) -> None:
        try:
            self._source = read_source_document(self.path)
        except TextSourceError as error:
            self._source = None
            self._source_error = str(error)
            self.editor.setPlainText(
                f"The {self.language} source text is unavailable.\n\n{error}"
            )
            self.status_label.setText(
                f"Missing source file: {self.path.name}. "
                "Place the fixed study text in the repository."
            )
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            self.use_button.setEnabled(False)
            self.all_button.setEnabled(False)
            self.clear_button.setEnabled(False)
            for spin in (
                self.start_line,
                self.start_column,
                self.end_line,
                self.end_column,
            ):
                spin.setEnabled(False)
            return
        self._source_error = ""
        self.editor.setPlainText(self._source.text)
        self.path_label.setText(
            f"{text_resource_relative(self.language)}  ·  "
            f"{self._source.line_count} lines"
        )

    # -- user selection -----------------------------------------------------
    def _selection_changed(self) -> None:
        if self._loading:
            return
        selected = self._cursor_range()
        self.use_button.setEnabled(selected is not None)
        if selected is None:
            if self._enabled:
                self._show_configured_status()
            else:
                self.status_label.setText(
                    "Select text in the source, then choose Use selected text."
                )
                self.status_label.setStyleSheet("color: #6a655c; font-size: 11px;")
            return
        start_line, start_column, end_line, end_column = selected
        self.status_label.setText(
            f"Selection: line {start_line}, column {start_column} → "
            f"line {end_line}, column {end_column} (end exclusive). "
            "Choose Use selected text to keep it."
        )
        self.status_label.setStyleSheet("color: #3e5f43; font-size: 11px;")

    def _use_selection(self) -> None:
        selected = self._cursor_range()
        if selected is None:
            return
        self._loading = True
        try:
            self._set_coordinates(*selected)
            cursor = self.editor.textCursor()
            cursor.clearSelection()
            self.editor.setTextCursor(cursor)
        finally:
            self._loading = False
        self._enabled = True
        self.use_button.setEnabled(False)
        self._refresh_highlight(scroll_to_start=True)
        self._emit_change()

    def _select_full_text(self) -> None:
        if self._source is None:
            return
        lines = self._source.lines
        if not lines:
            return
        self._loading = True
        try:
            self._set_coordinates(1, 1, len(lines), len(lines[-1]) + 1)
            cursor = self.editor.textCursor()
            cursor.clearSelection()
            self.editor.setTextCursor(cursor)
        finally:
            self._loading = False
        self._enabled = True
        self.use_button.setEnabled(False)
        self._refresh_highlight(scroll_to_start=True)
        self._emit_change()

    def _clear(self) -> None:
        self._loading = True
        try:
            self._enabled = False
            self._set_coordinates(1, 1, 1, 1)
        finally:
            self._loading = False
        self._refresh_highlight()
        self._emit_change()

    def _coordinates_changed(self, *_args) -> None:
        if self._loading:
            return
        self._enabled = True
        self._refresh_highlight()
        self._emit_change()

    # -- coordinate/highlight helpers --------------------------------------
    def _set_coordinates(
        self, start_line: int, start_column: int, end_line: int, end_column: int
    ) -> None:
        for spin, value in (
            (self.start_line, start_line),
            (self.start_column, start_column),
            (self.end_line, end_line),
            (self.end_column, end_column),
        ):
            blocker = QSignalBlocker(spin)
            spin.setValue(max(1, min(MAX_COORDINATE, value)))
            del blocker

    def _cursor_range(self) -> tuple[int, int, int, int] | None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return None
        start_position = cursor.selectionStart()
        end_position = cursor.selectionEnd()
        document = self.editor.document()
        start_block = document.findBlock(start_position)
        end_block = document.findBlock(end_position)
        return (
            start_block.blockNumber() + 1,
            start_position - start_block.position() + 1,
            end_block.blockNumber() + 1,
            end_position - end_block.position() + 1,
        )

    def _positions_for_range(
        self, start_line: int, start_column: int, end_line: int, end_column: int
    ) -> tuple[int, int] | None:
        document = self.editor.document()
        if (
            start_line < 1
            or end_line < 1
            or start_line > document.blockCount()
            or end_line > document.blockCount()
        ):
            return None
        start_block = document.findBlockByNumber(start_line - 1)
        end_block = document.findBlockByNumber(end_line - 1)
        start_position = start_block.position() + start_column - 1
        end_position = end_block.position() + end_column - 1
        if (
            start_position < 0
            or end_position < start_position
            or end_position > document.characterCount() - 1
        ):
            return None
        return start_position, end_position

    def _refresh_highlight(self, scroll_to_start: bool = False) -> None:
        if self._source is None:
            self.editor.setExtraSelections([])
            self._range_error = self._source_error or "The source file is unavailable."
            self.status_label.setText(
                f"Source unavailable: {self.path.name}. "
                "Place the fixed study text in the repository."
            )
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            self.clear_button.setEnabled(False)
            return
        if not self._enabled or self._source is None:
            self.editor.setExtraSelections([])
            self._range_error = ""
            self.status_label.setText("No passage selected.")
            self.status_label.setStyleSheet("color: #6a655c; font-size: 11px;")
            self.clear_button.setEnabled(False)
            return
        self.clear_button.setEnabled(True)
        coords = (
            self.start_line.value(),
            self.start_column.value(),
            self.end_line.value(),
            self.end_column.value(),
        )
        try:
            extract_range(self._source, *coords)
        except TextRangeError as error:
            self._range_error = str(error)
            self.editor.setExtraSelections([])
            self.status_label.setText(f"Invalid range — {error}")
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return
        positions = self._positions_for_range(*coords)
        if positions is None:
            self._range_error = "The range is outside the loaded source."
            self.editor.setExtraSelections([])
            self.status_label.setText(f"Invalid range — {self._range_error}")
            self.status_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return
        self._range_error = ""
        start_position, end_position = positions
        selection = QTextEdit.ExtraSelection()
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(start_position)
        cursor.setPosition(end_position, QTextCursor.MoveMode.KeepAnchor)
        selection.cursor = cursor
        selection_format = QTextCharFormat()
        selection_format.setBackground(QColor("#f6e7a8"))
        selection_format.setForeground(QColor("#2b2b2b"))
        selection.format = selection_format
        self.editor.setExtraSelections([selection])
        if scroll_to_start:
            scroll_cursor = QTextCursor(self.editor.document())
            scroll_cursor.setPosition(start_position)
            self.editor.setTextCursor(scroll_cursor)
            self.editor.centerCursor()
            self.use_button.setEnabled(False)
        self._show_configured_status()

    def _show_configured_status(self) -> None:
        self.status_label.setText(
            f"Configured: line {self.start_line.value()}, "
            f"column {self.start_column.value()} → "
            f"line {self.end_line.value()}, column {self.end_column.value()} "
            "(end exclusive)."
        )
        self.status_label.setStyleSheet("color: #4c674d; font-size: 11px;")

    def _emit_change(self) -> None:
        self.changed.emit()
