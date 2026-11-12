"""Timestamp, excerpt validation and YouTube URL tests."""

from __future__ import annotations

import pytest

from suttara.media import (
    ExcerptError,
    format_seconds,
    parse_timestamp,
    parse_youtube_id,
    validate_audio_excerpt,
    validate_video_excerpt,
    youtube_watch_url,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0.0),
        ("12.5", 12.5),
        ("1:02", 62.0),
        ("1:02.5", 62.5),
        ("1:02:03", 3723.0),
        ("0:00:00.250", 0.25),
    ],
)
def test_parse_timestamp(text, expected):
    assert parse_timestamp(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text",
    ["", "-1", "one", "1:60", "1:60:00", "1:2:3:4", ":"],
)
def test_parse_timestamp_rejects_invalid_values(text):
    with pytest.raises(ValueError):
        parse_timestamp(text)


def test_format_seconds_is_stable():
    assert format_seconds(0) == "0:00.0"
    assert format_seconds(5.25) == "0:05.3"
    assert format_seconds(65.44) == "1:05.4"
    assert format_seconds(3665.4) == "1:01:05.4"
    assert format_seconds(None) == "--:--"
    assert format_seconds(59.6, tenths=False) == "1:00"


def test_audio_excerpt_zero_end_means_file_end():
    excerpt = validate_audio_excerpt(10.0, 0.0, 100.0)
    assert excerpt.start == 10.0
    assert excerpt.end == 100.0
    assert excerpt.duration == 90.0


def test_audio_excerpt_validation_against_duration():
    with pytest.raises(ExcerptError):
        validate_audio_excerpt(0.0, 101.0, 100.0)
    with pytest.raises(ExcerptError):
        validate_audio_excerpt(100.0, 0.0, 100.0)
    with pytest.raises(ExcerptError):
        validate_audio_excerpt(10.0, 5.0, 100.0)
    with pytest.raises(ExcerptError):
        validate_audio_excerpt(-1.0, 5.0)
    with pytest.raises(ExcerptError):
        validate_audio_excerpt(0.0, -1.0)
    assert validate_audio_excerpt(0.0, 9.5, 10.0).end == 9.5


def test_video_excerpt_allows_open_end_and_rejects_reversed_range():
    assert validate_video_excerpt(5.0, 0.0).end is None
    assert validate_video_excerpt(5.0, 10.0).end == 10.0
    with pytest.raises(ExcerptError):
        validate_video_excerpt(5.0, 5.0)
    with pytest.raises(ExcerptError):
        validate_video_excerpt(5.0, 4.0)


@pytest.mark.parametrize(
    "value",
    [
        "BrRDmiw0jvA",
        "https://www.youtube.com/watch?v=BrRDmiw0jvA",
        "https://youtube.com/watch?v=BrRDmiw0jvA&list=x",
        "https://m.youtube.com/watch?v=BrRDmiw0jvA",
        "https://music.youtube.com/watch?v=BrRDmiw0jvA",
        "https://youtu.be/BrRDmiw0jvA",
        "https://www.youtube.com/embed/BrRDmiw0jvA",
        "https://www.youtube.com/shorts/BrRDmiw0jvA",
        "https://www.youtube-nocookie.com/embed/BrRDmiw0jvA",
    ],
)
def test_parse_youtube_id_accepts_supported_forms(value):
    assert parse_youtube_id(value) == "BrRDmiw0jvA"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "short",
        "https://example.com/watch?v=BrRDmiw0jvA",
        "https://www.youtube.com/watch?v=too-short",
        "https://www.youtube.com/",
    ],
)
def test_parse_youtube_id_rejects_other_values(value):
    assert parse_youtube_id(value) is None


def test_browser_fallback_url_carries_start_time():
    assert youtube_watch_url("BrRDmiw0jvA") == (
        "https://www.youtube.com/watch?v=BrRDmiw0jvA"
    )
    assert youtube_watch_url("BrRDmiw0jvA", 65.7) == (
        "https://www.youtube.com/watch?v=BrRDmiw0jvA&t=65s"
    )
