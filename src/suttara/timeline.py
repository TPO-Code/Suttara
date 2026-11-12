"""Shared precision timeline and waveform widget."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Sequence

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QToolTip, QWidget

from .media import format_seconds
from .model import Section
from .source_resources import canonical_recording_relative
from .waveform import WaveformData

BoundaryKind = Literal["start", "end"]


@dataclass(frozen=True)
class BoundaryMarker:
    time: float
    section_id: str
    section_name: str
    kind: BoundaryKind


@dataclass(frozen=True)
class TimelineTransform:
    start: float
    end: float
    left: float
    width: float

    def time_to_x(self, value: float) -> float:
        if self.end <= self.start or self.width <= 0:
            return self.left
        return self.left + (value - self.start) / (self.end - self.start) * self.width

    def x_to_time(self, x: float) -> float:
        if self.width <= 0:
            return self.start
        fraction = (x - self.left) / self.width
        return self.start + fraction * (self.end - self.start)


def clamp_seek(value: float, excerpt_start: float, excerpt_end: float) -> float:
    """Clamp an absolute seek target to an excerpt's inclusive bounds."""

    return max(excerpt_start, min(excerpt_end, float(value)))


def excerpt_seek_from_fraction(fraction: float, start: float, end: float) -> float:
    return start + max(0.0, min(1.0, fraction)) * (end - start)


def validate_strict_range(start: float, end: float, duration: float) -> str:
    if not all(math.isfinite(value) for value in (start, end, duration)):
        return "Timestamps and duration must be finite numbers"
    if duration <= 0:
        return "Recording duration is not available yet"
    if start < 0 or end > duration:
        return "The selected range must stay within the recording duration"
    if start >= end:
        return "The start timestamp must be earlier than the end timestamp"
    return ""


def snap_timestamp(value: float, candidates: Iterable[float], threshold: float) -> float:
    """Return the exact stored candidate nearest ``value`` within ``threshold``."""

    nearby = [candidate for candidate in candidates if abs(candidate - value) <= threshold]
    return min(nearby, key=lambda candidate: (abs(candidate - value), candidate)) if nearby else value


def markers_for_recording(
    sections: Sequence[Section],
    current_section_id: str,
    language: str,
    recording_path: str,
    project_root: Path,
) -> list[BoundaryMarker]:
    """Read-only boundaries from other sections using the same recording."""

    wanted = canonical_recording_relative(project_root, language, recording_path)
    markers: list[BoundaryMarker] = []
    attribute = "spoken_pali" if language == "pali" else "spoken_english"
    for section in sections:
        if section.id == current_section_id:
            continue
        reference = getattr(section, attribute)
        if reference is None:
            continue
        identity = canonical_recording_relative(project_root, language, reference.path)
        if identity != wanted:
            continue
        markers.append(BoundaryMarker(reference.start_seconds, section.id, section.display_title, "start"))
        if reference.end_seconds > 0:
            markers.append(BoundaryMarker(reference.end_seconds, section.id, section.display_title, "end"))
    return sorted(markers, key=lambda marker: (marker.time, marker.section_name, marker.kind))


def goenka_markers(sections: Sequence[Section], current_section_id: str) -> list[BoundaryMarker]:
    markers: list[BoundaryMarker] = []
    for section in sections:
        if section.id == current_section_id or section.goenka is None:
            continue
        markers.append(BoundaryMarker(section.goenka.start_seconds, section.id, section.display_title, "start"))
        if section.goenka.end_seconds > 0:
            markers.append(BoundaryMarker(section.goenka.end_seconds, section.id, section.display_title, "end"))
    return sorted(markers, key=lambda marker: (marker.time, marker.section_name, marker.kind))


