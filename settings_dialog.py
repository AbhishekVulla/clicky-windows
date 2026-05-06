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

from dataclasses import dataclass
from pathlib import Path

import keyring

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import KEYRING_SERVICE


# --- Sprint 4: provider category data model ---------------------------------
#
# Drives 3-row progressive-disclosure UX in the dialog: pick provider per
# category (LLM/STT/TTS) from a dropdown, only that provider's API key field
# is visible. Fixes the previous flat 3-required-field layout that would
# have grown to 6 fields with ElevenLabs (and 7+ with Deepgram).


@dataclass(frozen=True)
class _Provider:
    """Single provider in a category. ``provider_id`` is the lowercase
    string used as the value of LLM_PROVIDER / STT_PROVIDER / TTS_PROVIDER
    config + the dropdown's data slot. ``api_key_env_var`` is BOTH the
    env-var name AND the keyring slot name (they share namespace by
    convention — see config.resolve_api_key)."""

    provider_id: str            # e.g. "anthropic", "elevenlabs"
    display_name: str           # e.g. "Anthropic", "ElevenLabs"
    api_key_env_var: str        # e.g. "ANTHROPIC_API_KEY"
    signup_url: str


@dataclass(frozen=True)
class _ProviderCategory:
    """A row group in the dialog. ``category_key`` is the prefix of
    the provider-selection config (e.g. "LLM" → LLM_PROVIDER setting)."""

    category_key: str           # "LLM", "STT", "TTS"
    label: str                  # "LLM (vision)", etc.
    providers: tuple[_Provider, ...]
    default_index: int


