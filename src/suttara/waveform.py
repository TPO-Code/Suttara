"""Asynchronous, cached multiresolution waveforms for local recordings.

Waveforms are decoded to mono PCM by ffmpeg.  Only min/max peaks are retained;
the source samples are streamed and never accumulated in memory.  Cache files
are regenerable UI data and deliberately live outside ``suttara.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

CACHE_VERSION = 1
DECODE_RATE = 8_000
BASE_WINDOW = 32  # four milliseconds at DECODE_RATE


class WaveformError(RuntimeError):
    """A recording could not be decoded or its cache was unusable."""


@dataclass(frozen=True)
class PeakLevel:
    """Min/max amplitudes for consecutive windows of ``samples_per_peak``."""

    samples_per_peak: int
    minimum: array
    maximum: array

    def __len__(self) -> int:
        return len(self.minimum)


@dataclass(frozen=True)
class WaveformData:
    path: Path
    duration: float
    sample_rate: int
    levels: tuple[PeakLevel, ...]
    source_size: int
    source_mtime_ns: int

    def level_for(self, start: float, end: float, pixel_width: int) -> PeakLevel:
        """Choose the most detailed level that draws about one peak per pixel."""

        if not self.levels:
            raise WaveformError("waveform has no peak levels")
        seconds = max(1e-9, end - start)
        wanted = seconds * self.sample_rate / max(1, pixel_width)
        chosen = self.levels[0]
        for level in self.levels:
            if level.samples_per_peak <= wanted * 1.5:
                chosen = level
            else:
                break
        return chosen


def cache_identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


class WaveformCache:
    """Disk cache keyed by canonical path and validated by size and mtime."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root) / ".suttara_cache" / "waveforms"

    def path_for(self, source: Path) -> Path:
        canonical = str(Path(source).expanduser().resolve()).encode("utf-8")
        return self.root / f"{hashlib.sha256(canonical).hexdigest()}.wfc"

    def load(self, source: Path) -> WaveformData | None:
        source = Path(source).resolve()
        try:
            size, mtime_ns = cache_identity(source)
            with zipfile.ZipFile(self.path_for(source), "r") as archive:
                payload = json.loads(archive.read("metadata.json"))
                raw_levels = []
                for index, item in enumerate(payload["levels"]):
                    minimum = array("f")
                    minimum.frombytes(archive.read(f"level-{index}-minimum.bin"))
                    maximum = array("f")
                    maximum.frombytes(archive.read(f"level-{index}-maximum.bin"))
                    raw_levels.append((item, minimum, maximum))
            if payload.get("version") != CACHE_VERSION:
                return None
            if payload.get("size") != size or payload.get("mtime_ns") != mtime_ns:
                return None
            levels = tuple(
                PeakLevel(int(item["samples_per_peak"]), minimum, maximum)
                for item, minimum, maximum in raw_levels
            )
            return WaveformData(
                source,
                float(payload["duration"]),
                int(payload["sample_rate"]),
                levels,
                size,
                mtime_ns,
            )
        except (
            OSError, KeyError, TypeError, ValueError, json.JSONDecodeError,
            zipfile.BadZipFile,
        ):
            return None

    def store(self, data: WaveformData) -> Path:
        destination = self.path_for(data.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "size": data.source_size,
            "mtime_ns": data.source_mtime_ns,
            "duration": data.duration,
            "sample_rate": data.sample_rate,
            "levels": [
                {"samples_per_peak": level.samples_per_peak}
                for level in data.levels
            ],
        }
        temporary = destination.with_suffix(destination.suffix + f".{os.getpid()}.tmp")
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5
            ) as archive:
                archive.writestr("metadata.json", json.dumps(payload))
                for index, level in enumerate(data.levels):
                    archive.writestr(
                        f"level-{index}-minimum.bin", level.minimum.tobytes()
                    )
                    archive.writestr(
                        f"level-{index}-maximum.bin", level.maximum.tobytes()
                    )
            os.replace(temporary, destination)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return destination


def decode_waveform(
    source: Path,
    *,
    sample_rate: int = DECODE_RATE,
    base_window: int = BASE_WINDOW,
    ffmpeg: str | None = None,
) -> WaveformData:
    """Stream decoded mono samples and construct a min/max peak pyramid."""

    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise WaveformError(f"Recording not found: {source}")
    executable = ffmpeg or shutil.which("ffmpeg")
    if not executable:
        raise WaveformError("ffmpeg is required to extract audio waveforms")
    command = [
        executable,
        "-v",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ]
    process = subprocess.Popen(  # noqa: S603 - fixed executable and arguments
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    minimum = array("f")
    maximum = array("f")
    pending = array("h")
    sample_count = 0
    assert process.stdout is not None
    while True:
        chunk = process.stdout.read(256 * 1024)
        if not chunk:
            break
        usable = len(chunk) - (len(chunk) % 2)
        values = array("h")
        values.frombytes(chunk[:usable])
        if os.sys.byteorder != "little":
            values.byteswap()
        pending.extend(values)
        complete = len(pending) // base_window * base_window
        for offset in range(0, complete, base_window):
            block = pending[offset : offset + base_window]
            minimum.append(min(block) / 32768.0)
            maximum.append(max(block) / 32768.0)
        if complete:
            del pending[:complete]
        sample_count += len(values)
    stderr = (process.stderr.read() if process.stderr else b"").decode(
        "utf-8", "replace"
    )
    return_code = process.wait()
    if return_code:
        raise WaveformError(stderr.strip() or f"ffmpeg exited with {return_code}")
    if pending:
        minimum.append(min(pending) / 32768.0)
        maximum.append(max(pending) / 32768.0)
    if not minimum or sample_count <= 0:
        raise WaveformError("the recording contains no decodable audio samples")

    levels: list[PeakLevel] = [PeakLevel(base_window, minimum, maximum)]
    while len(levels[-1]) > 1:
        previous = levels[-1]
        low = array("f")
        high = array("f")
        for index in range(0, len(previous), 2):
            last = min(index + 2, len(previous))
            low.append(min(previous.minimum[index:last]))
            high.append(max(previous.maximum[index:last]))
        levels.append(PeakLevel(previous.samples_per_peak * 2, low, high))
    size, mtime_ns = cache_identity(source)
    return WaveformData(
        source,
        sample_count / sample_rate,
        sample_rate,
        tuple(levels),
        size,
        mtime_ns,
    )


class _WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class _WaveformWorker(QRunnable):
    def __init__(self, source: Path, cache: WaveformCache) -> None:
        super().__init__()
        self.source = source
        self.cache = cache
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            data = self.cache.load(self.source)
            if data is None:
                data = decode_waveform(self.source)
                self.cache.store(data)
            self.signals.finished.emit(data)
        except Exception as error:  # noqa: BLE001 - cross-thread user error
            self.signals.failed.emit(str(error))


class WaveformLoader(QObject):
    """Keep QRunnable signal objects alive and deliver results on the GUI thread."""

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cache = WaveformCache(project_root)
        self._workers: set[_WaveformWorker] = set()

    def load(
        self,
        source: Path,
        success: Callable[[WaveformData], None],
        failure: Callable[[str], None],
    ) -> None:
        worker = _WaveformWorker(Path(source), self.cache)
        self._workers.add(worker)

        def done(data: WaveformData) -> None:
            self._workers.discard(worker)
            success(data)

        def failed(message: str) -> None:
            self._workers.discard(worker)
            failure(message)

        worker.signals.finished.connect(done)
        worker.signals.failed.connect(failed)
        QThreadPool.globalInstance().start(worker)
