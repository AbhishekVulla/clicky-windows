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
