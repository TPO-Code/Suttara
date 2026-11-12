"""Timestamp, excerpt and YouTube-reference helpers."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


class ExcerptError(ValueError):
    """A configured excerpt cannot be played."""


YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class Excerpt:
    """A resolved excerpt: ``end`` is None when the recording end is unknown."""

    start: float
    end: float | None

    @property
    def duration(self) -> float | None:
        if self.end is None:
            return None
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class BoundaryDecision:
    """Deterministic result when playback reaches an excerpt boundary."""

    reached: bool
    target: float
    continue_playing: bool


def playback_boundary_decision(
    position: float, start: float, end: float, loop: bool
) -> BoundaryDecision:
    """Describe stop/loop behaviour without rounding to backend milliseconds."""

    if position < end:
        return BoundaryDecision(False, position, True)
    return BoundaryDecision(True, start if loop else end, loop)


def parse_timestamp(value: str) -> float:
    """Parse ``ss``, ``mm:ss`` or ``h:mm:ss`` into seconds.

    Fractional seconds are accepted.  Negative values and non-numeric parts are
    rejected.  This is mainly used by validation and tests; the UI presents
    numeric second fields with three decimal places.
    """

    text = value.strip()
    if not text:
        raise ValueError("a timestamp cannot be empty")
    if text.startswith("-"):
        raise ValueError("a timestamp cannot be negative")
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"not a timestamp: {value!r}")
    numbers: list[float] = []
    for part in parts:
        part = part.strip()
        if not part:
            raise ValueError(f"not a timestamp: {value!r}")
        try:
            number = float(part)
        except ValueError as error:
            raise ValueError(f"not a timestamp: {value!r}") from error
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"not a timestamp: {value!r}")
        numbers.append(number)
    if len(numbers) == 1:
        return numbers[0]
    if len(numbers) == 2:
        minutes, seconds = numbers
        if seconds >= 60:
            raise ValueError("seconds in mm:ss must be below 60")
        return minutes * 60 + seconds
    hours, minutes, seconds = numbers
    if minutes >= 60 or seconds >= 60:
        raise ValueError("minutes and seconds in h:mm:ss must be below 60")
    return hours * 3600 + minutes * 60 + seconds


def format_seconds(seconds: float | None, *, tenths: bool = True) -> str:
    """Format seconds as ``m:ss`` or ``h:mm:ss`` with a stable display."""

    if seconds is None or not math.isfinite(seconds):
        return "--:--"
    seconds = max(0.0, float(seconds))
    if tenths:
        total_tenths = math.floor(seconds * 10 + 0.5)
    else:
        total_tenths = math.floor(seconds + 0.5)
    if tenths:
        hours, remainder = divmod(total_tenths, 36000)
        minutes, remainder = divmod(remainder, 600)
        secs, tenth = divmod(remainder, 10)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}.{tenth}"
        return f"{minutes}:{secs:02d}.{tenth}"
    hours, remainder = divmod(total_tenths, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def validate_audio_excerpt(
    start_seconds: float,
    end_seconds: float,
    duration_seconds: float | None = None,
) -> Excerpt:
    """Validate a local-audio excerpt.

    ``end_seconds == 0`` means "to the end of the file".  When a duration is
    known, boundaries beyond it are rejected rather than silently clamped.
    """

    if not math.isfinite(start_seconds) or start_seconds < 0:
        raise ExcerptError("Start time must be zero or a positive number of seconds")
    if not math.isfinite(end_seconds) or end_seconds < 0:
        raise ExcerptError("End time must be zero or a positive number of seconds")
    if end_seconds != 0 and end_seconds <= start_seconds:
        raise ExcerptError("End time must be later than start time (or 0 for the end)")
    if duration_seconds is not None:
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            duration_seconds = None
        elif start_seconds >= duration_seconds:
            raise ExcerptError(
                f"Start time {format_seconds(start_seconds)} is at or beyond the "
                f"recording duration {format_seconds(duration_seconds)}"
            )
        elif end_seconds > duration_seconds:
            raise ExcerptError(
                f"End time {format_seconds(end_seconds)} is beyond the recording "
                f"duration {format_seconds(duration_seconds)}"
            )
    if end_seconds == 0:
        return Excerpt(start_seconds, duration_seconds)
    return Excerpt(start_seconds, end_seconds)


def validate_video_excerpt(start_seconds: float, end_seconds: float) -> Excerpt:
    """Validate a YouTube excerpt without assuming a known duration."""

    if not math.isfinite(start_seconds) or start_seconds < 0:
        raise ExcerptError("Start time must be zero or a positive number of seconds")
    if not math.isfinite(end_seconds) or end_seconds < 0:
        raise ExcerptError("End time must be zero or a positive number of seconds")
    if end_seconds != 0 and end_seconds <= start_seconds:
        raise ExcerptError("End time must be later than start time (or 0 for no end)")
    return Excerpt(start_seconds, end_seconds or None)


def parse_youtube_id(value: str) -> str | None:
    """Return a video id from a YouTube URL, embed URL or bare id."""

    text = value.strip()
    if not text:
        return None
    if YOUTUBE_ID_RE.fullmatch(text):
        return text
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") or host.startswith("music."):
        host = host.split(".", 1)[1]
    if host not in {
        "youtube.com",
        "youtube-nocookie.com",
        "youtu.be",
    }:
        return None
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/", 1)[0]
        return candidate if YOUTUBE_ID_RE.fullmatch(candidate) else None
    query_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_id and YOUTUBE_ID_RE.fullmatch(query_id):
        return query_id
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[0] in {"embed", "shorts", "live", "v"}:
        candidate = path_parts[1]
        return candidate if YOUTUBE_ID_RE.fullmatch(candidate) else None
    return None


def youtube_watch_url(video_id: str, start_seconds: float = 0.0) -> str:
    """A browser fallback URL that starts near the configured excerpt."""

    base = f"https://www.youtube.com/watch?v={video_id}"
    if start_seconds and start_seconds > 0:
        base += f"&t={max(0, int(start_seconds))}s"
    return base
