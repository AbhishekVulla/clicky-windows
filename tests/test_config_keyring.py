"""Unit tests for config.resolve_api_key — the env→keyring resolver.

The function is the load-bearing piece of Sprint 3's BYOK migration:
on launch with .env present, keys auto-write to keyring as backup;
on launch without .env, keys read from keyring transparently.

Tests mock keyring's get_password / set_password so we don't touch
the real Windows Credential Manager during CI.
"""
from __future__ import annotations

import pytest


KEY = "ANTHROPIC_API_KEY"  # any of the three keys; semantics identical


@pytest.fixture
def fake_keyring(monkeypatch):
    """In-memory dict-backed mock for keyring's two functions.

    Yields the dict so tests can pre-populate or assert on what got
    written. Mock is scoped to this test only — module-level keyring
    import in config.py is patched on the keyring module itself.
    """
    store: dict[tuple[str, str], str] = {}

    def fake_get(service, name):
        return store.get((service, name))

    def fake_set(service, name, value):
        store[(service, name)] = value

    import config
    monkeypatch.setattr(config.keyring, "get_password", fake_get)
    monkeypatch.setattr(config.keyring, "set_password", fake_set)
    yield store


class TestResolveApiKey:
    """resolve_api_key returns env first, keyring second, None last —
    and on env-present, ALSO migrates the value into keyring."""

    def test_returns_env_value_when_present(self, monkeypatch, fake_keyring):
        monkeypatch.setenv(KEY, "sk-from-env")
        from config import resolve_api_key
        assert resolve_api_key(KEY) == "sk-from-env"

    def test_migrates_env_to_keyring_on_resolve(
        self, monkeypatch, fake_keyring
    ):
        """When env is present, the value MUST also land in keyring.
        This is the one-shot migration so the user can later delete
        .env without losing the key."""
        monkeypatch.setenv(KEY, "sk-migrate-me")
        from config import resolve_api_key, KEYRING_SERVICE
        resolve_api_key(KEY)
        assert fake_keyring[(KEYRING_SERVICE, KEY)] == "sk-migrate-me"

    def test_falls_back_to_keyring_when_env_absent(
        self, monkeypatch, fake_keyring
    ):
        """No env var → read from keyring."""
        monkeypatch.delenv(KEY, raising=False)
        from config import resolve_api_key, KEYRING_SERVICE
        fake_keyring[(KEYRING_SERVICE, KEY)] = "sk-from-keyring"
        assert resolve_api_key(KEY) == "sk-from-keyring"

    def test_returns_none_when_neither_source_has_value(
        self, monkeypatch, fake_keyring
    ):
        """First-launch state: no env, empty keyring → None.
        The settings dialog gate uses this to decide whether to show."""
        monkeypatch.delenv(KEY, raising=False)
        from config import resolve_api_key
        assert resolve_api_key(KEY) is None

    def test_keyring_set_failure_does_not_block_env_path(
        self, monkeypatch
    ):
        """If keyring backend is unavailable (vault locked, no service
        registered, etc.), set_password raising must NOT prevent the
        env-var path from returning the user's value. The user has a
        valid .env — credential-store glitches shouldn't fail startup."""
        monkeypatch.setenv(KEY, "sk-env-survives")

        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated keyring failure")

        import config
        monkeypatch.setattr(config.keyring, "set_password", boom)
        from config import resolve_api_key
        assert resolve_api_key(KEY) == "sk-env-survives"

    def test_keyring_get_failure_returns_none_no_raise(
        self, monkeypatch
    ):
        """Keyring read errors swallowed → caller sees None and shows
        the settings dialog. No traceback up to main."""
        monkeypatch.delenv(KEY, raising=False)

        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated keyring read failure")

        import config
        monkeypatch.setattr(config.keyring, "get_password", boom)
        from config import resolve_api_key
        assert resolve_api_key(KEY) is None


# --- Sprint 4: resolve_setting (env→keyring→default for non-secret config) ---


class TestResolveSetting:
    """resolve_setting is a sibling to resolve_api_key for non-secret
    config. Same env→keyring semantics, plus a default fallback when
    neither env nor keyring has a value (since settings always have a
    sensible default, unlike API keys which require explicit entry)."""

    def test_returns_env_value_when_present(self, monkeypatch, fake_keyring):
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        from config import resolve_setting
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "elevenlabs"

    def test_migrates_env_to_keyring_on_resolve(self, monkeypatch, fake_keyring):
        """When env is present, the value MUST also land in keyring so the
        user can later delete .env without losing the choice."""
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        from config import resolve_setting, KEYRING_SERVICE
        resolve_setting("TTS_PROVIDER", default="cartesia")
        assert fake_keyring[(KEYRING_SERVICE, "TTS_PROVIDER")] == "elevenlabs"

    def test_falls_back_to_keyring_when_env_absent(self, monkeypatch, fake_keyring):
        monkeypatch.delenv("TTS_PROVIDER", raising=False)
        from config import resolve_setting, KEYRING_SERVICE
        fake_keyring[(KEYRING_SERVICE, "TTS_PROVIDER")] = "elevenlabs"
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "elevenlabs"

    def test_returns_default_when_neither_source_has_value(self, monkeypatch, fake_keyring):
        """First-launch state: no env, empty keyring → default. Distinct from
        resolve_api_key which returns None (settings always have a default)."""
        monkeypatch.delenv("TTS_PROVIDER", raising=False)
        from config import resolve_setting
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "cartesia"

    def test_keyring_failures_do_not_block_env_path(self, monkeypatch):
        """Keyring backend errors swallowed — env value still returned + default
        still works as final fallback."""
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")

        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated keyring failure")

        import config
        monkeypatch.setattr(config.keyring, "set_password", boom)
        from config import resolve_setting
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "elevenlabs"
