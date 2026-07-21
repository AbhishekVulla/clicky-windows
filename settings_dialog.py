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
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import KEYRING_SERVICE, OLLAMA_HOST


# v0.2.1 (Issue #1 fix B): pre-populated Ollama vision model suggestions
# in the dropdown. `llava:7b` first since it's the new default (works
# on all Ollama versions with vision). User can also type a custom
# model name — the combobox is editable.
_OLLAMA_MODEL_SUGGESTIONS: tuple[str, ...] = (
    "llava:7b",
    "llama3.2-vision",
    "qwen2.5-vl",
    "llava-llama3",
)


# --- Sprint 4: provider category data model ---------------------------------
#
# Drives 3-row progressive-disclosure UX in the dialog: pick provider per
# category (LLM/STT/TTS) from a dropdown, only that provider's API key field
# is visible. Fixes the previous flat 3-required-field layout that would
# have grown to 6 fields with ElevenLabs (and 7+ with Deepgram).


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
    convention — see config.resolve_api_key).

    v0.3.0: optional per-provider model picker. ``models`` (if non-empty)
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
                key_hint="Anthropic key (sk-ant-) or an OpenRouter key (sk-or-)",
                # No model sub-picker (v0.4.1 minimal UX): defaults to Claude
                # Sonnet 4.6, the best all-round default. Opus 4.8 stays reachable
                # via the ANTHROPIC_MODEL env var for the rare power user.
            ),
            # v0.3.0: OpenAI native vision. Direct sk-... key (NOT the OpenRouter
            # sk-or- key). Default gpt-5.4 is pixel-accurate (grid-locator
            # auto-skips); gpt-4o kept as a cheaper/weaker fallback option.
            _Provider(
                provider_id="openai",
                display_name="OpenAI",
                api_key_env_var="OPENAI_API_KEY",
                signup_url="https://platform.openai.com/api-keys",
                key_hint="OpenAI key (sk-...) or an OpenRouter key (sk-or-)",
                # No model sub-picker (v0.4.1 minimal UX): defaults to gpt-5.4
                # (pixel-accurate). gpt-4o is weaker and a footgun as a visible
                # choice; reachable via the OPENAI_MODEL_VISION env var if needed.
            ),
            # GPT-Realtime is intentionally NOT in this dropdown (v0.4.1). It's
            # an experimental speech-to-speech path with known audio issues
            # (no transcription / no playback on some setups). Still reachable
            # for advanced use via LLM_PROVIDER=openai-realtime in .env, just not
            # surfaced as a working option.
            # v0.2.0: Local Ollama. No API key — instead the "API key" field
            # stores the OLLAMA_HOST URL (default http://localhost:11434).
            # Repurposing the field as a host URL keeps the dialog uniform
            # (single field per provider) without adding a separate "host"
            # input row. Pixel-pointing for local vision models is handled
            # by locator.py's two-stage grid pattern (see ai.OllamaClient).
            # requires_key=False (2026-07-21): Ollama needs no key, but it was
            # inheriting the True default, so Save stayed disabled until the
            # user typed a URL into a box that looks exactly like every other
            # API-key box. That made the one "free, no signup" path feel like
            # it needed a signup. The field is prefilled with the documented
            # localhost default below, so it's now genuinely zero-input.
            _Provider(
                provider_id="ollama",
                display_name="Ollama (local)",
                api_key_env_var="OLLAMA_HOST",
                signup_url="https://ollama.com/download",
                requires_key=False,
                key_hint="Ollama server address (leave the default unless you moved it)",
                models=tuple(_Model(m, m) for m in _OLLAMA_MODEL_SUGGESTIONS),
                model_setting="OLLAMA_MODEL_VISION",
                models_editable=True,
            ),
            # Google Gemini via OpenRouter. ONE option, no model sub-picker
            # (minimal UX) — defaults to 3.1 Pro (most pixel-accurate Gemini).
            # requires_key=False so Save isn't gated: leave the field BLANK and
            # it reuses your existing OpenRouter (sk-or-) key from the Anthropic
            # slot (see app._resolve_llm_credentials gemini branch). The field is
            # still SHOWN (hide_key_field stays False) so a user who wants a
            # separate OpenRouter key for Gemini can paste one.
            _Provider(
                provider_id="gemini",
                display_name="Google Gemini",
                api_key_env_var="GEMINI_API_KEY",
                signup_url="https://aistudio.google.com/apikey",
                requires_key=False,
                key_hint="Google AI Studio key, or an OpenRouter key (sk-or-); blank reuses your OpenRouter key",
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
            # Local offline STT (faster-whisper). No API key; model weights
            # download on first use. requires_key=False so Save isn't gated on
            # a credential and the startup modal never forces one.
            _Provider(
                provider_id="faster-whisper",
                display_name="Local (faster-whisper)",
                api_key_env_var="FASTER_WHISPER_LOCAL",
                signup_url="https://github.com/SYSTRAN/faster-whisper",
                requires_key=False,
                hide_key_field=True,  # truly local — no key field at all
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
            # Local offline TTS (Kokoro-82M). No API key; model files download
            # on first use. requires_key=False (see faster-whisper note).
            _Provider(
                provider_id="kokoro",
                display_name="Local (Kokoro)",
                api_key_env_var="KOKORO_LOCAL",
                signup_url="https://github.com/thewh1teagle/kokoro-onnx",
                requires_key=False,
                hide_key_field=True,  # truly local — no key field at all
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
        # v0.3.0: generic per-provider model picker (generalized from the
        # v0.2.1 Ollama-only row). One model combo + row per category that has
        # any provider with models (in practice just LLM). The row is shown
        # only when the selected provider has models; the combo is repopulated
        # on provider change.
        self._model_combos: dict[str, QComboBox] = {}
        self._model_rows: dict[str, QWidget] = {}
        # v0.3.0: per-category container widgets, so the realtime provider can
        # collapse the STT + TTS rows (it does speech end-to-end).
        self._category_widgets: dict[str, QWidget] = {}
        self._realtime_note: QLabel | None = None
        self._draw_checkbox: QCheckBox | None = None
        self._build_ui()

    # ---------- UI construction -----------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # Lean privacy framing — two sentences (USER decision 2026-05-06,
        # rejected the multi-line splash version as too loud / suspicious).
        # Wording revised 2026-05-07 from "No server, no telemetry." after
        # USER feedback that "telemetry" is jargon for non-tech users.
        # Revised again 2026-07-21: the old second sentence ("Nothing leaves
        # your machine.") was FALSE on the cloud defaults — screenshots and
        # audio do leave, straight to the providers picked below. Replaced
        # with a plain statement of where they go. Deliberately NOT phrased
        # as a denial ("no server", "no telemetry"): denying something the
        # user never suspected reads as defensive, and the lean one-sentence
        # framing was the original 2026-05-06 call for exactly that reason.
        privacy = QLabel(
            "🔒 Keys stored locally, encrypted by Windows Credential Manager. "
            "Your screen and voice go to the providers you pick below."
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: gray; padding-bottom: 4px;")
        outer.addWidget(privacy)

        for category in _PROVIDER_CATEGORIES:
            category_widget = self._build_category_row(category)
            self._category_widgets[category.category_key] = category_widget
            outer.addWidget(category_widget)

        # v0.3.0: draw-on-screen teaching mode toggle. Single checkbox, off by
        # default. Persists ANNOTATION_MODE to keyring (config reads it). When
        # on, Clicky circles/arrows/underlines answers on screen.
        from config import resolve_setting
        self._draw_checkbox = QCheckBox(
            "✏️  Draw on screen — circle, arrow + underline the answer (teaching mode)"
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

        # v0.3.0: generic per-provider model picker row (generalized from the
        # v0.2.1 Ollama-only row). Built if ANY provider in this category has
        # models; shown only when the selected provider has models. Populated
        # for the current provider.
        if any(p.models for p in category.providers):
            model_row = self._build_model_row(category)
            v.addWidget(model_row)
            self._model_rows[category.category_key] = model_row
            current_provider = category.providers[selected_index]
            self._populate_model_combo(category, current_provider)
            model_row.setVisible(bool(current_provider.models))

        # v0.3.0: realtime note — shown under the LLM row when the realtime
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
        """Build the per-provider 'Model:' combobox row (v0.3.0, generalized
        from the v0.2.1 Ollama-only row). The combo is (re)populated for the
        selected provider by _populate_model_combo. Stored in _model_combos."""
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
        existing = keyring.get_password(KEYRING_SERVICE, provider.api_key_env_var) or ""
        # v0.4.x cache + reuse: one OpenRouter (sk-or-) key works for every LLM
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
        key_input.setText(existing)
        if existing:
            placeholder = _mask(existing)
        elif provider.key_hint:
            placeholder = provider.key_hint
        else:
            placeholder = f"paste {provider.api_key_env_var} here"
        key_input.setPlaceholderText(placeholder)
        # Pure-local providers (faster-whisper / kokoro) need no key — hide the
        # key field + Get-key button entirely. Everything else shows the field
        # (Gemini shows an OPTIONAL field: blank reuses the OpenRouter key).
        key_input.setVisible(not provider.hide_key_field)
        self._signup_buttons[category.category_key].setVisible(not provider.hide_key_field)

    # ---------- Slots ----------------------------------------------------

    def _on_provider_changed(self, category: _ProviderCategory, _index: int) -> None:
        """Dropdown changed — swap the key field to the newly-selected
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
        # v0.3.0: skip categories collapsed by realtime (STT+TTS aren't used /
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

        v0.2.1 (Issue #1 fix D): if user picks Ollama + a model that needs
        a newer Ollama version than they have, show a non-blocking warning
        BEFORE persisting. User can override and save anyway, or cancel.
        Compatibility check runs against live ``/api/version`` ping — if
        Ollama is unreachable we skip the check entirely (don't conflate
        "Ollama down" with "incompatible model").
        """
        # v0.2.1 fix D: pre-save compatibility check for Ollama LLM.
        llm_category = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        llm_dropdown = self._dropdowns["LLM"]
        llm_provider = llm_category.providers[llm_dropdown.currentIndex()]
        if llm_provider.provider_id == "ollama":
            model = self._selected_model_id(llm_category, llm_provider)
            if model and not self._confirm_ollama_compat(model):
                return  # user cancelled — abort save, no writes

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

            # 3. v0.3.0: persist the chosen model for providers with a model
            # picker (Anthropic→ANTHROPIC_MODEL, OpenAI→OPENAI_MODEL_VISION,
            # Ollama→OLLAMA_MODEL_VISION). Only the selected provider's model
            # combo is live, so we only persist that one.
            if provider.models and provider.model_setting:
                model_id = self._selected_model_id(category, provider)
                if model_id:
                    keyring.set_password(
                        KEYRING_SERVICE, provider.model_setting, model_id,
                    )

        # v0.3.0: persist the draw-on-screen toggle.
        if self._draw_checkbox is not None:
            keyring.set_password(
                KEYRING_SERVICE,
                "ANNOTATION_MODE",
                "on" if self._draw_checkbox.isChecked() else "off",
            )
        self.accept()

    def _confirm_ollama_compat(self, model: str) -> bool:
        """Pre-save Ollama compatibility check (v0.2.1 fix D).

        Returns True if the save should proceed, False if the user
        cancelled. Pings the user's Ollama server for its version,
        checks against the known mllama-supports-from table. Shows a
        QMessageBox warning ONLY if there's a confirmed incompatibility
        — silent on success or when Ollama is unreachable.
        """
        from config import resolve_setting
        from ollama_health import check_model_compatibility, detect_ollama_version

        host = resolve_setting("OLLAMA_HOST", "http://localhost:11434")
        ollama_version = detect_ollama_version(host)
        warning = check_model_compatibility(model, ollama_version)
        if warning is None:
            return True  # compatible OR can't check — proceed silently

        reply = QMessageBox.warning(
            self,
            "Ollama compatibility warning",
            warning + "\n\nSave anyway?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Save


def required_keys_present() -> bool:
    """Probe — does every required-provider's API key resolve?

    Sprint 4: "required" = the currently-SELECTED provider per category
    (resolved via resolve_setting on LLM_PROVIDER / STT_PROVIDER /
    TTS_PROVIDER). The probe is what the launcher uses to decide whether
    to show the modal at start.

    v0.2.0: special-case for OLLAMA_HOST — it's a config setting with a
    working default (http://localhost:11434), NOT an API key the user
    must provide. If the selected LLM provider is Ollama, this probe
    treats OLLAMA_HOST as always-present (because the default works
    out-of-the-box when Ollama is running locally). Without this
    special-case, picking Ollama in the Settings dropdown would force
    the user back into the first-launch modal forever even though they
    don't need any actual credential.
    """
    from config import resolve_api_key, resolve_setting

    def _selected(category: _ProviderCategory) -> _Provider:
        provider_id = resolve_setting(
            f"{category.category_key}_PROVIDER",
            default=category.providers[category.default_index].provider_id,
        )
        return next(
            (p for p in category.providers if p.provider_id == provider_id),
            category.providers[category.default_index],
        )

    # v0.3.0: if the selected LLM provider does speech end-to-end (realtime),
    # the STT + TTS keys aren't required — realtime never uses them.
    llm_category = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
    realtime = _selected(llm_category).hides_other_categories

    for category in _PROVIDER_CATEGORIES:
        if realtime and category.category_key in ("STT", "TTS"):
            continue
        provider = _selected(category)
        # v0.2.0: OLLAMA_HOST is a config knob with a working default, not
        # a credential the user must supply. config.OLLAMA_HOST always
        # resolves to at least "http://localhost:11434" via resolve_setting,
        # so consider it always-present from the launcher's perspective.
        if provider.api_key_env_var == "OLLAMA_HOST":
            continue
        if not provider.requires_key:
            continue  # local provider (faster-whisper / kokoro) — no key needed
        if not resolve_api_key(provider.api_key_env_var):
            return False
    return True