_PROVIDER_CATEGORIES: tuple[_ProviderCategory, ...] = (
    _ProviderCategory(
        category_key="LLM",
        label="LLM (vision)",
        providers=(
            _Provider(
                provider_id="anthropic",
                display_name="Anthropic",
                api_key_env_var="ANTHROPIC_API_KEY",
                signup_url="https://console.anthropic.com/settings/keys",
            ),
        ),
        default_index=0,
    ),
    _ProviderCategory(
        category_key="STT",
        label="STT (speech-to-text)",
        providers=(
            _Provider(
                provider_id="assemblyai",
                display_name="AssemblyAI",
                api_key_env_var="ASSEMBLYAI_API_KEY",
                signup_url="https://www.assemblyai.com/dashboard/signup",
            ),
        ),
        default_index=0,
    ),
    _ProviderCategory(
        category_key="TTS",
        label="TTS (text-to-speech)",
        providers=(
            _Provider(
                provider_id="cartesia",
                display_name="Cartesia",
                api_key_env_var="CARTESIA_API_KEY",
                signup_url="https://play.cartesia.ai/sign-in",
            ),
            _Provider(
                provider_id="elevenlabs",
                display_name="ElevenLabs",
                api_key_env_var="ELEVENLABS_API_KEY",
                signup_url="https://elevenlabs.io/app/sign-up",
            ),
        ),
        default_index=0,
    ),
)


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

        self._dropdowns: dict[str, QComboBox] = {}
        self._key_inputs: dict[str, QLineEdit] = {}
        self._signup_buttons: dict[str, QPushButton] = {}
        self._build_ui()

    # ---------- UI construction -----------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # Lean privacy framing — one sentence (USER decision 2026-05-06,
        # rejected the multi-line splash version as too loud / suspicious).
        privacy = QLabel(
            "🔒 Stored locally, encrypted via Windows Credential Manager. "
            "No server, no telemetry."
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: gray; padding-bottom: 4px;")
        outer.addWidget(privacy)

        for category in _PROVIDER_CATEGORIES:
            category_widget = self._build_category_row(category)
            outer.addWidget(category_widget)

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

    def _build_category_row(self, category: _ProviderCategory) -> QWidget:
        """Build one (label + dropdown + Get-key + key-field) row group."""
        from config import resolve_setting

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 4, 0, 8)

        label = QLabel(f"<b>{category.label}</b>")
        v.addWidget(label)

        # Resolve currently-selected provider for this category.
        selected_provider_id = resolve_setting(
            f"{category.category_key}_PROVIDER",
            default=category.providers[category.default_index].provider_id,
        )
        try:
            selected_index = next(
                i for i, p in enumerate(category.providers)
                if p.provider_id == selected_provider_id
            )
        except StopIteration:
            selected_index = category.default_index

        # Dropdown + Get-key button on one horizontal row.
        h = QHBoxLayout()
        dropdown = QComboBox()
        for provider in category.providers:
            dropdown.addItem(provider.display_name, provider.provider_id)
        dropdown.setCurrentIndex(selected_index)
        dropdown.currentIndexChanged.connect(
            lambda idx, c=category: self._on_provider_changed(c, idx)
        )
        self._dropdowns[category.category_key] = dropdown
        h.addWidget(dropdown, stretch=1)

        signup_button = QPushButton("Get key →")
        signup_button.clicked.connect(
            lambda _checked=False, c=category: self._on_signup_clicked(c)
        )
        self._signup_buttons[category.category_key] = signup_button
        h.addWidget(signup_button)
        v.addLayout(h)

        # API key field.
        key_input = QLineEdit()
        key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_input.textChanged.connect(self._update_save_enabled)
        self._key_inputs[category.category_key] = key_input
        v.addWidget(key_input)

        # Pre-populate the key field with masked existing value (if any).
        self._refresh_key_field_for_category(category)

        return container

    def _refresh_key_field_for_category(self, category: _ProviderCategory) -> None:
        """Read the keyring slot for the dropdown's currently-selected
        provider, set the key field's text + placeholder accordingly.
        Called on dialog construction AND on dropdown change."""
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        existing = keyring.get_password(KEYRING_SERVICE, provider.api_key_env_var) or ""
        key_input = self._key_inputs[category.category_key]
        key_input.setText(existing)
        key_input.setPlaceholderText(
            _mask(existing) if existing else f"paste {provider.api_key_env_var} here"
        )

    # ---------- Slots ----------------------------------------------------

    def _on_provider_changed(self, category: _ProviderCategory, _index: int) -> None:
        """Dropdown changed — swap the key field's contents to the newly-
        selected provider's stored key + update placeholder + Save state."""
        self._refresh_key_field_for_category(category)
        self._update_save_enabled()

    def _on_signup_clicked(self, category: _ProviderCategory) -> None:
        """User clicked 'Get key →' — open selected provider's signup URL
        in default browser via QDesktopServices."""
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        QDesktopServices.openUrl(QUrl(provider.signup_url))

    def _on_reveal_toggled(self, checked: bool) -> None:
        mode = (
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        for key_input in self._key_inputs.values():
            key_input.setEchoMode(mode)

    def _update_save_enabled(self) -> None:
        """Save enabled when every category's key field has non-empty content.

        Defensively no-op if self._buttons isn't constructed yet — the
        textChanged signal can fire during initial _build_category_row
        (when the keyring already has a key, setText(existing) fires
        before the QDialogButtonBox is added to the dialog at the end of
        _build_ui).
        """
        if not hasattr(self, "_buttons"):
            return
        all_filled = all(
            key_input.text().strip()
            for key_input in self._key_inputs.values()
        )
        self._buttons.button(
            QDialogButtonBox.StandardButton.Save
        ).setEnabled(all_filled)

    def _on_save(self) -> None:
        """Persist provider selection + currently-selected provider's key
        for each category to keyring."""
        for category in _PROVIDER_CATEGORIES:
            dropdown = self._dropdowns[category.category_key]
            provider = category.providers[dropdown.currentIndex()]

            # 1. Persist provider selection (e.g. "TTS_PROVIDER" → "elevenlabs")
            keyring.set_password(
                KEYRING_SERVICE,
                f"{category.category_key}_PROVIDER",
                provider.provider_id,
            )

            # 2. Persist the API key for the selected provider.
            key_value = self._key_inputs[category.category_key].text().strip()
            if key_value:
                keyring.set_password(
                    KEYRING_SERVICE, provider.api_key_env_var, key_value,
                )
        self.accept()


def required_keys_present() -> bool:
    """Probe — does every required-provider's API key resolve?

    Sprint 4: "required" = the currently-SELECTED provider per category
    (resolved via resolve_setting on LLM_PROVIDER / STT_PROVIDER /
    TTS_PROVIDER). The probe is what the launcher uses to decide whether
    to show the modal at start.
    """
    from config import resolve_api_key, resolve_setting

    for category in _PROVIDER_CATEGORIES:
        provider_id = resolve_setting(
            f"{category.category_key}_PROVIDER",
            default=category.providers[category.default_index].provider_id,
        )
        provider = next(
            (p for p in category.providers if p.provider_id == provider_id),
            category.providers[category.default_index],  # fallback if stored value invalid
        )
        if not resolve_api_key(provider.api_key_env_var):
            return False
    return True
