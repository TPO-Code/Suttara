# Suttara — Agent Instructions

## Project purpose

Build **Suttara**, a local-first **PySide6 desktop application** for studying the
**Mahāsatipaṭṭhāna Sutta** through curated passages of Pāli and English text,
spoken readings in both languages, S. N. Goenka's Pāli chanting, and personal
notes.

The application has two clearly separated modes:

1. **Study view** — the normal reader/listener view, with passages, playback,
   and editable notes.
2. **Section Editor** — a modal configuration dialog for section titles,
   passage ranges, audio recordings, and Goenka excerpt timestamps.

Configuration controls must not appear in the normal study view.

## Existing project materials

The repository contains `sources.txt`, `suttara_sources/sources.json`, downloaded
study texts, and extracted audio. Inspect the actual files before changing
resource handling. Do not redownload, replace, or redistribute downloaded works.

The implemented fixed resources are:

- Pāli text: `suttara_sources/texts/mahasatipatthana_pali.txt`
- English text: `suttara_sources/texts/mahasatipatthana_english.txt`
- Spoken Pāli directory: `suttara_sources/audio/spoken_pali/`
- Spoken English directory: `suttara_sources/audio/spoken_english/`
- Goenka chanting URL, application constant:
  `https://www.youtube.com/watch?v=BrRDmiw0jvA`

The source files are local study resources. They are not embedded in the
application or copied into `suttara.json`.

## Required application structure

Use Python 3.11 and PySide6. Keep persistence, source extraction, resource
discovery, playback and UI separated where helpful. Do not add plugin systems,
databases, remote services, alternate-reader selectors, or speculative
extensibility.

### Main study window

- Left sidebar: the ordered list of user-created sections.
- Main area: a reading/listening view for the selected section.
- Side-by-side read-only Pāli and English passage viewers.
- Spoken Pāli and spoken English playback, including play/pause, stop, seek,
  visible position, and loop-excerpt controls.
- Embedded Goenka chanting playback with the same transport controls and browser
  fallback.
- Editable study notes.
- An obvious **Edit Section…** action in the study header and sidebar.
- An empty-state view when no section exists or none is selected.

The study view must not show text file paths, file browsers, coordinate inputs,
YouTube URL fields, audio file selectors, or other resource-configuration
controls. Read-only timeframe labels and playback transport controls are
appropriate.

### Section management

Each section has a title and may independently contain:

1. A Pāli passage range.
2. An English passage range.
3. One spoken-Pāli recording excerpt.
4. One spoken-English recording excerpt.
5. One Goenka chanting excerpt.
6. User notes.

All fields are optional. Do not support multiple alternatives of the same
resource type in one section.

The sidebar must support creating, inserting before/after, renaming, reordering,
selecting, and deleting sections, with confirmation for deletion. Keep permanent
sidebar controls compact; use menu and context-menu actions for less frequent
operations.

Creating a section opens the modal Section Editor immediately. Editing an
existing section opens the same dialog populated with current settings.

### Modal Section Editor

The Section Editor is a dialog with **Save** and **Cancel**. It must edit a
copy: **Cancel must leave the existing section completely unchanged.** Creating a
section and then cancelling must not add it.

The dialog contains:

- Section title.
- Combined side-by-side full Pāli and English source panels.
- Independent start/end line and column coordinates for both languages.
- Audio recording dropdowns for each language, populated from the fixed audio
  directories.
- Independent audio start/end timestamps and loop preferences.
- Goenka excerpt start/end timestamps and loop preference; the URL is fixed and
  displayed only as information, not as an editable field.

The dialog must not open a file-selection dialog. It should remain usable at
ordinary desktop resolutions, using resizable splitters and/or scrolling rather
than requiring an excessively large window. Its geometry and splitter state may
be remembered separately in `QSettings`.

### Text range selection

Both passage panels use the known, fixed project-relative source files. Do not
ask the user to browse for them.

The full source text is shown, with line numbers and wrapped long lines. The
user can:

- Select text with the mouse and apply it with **Use selected text**.
- Select the full source.
- Clear the passage.
- Edit start/end line and column values directly.

Configured ranges must be visibly highlighted. Coordinates are **1-based**,
start inclusive and end exclusive; columns count Unicode characters (code
points), not UTF-8 bytes. Preserve Pāli diacritics. Validate ranges against the
loaded source and clearly report invalid or out-of-bounds ranges.

### Audio configuration and playback

The Section Editor discovers recordings by extension from the two fixed audio
directories. The dropdown contains **No recording assigned** plus the discovered
files, sorted naturally. Do not introduce file browsers or multiple recording
alternatives per language.

