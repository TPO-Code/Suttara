"""Main Suttara window: section sidebar plus study reader."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..model import Section, StudyProject, next_section_title
from ..persistence import (
    ProjectError,
    ProjectLoadError,
    backup_file,
    find_project_root,
    load_project,
    project_file_for,
    save_project,
)
from .dialogs import SourceMaterialsDialog
from .section_editor import SectionEditorDialog
from .study_view import StudyView


class MainWindow(QMainWindow):
    """The Suttara study workspace."""

    def __init__(
        self,
        project_root: Path | None = None,
        project_path: Path | None = None,
        show_dialogs: bool = True,
        settings: QSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root is not None
            else find_project_root()
        )
        self.project_path = (
            Path(project_path).expanduser().resolve()
            if project_path is not None
            else project_file_for(self.project_root)
        )
        self.show_dialogs = show_dialogs
        self.settings = settings or QSettings("Suttara", "Suttara")
        self.project = StudyProject()
        self._dirty = False
        self._load_failed = False
        self._load_error = ""
        self._current_id: str | None = None
        self._settings_key = "project/last_section_id"

        self.setWindowTitle("Suttara")
        self.resize(1280, 860)
        self.setMinimumSize(980, 680)
        self._build_ui()
        self._build_menus()
        self._restore_window_state()
        self._load_from_disk(initial=True)

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.setCentralWidget(self.splitter)

        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 12, 9, 12)
        sidebar_layout.setSpacing(8)
        self.sidebar_title = QLabel("Study sections")
        self.sidebar_title.setStyleSheet(
            "font-size: 15px; font-weight: 700; color: #3e3a33;"
        )
        sidebar_layout.addWidget(self.sidebar_title)
        self.section_list = QListWidget()
        self.section_list.setAlternatingRowColors(True)
        self.section_list.setMinimumWidth(230)
        self.section_list.setToolTip(
            "Sections appear in this order. Double-click a section to edit it."
        )
        self.section_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        sidebar_layout.addWidget(self.section_list, 1)

        new_button = QPushButton("New section")
        new_button.setDefault(True)
        self.edit_button = QPushButton("Edit section…")
        compact_buttons = QHBoxLayout()
        compact_buttons.addWidget(new_button)
        compact_buttons.addWidget(self.edit_button)
        sidebar_layout.addLayout(compact_buttons)
        sidebar_tip = QLabel(
            "Double-click or use Edit section… to configure a section. "
            "Right-click for insert, rename, move and delete actions."
        )
        sidebar_tip.setWordWrap(True)
        sidebar_tip.setStyleSheet("color: #6a655c; font-size: 10px;")
        sidebar_layout.addWidget(sidebar_tip)
        self.splitter.addWidget(sidebar)

        self.content_stack = QStackedWidget()
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(48, 48, 48, 48)
        empty_layout.addStretch(1)
        self.empty_label = QLabel("No study sections yet.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet(
            "font-size: 20px; font-weight: 600; color: #555047;"
        )
        self.empty_detail = QLabel(
            "Create a section, then use Edit Section to choose its Pāli and "
            "English passages, spoken recordings, and Goenka chanting excerpt."
        )
        self.empty_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_detail.setWordWrap(True)
        self.empty_detail.setStyleSheet("color: #6e685f; font-size: 13px;")
        self.empty_button = QPushButton("Create first section")
        self.empty_button.setMinimumWidth(190)
        empty_row = QHBoxLayout()
        empty_row.addStretch(1)
        empty_row.addWidget(self.empty_button)
        empty_row.addStretch(1)
        empty_layout.addWidget(self.empty_label)
        empty_layout.addWidget(self.empty_detail)
        empty_layout.addSpacing(12)
        empty_layout.addLayout(empty_row)
        empty_layout.addStretch(2)
        self.content_stack.addWidget(self.empty_page)

        self.study_view = StudyView(self.project_root)
        self.content_stack.addWidget(self.study_view)
        self.splitter.addWidget(self.content_stack)
        self.splitter.setSizes([288, 1000])

        self.status_message = QLabel("Starting Suttara…")
        self.statusBar().addWidget(self.status_message, 1)
        self.project_label = QLabel(str(self.project_path))
        self.project_label.setToolTip(
            "The project is always saved to suttara.json in the repository root."
        )
        self.project_label.setStyleSheet("color: #6a655c;")
        self.statusBar().addPermanentWidget(self.project_label)

        new_button.clicked.connect(self._new_section)
        self.empty_button.clicked.connect(self._new_section)
        self.edit_button.clicked.connect(self._edit_section)
        self.section_list.currentRowChanged.connect(self._selection_changed)
        self.section_list.itemDoubleClicked.connect(lambda _item: self._edit_section())
        self.section_list.customContextMenuRequested.connect(
            self._show_section_context_menu
        )
        self.study_view.changed.connect(self._on_section_changed)
        self.study_view.edit_requested.connect(self._edit_section)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.save_action = self._action(
            "&Save project", self.save, QKeySequence.StandardKey.Save
        )
        file_menu.addAction(self.save_action)
        self.reload_action = self._action(
            "&Reload saved project", self._reload_from_disk, "Ctrl+R"
        )
        file_menu.addAction(self.reload_action)
        file_menu.addSeparator()
        exit_action = self._action("E&xit", self.close, QKeySequence.StandardKey.Quit)
        file_menu.addAction(exit_action)

        section_menu = self.menuBar().addMenu("&Section")
        self.new_action = self._action(
            "&New section", self._new_section, QKeySequence.StandardKey.New
        )
        section_menu.addAction(self.new_action)
        self.edit_action = self._action("&Edit section…", self._edit_section, "Ctrl+E")
        section_menu.addAction(self.edit_action)
        section_menu.addSeparator()
        self.insert_before_action = self._action(
            "Insert &before", lambda: self._insert_section(-1)
        )
        self.insert_after_action = self._action(
            "Insert &after", lambda: self._insert_section(1)
        )
        section_menu.addAction(self.insert_before_action)
        section_menu.addAction(self.insert_after_action)
        self.rename_action = self._action("&Rename…", self._rename_section, "F2")
        section_menu.addAction(self.rename_action)
        section_menu.addSeparator()
        self.move_up_action = self._action(
            "Move &up", lambda: self._move_section(-1), "Alt+Up"
        )
        self.move_down_action = self._action(
            "Move &down", lambda: self._move_section(1), "Alt+Down"
        )
        section_menu.addAction(self.move_up_action)
        section_menu.addAction(self.move_down_action)
        section_menu.addSeparator()
        self.delete_action = self._action("&Delete section…", self._delete_section)
        section_menu.addAction(self.delete_action)

        help_menu = self.menuBar().addMenu("&Help")
        self.sources_action = self._action(
            "&Sources & attribution…", self._show_sources
        )
        help_menu.addAction(self.sources_action)
        about_action = self._action("&About Suttara", self._show_about)
        help_menu.addAction(about_action)

    def _action(self, text: str, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.addAction(action)
        return action

    # -- project loading ----------------------------------------------------
    def _load_from_disk(self, initial: bool = False) -> bool:
        try:
            project = load_project(self.project_path, self.project_root)
        except ProjectLoadError as error:
            self._load_failed = True
            self._load_error = str(error)
            self.project = StudyProject()
            self._dirty = False
            self._current_id = None
            self._populate_list()
            self._set_status(
                "The saved project could not be loaded; it has not been modified."
            )
            self._update_title()
            if initial and self.show_dialogs:
                QTimer.singleShot(0, self._report_initial_load_error)
            return False
        self.project = project
        self._load_failed = False
        self._load_error = ""
        self._dirty = False
        preferred = self.settings.value(self._settings_key)
        self._populate_list(preferred_id=str(preferred) if preferred else None)
        if self.project_path.exists():
            self._set_status(
                f"Loaded {len(self.project.sections)} section(s) from "
                f"{self.project_path.name}."
            )
        else:
            self._set_status(
                "No saved project yet. Create a section and use File → Save project."
            )
        self._update_title()
        return True

    def _report_initial_load_error(self) -> None:
        QMessageBox.critical(
            self,
            "Project file could not be loaded",
            f"Suttara could not load:\n{self.project_path}\n\n"
            f"{self._load_error}\n\n"
            "The file has been left untouched. You can repair it and use "
            "File → Reload saved project. If you choose to save an empty project, "
            "Suttara will ask first and create a timestamped backup.",
        )

    def _populate_list(self, preferred_id: str | None = None) -> None:
        blocker = QSignalBlocker(self.section_list)
        self.section_list.clear()
        for section in self.project.sections:
            item = QListWidgetItem(section.display_title)
            item.setData(Qt.ItemDataRole.UserRole, section.id)
            item.setToolTip(self._item_tooltip(section))
            self.section_list.addItem(item)
        if preferred_id is None:
            preferred_id = self._current_id
        row = 0 if self.project.sections else -1
        if preferred_id:
            index = self.project.index_of(preferred_id)
            if index >= 0:
                row = index
        self.section_list.setCurrentRow(row)
        del blocker
        self.sidebar_title.setText(f"Study sections ({len(self.project.sections)})")
        self._selection_changed(row)

    def _item_tooltip(self, section: Section) -> str:
        return (
            f"{section.display_title}\n"
            f"Resources: {section.resource_summary()}\n\n"
            "Double-click to edit the section."
        )

    def _refresh_current_item(self) -> None:
        if self.study_view.section is None:
            return
        row = self.section_list.currentRow()
        if row < 0 or row >= self.section_list.count():
            return
        item = self.section_list.item(row)
        section = self.study_view.section
        blocker = QSignalBlocker(self.section_list)
        item.setText(section.display_title)
        item.setToolTip(self._item_tooltip(section))
        del blocker

    # -- selection and empty state -----------------------------------------
    def _selection_changed(self, row: int) -> None:
        if 0 <= row < len(self.project.sections):
            section = self.project.sections[row]
            self._current_id = section.id
            self.study_view.set_section(section)
            self.content_stack.setCurrentWidget(self.study_view)
            if self.settings is not None:
                self.settings.setValue(self._settings_key, section.id)
        else:
            self._current_id = None
            self.study_view.set_section(None)
            self._show_empty_state()
        self._update_action_states()

    def _show_empty_state(self) -> None:
        if not self.project.sections:
            self.empty_label.setText("No study sections yet.")
            self.empty_detail.setText(
                "Create a section, then use Edit Section to choose its Pāli and "
                "English passages, spoken recordings and chanting excerpt."
            )
            self.empty_button.setText("Create first section")
        else:
            self.empty_label.setText("Select a study section.")
            self.empty_detail.setText(
                "Choose a section in the sidebar to read, listen and take notes."
            )
            self.empty_button.setText("New section")
        self.content_stack.setCurrentWidget(self.empty_page)

    def _update_action_states(self) -> None:
        has_selection = self.current_section() is not None
        row = self.section_list.currentRow()
        self.edit_button.setEnabled(has_selection)
        self.edit_action.setEnabled(has_selection)
        self.insert_before_action.setEnabled(has_selection)
        self.insert_after_action.setEnabled(has_selection)
        self.rename_action.setEnabled(has_selection)
        self.delete_action.setEnabled(has_selection)
        self.move_up_action.setEnabled(has_selection and row > 0)
        self.move_down_action.setEnabled(
            has_selection and row < len(self.project.sections) - 1
        )
        self.save_action.setEnabled(self._dirty or self._load_failed)

    def current_section(self) -> Section | None:
        return self.study_view.section

    # -- section operations -------------------------------------------------
    def _new_section(self) -> None:
        self._create_section(None)

    def _insert_section(self, offset: int) -> None:
        row = self.section_list.currentRow()
        if row < 0:
            self._new_section()
            return
        index = row if offset < 0 else row + 1
        self._create_section(index)

    def _create_section(self, index: int | None) -> None:
        candidate = Section(title=next_section_title(self.project.sections))
        edited = self._open_section_editor(candidate, is_new=True)
        if edited is None:
            self._set_status("Section creation cancelled.")
            return
        if index is None:
            self.project.sections.append(edited)
        else:
            index = max(0, min(index, len(self.project.sections)))
            self.project.sections.insert(index, edited)
        self._mark_dirty()
        self._populate_list(preferred_id=edited.id)
        self._set_status("Section created.")

    def _edit_section(self) -> None:
        row = self.section_list.currentRow()
        section = self.current_section()
        if row < 0 or section is None:
            return
        edited = self._open_section_editor(section, is_new=False)
        if edited is None:
            self._set_status("Section editing cancelled.")
            return
        edited.id = section.id
        self.project.sections[row] = edited
        self._mark_dirty()
        self._populate_list(preferred_id=edited.id)
        self._set_status("Section updated.")

    def _open_section_editor(self, section: Section, is_new: bool) -> Section | None:
        dialog = SectionEditorDialog(
            section,
            self.project_root,
            self,
            settings=self.settings,
            is_new=is_new,
            sections=self.project.sections,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.edited_section()

    def _rename_section(self) -> None:
        section = self.current_section()
        if section is None:
            return
        title, accepted = QInputDialog.getText(
            self,
            "Rename section",
            "Section title:",
            text=section.title,
        )
        if not accepted:
            return
        title = title.strip() or "Untitled section"
        if title == section.title:
            return
        section.title = title
        self._mark_dirty()
        self._refresh_current_item()
        self.study_view.refresh_section()
        self._set_status("Section renamed.")

    def _move_section(self, offset: int) -> None:
        row = self.section_list.currentRow()
        if row < 0:
            return
        section = self.project.sections[row]
        if not self.project.move_section(row, offset):
            return
        self._mark_dirty()
        self._populate_list(preferred_id=section.id)
        self._set_status("Section order updated.")

    def _delete_section(self) -> None:
        row = self.section_list.currentRow()
        if row < 0 or row >= len(self.project.sections):
            return
        section = self.project.sections[row]
        answer = QMessageBox.question(
            self,
            "Delete section",
            f"Delete “{section.display_title}”?\n\n"
            "This removes the section, its resource references and its notes "
            "from the project. The source files and recordings themselves are "
            "not deleted. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.project.remove_section(row)
        self._mark_dirty()
        preferred = None
        if self.project.sections:
            new_row = min(row, len(self.project.sections) - 1)
            preferred = self.project.sections[new_row].id
        self._populate_list(preferred_id=preferred)
        self._set_status("Section deleted.")

    def _show_section_context_menu(self, position) -> None:
        item = self.section_list.itemAt(position)
        if item is not None:
            self.section_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(self.new_action)
        menu.addAction(self.edit_action)
        menu.addSeparator()
        menu.addAction(self.insert_before_action)
        menu.addAction(self.insert_after_action)
        menu.addAction(self.rename_action)
        menu.addSeparator()
        menu.addAction(self.move_up_action)
        menu.addAction(self.move_down_action)
        menu.addSeparator()
        menu.addAction(self.delete_action)
        menu.exec(self.section_list.mapToGlobal(position))

    # -- dirty state and saving --------------------------------------------
    def _on_section_changed(self) -> None:
        self._mark_dirty()
        self._refresh_current_item()
        self.study_view.refresh_section()

    def _mark_dirty(self) -> None:
        changed = not self._dirty
        self._dirty = True
        if changed:
            self._set_status("Unsaved changes.")
        self._update_title()
        self._update_action_states()

    def _update_title(self) -> None:
        marker = " *" if self._dirty else ""
        state = " — file not loaded" if self._load_failed else ""
        self.setWindowTitle(f"Suttara — {self.project_path.name}{state}{marker}")

    def _set_status(self, message: str) -> None:
        self.status_message.setText(message)

    def save(self) -> bool:
        if self._load_failed and self.project_path.exists():
            answer = QMessageBox.warning(
                self,
                "Unreadable project file",
                f"{self.project_path} could not be loaded.\n\n"
                "Saving now will replace it. Suttara will first create a "
                "timestamped backup of the unreadable file.",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Save:
                return False
            try:
                backup = backup_file(self.project_path)
            except ProjectError as error:
                QMessageBox.critical(self, "Could not back up project", str(error))
                return False
            if backup is not None:
                self._set_status(f"Backed up unreadable project to {backup.name}.")
        try:
            save_project(self.project, self.project_path, self.project_root)
        except ProjectError as error:
            QMessageBox.critical(self, "Could not save project", str(error))
            return False
        self._load_failed = False
        self._load_error = ""
        self._dirty = False
        self._update_title()
        self._update_action_states()
        self._set_status(
            f"Saved {len(self.project.sections)} section(s) at "
            f"{datetime.now().strftime('%H:%M:%S')}."
        )
        return True

    def _maybe_discard_changes(self, action: str) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.warning(
            self,
            "Unsaved changes",
            f"You have unsaved changes. Save them before you {action}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        if answer == QMessageBox.StandardButton.Discard:
            return True
        return False

    def _reload_from_disk(self) -> None:
        if not self._maybe_discard_changes("reload the saved project"):
            return
        try:
            project = load_project(self.project_path, self.project_root)
        except ProjectLoadError as error:
            QMessageBox.critical(
                self,
                "Could not reload project",
                f"Suttara could not reload:\n{self.project_path}\n\n{error}\n\n"
                "Your current in-memory sections have been kept.",
            )
            return
        self.project = project
        self._dirty = False
        self._load_failed = False
        self._current_id = None
        self._populate_list()
        self._update_title()
        self._set_status(f"Reloaded {len(self.project.sections)} section(s) from disk.")

    # -- dialogs and window state ------------------------------------------
    def _show_sources(self) -> None:
        SourceMaterialsDialog(self.project_root, self).exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Suttara",
            f"<h3>Suttara {__version__}</h3>"
            "<p>A local-first study workspace for the Mahāsatipaṭṭhāna Sutta.</p>"
            "<p>Texts and recordings remain in their original local files; "
            "suttara.json stores only references, timestamps and your notes.</p>"
            "<p>Source scholarship, translations and recordings are credited in "
            "Help → Sources & attribution.</p>",
        )

    def _restore_window_state(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            try:
                self.restoreGeometry(geometry)
            except (TypeError, ValueError):
                pass
        splitter_state = self.settings.value("window/splitter")
        if splitter_state is not None:
            try:
                self.splitter.restoreState(splitter_state)
            except (TypeError, ValueError):
                pass
        body_state = self._settings_byte_array(
            self.settings.value("window/section_body_splitter")
        )
        text_state = self._settings_byte_array(
            self.settings.value("window/section_text_splitter")
        )
        self.study_view.restore_splitter_states(body_state, text_state)
        tab_index = self.settings.value("window/media_tab_index")
        if tab_index is not None:
            try:
                self.study_view.set_media_tab_index(int(tab_index))
            except (TypeError, ValueError):
                pass

    def _save_window_state(self) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/splitter", self.splitter.saveState())
        body_state, text_state = self.study_view.splitter_states()
        self.settings.setValue("window/section_body_splitter", body_state)
        self.settings.setValue("window/section_text_splitter", text_state)
        self.settings.setValue(
            "window/media_tab_index", self.study_view.media_tab_index()
        )
        if self._current_id:
            self.settings.setValue(self._settings_key, self._current_id)
        self.settings.sync()

    @staticmethod
    def _settings_byte_array(value) -> QByteArray | None:
        if isinstance(value, QByteArray):
            return value
        if isinstance(value, bytes):
            return QByteArray(value)
        return None

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._maybe_discard_changes("close Suttara"):
            event.ignore()
            return
        self._save_window_state()
        event.accept()


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication(argv if argv is not None else [])
    app.setApplicationName("Suttara")
    app.setApplicationDisplayName("Suttara")
    app.setOrganizationName("Suttara")
    app.setApplicationVersion(__version__)
    return app
