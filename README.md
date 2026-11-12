# Suttara

Suttara is a local-first PySide6 desktop study reader for the
**Mahāsatipaṭṭhāna Sutta (DN 22)**. Normal use is a clean reading and listening
view; resource configuration is confined to a modal **Section Editor**.

The application references local resources and never copies passages or
recordings into `suttara.json` or downloads anything at startup.

## Launch

```bash
.venv/bin/python -m suttara
```

or:

```bash
uv run suttara
```

To recreate the development environment:

```bash
uv sync --extra dev
```

The project currently constrains PySide6 to `>=6.8,<6.12`. PySide6 6.12.0 on
CPython 3.11 reproducibly corrupts interpreter reference counts during rapid
custom `QPainter` repaints; this appears when dragging waveform/timeline handles.
`uv sync` or `uv run` installs the compatible version recorded in `uv.lock`.

## Study view

The main window provides:

- A sidebar of ordered study sections.
- Side-by-side read-only Pāli and English passages.
- Spoken Pāli and spoken English players with play/pause, stop, seeking, visible
  position, excerpt-only real waveforms and loop-excerpt controls.
- Embedded S. N. Goenka chanting with the same transport controls and an
  **Open in browser** fallback.
- Editable study notes.
- A prominent **Edit Section…** button and a compact sidebar action.

The study view deliberately contains no text paths, file browsers, coordinate
inputs, audio file selectors or YouTube URL field. Those belong to the Section
Editor.

## Section management

Use **New section** or **Section → New section**. Creating a section opens the
Section Editor immediately. Double-click an existing section, press
**Ctrl+E**, use **Edit Section…**, or choose **Section → Edit section…** to edit
one. The sidebar context menu and Section menu provide insert before/after,
rename, reorder and delete actions.

The Section Editor works on a copy. **Cancel** leaves the original section
unchanged; cancelling a new section does not add it. Unsaved project changes are
prompted for on close or reload.

## Section Editor

### Pāli and English passages

The dialog shows the complete fixed Pāli and English texts side by side, with
line numbers and wrapped long lines:

- `suttara_sources/texts/mahasatipatthana_pali.txt`
- `suttara_sources/texts/mahasatipatthana_english.txt`

No file-selection dialog is used. Select text with the mouse and choose
**Use selected text**, select the full text, or edit start/end line and column
values directly. The configured range is highlighted.

Coordinates are 1-based, start-inclusive and end-exclusive; columns count
Unicode characters, not UTF-8 bytes. Invalid or out-of-bounds ranges are
reported and must be corrected before saving.

### Spoken recordings

The two recording dropdowns are populated from:

- `suttara_sources/audio/spoken_pali/`
- `suttara_sources/audio/spoken_english/`

Each language has one selected recording or **No recording assigned**, plus
independent start/end timestamps and a loop preference. The full recording is
shown as a zoomable, pannable waveform with synchronized draggable handles,
playback cursor, precise fields, and other-section boundaries for that file.
Unknown references are preserved rather than silently discarded.

Local-audio authoring has two separate transports: **Play full recording**
continues freely from the current cursor so material before or after the handles
can be assessed, while **Play selected excerpt** stops or loops strictly between
the S and E handles. Study-mode playback remains excerpt-only.

Waveforms contain real decoded amplitude peaks. Extraction runs outside the GUI
thread with `ffmpeg`; multiresolution caches live separately under
`.suttara_cache/waveforms/`, are invalidated when a recording changes, and can
be deleted safely.

### Goenka chanting

The chanting video is fixed:

`https://www.youtube.com/watch?v=BrRDmiw0jvA`

The Section Editor displays it as information and configures the excerpt on an
interactive full-video timeline. The URL is an application constant and is
not written into project files. The study view embeds the official YouTube
IFrame player and provides a browser fallback; the application does not download
or scrape the video.

Use **Play full recording** while locating boundaries and **Play selected
excerpt** to audition exactly from the S handle to the E handle. Repeated video
duration updates preserve the user's current timeline zoom and pan.

## Persistence and compatibility

The project is saved atomically as UTF-8 JSON to `suttara.json` in the repository
root. It stores section order, titles, passage coordinates, recording paths and
timestamps, Goenka timestamps/loop preferences, notes and unrecognised fields.

Existing projects are migrated safely where the new fixed-resource model makes
old references redundant:

- Old absolute/equivalent text paths are canonicalised when their filename
  matches the fixed text.
- Existing recordings are canonicalised when found in the fixed audio
  directories; unknown files are preserved.
- A saved Goenka URL is replaced by the application constant, while its
  timestamps and loop preference are retained.

Text coordinates, notes and timestamps are never rewritten by migration.
Malformed project files are left untouched; saving over one requires explicit
confirmation and creates a timestamped backup.

Window geometry, all splitter positions, the selected media tab and Section
Editor geometry are kept separately in Qt `QSettings`.

## Resource provenance

`sources.txt` and `suttara_sources/sources.json` are the provenance records.
**Help → Sources & attribution** displays credits, source metadata and a local
resource inventory.

The application does not bundle or redistribute third-party texts or
recordings. Users are responsible for acquiring resources lawfully and observing
their licences.

## Tests

```bash
uv sync --extra dev
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
```

The suite covers:

- Section ordering and model compatibility.
- Atomic persistence, malformed files and resource migration.
- Fixed text resources and Unicode range extraction.
- Audio discovery, timestamps and excerpt validation.
- Timeline mapping, excerpt-relative seeking, exact snapping and recording-marker
  identity.
- Real waveform generation, multiresolution cache reuse and invalidation.
- Combined Section Editor source selection, audio dropdowns and Goenka config.
- Save/Cancel semantics and absence of configuration controls in study view.
- Study-view layout persistence and save/restart restoration.

## Limitations

- The sandbox may have no audio output device, so automated or offscreen runs
  verify playback state, seeking and boundaries rather than audible sound.
- YouTube embedding depends on network access, regional availability and the
  publisher's embed permissions; **Open in browser** is the fallback.
- Qt WebEngine requires a suitable display/compositor on desktop platforms.
- Waveform extraction requires `ffmpeg` on `PATH`; playback remains available
  with a clear error if extraction is unavailable.
- Qt Multimedia seeks at millisecond resolution, but final positioning depends
  on the platform backend and codec/keyframe precision.
- Waveforms display amplitude only; they do not recognize or transcribe words.
