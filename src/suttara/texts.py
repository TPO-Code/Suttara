"""Read local source texts and extract coordinate-based passages.

Plain UTF-8 files are used directly.  The downloaded Ancient Buddhist Texts
sources are HTML, so Suttara offers a deterministic visible-text view for
those files: scripts, styles, navigation blocks, audio widgets and the
editors' inline tooltip notes are omitted, and block elements become lines.
The source browser in the application displays exactly this view, with line
numbers, so coordinates never depend on an unseen transformation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

HTML_SUFFIXES = {".htm", ".html", ".xhtml"}


class TextSourceError(Exception):
    """A source file could not be read or decoded."""


class TextRangeError(ValueError):
    """Coordinates do not describe a valid, non-empty passage."""


@dataclass(frozen=True)
class SourceDocument:
    """A source file in the same form the user browses and selects from."""

    path: Path
    text: str
    mode: str
    mode_description: str

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    @property
    def line_count(self) -> int:
        return len(self.lines)


def is_html_path(path: Path) -> bool:
    return Path(path).suffix.lower() in HTML_SUFFIXES


def read_source_document(path: Path) -> SourceDocument:
    path = Path(path)
    if not path.exists():
        raise TextSourceError(f"Text source does not exist: {path}")
    if not path.is_file():
        raise TextSourceError(f"Text source is not a file: {path}")
    try:
        raw_bytes = path.read_bytes()
    except OSError as error:
        raise TextSourceError(f"Could not read text source {path}: {error}") from error
    try:
        decoded = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise TextSourceError(
            f"{path} is not valid UTF-8 (byte {error.start}). Convert it to "
            "plain UTF-8 text before referencing it."
        ) from error

    if is_html_path(path):
        text = html_to_visible_text(decoded)
        description = (
            "HTML visible text: scripts, styles, navigation, audio widgets and "
            "editorial tooltip notes are omitted; coordinates refer to this view."
        )
        mode = "html"
    else:
        text = normalise_text(decoded)
        description = "UTF-8 text; coordinates refer to its physical lines."
        mode = "text"
    return SourceDocument(path=path, text=text, mode=mode, mode_description=description)


def extract_range(
    source: SourceDocument | str,
    start_line: int,
    start_column: int,
    end_line: int,
    end_column: int,
) -> str:
    """Extract a passage using 1-based, inclusive-start/exclusive-end positions.

    Columns count Unicode code points.  The returned text joins selected lines
    with ``"\n"`` regardless of the source file's original line endings.
    """

    text = source.text if isinstance(source, SourceDocument) else source
    lines = text.splitlines()
    if not lines:
        raise TextRangeError("The source contains no text lines")
    for name, value in (
        ("start line", start_line),
        ("start column", start_column),
        ("end line", end_line),
        ("end column", end_column),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise TextRangeError(f"The {name} must be a whole number of at least 1")

    if start_line > len(lines):
        raise TextRangeError(
            f"Start line {start_line} is beyond the end of the source "
            f"({len(lines)} lines)"
        )
    if end_line > len(lines):
        raise TextRangeError(
            f"End line {end_line} is beyond the end of the source ({len(lines)} lines)"
        )

    start_line_text = lines[start_line - 1]
    end_line_text = lines[end_line - 1]
    if start_column > len(start_line_text) + 1:
        raise TextRangeError(
            f"Start column {start_column} is beyond line {start_line} "
            f"({len(start_line_text)} characters)"
        )
    if end_column > len(end_line_text) + 1:
        raise TextRangeError(
            f"End column {end_column} is beyond line {end_line} "
            f"({len(end_line_text)} characters)"
        )

    start_position = (start_line, start_column)
    end_position = (end_line, end_column)
    if start_position == end_position:
        raise TextRangeError("The selected passage is empty")
    if start_position > end_position:
        raise TextRangeError("The start position must come before the end position")

    if start_line == end_line:
        excerpt = start_line_text[start_column - 1 : end_column - 1]
    else:
        pieces = [start_line_text[start_column - 1 :]]
        pieces.extend(lines[start_line : end_line - 1])
        pieces.append(end_line_text[: end_column - 1])
        excerpt = "\n".join(pieces)
    if not excerpt:
        raise TextRangeError("The selected passage is empty")
    return excerpt


class _VisibleTextParser(HTMLParser):
    """A stable, readable-text projection of the downloaded HTML sources."""

    _BLOCK_TAGS: ClassVar[set[str]] = {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
    _VOID_TAGS: ClassVar[set[str]] = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    _SKIP_TAGS: ClassVar[set[str]] = {
        "audio",
        "button",
        "canvas",
        "footer",
        "form",
        "head",
        "iframe",
        "nav",
        "noscript",
        "script",
        "select",
        "style",
        "svg",
        "template",
        "textarea",
        "title",
        "video",
    }
    _SKIP_CLASSES: ClassVar[set[str]] = {
        "autonumber",
        "navbar1",
        "navbar3",
        "saveas",
        "tt",  # Ānandajoti's inline editorial tooltip notes
    }
    _SKIP_IDS: ClassVar[set[str]] = {"audio-player"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._pending_breaks = 0
        self._pending_space = False
        self._skip_stack: list[str] = []

    def result(self) -> str:
        raw = "".join(self._parts).replace("\u00a0", " ")
        lines = [line.rstrip() for line in raw.splitlines()]
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        compact: list[str] = []
        for line in lines:
            if not line and compact and not compact[-1]:
                continue
            compact.append(line)
        return "\n".join(compact)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._is_skipped(tag, attrs):
            if tag not in self._VOID_TAGS:
                self._skip_stack.append(tag)
            return
        if self._skip_stack:
            if tag not in self._VOID_TAGS:
                self._skip_stack.append(tag)
            return
        if tag == "br":
            self._break(1)
        elif tag in self._BLOCK_TAGS:
            self._break(2)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._is_skipped(tag, attrs) or self._skip_stack:
            return
        if tag == "br":
            self._break(1)
        elif tag in self._BLOCK_TAGS:
            self._break(2)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_stack:
            if tag in self._skip_stack:
                while self._skip_stack:
                    open_tag = self._skip_stack.pop()
                    if open_tag == tag:
                        break
            return
        if tag in self._BLOCK_TAGS:
            self._break(2)

    def handle_data(self, data: str) -> None:
        if self._skip_stack or not data:
            return
        collapsed = re.sub(r"\s+", " ", data)
        if not collapsed:
            return
        token = collapsed.strip()
        if not token:
            if (
                self._parts
                and not self._pending_breaks
                and not self._parts[-1].endswith((" ", "\n"))
            ):
                self._pending_space = True
            return
        leading_space = collapsed.startswith(" ")
        trailing_space = collapsed.endswith(" ")
        self._flush_breaks()
        if leading_space:
            self._pending_space = True
        if (
            self._pending_space
            and self._parts
            and not self._parts[-1].endswith((" ", "\n"))
        ):
            self._parts.append(" ")
        self._pending_space = False
        self._parts.append(token)
        if trailing_space:
            self._pending_space = True

    def _break(self, count: int) -> None:
        self._pending_breaks = max(self._pending_breaks, count)
        self._pending_space = False

    def _flush_breaks(self) -> None:
        if not self._pending_breaks:
            return
        if self._parts:
            self._parts.append("\n" * min(self._pending_breaks, 2))
        self._pending_breaks = 0
        self._pending_space = False

    def _is_skipped(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in self._SKIP_TAGS:
            return True
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if attributes.get("id", "").lower() in self._SKIP_IDS:
            return True
        classes = {
            token.lower()
            for token in re.split(r"\s+", attributes.get("class", "").strip())
            if token
        }
        return bool(classes & self._SKIP_CLASSES)


def html_to_visible_text(html: str) -> str:
    """Return the deterministic visible-text view used for HTML sources."""

    parser = _VisibleTextParser()
    parser.feed(html)
    parser.close()
    return parser.result()


def normalise_text(text: str) -> str:
    """Use ``\n`` line separators and drop trailing empty lines.

    The visual source browser and the range extractor therefore agree about
    what line ``n`` contains.  Interior blank lines are preserved.
    """

    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)
