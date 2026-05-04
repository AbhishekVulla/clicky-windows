"""First-launch + tray-menu settings dialog for Clicky Windows.

Modal QDialog with three password fields (Anthropic / AssemblyAI /
Cartesia API keys). Save persists to Windows Credential Manager via
keyring. App refuses to start until at least the three required keys
are present (env or keyring).

The dialog is reusable: it's shown at first-launch when keys are
missing, AND from the tray menu as a "Settings..." entry. Users can
swap keys (rotation) without editing .env.

Ergonomics:
- Password-mode fields (echoed as bullets), but with a checkbox to
  reveal so users can paste-verify the long sk-* / cartesia-* tokens.
- Existing keyring values are pre-populated so users see a partial
  preview (last 4 chars) without exposing the full secret on screen.
- Save button is disabled until all three fields are non-empty.

Threading: this dialog runs on the Qt main thread (it's modal). No
threading concerns. ``keyring.set_password`` is synchronous + ~10ms
on Windows DPAPI — no async needed.
"""
from __future__ import annotations

from pathlib import Path

import keyring

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from config import KEYRING_SERVICE


# Tuples of (env-var name, friendly label, signup URL).
# Order is the same order shown in the dialog top-to-bottom.
_KEY_FIELDS: list[tuple[str, str, str]] = [
    (
        "ANTHROPIC_API_KEY",
        "Anthropic (Claude vision)",
        "https://console.anthropic.com/settings/keys",
    ),
    (
        "ASSEMBLYAI_API_KEY",
        "AssemblyAI (speech-to-text)",
        "https://www.assemblyai.com/dashboard/signup",
    ),
    (
        "CARTESIA_API_KEY",
        "Cartesia (text-to-speech)",
        "https://play.cartesia.ai/sign-in",
    ),
]


def _mask(value: str | None) -> str:
    """Return a privacy-preserving preview like 'sk-...****abc4' for an
    existing key. Empty input → empty string."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:5]}{'*' * 6}{value[-4:]}"


class SettingsDialog(QDialog):
    """Modal dialog for entering / rotating BYOK API keys.

    Constructor doesn't block — call ``exec()`` to show modally and
    wait for OK/Cancel. Returns ``QDialog.DialogCode.Accepted`` on
    Save, ``QDialog.DialogCode.Rejected`` on Cancel.

    Saved values land in Windows Credential Manager under service
    ``KEYRING_SERVICE`` ("clicky-windows"), one entry per env-var name.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clicky Windows — API Keys")
        self.setModal(True)
        self.setMinimumWidth(520)
        # Use the tray icon as the window icon for visual consistency.
        # Path resolved via __file__ so it works inside both the dev
        # checkout (CWD = repo root) AND the bundled EXE (CWD =
        # wherever the user launched from). Plain "assets/..." would
        # be CWD-relative — broken in the bundled case.
        icon_path = Path(__file__).parent / "assets" / "clicky_tray.ico"
        try:
            self.setWindowIcon(QIcon(str(icon_path)))
        except Exception:
            pass  # icon missing in dev install; not critical

        self._inputs: dict[str, QLineEdit] = {}
        self._build_ui()

    # ---------- UI construction -----------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        intro = QLabel(
            "Clicky needs three API keys to run. Keys are stored in "
            "Windows Credential Manager (DPAPI per-user encryption) — "
            "they never touch a remote server.\n\n"
            "Sign-up links are free-tier with no credit card required."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        for name, label, url in _KEY_FIELDS:
            existing = keyring.get_password(KEYRING_SERVICE, name) or ""
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText(
                _mask(existing) if existing else f"paste {name} here"
            )
            edit.setText(existing)
            edit.textChanged.connect(self._update_save_enabled)
            self._inputs[name] = edit
            row_label = QLabel(f'{label}\n<a href="{url}">{url}</a>')
            row_label.setOpenExternalLinks(True)
            row_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction
            )
            form.addRow(row_label, edit)
        outer.addLayout(form)

        # Reveal checkbox — flip all 3 password fields to plain text +
        # back. Useful for paste-verify of long tokens.
        self._reveal = QCheckBox("Show keys in plain text (paste-verify)")
        self._reveal.toggled.connect(self._on_reveal_toggled)
        outer.addWidget(self._reveal)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)
        outer.addWidget(self._buttons)
        self._update_save_enabled()

    # ---------- Slots ----------------------------------------------------

    def _on_reveal_toggled(self, checked: bool) -> None:
        mode = (
            QLineEdit.EchoMode.Normal
            if checked
            else QLineEdit.EchoMode.Password
        )
        for edit in self._inputs.values():
            edit.setEchoMode(mode)

    def _update_save_enabled(self) -> None:
        all_filled = all(
            edit.text().strip() for edit in self._inputs.values()
        )
        self._buttons.button(
            QDialogButtonBox.StandardButton.Save
        ).setEnabled(all_filled)

    def _on_save(self) -> None:
        """Persist non-empty fields to keyring and accept the dialog."""
        for name, edit in self._inputs.items():
            value = edit.text().strip()
            if value:
                keyring.set_password(KEYRING_SERVICE, name, value)
        self.accept()


def required_keys_present() -> bool:
    """Probe — does every required key resolve to a non-empty value?

    Used by the launcher to decide whether to show the modal at start.
    Lives here (next to the dialog) so the launcher only needs one
    import. Reads via ``config.resolve_api_key`` so env-then-keyring
    semantics match the rest of the app.
    """
    from config import resolve_api_key

    return all(
        bool(resolve_api_key(name)) for name, _, _ in _KEY_FIELDS
    )
