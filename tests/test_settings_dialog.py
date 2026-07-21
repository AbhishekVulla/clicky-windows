"""Unit tests for settings_dialog — required_keys_present probe + mask
helper. The full dialog rendering is verified manually (PyQt6 modal
exec needs a real Qt event loop)."""
from __future__ import annotations

import pytest


def _dropdown_index(dropdown, provider_id: str) -> int:
    """Find a provider's index in a dropdown by its id, so tests don't hardcode
    positions that shift when providers are added or reordered."""
    for i in range(dropdown.count()):
        if dropdown.itemData(i) == provider_id:
            return i
    raise AssertionError(f"{provider_id!r} not found in dropdown")


# --- _mask helper ------------------------------------------------------------

class TestMask:
    """_mask shows last-4-chars + bullets for existing keys without
    leaking the full secret on screen. Empty input → empty string."""

    def test_empty_input_returns_empty_string(self):
        from settings_dialog import _mask
        assert _mask("") == ""
        assert _mask(None) == ""

    def test_short_value_fully_masked(self):
        """<=8 chars → all bullets (any reveal would be too much)."""
        from settings_dialog import _mask
        assert _mask("abc") == "***"
        assert _mask("12345678") == "********"

    def test_typical_key_shows_first_5_and_last_4(self):
        """Long values: first-5 + 6 bullets + last-4 (preview-without-leak)."""
        from settings_dialog import _mask
        masked = _mask("sk-ant-abcdefghijklmnopqrstuvwxyz1234")
        assert masked.startswith("sk-an")
        assert masked.endswith("1234")
        assert "*" in masked


# --- required_keys_present probe --------------------------------------------

