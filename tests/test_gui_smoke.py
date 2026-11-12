"""Offscreen GUI tests for the study reader and modal Section Editor."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from suttara.persistence import load_project
from suttara.ui.main_window import MainWindow
from suttara.ui.section_editor import SectionEditorDialog
from suttara.ui.study_view import StudyView

PALI_TEXT = "Dīgha Nikāya 22\n\nMahāsatipaṭṭhānasutta\n\nEvaṁ me sutaṁ.\n"
ENGLISH_TEXT = (
    "Long Discourses 22\n"
    "\n"
    "The Longer Discourse on Mindfulness Meditation\n"
    "\n"
    "So I have heard.\n"
)


def make_window(qapp, tmp_path: Path) -> MainWindow:
    root = tmp_path / "repository"
    text_dir = root / "suttara_sources" / "texts"
    pali_audio = root / "suttara_sources" / "audio" / "spoken_pali"
    english_audio = root / "suttara_sources" / "audio" / "spoken_english"
    text_dir.mkdir(parents=True)
    pali_audio.mkdir(parents=True)
    english_audio.mkdir(parents=True)
    (root / "sources.txt").write_text("Test credits\n", encoding="utf-8")
    (text_dir / "mahasatipatthana_pali.txt").write_text(PALI_TEXT, encoding="utf-8")
    (text_dir / "mahasatipatthana_english.txt").write_text(
        ENGLISH_TEXT, encoding="utf-8"
    )
    (pali_audio / "01-Uddeso.mp3").write_bytes(b"")
    (pali_audio / "02-Anapanapabbam.mp3").write_bytes(b"")
    (english_audio / "01-Mindfulness.mp3").write_bytes(b"")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    return MainWindow(
        project_root=root,
        show_dialogs=False,
        settings=settings,
    )


def accept_editor(monkeypatch, configure) -> None:
    """Make the next modal editor auto-accept after configuring itself."""

    def fake_exec(dialog: SectionEditorDialog) -> int:
        configure(dialog)
        dialog.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SectionEditorDialog, "exec", fake_exec)


def reject_editor(monkeypatch) -> None:
    monkeypatch.setattr(
        SectionEditorDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Rejected,
    )


def create_title(monkeypatch, title: str) -> None:
    accept_editor(
        monkeypatch,
        lambda dialog: dialog.title_edit.setText(title),
    )


def test_new_section_opens_editor_and_study_view_is_clean(qapp, tmp_path, monkeypatch):
    window = make_window(qapp, tmp_path)
    window.show()
    qapp.processEvents()
    assert window.project.sections == []
    assert window.content_stack.currentWidget() is window.empty_page

    seen: dict[str, object] = {}

    def configure(dialog: SectionEditorDialog) -> None:
        seen["sources"] = (
            dialog.pali_text._source is not None,
            dialog.english_text._source is not None,
        )
        seen["audio_items"] = dialog.pali_audio.recording_combo.count()
        seen["url"] = dialog.goenka.url_label.text()
        dialog.title_edit.setText("First study")
        dialog.pali_text.start_line.setValue(3)
        dialog.pali_text.start_column.setValue(1)
        dialog.pali_text.end_line.setValue(3)
        dialog.pali_text.end_column.setValue(22)

    accept_editor(monkeypatch, configure)
    window._new_section()
    assert seen["sources"] == (True, True)
    assert seen["audio_items"] == 3
    assert seen["url"] == "https://www.youtube.com/watch?v=BrRDmiw0jvA"
    assert len(window.project.sections) == 1
    section = window.current_section()
    assert section is not None
    assert section.title == "First study"
    assert section.pali_text is not None
    assert section.pali_text.start_line == 3
    assert isinstance(window.study_view, StudyView)
    assert window.study_view.title_label.text() == "First study"
    assert window.study_view.pali.text_edit.toPlainText().startswith("Mahā")

    # The ordinary study view has no resource configuration controls.
    assert not hasattr(window.study_view, "pali_text")
    assert not hasattr(window.study_view.spoken_pali, "path_edit")
    assert not hasattr(window.study_view.spoken_pali, "start_spin")
    assert not hasattr(window.study_view.goenka, "url_edit")
    assert not hasattr(window.study_view.goenka, "start_spin")
    assert window.study_view.notes_edit is not None
    assert window.study_view.edit_button.text() == "Edit Section…"

    window.study_view.notes_edit.setPlainText("My notes")
    assert window._dirty is True
    assert window.save() is True
    window.close()

    restored = MainWindow(
        project_root=window.project_root,
        show_dialogs=False,
        settings=window.settings,
    )
    restored.show()
    qapp.processEvents()
    restored_section = restored.current_section()
    assert restored_section is not None
    assert restored_section.title == "First study"
    assert restored_section.notes == "My notes"
    assert restored.study_view.pali.text_edit.toPlainText().startswith("Mahā")
    restored._dirty = False
    restored.close()


def test_cancelling_new_section_does_not_add_it(qapp, tmp_path, monkeypatch):
    window = make_window(qapp, tmp_path)
    window.show()
    qapp.processEvents()
    reject_editor(monkeypatch)
    window._new_section()
    assert window.project.sections == []
    assert window._dirty is False
    window.close()


def test_editing_section_saves_or_leaves_it_unchanged(qapp, tmp_path, monkeypatch):
    window = make_window(qapp, tmp_path)
    window.show()
    qapp.processEvents()
    create_title(monkeypatch, "Original")
    window._new_section()
    section = window.current_section()
    assert section is not None
    assert section.title == "Original"
    window.study_view.notes_edit.setPlainText("Notes survive editing")

    reject_editor(monkeypatch)
    window._edit_section()
    section = window.current_section()
    assert section is not None
    assert section.title == "Original"
    assert section.notes == "Notes survive editing"

    def edit(dialog: SectionEditorDialog) -> None:
        dialog.title_edit.setText("Edited")
        dialog.english_text.start_line.setValue(3)
        dialog.english_text.start_column.setValue(1)
        dialog.english_text.end_line.setValue(3)
        dialog.english_text.end_column.setValue(47)

    accept_editor(monkeypatch, edit)
    window._edit_section()
    section = window.current_section()
    assert section is not None
    assert section.title == "Edited"
    assert section.notes == "Notes survive editing"
    assert section.english_text is not None
    assert section.english_text.start_line == 3
    assert "The Longer Discourse" in window.study_view.english.text_edit.toPlainText()
    window._dirty = False
    window.close()


def test_insert_before_and_after_and_context_actions(qapp, tmp_path, monkeypatch):
    window = make_window(qapp, tmp_path)
    window.show()
    qapp.processEvents()
    for title in ("One", "Two", "Three"):
        create_title(monkeypatch, title)
        window._new_section()
    window.section_list.setCurrentRow(1)
    create_title(monkeypatch, "Before Two")
    window._insert_section(-1)
    assert [s.title for s in window.project.sections] == [
        "One",
        "Before Two",
        "Two",
        "Three",
    ]
    create_title(monkeypatch, "After Before Two")
    window._insert_section(1)
    assert [s.title for s in window.project.sections] == [
        "One",
        "Before Two",
        "After Before Two",
        "Two",
        "Three",
    ]
    window._move_section(-1)
    assert window.project.sections[1].title == "After Before Two"
    assert window.project.sections[2].title == "Before Two"
    window._dirty = False
    window.close()


def test_editor_save_restores_audio_and_goenka_timestamps(qapp, tmp_path, monkeypatch):
    window = make_window(qapp, tmp_path)
    window.show()
    qapp.processEvents()

    def configure(dialog: SectionEditorDialog) -> None:
        dialog.title_edit.setText("Media section")
        dialog.pali_audio.recording_combo.setCurrentIndex(1)
        dialog.pali_audio.start_spin.setValue(5)
        dialog.pali_audio.end_spin.setValue(15)
        dialog.pali_audio.loop_check.setChecked(True)
        dialog.english_audio.recording_combo.setCurrentIndex(1)
        dialog.english_audio.start_spin.setValue(2)
        dialog.english_audio.end_spin.setValue(12)
        dialog.goenka.enabled_check.setChecked(True)
        dialog.goenka.start_spin.setValue(30)
        dialog.goenka.end_spin.setValue(60)
        dialog.goenka.loop_check.setChecked(True)

    accept_editor(monkeypatch, configure)
    window._new_section()
    section = window.current_section()
    assert section is not None
    assert section.spoken_pali is not None
    assert section.spoken_pali.start_seconds == 5
    assert section.spoken_pali.end_seconds == 15
    assert section.spoken_pali.loop is True
    assert section.spoken_english is not None
    assert section.goenka is not None
    assert section.goenka.start_seconds == 30
    assert section.goenka.end_seconds == 60
    assert window.save() is True
    window.close()

    restored = MainWindow(
        project_root=window.project_root,
        show_dialogs=False,
        settings=window.settings,
    )
    restored.show()
    qapp.processEvents()
    restored_section = restored.current_section()
    assert restored_section is not None
    assert restored_section.spoken_pali is not None
    assert restored_section.spoken_pali.start_seconds == 5
    assert restored_section.spoken_pali.end_seconds == 15
    assert restored_section.spoken_english is not None
    assert restored_section.goenka is not None
    assert restored_section.goenka.start_seconds == 30
    assert restored_section.goenka.end_seconds == 60
    assert restored_section.goenka.loop is True
    assert restored.study_view.goenka.value() is not None
    restored._dirty = False
    restored.close()


def test_study_geometry_persists(qapp, tmp_path, monkeypatch):
    window = make_window(qapp, tmp_path)
    window.resize(1180, 840)
    window.show()
    qapp.processEvents()
    create_title(monkeypatch, "Geometry")
    window._new_section()
    assert window.save() is True

    view = window.study_view
    view.body_splitter.setSizes([360, 240, 110])
    view.text_splitter.setSizes([520, 420])
    view.set_media_tab_index(2)
    window.splitter.setSizes([340, 840])
    qapp.processEvents()
    main_state = window.splitter.saveState()
    body_state = view.body_splitter.saveState()
    text_state = view.text_splitter.saveState()
    window._save_window_state()
    window._dirty = False
    window.close()
    qapp.processEvents()

    restored = MainWindow(
        project_root=window.project_root,
        show_dialogs=False,
        settings=window.settings,
    )
    restored.resize(1180, 840)
    restored.show()
    qapp.processEvents()
    assert restored.splitter.saveState() == main_state
    assert restored.study_view.body_splitter.saveState() == body_state
    assert restored.study_view.text_splitter.saveState() == text_state
    assert restored.study_view.media_tab_index() == 2
    assert restored.size().width() == 1180
    assert restored.size().height() == 840
    restored._dirty = False
    restored.close()


def test_unreadable_project_is_left_alone_until_confirmed_save(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "sources.txt").write_text("credits\n", encoding="utf-8")
    project_file = root / "suttara.json"
    original = '{"schema_version": 1, "sections": [broken'
    project_file.write_text(original, encoding="utf-8")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(project_root=root, show_dialogs=False, settings=settings)
    window.show()
    qapp.processEvents()
    assert window.project.sections == []
    assert window._load_failed is True
    assert project_file.read_text(encoding="utf-8") == original

    monkeypatch.setattr(
        "suttara.ui.main_window.QMessageBox.warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Save,
    )
    assert window.save() is True
    assert window._load_failed is False
    assert load_project(project_file, root).sections == []
    backups = list(root.glob("suttara.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    window._dirty = False
    window.close()
