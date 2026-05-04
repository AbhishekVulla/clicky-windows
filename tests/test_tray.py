"""Unit tests for tray.py — system tray menu + folder actions.

Tray icon construction needs a QApplication. We use a session-scoped
fixture that creates one if none exists. The actual rendering
(``self._icon.show()``) is silently no-op'd on systems without a
display, so tests run headless cleanly.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def qapp():
    """Session-shared QApplication. Created once; reused across tests."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_tray_module_importable():
    import tray  # noqa: F401


class TestClickyTrayMenu:
    """The tray menu must have exactly 4 visible actions in this order:
    Settings... / Open Knowledge Folder / Open Memory Folder / Quit
    Clicky. The Quit action's callback must be the on_quit kwarg passed
    at construction time."""

    def test_menu_has_four_actions_in_order(self, qapp, mocker):
        """The 4 visible menu items + 1 separator. Verified by reading
        QMenu.actions() in order."""
        from tray import ClickyTray
        on_quit = mocker.MagicMock()
        on_settings = mocker.MagicMock()
        t = ClickyTray(on_quit=on_quit, on_settings=on_settings)

        actions = [a for a in t._menu.actions() if not a.isSeparator()]
        labels = [a.text() for a in actions]
        assert labels == [
            "Settings...",
            "Open Knowledge Folder",
            "Open Memory Folder",
            "Quit Clicky",
        ]

    def test_quit_action_triggers_on_quit_callback(self, qapp, mocker):
        from tray import ClickyTray
        on_quit = mocker.MagicMock()
        on_settings = mocker.MagicMock()
        t = ClickyTray(on_quit=on_quit, on_settings=on_settings)

        quit_action = next(
            a for a in t._menu.actions() if a.text() == "Quit Clicky"
        )
        quit_action.trigger()
        on_quit.assert_called_once()
        on_settings.assert_not_called()

    def test_settings_action_triggers_on_settings_callback(self, qapp, mocker):
        from tray import ClickyTray
        on_quit = mocker.MagicMock()
        on_settings = mocker.MagicMock()
        t = ClickyTray(on_quit=on_quit, on_settings=on_settings)

        settings_action = next(
            a for a in t._menu.actions() if a.text() == "Settings..."
        )
        settings_action.trigger()
        on_settings.assert_called_once()
        on_quit.assert_not_called()

    def test_open_kb_folder_uses_kb_dir_and_creates_if_missing(
        self, qapp, mocker, tmp_path: Path
    ):
        """Open Knowledge Folder must call os.startfile on KB_DIR
        AND mkdir-p the path if it doesn't exist. First-launch users
        haven't dropped any .md files yet — no error dialog."""
        kb_path = tmp_path / "knowledge"
        assert not kb_path.exists()

        mocker.patch("tray.KB_DIR", kb_path)
        startfile_mock = mocker.patch("tray.os.startfile")

        from tray import ClickyTray
        t = ClickyTray(on_quit=mocker.MagicMock(), on_settings=mocker.MagicMock())
        action = next(
            a for a in t._menu.actions()
            if a.text() == "Open Knowledge Folder"
        )
        action.trigger()

        assert kb_path.exists(), "Expected KB folder to be auto-created"
        startfile_mock.assert_called_once_with(str(kb_path))

    def test_open_memory_folder_uses_memory_dir_and_creates_if_missing(
        self, qapp, mocker, tmp_path: Path
    ):
        mem_path = tmp_path / "memory"
        mocker.patch("tray.MEMORY_DIR", mem_path)
        startfile_mock = mocker.patch("tray.os.startfile")

        from tray import ClickyTray
        t = ClickyTray(on_quit=mocker.MagicMock(), on_settings=mocker.MagicMock())
        action = next(
            a for a in t._menu.actions()
            if a.text() == "Open Memory Folder"
        )
        action.trigger()

        assert mem_path.exists()
        startfile_mock.assert_called_once_with(str(mem_path))

    def test_raises_runtime_error_when_system_tray_unavailable(
        self, qapp, mocker
    ):
        """If QSystemTrayIcon.isSystemTrayAvailable() returns False (rare
        Windows config — kiosk mode, custom shell, certain VMs), the
        constructor must raise RuntimeError so the caller can show a
        QMessageBox + exit cleanly. Without this guard the tray icon
        silently doesn't appear and users have no diagnostic."""
        from tray import ClickyTray
        mocker.patch(
            "tray.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=False,
        )
        with pytest.raises(RuntimeError, match="System tray is not available"):
            ClickyTray(
                on_quit=mocker.MagicMock(),
                on_settings=mocker.MagicMock(),
            )
