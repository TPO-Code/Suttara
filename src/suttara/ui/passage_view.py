"""Read-only passage panes for the main study view."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..model import TextReference
from ..source_resources import text_resource_path
from ..texts import TextRangeError, TextSourceError, extract_range, read_source_document


class PassageViewer(QGroupBox):
    """A section's selected passage, with no resource-configuration controls."""

    def __init__(
        self,
        title: str,
        project_root: Path,
        language: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.project_root = project_root
        self.language = language
        self._reference: TextReference | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText("The selected passage will appear here.")
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        self.text_edit.setFont(font)
        self.text_edit.setMinimumHeight(80)
        layout.addWidget(self.text_edit, 1)

        self.range_label = QLabel("No passage selected.")
        self.range_label.setWordWrap(True)
        self.range_label.setStyleSheet("color: #6a655c; font-size: 11px;")
        layout.addWidget(self.range_label)

    def set_reference(self, reference: TextReference | None) -> None:
        self._reference = reference
        if reference is None:
            self.text_edit.clear()
            self.text_edit.setPlaceholderText("No passage selected for this section.")
            self.range_label.setText("No passage selected.")
            self.range_label.setStyleSheet("color: #6a655c; font-size: 11px;")
            return

        path = text_resource_path(self.project_root, self.language)
        try:
            document = read_source_document(path)
            excerpt = extract_range(
                document,
                reference.start_line,
                reference.start_column,
                reference.end_line,
                reference.end_column,
            )
        except TextSourceError as error:
            self.text_edit.setPlainText(
                f"The {self.language} source text is unavailable.\n\n{error}"
            )
            self.range_label.setText(
                f"Missing source file: {path.name}. Check the local study resources."
            )
            self.range_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return
        except TextRangeError as error:
            self.text_edit.setPlainText(
                f"The passage coordinates are invalid.\n\n{error}"
            )
            self.range_label.setText(
                "This section's passage range needs to be corrected."
            )
            self.range_label.setStyleSheet("color: #a33a2b; font-weight: 600;")
            return

        self.text_edit.setPlainText(excerpt)
        line_count = len(excerpt.splitlines())
        self.range_label.setText(
            f"Lines {reference.start_line}:{reference.start_column} → "
            f"{reference.end_line}:{reference.end_column}  ·  "
            f"{line_count} displayed line(s)."
        )
        self.range_label.setStyleSheet("color: #4c674d; font-size: 11px;")
