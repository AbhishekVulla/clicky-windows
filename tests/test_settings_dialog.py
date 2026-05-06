"""Unit tests for settings_dialog — required_keys_present probe + mask
helper. The full dialog rendering is verified manually (PyQt6 modal
exec needs a real Qt event loop)."""
from __future__ import annotations

import pytest


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

    def test_llm_category_has_only_anthropic(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        llm = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        assert [p.provider_id for p in llm.providers] == ["anthropic"]
        assert llm.default_index == 0

    def test_stt_category_has_only_assemblyai(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        stt = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "STT")
        assert [p.provider_id for p in stt.providers] == ["assemblyai"]

    def test_tts_category_has_cartesia_and_elevenlabs(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        tts = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "TTS")
        assert [p.provider_id for p in tts.providers] == ["cartesia", "elevenlabs"]
        assert tts.default_index == 0  # Cartesia default

    def test_each_provider_has_env_var_and_signup_url(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        for category in _PROVIDER_CATEGORIES:
            for provider in category.providers:
                assert provider.api_key_env_var.endswith("_API_KEY")
                assert provider.signup_url.startswith("https://")
                assert provider.display_name  # non-empty


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
            if "encrypted" in l.text() or "telemetry" in l.text()
        ]
        assert len(privacy_texts) >= 1, "Privacy line not rendered"
        privacy = privacy_texts[0]
        assert "no server" in privacy.lower() or "no telemetry" in privacy.lower()

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

    def test_tts_dropdown_has_two_options(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        tts_dropdown = dlg._dropdowns["TTS"]
        items = [tts_dropdown.itemText(i) for i in range(tts_dropdown.count())]
        assert items == ["Cartesia", "ElevenLabs"]

    def test_llm_dropdown_has_one_option(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        llm_dropdown = dlg._dropdowns["LLM"]
        assert llm_dropdown.count() == 1
        assert llm_dropdown.itemText(0) == "Anthropic"


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