class TestRequiredKeysPresent:
    """The probe used by app.py main to decide whether to show the
    first-launch dialog. All 3 keys must resolve (env or keyring) for
    the app to start without prompting."""

    @pytest.fixture
    def fake_keyring(self, monkeypatch):
        store: dict[tuple[str, str], str] = {}
        import config
        monkeypatch.setattr(
            config.keyring,
            "get_password",
            lambda s, n: store.get((s, n)),
        )
        monkeypatch.setattr(
            config.keyring,
            "set_password",
            lambda s, n, v: store.update({(s, n): v}),
        )
        yield store

    def test_all_three_present_in_env_returns_true(
        self, monkeypatch, fake_keyring
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
        monkeypatch.setenv("ASSEMBLYAI_API_KEY", "b")
        monkeypatch.setenv("CARTESIA_API_KEY", "c")
        from settings_dialog import required_keys_present
        assert required_keys_present() is True

    def test_one_missing_returns_false(self, monkeypatch, fake_keyring):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
        monkeypatch.setenv("ASSEMBLYAI_API_KEY", "b")
        monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
        from settings_dialog import required_keys_present
        assert required_keys_present() is False

    def test_all_in_keyring_no_env_returns_true(
        self, monkeypatch, fake_keyring
    ):
        """Post-migration steady state: env empty, keyring full."""
        for k in ("ANTHROPIC_API_KEY", "ASSEMBLYAI_API_KEY", "CARTESIA_API_KEY"):
            monkeypatch.delenv(k, raising=False)
            fake_keyring[("clicky-windows", k)] = "stored"
        from settings_dialog import required_keys_present
        assert required_keys_present() is True

    def test_none_anywhere_returns_false(self, monkeypatch, fake_keyring):
        """First-launch state: no env, empty keyring → modal must show."""
        for k in ("ANTHROPIC_API_KEY", "ASSEMBLYAI_API_KEY", "CARTESIA_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        from settings_dialog import required_keys_present
        assert required_keys_present() is False


# --- Sprint 4: provider category data model ---------------------------------


class TestProviderCategoriesData:
    """The _PROVIDER_CATEGORIES data drives dialog rendering. Each
    category has: a label, a list of provider options, a default
    provider key, the keyring slot prefix (env-var name root). Each
    provider has: display name, env-var name (= keyring slot), signup URL."""

    def test_three_categories_in_correct_order(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        assert [c.category_key for c in _PROVIDER_CATEGORIES] == ["LLM", "STT", "TTS"]

    def test_llm_category_has_expected_providers(self):
        """The LLM category includes anthropic, openai, ollama, gemini (Realtime
        removed). Order-independent membership so adding/reordering a provider
        doesn't break this; the load-bearing fact is that Anthropic is default."""
        from settings_dialog import _PROVIDER_CATEGORIES
        llm = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        ids = {p.provider_id for p in llm.providers}
        assert {"anthropic", "openai", "ollama", "gemini"} <= ids
        assert "openai-realtime" not in ids  # hidden from the dropdown
        assert llm.providers[llm.default_index].provider_id == "anthropic"

    def test_openai_llm_provider_uses_openai_api_key_slot(self):
        """v0.3.0: OpenAI provider stores a direct OPENAI_API_KEY (NOT the
        OpenRouter sk-or- key in ANTHROPIC_API_KEY)."""
        from settings_dialog import _PROVIDER_CATEGORIES
        llm = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        openai = next(p for p in llm.providers if p.provider_id == "openai")
        assert openai.api_key_env_var == "OPENAI_API_KEY"
        assert openai.display_name == "OpenAI"
        assert "platform.openai.com" in openai.signup_url

    def test_realtime_not_in_dropdown(self):
        """v0.4.1: GPT-Realtime removed from the Settings dropdown (experimental,
        audio issues on some setups). Still reachable via the env var
        LLM_PROVIDER=openai-realtime, just not surfaced as a working option."""
        from settings_dialog import _PROVIDER_CATEGORIES
        llm = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        assert "openai-realtime" not in [p.provider_id for p in llm.providers]

    def test_anthropic_provider_has_no_model_picker(self):
        """v0.4.1 minimal UX: Anthropic has no model sub-picker — it defaults to
        Claude Sonnet 4.6 (pixel-tight). Opus 4.8 reachable via ANTHROPIC_MODEL
        env for the rare power user, but not a visible, overwhelming choice."""
        from settings_dialog import _PROVIDER_CATEGORIES
        llm = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        ant = next(p for p in llm.providers if p.provider_id == "anthropic")
        assert ant.models == ()
        assert ant.model_setting == ""

    def test_ollama_llm_provider_has_host_field_not_api_key(self):
        """Ollama is special: its 'api_key_env_var' slot stores OLLAMA_HOST
        (the local server URL) instead of an API key. Default in config.py
        points at http://localhost:11434 (Ollama's default binding)."""
        from settings_dialog import _PROVIDER_CATEGORIES
        llm = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        ollama = next(p for p in llm.providers if p.provider_id == "ollama")
        assert ollama.api_key_env_var == "OLLAMA_HOST"
        assert ollama.display_name == "Ollama (local)"
        assert "ollama.com" in ollama.signup_url

    def test_stt_category_has_assemblyai_and_faster_whisper(self):
        """Default AssemblyAI (cloud, index 0) + opt-in local faster-whisper."""
        from settings_dialog import _PROVIDER_CATEGORIES
        stt = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "STT")
        assert [p.provider_id for p in stt.providers] == [
            "assemblyai", "faster-whisper",
        ]
        assert stt.default_index == 0  # AssemblyAI (cloud) stays default

    def test_tts_category_has_cartesia_and_elevenlabs(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        tts = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "TTS")
        assert [p.provider_id for p in tts.providers] == [
            "cartesia", "elevenlabs", "kokoro",
        ]
        assert tts.default_index == 0  # Cartesia default

    def test_each_provider_has_env_var_and_signup_url(self):
        """Every provider has a non-empty display name + keyring slot + signup
        URL. The slot is _API_KEY suffix for cloud providers, OLLAMA_HOST for
        Ollama (no API key — local server, slot stores the host URL)."""
        from settings_dialog import _PROVIDER_CATEGORIES
        for category in _PROVIDER_CATEGORIES:
            for provider in category.providers:
                # Cloud providers use an _API_KEY slot; Ollama reuses the field
                # for OLLAMA_HOST; local providers (requires_key=False) need no
                # key so their slot is a benign unused placeholder.
                assert (
                    provider.api_key_env_var.endswith("_API_KEY")
                    or provider.api_key_env_var == "OLLAMA_HOST"
                    or not provider.requires_key
                ), f"{provider.provider_id!r} has unexpected slot {provider.api_key_env_var!r}"
                assert provider.signup_url.startswith("https://")
                assert provider.display_name  # non-empty

    def test_gemini_provider_data(self):
        """v0.4.1: Gemini is ONE keyless option (no model sub-picker — minimal
        UX). It reuses the user's OpenRouter (sk-or-) key at runtime, so
        requires_key=False and no separate key field renders. The default model
        (3.1 Pro, most accurate) lives in config.GEMINI_MODEL_VISION."""
        from settings_dialog import _PROVIDER_CATEGORIES
        llm = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        gem = next(p for p in llm.providers if p.provider_id == "gemini")
        assert gem.api_key_env_var == "GEMINI_API_KEY"
        assert gem.models == ()
        assert gem.requires_key is False

    def test_local_providers_require_no_key(self):
        """faster-whisper + kokoro are local (no API key): requires_key=False so
        Save isn't gated on a credential and the startup modal never forces one."""
        from settings_dialog import _PROVIDER_CATEGORIES
        found = {
            p.provider_id: p.requires_key
            for c in _PROVIDER_CATEGORIES
            for p in c.providers
            if p.provider_id in {"faster-whisper", "kokoro"}
        }
        assert found == {"faster-whisper": False, "kokoro": False}


# --- Sprint 4: dialog render tests (qapp fixture) ---------------------------


@pytest.fixture(scope="session")
def qapp():
    """Session-shared QApplication. Mirrors test_tray.py fixture."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestSettingsDialogRender:
    """Verify the dialog renders the expected widgets in the expected
    structure. Inspects internal state (self._dropdowns, self._key_inputs,
    self._signup_buttons) rather than simulating user clicks — the
    `qapp` fixture provides a QApplication but no event loop runs."""

    def test_dialog_has_privacy_line(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        from PyQt6.QtWidgets import QLabel
        labels = [w for w in dlg.findChildren(QLabel)]
        privacy_texts = [
            l.text() for l in labels
            if "encrypted" in l.text()
        ]
        assert len(privacy_texts) >= 1, "Privacy line not rendered"
        privacy = privacy_texts[0].lower()
        # The line must say where the keys live. That part is true and it is
        # the reassurance a first-time user is actually looking for.
        assert "credential manager" in privacy, (
            f"privacy line does not say where keys are stored; got: {privacy!r}"
        )
        # And it must NOT claim nothing leaves the machine. It used to, and it
        # was false on the cloud defaults: screenshots go to the LLM provider
        # and audio goes to the STT provider. This guard exists so that claim
        # cannot quietly come back in a future copy tweak.
        assert "nothing leaves your machine" not in privacy, (
            "privacy line claims no egress, which is false on the cloud "
            f"defaults; got: {privacy!r}"
        )

    def test_dialog_has_three_dropdowns(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        assert set(dlg._dropdowns.keys()) == {"LLM", "STT", "TTS"}

    def test_dialog_has_three_key_inputs(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        assert set(dlg._key_inputs.keys()) == {"LLM", "STT", "TTS"}

    def test_dialog_has_three_signup_buttons(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        assert set(dlg._signup_buttons.keys()) == {"LLM", "STT", "TTS"}

    def test_tts_dropdown_has_three_options(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        tts_dropdown = dlg._dropdowns["TTS"]
        items = [tts_dropdown.itemText(i) for i in range(tts_dropdown.count())]
        assert items == ["Cartesia", "ElevenLabs", "Local (Kokoro)"]

    def test_llm_dropdown_has_expected_providers(self, qapp, mocker):
        """LLM dropdown includes Anthropic, OpenAI, Ollama, Gemini (Realtime
        removed) and defaults to Anthropic. Order-independent so adding a
        provider doesn't break it."""
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        llm_dropdown = dlg._dropdowns["LLM"]
        ids = {llm_dropdown.itemData(i) for i in range(llm_dropdown.count())}
        assert {"anthropic", "openai", "ollama", "gemini"} <= ids
        assert "openai-realtime" not in ids
        assert llm_dropdown.itemData(llm_dropdown.currentIndex()) == "anthropic"

    def test_stt_dropdown_has_two_options(self, qapp, mocker):
        """v0.3.x: STT dropdown has AssemblyAI (default) + Local (faster-whisper)."""
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        stt_dropdown = dlg._dropdowns["STT"]
        ids = [stt_dropdown.itemData(i) for i in range(stt_dropdown.count())]
        assert ids == ["assemblyai", "faster-whisper"]
        assert stt_dropdown.currentIndex() == 0

class TestSettingsDialogDropdownSwap:
    """Switching the TTS dropdown from Cartesia → ElevenLabs must:
    (a) update the key field's placeholder to mention ELEVENLABS_API_KEY
    (b) load the existing ElevenLabs key from keyring (if any)
    (c) NOT carry the previously-displayed Cartesia key into the field
    """

    def test_switching_provider_loads_new_providers_existing_key(
        self, qapp, mocker, monkeypatch
    ):
        # Pre-populate keyring with both Cartesia and ElevenLabs keys.
        store = {
            ("clicky-windows", "CARTESIA_API_KEY"): "sk_car_existing",
            ("clicky-windows", "ELEVENLABS_API_KEY"): "eleven_existing",
        }
        monkeypatch.setattr(
            "settings_dialog.keyring.get_password",
            lambda service, name: store.get((service, name)),
        )

        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()

        # Initially TTS dropdown selects Cartesia → key field shows that key.
        tts_input = dlg._key_inputs["TTS"]
        assert tts_input.text() == "sk_car_existing"

        # Switch dropdown to ElevenLabs (index 1).
        dlg._dropdowns["TTS"].setCurrentIndex(1)

        # Key field now shows the ElevenLabs key.
        assert tts_input.text() == "eleven_existing"

    def test_switching_provider_with_no_existing_key_clears_field(
        self, qapp, mocker, monkeypatch
    ):
        store = {
            ("clicky-windows", "CARTESIA_API_KEY"): "sk_car_existing",
            # No ElevenLabs key stored.
        }
        monkeypatch.setattr(
            "settings_dialog.keyring.get_password",
            lambda service, name: store.get((service, name)),
        )

        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        tts_input = dlg._key_inputs["TTS"]
        assert tts_input.text() == "sk_car_existing"

        dlg._dropdowns["TTS"].setCurrentIndex(1)

        # No previous ElevenLabs key — field cleared.
        assert tts_input.text() == ""
        # Placeholder mentions the new env-var name.
        assert "ELEVENLABS_API_KEY" in tts_input.placeholderText()


class TestSettingsDialogSave:
    """Save persists (a) the selected provider per category as
    {LLM,STT,TTS}_PROVIDER in keyring, AND (b) the API key field's
    contents to that provider's keyring slot."""

    def test_save_persists_provider_selection_to_keyring(
        self, qapp, mocker, monkeypatch
    ):
        saved: dict[tuple[str, str], str] = {}
        monkeypatch.setattr(
            "settings_dialog.keyring.get_password",
            lambda service, name: None,
        )
        monkeypatch.setattr(
            "settings_dialog.keyring.set_password",
            lambda service, name, value: saved.update({(service, name): value}),
        )

        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        # Switch TTS to ElevenLabs and enter a key.
        dlg._dropdowns["TTS"].setCurrentIndex(1)
        dlg._key_inputs["LLM"].setText("sk-llm-key")
        dlg._key_inputs["STT"].setText("stt-key")
        dlg._key_inputs["TTS"].setText("eleven-key")

        dlg._on_save()

        assert saved[("clicky-windows", "LLM_PROVIDER")] == "anthropic"
        assert saved[("clicky-windows", "STT_PROVIDER")] == "assemblyai"
        assert saved[("clicky-windows", "TTS_PROVIDER")] == "elevenlabs"
        assert saved[("clicky-windows", "ANTHROPIC_API_KEY")] == "sk-llm-key"
        assert saved[("clicky-windows", "ASSEMBLYAI_API_KEY")] == "stt-key"
        assert saved[("clicky-windows", "ELEVENLABS_API_KEY")] == "eleven-key"

    def test_save_only_persists_to_currently_selected_providers_slot(
        self, qapp, mocker, monkeypatch
    ):
        """If TTS dropdown is on Cartesia, save MUST write to
        CARTESIA_API_KEY, NOT ELEVENLABS_API_KEY."""
        saved: dict[tuple[str, str], str] = {}
        monkeypatch.setattr(
            "settings_dialog.keyring.get_password",
            lambda service, name: None,
        )
        monkeypatch.setattr(
            "settings_dialog.keyring.set_password",
            lambda service, name, value: saved.update({(service, name): value}),
        )

        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        # Stay on Cartesia (default index 0).
        dlg._key_inputs["LLM"].setText("a")
        dlg._key_inputs["STT"].setText("a")
        dlg._key_inputs["TTS"].setText("sk_car_value")
        dlg._on_save()

        assert ("clicky-windows", "CARTESIA_API_KEY") in saved
        assert ("clicky-windows", "ELEVENLABS_API_KEY") not in saved


# --- v0.2.1 (Issue #1 fix B + D): Ollama model dropdown + compat warn -------


class TestOllamaModelDropdown:
    """v0.2.1: when LLM provider is Ollama, an editable QComboBox appears
    for OLLAMA_MODEL_VISION. Hidden when provider is Anthropic. Save
    persists to keyring + runs Fix D compatibility check (which can
    block save via QMessageBox)."""

    def test_model_suggestions_includes_llava_as_first(self):
        """llava:7b must be index 0 — it's the new default in v0.2.1.
        llama3.2-vision comes after (more accurate but needs Ollama
        >=0.4.x; users can pick it manually)."""
        from settings_dialog import _OLLAMA_MODEL_SUGGESTIONS
        assert _OLLAMA_MODEL_SUGGESTIONS[0] == "llava:7b"
        assert "llama3.2-vision" in _OLLAMA_MODEL_SUGGESTIONS

    def test_model_row_hidden_for_anthropic_default(self, qapp, mocker):
        """v0.4.1: Anthropic has no model sub-picker (minimal UX), so the model
        row is HIDDEN when Anthropic (the default) is selected.
        (isHidden() not isVisible() — the dialog is never .show()-n in tests.)"""
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        assert dlg._model_rows["LLM"].isHidden() is True

    def test_model_combo_repopulates_for_ollama(self, qapp, mocker):
        """Switching LLM to Ollama (index 2: ant=0, openai=1, ollama=2, gemini=3)
        repopulates the model combo with Ollama suggestions, keeps it visible +
        editable."""
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        dlg._dropdowns["LLM"].setCurrentIndex(_dropdown_index(dlg._dropdowns["LLM"], "ollama"))
        assert dlg._model_rows["LLM"].isHidden() is False
        combo = dlg._model_combos["LLM"]
        assert combo.isEditable() is True
        items = [combo.itemText(i) for i in range(combo.count())]
        assert "llava:7b" in items

    def test_save_persists_ollama_model_to_keyring(
        self, qapp, mocker, monkeypatch
    ):
        """Select Ollama, type a model → Save persists OLLAMA_MODEL_VISION."""
        saved: dict[tuple[str, str], str] = {}
        monkeypatch.setattr("settings_dialog.keyring.get_password", lambda s, n: None)
        monkeypatch.setattr(
            "settings_dialog.keyring.set_password",
            lambda s, n, v: saved.update({(s, n): v}),
        )
        mocker.patch("ollama_health.check_model_compatibility", return_value=None)
        mocker.patch("ollama_health.detect_ollama_version", return_value="0.5.0")

        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        dlg._dropdowns["LLM"].setCurrentIndex(_dropdown_index(dlg._dropdowns["LLM"], "ollama"))
        dlg._key_inputs["LLM"].setText("http://localhost:11434")
        dlg._key_inputs["STT"].setText("stt-key")
        dlg._key_inputs["TTS"].setText("tts-key")
        dlg._model_combos["LLM"].setCurrentText("qwen2.5-vl")
        dlg._on_save()
        assert saved[("clicky-windows", "OLLAMA_MODEL_VISION")] == "qwen2.5-vl"

    def test_save_persists_draw_toggle(self, qapp, mocker, monkeypatch):
        """Ticking the Draw checkbox persists ANNOTATION_MODE=on on Save."""
        saved: dict[tuple[str, str], str] = {}
        monkeypatch.setattr("settings_dialog.keyring.get_password", lambda s, n: None)
        monkeypatch.setattr(
            "settings_dialog.keyring.set_password",
            lambda s, n, v: saved.update({(s, n): v}),
        )
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        dlg._draw_checkbox.setChecked(True)
        dlg._key_inputs["LLM"].setText("a")
        dlg._key_inputs["STT"].setText("b")
        dlg._key_inputs["TTS"].setText("c")
        dlg._on_save()
        assert saved[("clicky-windows", "ANNOTATION_MODE")] == "on"

    def test_save_aborts_when_user_cancels_compat_warning(
        self, qapp, mocker, monkeypatch
    ):
        """Fix D: Ollama provider + incompatible model + user clicks Cancel →
        save MUST NOT persist anything (don't half-save)."""
        from PyQt6.QtWidgets import QMessageBox

        saved: dict[tuple[str, str], str] = {}
        monkeypatch.setattr("settings_dialog.keyring.get_password", lambda s, n: None)
        monkeypatch.setattr(
            "settings_dialog.keyring.set_password",
            lambda s, n, v: saved.update({(s, n): v}),
        )
        mocker.patch("ollama_health.detect_ollama_version", return_value="0.3.14")
        mocker.patch(
            "settings_dialog.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.Cancel,
        )

        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        dlg._dropdowns["LLM"].setCurrentIndex(_dropdown_index(dlg._dropdowns["LLM"], "ollama"))
        dlg._key_inputs["LLM"].setText("http://localhost:11434")
        dlg._key_inputs["STT"].setText("stt-key")
        dlg._key_inputs["TTS"].setText("tts-key")
        dlg._model_combos["LLM"].setCurrentText("llama3.2-vision")
        dlg._on_save()
        assert saved == {}