Playback widgets use Qt Multimedia (`QMediaPlayer`/`QAudioOutput`), validate
excerpt boundaries against the media duration when known, seek only within the
excerpt, and stop or loop at the configured end. Sections with no assigned
recording show a clear unassigned state.

#### Implemented waveform and timeline interaction

- Local recordings are decoded off the GUI thread with `ffmpeg` into cached,
  multiresolution min/max peaks under `.suttara_cache/waveforms/`. Cache entries
  are regenerable, invalidated by source size or modification time, and never
  stored in `suttara.json`.
- The shared timeline keeps exact absolute timestamps separate from rendering.
  Mouse-wheel zoom reveals finer peak levels; Shift+wheel or right/middle drag
  pans the visible range.
- Section Editor timelines show the complete recording, playback cursor,
  draggable S/E handles, shaded excerpt, and read-only boundaries from other
  sections using the same recording. Hover lists all coincident boundaries;
  snapping assigns the exact stored timestamp.
- Timestamp fields and handles update each other. Cursor actions set either
  boundary, and neighbour actions reuse a preceding end or following start.
- Local-audio authoring has distinct **Play full recording** and **Play selected
  excerpt** transports. Full playback starts at the current cursor without
  enforcing S/E; excerpt playback stays strictly within S/E. Study playback
  remains excerpt-only.
- Study-view timelines show only the selected excerpt, remain seekable, and
  contain no authoring handles or other-section markers.
- Goenka uses the same component without waveform peaks. It relies only on the
  duration and position reported by the official IFrame player; no video audio
  is downloaded, decoded, or captured.
- Goenka authoring provides separate **Play full recording** and **Play selected
  excerpt** actions. Duration polling must preserve any user-selected zoom and
  pan rather than resetting the full-video view.

### Goenka chanting

The video URL is an application-level constant. Section data stores only the
excerpt start/end timestamps and loop preference. Use the official YouTube
IFrame API in `QWebEngineView`. Enforce the excerpt end and looping as reliably
as the API permits. Provide an explicit **Open in browser** fallback. Do not
download, scrape, or bypass access restrictions.

### Notes

Notes are editable freeform user data in the study view and must be saved with
the project.

## Persistence and compatibility

- Save the complete project to fixed-name `suttara.json` in the repository root.
- Use human-readable UTF-8 JSON with a schema/version field.
- Store section order, titles, text coordinates, audio references/timestamps,
  Goenka timestamps/playback preferences, and notes.
- Do not serialize the fixed Goenka URL as section data.
- Do not copy source passage text, audio, or video into the project file.
- Prefer repository-relative paths for files under the repository.
- Preserve existing study sections, notes, coordinates, audio references and
  timestamps.
- Preserve unrecognised project and section fields where possible.
- Safely migrate equivalent old resource paths to the fixed conventions. Do not
  silently discard user data.
- Use atomic saves (temporary file then replace) and prompt for unsaved changes
  on close/reload/destructive actions.
- Keep window geometry, splitter positions and other UI preferences in
  `QSettings`, separate from study content.

## Source provenance and distribution

Keep attribution accessible via `sources.txt`, `suttara_sources/sources.json`,
and the Help → Sources & attribution dialog. Do not assume downloads grant
redistribution rights. The distributable code must work without shipping
third-party text, audio, or video; document how users acquire/configure their
own resources.

Do not download anything at application startup.

## User experience

- Study mode should feel like a reader/listener, not a configuration form.
- Configuration belongs in the Section Editor only.
- Use clear labels, sensible spacing, readable Pāli typography, resizable panes,
  keyboard navigation, and visible error states.
- Errors should identify the failing resource, range, timestamp, or player
  without crashing.
- Avoid modal-dialog overload in normal study use.

## Engineering and verification

- Inspect existing repository conventions and integrate rather than replacing
  unrelated files.
- Maintain project setup instructions for Qt Multimedia and Qt WebEngine.
- Add focused tests for section ordering, JSON compatibility/migration, fixed
  resource discovery, Unicode extraction, invalid ranges, timestamps, Section
  Editor save/cancel semantics, study-view absence of config controls, and
  save/restore.
- Exercise the GUI and media controls where the environment supports them.
- Distinguish implemented behaviour from what was actually verified. Do not
  claim audible playback, looping, embedded YouTube, or downloads were tested
  unless they were.

Run the suite with:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
```

## Scope control

Keep the first version deliberately limited to one text source per language, one
local recording per language, and one Goenka excerpt per section. Expansion to
multiple readers, translations, resource alternatives, synchronisation, cloud
services, databases, or publishing is out of scope.
