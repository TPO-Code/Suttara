"""Informational dialogs: source provenance and about."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as error:
        return f"Could not read {path}:\n{error}"
    except UnicodeDecodeError as error:
        return f"{path} is not valid UTF-8:\n{error}"


def _read_json_document(path: Path) -> str:
    text = _read_text(path)
    if text.startswith("Could not") or text.startswith("Could not read"):
        return text
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError) as error:
        return f"{path} could not be parsed as JSON:\n{error}\n\nRaw content:\n{text}"


def _file_inventory(root: Path) -> str:
    source_root = root / "suttara_sources"
    if not source_root.exists():
        return (
            f"No local source-material directory was found at:\n{source_root}\n\n"
            "Suttara does not download material automatically. Use the source "
            "download script or configure your own local files in each section."
        )
    lines: list[str] = [
        f"Local material under {source_root}:",
        "",
        "These files are local study resources. They are not embedded in the "
        "application or copied into suttara.json.",
        "",
    ]
    try:
        paths = sorted(
            path
            for path in source_root.rglob("*")
            if path.is_file() and path.name != ".DS_Store"
        )
    except OSError as error:
        return f"Could not list {source_root}:\n{error}"
    current_parent: Path | None = None
    for path in paths:
        if path.parent != current_parent:
            current_parent = path.parent
            try:
                relative_parent = path.parent.relative_to(root)
            except ValueError:
                relative_parent = path.parent
            lines.append(f"[{relative_parent}]")
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        lines.append(f"  {path.name}  ({size / 1024:.1f} KiB)")
    return "\n".join(lines)


class SourceMaterialsDialog(QDialog):
    """Read-only access to credits, metadata and the local inventory."""

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sources & attribution — Suttara")
        self.resize(920, 700)

        layout = QVBoxLayout(self)
        notice = QLabel(
            "Suttara references local third-party study material; it does not "
            "redistribute it. The credits and acquisition URLs below come from "
            "the repository's sources.txt and sources metadata. Review the "
            "original licences before sharing any material."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(
            "background: #f3f0e8; border: 1px solid #ddd6c8; border-radius: 4px; "
            "padding: 9px; color: #4e493f;"
        )
        layout.addWidget(notice)

        tabs = QTabWidget()
        tabs.addTab(
            self._text_tab(project_root / "sources.txt"),
            "Credits (sources.txt)",
        )
        metadata_path = project_root / "suttara_sources" / "sources.json"
        if not metadata_path.exists():
            metadata_path = project_root / "sources.json"
        tabs.addTab(
            self._json_tab(metadata_path),
            "Source metadata",
        )
        tabs.addTab(
            self._plain_tab(_file_inventory(project_root)),
            "Local resource inventory",
        )
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _monospace(self) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(max(9, font.pointSize()))
        editor.setFont(font)
        return editor

    def _plain_tab(self, text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        editor = self._monospace()
        editor.setPlainText(text)
        layout.addWidget(editor)
        return page

    def _text_tab(self, path: Path) -> QWidget:
        if not path.exists():
            return self._plain_tab(
                f"No credits file was found at:\n{path}\n\n"
                "Keep sources.txt in the repository so attribution remains "
                "available. Suttara never downloads material automatically."
            )
        return self._plain_tab(_read_text(path))

    def _json_tab(self, path: Path) -> QWidget:
        if not path.exists():
            return self._plain_tab(
                f"No source metadata file was found at:\n{path}\n\n"
                "Expected source metadata under suttara_sources/sources.json."
            )
        return self._plain_tab(_read_json_document(path))
