"""Unicode text extraction, HTML projection and range validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from suttara.texts import (
    TextRangeError,
    TextSourceError,
    extract_range,
    html_to_visible_text,
    normalise_text,
    read_source_document,
)


def test_plain_text_range_is_inclusive_start_exclusive_end():
    text = "first line\nsecond line\nthird line"
    assert extract_range(text, 1, 1, 1, 6) == "first"
    assert extract_range(text, 1, 7, 1, 11) == "line"
    assert extract_range(text, 2, 1, 2, 7) == "second"
    assert extract_range(text, 1, 1, 2, 7) == "first line\nsecond"


def test_columns_count_unicode_characters_not_bytes():
    text = "Ānāpānasati\nsatipaṭṭhāna"
    # Each Pāli diacritic is one character in the coordinate system.
    assert extract_range(text, 1, 1, 1, 5) == "Ānāp"
    assert extract_range(text, 1, 6, 1, 12) == "nasati"
    assert extract_range(text, 2, 7, 2, 11) == "ṭṭhā"


def test_multiline_extraction_preserves_interior_blank_lines():
    text = "alpha\n\nbeta\n\ngamma"
    assert extract_range(text, 1, 1, 5, 6) == text
    assert extract_range(text, 1, 6, 5, 1) == "\n\nbeta\n\n"


def test_out_of_bounds_and_empty_ranges_are_rejected():
    text = "one\ntwo"
    cases = [
        (0, 1, 1, 2),
        (1, 0, 1, 2),
        (3, 1, 3, 2),
        (1, 5, 1, 6),
        (1, 1, 2, 5),
        (2, 1, 1, 2),
        (1, 2, 1, 2),
    ]
    for case in cases:
        with pytest.raises(TextRangeError):
            extract_range(text, *case)


def test_empty_source_is_rejected():
    with pytest.raises(TextRangeError):
        extract_range("", 1, 1, 1, 2)


def test_read_source_normalises_bom_and_crlf_and_trailing_lines(tmp_path: Path):
    path = tmp_path / "source.txt"
    path.write_bytes(b"\xef\xbb\xbfPali\r\n\r\nSecond\r\n\r\n")
    document = read_source_document(path)
    assert document.text == "Pali\n\nSecond"
    assert document.mode == "text"
    assert extract_range(document, 3, 1, 3, 7) == "Second"


def test_missing_and_non_utf8_sources_report_clear_errors(tmp_path: Path):
    with pytest.raises(TextSourceError):
        read_source_document(tmp_path / "missing.txt")
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(TextSourceError) as caught:
        read_source_document(bad)
    assert "UTF-8" in str(caught.value)


def test_html_visible_text_is_deterministic_and_omits_notes():
    html = """
    <html><head><title>Ignored</title><style>.x{}</style>
    <script>bad()</script></head>
    <body>
      <p class="NavBar1">navigation</p>
      <p>Pāli <span class="TT"><span>editorial tooltip note</span></span> text</p>
      <p>Second <b>paragraph</b><br>with a break</p>
      <audio controls><source src="x.mp3"></audio>
      <footer>footer text</footer>
    </body></html>
    """
    first = html_to_visible_text(html)
    second = html_to_visible_text(html)
    assert first == second
    assert "Ignored" not in first
    assert "editorial tooltip" not in first
    assert "navigation" not in first
    assert "footer text" not in first
    assert "Pāli text" in first
    assert "Second paragraph" in first
    assert "with a break" in first
    assert "\n\n" in first


def test_downloaded_plain_text_source_keeps_pali_diacritics():
    path = (
        Path(__file__).resolve().parents[1]
        / "suttara_sources"
        / "texts"
        / "mahasatipatthana_pali.txt"
    )
    if not path.exists():
        pytest.skip("downloaded plain-text Pāli source is not present")
    document = read_source_document(path)
    assert document.mode == "text"
    assert "Mahāsatipaṭṭhānasutta" in document.text
    first_line = document.text.splitlines()[2]
    assert extract_range(document, 3, 1, 3, len(first_line) + 1) == first_line


def test_downloaded_html_source_projects_readable_text():
    path = (
        Path(__file__).resolve().parents[1]
        / "suttara_sources"
        / "texts"
        / "pali"
        / "Mahasatipatthana-01.htm"
    )
    if not path.exists():
        pytest.skip("downloaded HTML Pāli source is not present")
    document = read_source_document(path)
    assert document.mode == "html"
    assert "Mahāsatipaṭṭhānasuttaṁ" in document.text
    excerpt = extract_range(document, 1, 1, 1, 22)
    assert excerpt == "Mahāsatipaṭṭhānasutta"


def test_normalise_text_keeps_interior_blank_lines():
    assert normalise_text("a\n\nb\n\n") == "a\n\nb"