def adjacent_audio_boundary(
    sections: Sequence[Section],
    current_section_id: str,
    language: str,
    recording_path: str,
    project_root: Path,
    direction: Literal["previous", "next"],
) -> float | None:
    """Boundary from the nearest ordered neighbour using the same recording."""

    current = next(
        (index for index, section in enumerate(sections) if section.id == current_section_id),
        None,
    )
    if current is None:
        return None
    wanted = canonical_recording_relative(project_root, language, recording_path)
    attribute = "spoken_pali" if language == "pali" else "spoken_english"
    indices = (
        range(current - 1, -1, -1)
        if direction == "previous"
        else range(current + 1, len(sections))
    )
    for index in indices:
        reference = getattr(sections[index], attribute)
        if reference is None:
            continue
        identity = canonical_recording_relative(
            project_root, language, reference.path
        )
        if identity != wanted:
            continue
        if direction == "previous" and reference.end_seconds > 0:
            return reference.end_seconds
        if direction == "next":
            return reference.start_seconds
    return None


def adjacent_goenka_boundary(
    sections: Sequence[Section],
    current_section_id: str,
    direction: Literal["previous", "next"],
) -> float | None:
    current = next(
        (index for index, section in enumerate(sections) if section.id == current_section_id),
        None,
    )
    if current is None:
        return None
    indices = (
        range(current - 1, -1, -1)
        if direction == "previous"
        else range(current + 1, len(sections))
    )
    for index in indices:
        reference = sections[index].goenka
        if reference is None:
            continue
        if direction == "previous" and reference.end_seconds > 0:
            return reference.end_seconds
        if direction == "next":
            return reference.start_seconds
    return None


def grouped_marker_tooltip(markers: Sequence[BoundaryMarker]) -> str:
    return "\n".join(
        f"{format_seconds(marker.time)} — {marker.section_name} ({marker.kind})"
        for marker in markers
    )


