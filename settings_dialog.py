"""First-launch + tray-menu settings dialog for Clicky Windows.

Modal QDialog with one row per category (AI model, Listening, Voice): a
provider dropdown and that provider's key field. Save persists the choices
and keys to Windows Credential Manager via keyring. A new install needs two
keys: one for the AI model and one Cartesia key, which covers both
listening and the voice.

The dialog is reusable: it's shown at first launch when keys are missing,
and from the tray menu as "Settings...". Users can swap keys or providers
without editing .env.

Ergonomics:
- Password-mode fields (echoed as bullets), with a checkbox to reveal so
  users can paste-verify long keys.
- Existing keyring values are pre-populated; the placeholder shows only a
  masked preview.
- When two rows use the same key, only one key field is shown.
- Save is disabled until every provider that needs a key has one.

Threading: this dialog runs on the Qt main thread (it's modal). No
threading concerns. ``keyring.set_password`` is synchronous + ~10ms
on Windows DPAPI, so no async needed.
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
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import KEYRING_SERVICE, OLLAMA_HOST


# Pre-populated Ollama vision model suggestions for the dropdown (Issue #1).
# `llava:7b` comes first since it's the default (works on all Ollama
# versions with vision). The combobox is editable, so the user can also
# type a custom model name.
_OLLAMA_MODEL_SUGGESTIONS: tuple[str, ...] = (
    "llava:7b",
    "llama3.2-vision",
    "qwen2.5-vl",
    "llava-llama3",
)


# --- Provider category data model -------------------------------------------
#
# Drives 3-row progressive-disclosure UX in the dialog: pick provider per
# category (LLM/STT/TTS) from a dropdown, only that provider's API key field
# is visible. The dialog stays at one key field per category however many
# providers there are.


@dataclass(frozen=True)
class _Model:
    """One selectable model for a provider. ``model_id`` is the bare model
    string passed to the SDK / stored in the provider's model_setting slot
    (e.g. "claude-opus-4-8", "gpt-5.4", "llava:7b")."""

    display_name: str           # e.g. "Claude Sonnet 4.6 (default)"
    model_id: str               # e.g. "claude-sonnet-4-6"


@dataclass(frozen=True)
class _Provider:
    """Single provider in a category. ``provider_id`` is the lowercase
    string used as the value of LLM_PROVIDER / STT_PROVIDER / TTS_PROVIDER
    config + the dropdown's data slot. ``api_key_env_var`` is BOTH the
    env-var name AND the keyring slot name (they share namespace by
    convention; see config.resolve_api_key).

    Optional per-provider model picker: ``models`` (if non-empty)
    drives a contextual model dropdown shown only when this provider is
    selected; the chosen model_id persists to ``model_setting`` (a keyring
    slot like "ANTHROPIC_MODEL"). ``models_editable`` lets the user type a
    custom model (Ollama). ``hides_other_categories`` collapses the STT+TTS
    rows when selected (GPT-Realtime does speech end-to-end)."""

    provider_id: str            # e.g. "anthropic", "elevenlabs"
    display_name: str           # e.g. "Anthropic", "ElevenLabs"
    api_key_env_var: str        # e.g. "ANTHROPIC_API_KEY"
    signup_url: str
    models: tuple[_Model, ...] = ()
    model_setting: str = ""           # keyring slot for the chosen model
    models_editable: bool = False     # True → user can type a custom model
    hides_other_categories: bool = False  # True → collapse STT+TTS (realtime)
    requires_key: bool = True         # False → key not required (Save not gated)
    hide_key_field: bool = False      # True → no key field at all (pure-local)
    key_hint: str = ""                # custom empty-field placeholder text
    secret: bool = True               # False: the field holds an address, shown in plain text
    signup_label: str = "Get key →"   # text on the button that opens signup_url
    shares_key_with: str = ""         # category whose same-key provider makes this row keyless


@dataclass(frozen=True)
class _ProviderCategory:
    """A row group in the dialog. ``category_key`` is the prefix of
    the provider-selection config (e.g. "LLM" → LLM_PROVIDER setting)."""

    category_key: str           # "LLM", "STT", "TTS"
    label: str                  # "LLM (vision)", etc.
    providers: tuple[_Provider, ...]
    default_index: int


# A pasted key names its own AI provider; "sk-" alone is OpenAI, checked last.
# sk-or- (OpenRouter) works with any provider: it keeps a provider the user
# chose, and otherwise picks Anthropic, the fastest through OpenRouter.
_KEY_PREFIX_PROVIDERS = (("sk-ant-", "anthropic"), ("AIza", "gemini"), ("sk-or-", None), ("sk-", "openai"))


def _default_provider_id(category: "_ProviderCategory") -> str:
    """Provider shown before the user has chosen one. For the AI model it is
    the provider there is already a key for, else the first in the list."""
    if category.category_key == "LLM":
        from config import default_llm_provider
        return default_llm_provider()
    return category.providers[category.default_index].provider_id


_PROVIDER_CATEGORIES: tuple[_ProviderCategory, ...] = (
    _ProviderCategory(
        category_key="LLM",
        label="AI model",
        providers=(
            # First, so it is the default: GPT-6.1 Sol was the fastest to its
            # first word in testing and points as accurately as the others.
            # No model sub-pickers on the cloud providers: one default each,
            # with an env var (OPENAI_MODEL_VISION, ANTHROPIC_MODEL) to override.
            _Provider(
                provider_id="openai",
                display_name="OpenAI",
                api_key_env_var="OPENAI_API_KEY",
                signup_url="https://platform.openai.com/api-keys",
                key_hint="OpenAI key (sk-...), or paste an Anthropic, Google or OpenRouter key",
            ),
            _Provider(
                provider_id="anthropic",
                display_name="Anthropic",
                api_key_env_var="ANTHROPIC_API_KEY",
                signup_url="https://console.anthropic.com/settings/keys",
                key_hint="Anthropic key (sk-ant-) or an OpenRouter key (sk-or-)",
            ),
            # GPT-Realtime is intentionally NOT in this dropdown. It's
            # an experimental speech-to-speech path with known audio issues
            # (no transcription / no playback on some setups). Still reachable
            # for advanced use via LLM_PROVIDER=openai-realtime in .env, just not
            # surfaced as a working option.
            # Local Ollama. No API key: the "API key" field stores the
            # OLLAMA_HOST URL (default http://localhost:11434) instead.
            # Repurposing the field as a host URL keeps the dialog uniform
            # (single field per provider) without adding a separate "host"
            # input row. Pixel-pointing for local vision models is handled
            # by locator.py's two-stage grid pattern (see ai.OllamaClient).
            # requires_key=False and the field is prefilled with the localhost
            # default, so the one "free, no signup" path needs zero input and
            # Save is never gated on a box that looks like an API-key box.
            _Provider(
                provider_id="ollama",
                display_name="Ollama (local)",
                api_key_env_var="OLLAMA_HOST",
                signup_url="https://ollama.com/download",
                requires_key=False,
                secret=False,
                signup_label="Get Ollama →",
                key_hint="Ollama server address (leave the default unless you moved it)",
                models=tuple(_Model(m, m) for m in _OLLAMA_MODEL_SUGGESTIONS),
                model_setting="OLLAMA_MODEL_VISION",
                models_editable=True,
            ),
            # Google Gemini. Takes a Google AI Studio key or an OpenRouter key;
            # a saved OpenRouter key from another slot is filled in for you.
            _Provider(
                provider_id="gemini",
                display_name="Google Gemini",
                api_key_env_var="GEMINI_API_KEY",
                signup_url="https://aistudio.google.com/apikey",
                key_hint="Google AI Studio key, or an OpenRouter key (sk-or-)",
            ),
        ),
        default_index=0,
    ),
    _ProviderCategory(
        category_key="STT",
        label="Listening (speech to text)",
        providers=(
            # Same key as the Cartesia voice, so a new install needs two keys.
            _Provider(
                provider_id="cartesia",
                display_name="Cartesia",
                api_key_env_var="CARTESIA_API_KEY",
                signup_url="https://play.cartesia.ai/sign-in",
                shares_key_with="TTS",
            ),
            _Provider(
                provider_id="assemblyai",
                display_name="AssemblyAI",
                api_key_env_var="ASSEMBLYAI_API_KEY",
                signup_url="https://www.assemblyai.com/dashboard/signup",
            ),
            # Local offline STT (faster-whisper). No API key; model weights
            # download on first use. requires_key=False so Save isn't gated on
            # a credential and the startup modal never forces one.
            _Provider(
                provider_id="faster-whisper",
                display_name="Local (faster-whisper)",
                api_key_env_var="FASTER_WHISPER_LOCAL",
                signup_url="https://github.com/SYSTRAN/faster-whisper",
                requires_key=False,
                hide_key_field=True,  # truly local, no key field at all
            ),
        ),
        default_index=0,
    ),
    _ProviderCategory(
        category_key="TTS",
        label="Voice (text to speech)",
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
            # Local offline TTS (Kokoro-82M). No API key; model files download
            # on first use. requires_key=False (see faster-whisper note).
            _Provider(
                provider_id="kokoro",
                display_name="Local (Kokoro)",
                api_key_env_var="KOKORO_LOCAL",
                signup_url="https://github.com/thewh1teagle/kokoro-onnx",
                requires_key=False,
                hide_key_field=True,  # truly local, no key field at all
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

    Constructor doesn't block; call ``exec()`` to show modally and
    wait for OK/Cancel. Returns ``QDialog.DialogCode.Accepted`` on
    Save, ``QDialog.DialogCode.Rejected`` on Cancel.

    Saved values land in Windows Credential Manager under service
    ``KEYRING_SERVICE`` ("clicky-windows"), one entry per env-var name.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clicky Settings")
        self.setModal(True)
        self.setMinimumWidth(520)
        # Use the tray icon as the window icon for visual consistency.
        # Path resolved via __file__ so it works inside both the dev
        # checkout (CWD = repo root) AND the bundled EXE (CWD =
        # wherever the user launched from). Plain "assets/..." would
        # be CWD-relative, which breaks in the bundled case.
        icon_path = Path(__file__).parent / "assets" / "clicky_tray.ico"
        try:
            self.setWindowIcon(QIcon(str(icon_path)))
        except Exception:
            pass  # icon missing in dev install; not critical

        self._dropdowns: dict[str, QComboBox] = {}
        self._key_inputs: dict[str, QLineEdit] = {}
        self._signup_buttons: dict[str, QPushButton] = {}
        self._share_notes: dict[str, QLabel] = {}
        # Keys typed in this dialog, by keyring slot. Two rows can show the same
        # slot (Cartesia for listening and voice), so a row loading a slot shows
        # what was typed for it, not the older saved value.
        self._typed_keys: dict[str, str] = {}
        self._loading_key = False
        # True once an AI provider was chosen (saved earlier, or picked in the
        # dropdown now). Until then a pasted OpenRouter key picks the provider.
        self._llm_chosen = False
        # Per-provider model picker. One model combo + row per category that
        # has any provider with models (in practice just LLM). The row is shown
        # only when the selected provider has models; the combo is repopulated
        # on provider change.
        self._model_combos: dict[str, QComboBox] = {}
        self._model_rows: dict[str, QWidget] = {}
        # Per-category container widgets, so the realtime provider can
        # collapse the STT + TTS rows (it does speech end-to-end).
        self._category_widgets: dict[str, QWidget] = {}
        self._realtime_note: QLabel | None = None
        self._draw_checkbox: QCheckBox | None = None
        self._build_ui()

    # ---------- UI construction -----------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # Lean privacy framing, two sentences; a multi-line splash reads as
        # too loud. On the cloud defaults, screenshots and audio go straight
        # to the providers picked below, so the second sentence says where
        # they go. Deliberately NOT phrased as a denial ("no server", "no
        # telemetry"): denying something the user never suspected reads as
        # defensive.
        privacy = QLabel(
            "🔒 Keys stored locally, encrypted by Windows Credential Manager. "
            "Your screen, your voice and Clicky's notes for the current app go to the providers you pick below."
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: gray; padding-bottom: 4px;")
        outer.addWidget(privacy)

        for category in _PROVIDER_CATEGORIES:
            category_widget = self._build_category_row(category)
            self._category_widgets[category.category_key] = category_widget
            outer.addWidget(category_widget)

        # Draw-on-screen teaching mode toggle. Single checkbox, off by
        # default. Persists ANNOTATION_MODE to keyring (config reads it). When
        # on, Clicky circles/arrows/underlines answers on screen.
        from config import resolve_setting
        self._draw_checkbox = QCheckBox(
            "✏️  Draw on screen: circle, arrow and underline the answer (teaching mode)"
        )
        self._draw_checkbox.setChecked(
            resolve_setting("ANNOTATION_MODE", "off") == "on"
        )
        outer.addWidget(self._draw_checkbox)

        self._reveal = QCheckBox("Show keys in plain text (paste-verify)")
        self._reveal.toggled.connect(self._on_reveal_toggled)
        outer.addWidget(self._reveal)

        # Apply the initial realtime collapse (if LLM provider is realtime).
        self._apply_realtime_collapse()

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
            default=_default_provider_id(category),
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
        if category.category_key == "LLM":
            self._llm_chosen = bool(resolve_setting("LLM_PROVIDER", default=""))
            dropdown.activated.connect(lambda _index: setattr(self, "_llm_chosen", True))
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
        key_input.textChanged.connect(
            lambda text, c=category: self._remember_typed_key(c, text)
        )
        self._key_inputs[category.category_key] = key_input
        v.addWidget(key_input)

        share_note = QLabel()
        share_note.setStyleSheet("color: gray;")
        share_note.setVisible(False)
        self._share_notes[category.category_key] = share_note
        v.addWidget(share_note)

        # Pre-populate the key field with masked existing value (if any).
        self._refresh_key_field_for_category(category)

        # Per-provider model picker row. Built if ANY provider in this
        # category has models; shown only when the selected provider has
        # models. Populated for the current provider.
        if any(p.models for p in category.providers):
            model_row = self._build_model_row(category)
            v.addWidget(model_row)
            self._model_rows[category.category_key] = model_row
            current_provider = category.providers[selected_index]
            self._populate_model_combo(category, current_provider)
            model_row.setVisible(bool(current_provider.models))

        # Realtime note, shown under the LLM row when the realtime
        # provider is selected (it collapses STT+TTS; tell the user why).
        if category.category_key == "LLM":
            note = QLabel(
                "⚡ Realtime handles speech end-to-end (lowest latency). "
                "STT + TTS aren't used in this mode."
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: #2563eb; padding-top: 2px;")
            v.addWidget(note)
            self._realtime_note = note
            current_provider = category.providers[selected_index]
            note.setVisible(current_provider.hides_other_categories)

        return container

    def _build_model_row(self, category: _ProviderCategory) -> QWidget:
        """Build the per-provider 'Model:' combobox row. The combo is
        (re)populated for the selected provider by _populate_model_combo.
        Stored in _model_combos."""
        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 4, 0, 0)
        h.addWidget(QLabel("Model:"))
        combo = QComboBox()
        h.addWidget(combo, stretch=1)
        self._model_combos[category.category_key] = combo
        return container

    def _populate_model_combo(
        self, category: _ProviderCategory, provider: _Provider
    ) -> None:
        """Fill the category's model combo with the provider's models and
        select the stored choice (resolve_setting on provider.model_setting,
        default = the provider's first model). Editable for Ollama (custom)."""
        from config import resolve_setting

        combo = self._model_combos.get(category.category_key)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.setEditable(provider.models_editable)
        for m in provider.models:
            combo.addItem(m.display_name, m.model_id)
        if provider.models and provider.model_setting:
            default_id = provider.models[0].model_id
            stored = resolve_setting(provider.model_setting, default_id)
            idx = combo.findData(stored)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            elif provider.models_editable:
                combo.addItem(stored, stored)
                combo.setCurrentText(stored)
            else:
                combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _selected_model_id(self, category: _ProviderCategory, provider: _Provider) -> str:
        """The chosen model id for a provider. Editable combos (Ollama) use the
        typed text; fixed combos use the selected item's data (the bare id)."""
        combo = self._model_combos.get(category.category_key)
        if combo is None:
            return ""
        if provider.models_editable:
            return combo.currentText().strip()
        data = combo.currentData()
        return data if data else combo.currentText().strip()

    def _selected_requires_key(self, category_key: str) -> bool:
        """True if the category's currently-selected provider needs an API key.
        Local providers (faster-whisper / kokoro) return False."""
        dropdown = self._dropdowns.get(category_key)
        category = next(
            (c for c in _PROVIDER_CATEGORIES if c.category_key == category_key), None
        )
        if dropdown is None or category is None:
            return True
        if self._shares_key(category_key):
            return False  # the row it shares with gates Save
        return category.providers[dropdown.currentIndex()].requires_key

    def _collapsed_categories(self) -> set[str]:
        """Categories collapsed because the selected LLM provider does speech
        end-to-end (realtime). Returns {"STT","TTS"} or an empty set."""
        llm_dropdown = self._dropdowns.get("LLM")
        if llm_dropdown is None:
            return set()
        llm_cat = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        provider = llm_cat.providers[llm_dropdown.currentIndex()]
        return {"STT", "TTS"} if provider.hides_other_categories else set()

    def _apply_realtime_collapse(self) -> None:
        """Hide/show the STT+TTS rows + the realtime note based on the current
        LLM provider. Called on construction + on LLM provider change."""
        collapsed = self._collapsed_categories()
        for key in ("STT", "TTS"):
            widget = self._category_widgets.get(key)
            if widget is not None:
                widget.setVisible(key not in collapsed)
        if self._realtime_note is not None:
            self._realtime_note.setVisible(bool(collapsed))

    def _cached_openrouter_key(self) -> str:
        """An sk-or- OpenRouter key already saved for any LLM provider slot.
        One OpenRouter key serves all LLM providers, so reuse it (cache +
        reuse) instead of making the user re-enter it per provider."""
        for slot in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
            k = keyring.get_password(KEYRING_SERVICE, slot) or ""
            if k.startswith("sk-or-"):
                return k
        return ""

    def _refresh_key_field_for_category(self, category: _ProviderCategory) -> None:
        """Read the keyring slot for the dropdown's currently-selected
        provider, set the key field's text + placeholder accordingly.
        Called on dialog construction AND on dropdown change."""
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        existing = self._typed_keys.get(provider.api_key_env_var) or (
            keyring.get_password(KEYRING_SERVICE, provider.api_key_env_var) or ""
        )
        # Cache + reuse: one OpenRouter (sk-or-) key works for every LLM
        # provider. If this provider's own slot is empty but you pasted an
        # OpenRouter key for another LLM provider, reuse it here so you don't
        # re-enter it and Save isn't gated on an empty field. The _API_KEY
        # filter skips Ollama (its field holds OLLAMA_HOST, not a key).
        if (not existing and category.category_key == "LLM"
                and provider.api_key_env_var.endswith("_API_KEY")):
            existing = self._cached_openrouter_key()
        # Ollama's field holds a server address, not a key. Prefill the
        # documented default so the no-signup path needs zero typing.
        if not existing and provider.provider_id == "ollama":
            existing = OLLAMA_HOST
        key_input = self._key_inputs[category.category_key]
        self._loading_key = True
        try:
            key_input.setText(existing)
        finally:
            self._loading_key = False
        if existing:
            placeholder = _mask(existing)
        elif provider.key_hint:
            placeholder = provider.key_hint
        else:
            placeholder = f"Your {provider.display_name} key"
        key_input.setPlaceholderText(placeholder)
        # Pure-local providers (faster-whisper / kokoro) need no key, so hide the
        # key field + Get-key button entirely. Everything else shows the field
        # (Gemini shows an OPTIONAL field: blank reuses the OpenRouter key).
        key_input.setVisible(not provider.hide_key_field)
        self._signup_buttons[category.category_key].setVisible(not provider.hide_key_field)
        self._signup_buttons[category.category_key].setText(provider.signup_label)
        self._apply_key_sharing()
        revealed = self._reveal.isChecked() if hasattr(self, "_reveal") else False
        key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if revealed or not provider.secret
            else QLineEdit.EchoMode.Password
        )

    def _remember_typed_key(self, category: _ProviderCategory, text: str) -> None:
        if self._loading_key or category.category_key not in self._dropdowns:
            return
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        self._typed_keys[provider.api_key_env_var] = text
        if category.category_key != "LLM" or len(text.strip()) < 20:
            return  # only a whole pasted key, not one being typed
        match = next((pid for prefix, pid in _KEY_PREFIX_PROVIDERS if text.strip().startswith(prefix)), None)
        if text.strip().startswith("sk-or-") and not self._llm_chosen:
            match = "anthropic"  # through OpenRouter, Haiku answers first (2.8 s vs 4.6-6.6 s for Sol)
        target = next((p for p in category.providers if p.provider_id == match), None)
        if target is None or target is provider:
            return
        # The key belongs to another provider: move it there and select it.
        self._typed_keys.pop(provider.api_key_env_var, None)
        self._typed_keys[target.api_key_env_var] = text
        dropdown.setCurrentIndex(category.providers.index(target))

    # ---------- Slots ----------------------------------------------------

    def _on_provider_changed(self, category: _ProviderCategory, _index: int) -> None:
        """Dropdown changed: swap the key field to the newly-selected
        provider's stored key, repopulate + show/hide the model row, and (for
        the LLM category) collapse STT+TTS when realtime is selected."""
        self._refresh_key_field_for_category(category)
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]

        # Repopulate + show/hide the per-provider model row.
        model_row = self._model_rows.get(category.category_key)
        if model_row is not None:
            self._populate_model_combo(category, provider)
            model_row.setVisible(bool(provider.models))

        # LLM realtime collapse (hide STT+TTS rows + show the note).
        if category.category_key == "LLM":
            self._apply_realtime_collapse()

        self._update_save_enabled()

    def _on_signup_clicked(self, category: _ProviderCategory) -> None:
        """User clicked 'Get key →': open selected provider's signup URL
        in default browser via QDesktopServices."""
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        QDesktopServices.openUrl(QUrl(provider.signup_url))

    def _selected_provider(self, category_key: str) -> _Provider | None:
        dropdown = self._dropdowns.get(category_key)
        if dropdown is None:
            return None
        category = next(c for c in _PROVIDER_CATEGORIES if c.category_key == category_key)
        return category.providers[dropdown.currentIndex()]

    def _shares_key(self, category_key: str) -> bool:
        """True when this row's provider uses the same key slot as the provider
        picked in the row it shares with, so that row's key covers both."""
        provider = self._selected_provider(category_key)
        if provider is None or not provider.shares_key_with:
            return False
        other = self._selected_provider(provider.shares_key_with)
        return other is not None and other.api_key_env_var == provider.api_key_env_var

    def _apply_key_sharing(self) -> None:
        """Hide a row's key box when another row's key covers it. A box that
        comes back into view shows the latest key typed for its slot, so an
        edit made while it was hidden is the one that gets saved."""
        for key, note in self._share_notes.items():
            provider = self._selected_provider(key)
            if provider is None or key not in self._key_inputs:
                continue
            shared = self._shares_key(key)
            typed = self._typed_keys.get(provider.api_key_env_var)
            if not shared and typed and self._key_inputs[key].text() != typed:
                self._loading_key = True
                try:
                    self._key_inputs[key].setText(typed)
                finally:
                    self._loading_key = False
            self._key_inputs[key].setVisible(not shared and not provider.hide_key_field)
            self._signup_buttons[key].setVisible(not shared and not provider.hide_key_field)
            note.setText(f"Uses your {provider.display_name} key below." if shared else "")
            note.setVisible(shared)
        self._update_save_enabled()

    def _on_reveal_toggled(self, checked: bool) -> None:
        for category in _PROVIDER_CATEGORIES:
            key_input = self._key_inputs.get(category.category_key)
            if key_input is None:
                continue
            provider = category.providers[self._dropdowns[category.category_key].currentIndex()]
            key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked or not provider.secret
                else QLineEdit.EchoMode.Password
            )

    def _update_save_enabled(self) -> None:
        """Save enabled when every category's key field has non-empty content.

        Defensively no-op if self._buttons isn't constructed yet. The
        textChanged signal can fire during initial _build_category_row
        (when the keyring already has a key, setText(existing) fires
        before the QDialogButtonBox is added to the dialog at the end of
        _build_ui).
        """
        if not hasattr(self, "_buttons"):
            return
        # Skip categories collapsed by realtime (STT+TTS aren't used /
        # editable in realtime mode, so don't gate Save on their key fields).
        collapsed = self._collapsed_categories()
        all_filled = all(
            key_input.text().strip()
            for key, key_input in self._key_inputs.items()
            if key not in collapsed and self._selected_requires_key(key)
        )
        self._buttons.button(
            QDialogButtonBox.StandardButton.Save
        ).setEnabled(all_filled)

    def _on_save(self) -> None:
        """Persist provider selection + currently-selected provider's key
        for each category to keyring.

        If the user picks Ollama + a model that needs a newer Ollama version
        than they have (Issue #1), show a non-blocking warning BEFORE
        persisting. User can override and save anyway, or cancel.
        Compatibility check runs against a live ``/api/version`` ping. If
        Ollama is unreachable we skip the check entirely (don't conflate
        "Ollama down" with "incompatible model").
        """
        # Pre-save compatibility check for Ollama LLM.
        llm_category = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        llm_dropdown = self._dropdowns["LLM"]
        llm_provider = llm_category.providers[llm_dropdown.currentIndex()]
        if llm_provider.provider_id == "ollama":
            model = self._selected_model_id(llm_category, llm_provider)
            if model and not self._confirm_ollama_compat(model):
                return  # user cancelled: abort save, no writes

        for category in _PROVIDER_CATEGORIES:
            dropdown = self._dropdowns[category.category_key]
            provider = category.providers[dropdown.currentIndex()]

            # 1. Persist provider selection (e.g. "TTS_PROVIDER" → "elevenlabs")
            keyring.set_password(
                KEYRING_SERVICE,
                f"{category.category_key}_PROVIDER",
                provider.provider_id,
            )

            # 2. Persist the API key for the selected provider. A row whose key
            # is covered by another row writes nothing; that row saves it.
            key_value = "" if self._shares_key(category.category_key) else (
                self._key_inputs[category.category_key].text().strip()
            )
            if key_value:
                keyring.set_password(
                    KEYRING_SERVICE, provider.api_key_env_var, key_value,
                )

            # 3. Persist the chosen model for providers with a model picker
            # (Ollama→OLLAMA_MODEL_VISION). Only the selected provider's model
            # combo is live, so we only persist that one.
            if provider.models and provider.model_setting:
                model_id = self._selected_model_id(category, provider)
                if model_id:
                    keyring.set_password(
                        KEYRING_SERVICE, provider.model_setting, model_id,
                    )

        # Persist the draw-on-screen toggle.
        if self._draw_checkbox is not None:
            keyring.set_password(
                KEYRING_SERVICE,
                "ANNOTATION_MODE",
                "on" if self._draw_checkbox.isChecked() else "off",
            )
        self.accept()

    def _confirm_ollama_compat(self, model: str) -> bool:
        """Pre-save Ollama compatibility check (Issue #1).

        Returns True if the save should proceed, False if the user
        cancelled. Pings the user's Ollama server for its version,
        checks against the known mllama-supports-from table. Shows a
        QMessageBox warning ONLY if there's a confirmed incompatibility;
        silent on success or when Ollama is unreachable.
        """
        from ollama_health import check_model_compatibility, detect_ollama_version

        host = self._key_inputs["LLM"].text().strip() or OLLAMA_HOST
        ollama_version = detect_ollama_version(host)
        warning = check_model_compatibility(model, ollama_version)
        if warning is None:
            return True  # compatible OR can't check: proceed silently

        reply = QMessageBox.warning(
            self,
            "Ollama compatibility warning",
            warning + "\n\nSave anyway?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Save


def required_keys_present() -> bool:
    """Probe: does every required-provider's API key resolve?

    "Required" = the currently-SELECTED provider per category
    (resolved via resolve_setting on LLM_PROVIDER / STT_PROVIDER /
    TTS_PROVIDER). The probe is what the launcher uses to decide whether
    to show the modal at start.

    A brain (LLM) provider counts as ready with its own key or with an
    OpenRouter key saved in any brain slot, the same rule the app uses.
    """
    from config import resolve_api_key, resolve_setting

    def _selected(category: _ProviderCategory) -> _Provider:
        provider_id = resolve_setting(
            f"{category.category_key}_PROVIDER",
            default=_default_provider_id(category),
        )
        return next(
            (p for p in category.providers if p.provider_id == provider_id),
            category.providers[category.default_index],
        )

    # If the selected LLM provider does speech end-to-end (realtime),
    # the STT + TTS keys aren't required: realtime never uses them.
    llm_category = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
    realtime = _selected(llm_category).hides_other_categories

    for category in _PROVIDER_CATEGORIES:
        if realtime and category.category_key in ("STT", "TTS"):
            continue
        provider = _selected(category)
        if not provider.requires_key:
            continue  # local provider (Ollama / faster-whisper / kokoro): no key needed
        key = resolve_api_key(provider.api_key_env_var)
        if not key and category.category_key == "LLM":
            key = next(
                (k for k in (resolve_api_key(p.api_key_env_var) for p in category.providers if p.requires_key)
                 if k and k.startswith("sk-or-")),
                None,
            )
        if not key:
            return False
    return True
