"""System tray icon for Clicky Windows.

Provides the ONLY clean exit path from the running app — without this
icon, users have no menu/taskbar entry to right-click and quit, and
must reach for Task Manager (Ctrl+Shift+Esc) to kill ``Clicky.exe``.

Menu structure (right-click the tray icon):
    - Settings...                   ← reopen the BYOK keyring dialog
    - Open Knowledge Folder         ← jump to ~/Documents/Clicky Wiki/
    - Open Memory Folder            ← jump to ~/.clicky-windows/memory/
    - --------
    - Quit Clicky                   ← clean shutdown via callback

Implementation notes:
- ``QSystemTrayIcon`` is the PyQt6 native widget — no extra deps
  (we explicitly chose this over ``pystray``, which hasn't shipped
  since 2023-09 per the verified research). Plays nicely with our
  existing Qt event loop.
- Tray icon source: ``assets/clicky_tray.ico`` (multi-res 16/32/48/
  64/128/256, hand-drawn blue cursor polygon — verified DALL-E /
  GPT Image v2 fail at 16x16 so we generate via PIL programmatically).
- "Open Folder" actions use ``os.startfile`` (Windows native — opens
  in File Explorer). Folders are auto-created if missing so the user
  doesn't get an error on first click before they've dropped any
  files.
- The Quit action calls a parent-supplied callback rather than
  closing windows directly. The callback in app.py runs ``stop()``
  on STT/TTS/hotkey before ``QApplication.quit()`` to avoid leaking
  worker threads / WebSocket connections.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from config import KB_DIR, MEMORY_DIR


class ClickyTray(QObject):
    """Tray icon + right-click menu wrapper.

    The icon is shown as soon as ``__init__`` completes. Pass
    ``on_quit`` (called when the user clicks Quit) and
    ``on_settings`` (called when they click Settings...). Both fire
    on the Qt main thread.
    """

    def __init__(
        self,
        *,
        on_quit: Callable[[], None],
        on_settings: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        # Guard against weird Windows configs without a system tray
        # (kiosk mode, custom shells, certain VMs). Without this check
        # the tray icon silently fails to appear and the user has no
        # diagnostic — just an apparently-running app with no quit
        # menu. Caller in app.py wraps with try/except + QMessageBox
        # so the user gets an actionable dialog before exit.
        if not QSystemTrayIcon.isSystemTrayAvailable():
            raise RuntimeError(
                "System tray is not available on this Windows "
                "configuration. Clicky needs the system tray to "
                "provide its quit menu and settings access. Check "
                "Windows taskbar settings (Settings -> "
                "Personalisation -> Taskbar -> Other system tray "
                "icons)."
            )

        self._on_quit = on_quit
        self._on_settings = on_settings

        self._icon = QSystemTrayIcon(parent=self)
        self._icon.setToolTip("Clicky Windows — push-to-talk AI buddy")
        icon_path = Path(__file__).parent / "assets" / "clicky_tray.ico"
        if icon_path.is_file():
            self._icon.setIcon(QIcon(str(icon_path)))

        self._menu = QMenu()
        self._build_menu()
        self._icon.setContextMenu(self._menu)
        self._icon.show()

    # ---------- Menu construction ---------------------------------------

    def _build_menu(self) -> None:
        act_settings = QAction("Settings...", self)
        act_settings.triggered.connect(self._on_settings)
        self._menu.addAction(act_settings)

        act_open_kb = QAction("Open Knowledge Folder", self)
        act_open_kb.triggered.connect(self._open_kb_folder)
        self._menu.addAction(act_open_kb)

        act_open_mem = QAction("Open Memory Folder", self)
        act_open_mem.triggered.connect(self._open_memory_folder)
        self._menu.addAction(act_open_mem)

        self._menu.addSeparator()

        act_quit = QAction("Quit Clicky", self)
        act_quit.triggered.connect(self._on_quit)
        self._menu.addAction(act_quit)

    # ---------- Folder actions ------------------------------------------

    @staticmethod
    def _open_in_explorer(path: Path) -> None:
        """Open ``path`` in Windows File Explorer; create-if-missing.

        Folders that don't exist yet (first launch, user hasn't dropped
        anything) get created so the user lands in an empty folder
        rather than an error dialog.
        """
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    def _open_kb_folder(self) -> None:
        self._open_in_explorer(KB_DIR)

    def _open_memory_folder(self) -> None:
        self._open_in_explorer(MEMORY_DIR)