class TimelineWidget(QWidget):
    """Timeline used in full-recording authoring and excerpt playback modes."""

    seek_requested = Signal(float)
    range_changed = Signal(float, float)
    view_changed = Signal(float, float)

    def __init__(self, parent: QWidget | None = None, *, authoring: bool = False) -> None:
        super().__init__(parent)
        self.authoring = authoring
        self.duration = 0.0
        self.visible_start = 0.0
        self.visible_end = 1.0
        self.selection_start = 0.0
        self.selection_end = 0.0
        self.playback_position = 0.0
        self.markers: list[BoundaryMarker] = []
        self.waveform: WaveformData | None = None
        self._drag: str | None = None
        self._last_x = 0.0
        # Keep paint resources alive for the widget lifetime. PySide 6.12 can
        # corrupt wrapper refcounts when thousands of temporary QColor/QPen
        # instances are created during a handle drag.
        self._background_color = QColor("#f7f4ed")
        self._selection_color = QColor(92, 135, 93, 55)
        self._wave_color = QColor("#506d72")
        self._baseline_color = QColor("#8a857b")
        self._marker_color = QColor("#a78343")
        self._cursor_color = QColor("#202020")
        self._axis_color = QColor("#68645c")
        self._start_color = QColor("#2776a3")
        self._end_color = QColor("#a34b38")
        self._white_pen = QPen(QColor("#ffffff"))
        self._axis_pen = QPen(self._axis_color)
        self._marker_pen = QPen(
            self._marker_color, 1, Qt.PenStyle.DashLine
        )
        self.setMinimumHeight(118)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Click to seek. Mouse wheel zooms; Shift+wheel or drag the background pans.")

    def set_duration(self, duration: float, *, reset_view: bool = False) -> None:
        self.duration = max(0.0, float(duration))
        if reset_view or self.visible_end <= self.visible_start:
            self.visible_start = 0.0
            self.visible_end = max(1.0, self.duration)
        else:
            self._normalise_view()
        self.update()

    def set_excerpt_view(self, start: float, end: float) -> None:
        self.selection_start = float(start)
        self.selection_end = float(end)
        self.visible_start = float(start)
        self.visible_end = max(float(end), float(start) + 0.001)
        self.update()

    def set_selection(self, start: float, end: float) -> None:
        self.selection_start = float(start)
        self.selection_end = float(end)
        self.update()

    def set_markers(self, markers: Sequence[BoundaryMarker]) -> None:
        self.markers = list(markers)
        self.update()

    def set_waveform(self, waveform: WaveformData | None) -> None:
        self.waveform = waveform
        if waveform is not None and self.duration <= 0:
            self.set_duration(waveform.duration, reset_view=True)
        self.update()

    def set_playback_position(self, position: float) -> None:
        self.playback_position = float(position)
        self.update()

    def zoom(self, factor: float, anchor: float | None = None) -> None:
        if self.duration <= 0:
            return
        span = self.visible_end - self.visible_start
        minimum = min(self.duration, 0.05)
        new_span = max(minimum, min(self.duration, span / factor))
        anchor = self.playback_position if anchor is None else anchor
        fraction = (anchor - self.visible_start) / span if span > 0 else 0.5
        self.visible_start = anchor - fraction * new_span
        self.visible_end = self.visible_start + new_span
        self._normalise_view()
        self.view_changed.emit(self.visible_start, self.visible_end)
        self.update()

    def pan(self, seconds: float) -> None:
        self.visible_start += seconds
        self.visible_end += seconds
        self._normalise_view()
        self.view_changed.emit(self.visible_start, self.visible_end)
        self.update()

    def transform(self) -> TimelineTransform:
        return TimelineTransform(self.visible_start, self.visible_end, 10.0, max(1.0, self.width() - 20.0))

    def _normalise_view(self) -> None:
        span = min(max(0.001, self.visible_end - self.visible_start), max(0.001, self.duration))
        self.visible_start = max(0.0, min(self.visible_start, max(0.0, self.duration - span)))
        self.visible_end = self.visible_start + span

    def _snap_threshold(self) -> float:
        return (self.visible_end - self.visible_start) * 8.0 / max(1, self.width() - 20)

    def _marker_hits(self, x: float, pixels: float = 5.0) -> list[BoundaryMarker]:
        transform = self.transform()
        return [marker for marker in self.markers if abs(transform.time_to_x(marker.time) - x) <= pixels]

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.fillRect(self.rect(), self._background_color)
            transform = self.transform()
            top, bottom = 20.0, self.height() - 25.0
            center = (top + bottom) / 2
            if self.authoring and self.selection_end > self.selection_start:
                x1 = transform.time_to_x(self.selection_start)
                x2 = transform.time_to_x(self.selection_end)
                painter.fillRect(
                    QRectF(x1, top, x2 - x1, bottom - top),
                    self._selection_color,
                )
            self._paint_waveform(painter, transform, top, bottom, center)
            self._paint_axis(painter, transform, bottom)
            painter.setPen(self._marker_pen)
            for marker in self.markers:
                if self.visible_start <= marker.time <= self.visible_end:
                    x = transform.time_to_x(marker.time)
                    painter.fillRect(
                        QRect(
                            int(round(x)),
                            int(round(top)),
                            1,
                            max(1, int(round(bottom - top))),
                        ),
                        self._marker_color,
                    )
            if self.authoring:
                self._paint_handle(
                    painter,
                    transform.time_to_x(self.selection_start),
                    top,
                    bottom,
                    self._start_color,
                    "S",
                )
                self._paint_handle(
                    painter,
                    transform.time_to_x(self.selection_end),
                    top,
                    bottom,
                    self._end_color,
                    "E",
                )
            if self.visible_start <= self.playback_position <= self.visible_end:
                x = transform.time_to_x(self.playback_position)
                painter.fillRect(
                    QRect(
                        int(round(x)) - 1,
                        int(round(top - 3)),
                        2,
                        max(1, int(round(bottom - top + 5))),
                    ),
                    self._cursor_color,
                )
        finally:
            # Let QPainter's wrapper end the native painter exactly once. Calling
            # end() explicitly here triggers a PySide 6.12 refcount bug during
            # rapid handle repaints.
            pass

    def _paint_waveform(self, painter: QPainter, transform: TimelineTransform, top: float, bottom: float, center: float) -> None:
        if self.waveform is None:
            painter.fillRect(
                QRect(10, int(round(center)), max(1, self.width() - 20), 1),
                self._baseline_color,
            )
            return
        level = self.waveform.level_for(self.visible_start, self.visible_end, self.width())
        first = max(0, int(self.visible_start * self.waveform.sample_rate / level.samples_per_peak))
        last = min(len(level), int(math.ceil(self.visible_end * self.waveform.sample_rate / level.samples_per_peak)))
        scale = (bottom - top) * 0.48
        for index in range(first, last):
            time = index * level.samples_per_peak / self.waveform.sample_rate
            x = transform.time_to_x(time)
            y1 = int(round(center - level.maximum[index] * scale))
            y2 = int(round(center - level.minimum[index] * scale))
            painter.fillRect(
                QRect(int(round(x)), min(y1, y2), 1, max(1, abs(y2 - y1))),
                self._wave_color,
            )

    def _paint_axis(self, painter: QPainter, transform: TimelineTransform, bottom: float) -> None:
        span = self.visible_end - self.visible_start
        raw = span / max(2, self.width() // 100)
        powers = 10 ** math.floor(math.log10(max(raw, 1e-6)))
        interval = next(value * powers for value in (1, 2, 5, 10) if value * powers >= raw)
        tick = math.ceil(self.visible_start / interval) * interval
        painter.setPen(self._axis_pen)
        while tick <= self.visible_end + 1e-9:
            x = transform.time_to_x(tick)
            painter.fillRect(
                QRect(int(round(x)), int(round(bottom)), 1, 4),
                self._axis_color,
            )
            painter.drawText(QRectF(x - 40, bottom + 4, 80, 17), Qt.AlignmentFlag.AlignHCenter, format_seconds(tick))
            tick += interval

    def _paint_handle(
        self,
        painter: QPainter,
        x: float,
        top: float,
        bottom: float,
        color: QColor,
        label: str,
    ) -> None:
        center_x = int(round(x))
        painter.fillRect(
            QRect(center_x - 1, int(round(top)), 3, max(1, int(round(bottom - top)))),
            color,
        )
        handle = QRect(center_x - 7, int(round(top)) - 15, 14, 15)
        painter.fillRect(handle, color)
        painter.setPen(self._white_pen)
        painter.drawText(handle, Qt.AlignmentFlag.AlignCenter, label)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        transform = self.transform()
        if self.authoring and abs(x - transform.time_to_x(self.selection_start)) <= 9:
            self._drag = "start"
        elif self.authoring and abs(x - transform.time_to_x(self.selection_end)) <= 9:
            self._drag = "end"
        elif event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._drag = "pan"
            self._last_x = x
        else:
            target = clamp_seek(transform.x_to_time(x), self.visible_start, self.visible_end)
            self.seek_requested.emit(target)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        if self._drag == "pan":
            span = self.visible_end - self.visible_start
            self.pan((self._last_x - x) / max(1, self.width() - 20) * span)
            self._last_x = x
            return
        if self._drag in {"start", "end"}:
            value = clamp_seek(self.transform().x_to_time(x), 0.0, self.duration)
            value = snap_timestamp(value, (marker.time for marker in self.markers), self._snap_threshold())
            epsilon = min(0.001, max(1e-6, self.duration / 1_000_000))
            if self._drag == "start":
                self.selection_start = min(value, self.selection_end - epsilon)
            else:
                self.selection_end = max(value, self.selection_start + epsilon)
            self.range_changed.emit(self.selection_start, self.selection_end)
            self.update()
            return
        hits = self._marker_hits(x)
        if hits:
            QToolTip.showText(event.globalPosition().toPoint(), grouped_marker_tooltip(hits), self)
        else:
            QToolTip.hideText()

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._drag = None

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.duration <= 0:
            return
        steps = event.angleDelta().y() / 120.0
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.pan(-steps * (self.visible_end - self.visible_start) * 0.1)
        else:
            self.zoom(1.25**steps, self.transform().x_to_time(event.position().x()))
        event.accept()
