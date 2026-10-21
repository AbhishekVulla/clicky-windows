"""Default models, and moving people off defaults from older releases."""
import pytest


@pytest.fixture
def stored(mocker, monkeypatch):
    """Fake keyring contents, with env overrides cleared."""
    values: dict[str, str] = {}
    for name in ("MODEL_ID", "LLM_PROVIDER", "ANTHROPIC_MODEL", "OPENAI_MODEL_VISION",
                 "GEMINI_MODEL_VISION", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    mocker.patch("keyring.get_password", side_effect=lambda service, name: values.get(name))
    mocker.patch("keyring.set_password")
    return values


def test_old_saved_default_moves_to_the_new_default(stored):
    from app import _resolve_llm_credentials
    stored.update(LLM_PROVIDER="anthropic", ANTHROPIC_MODEL="claude-sonnet-4-6")
    model_id, _ = _resolve_llm_credentials()
    assert model_id == "anthropic/claude-haiku-5-5"


def test_a_model_someone_picked_is_kept(stored):
    from app import _resolve_llm_credentials
    stored.update(LLM_PROVIDER="anthropic", ANTHROPIC_MODEL="claude-opus-5-5")
    model_id, _ = _resolve_llm_credentials()
    assert model_id == "anthropic/claude-opus-5-5"


@pytest.mark.parametrize("name,old,new", [
    ("OPENAI_MODEL_VISION", "gpt-5.4", "gpt-6.1-sol"),
    ("GEMINI_MODEL_VISION", "google/gemini-3.1-pro-preview", "google/gemini-4-argon"),
    ("GEMINI_MODEL_VISION", "google/gemini-3.5-flash", "google/gemini-4-argon"),
])
def test_old_defaults_for_the_other_providers(stored, name, old, new):
    import config
    stored[name] = old
    assert config.resolve_model(name, new) == new


def test_an_env_override_always_wins(stored, monkeypatch):
    import config
    monkeypatch.setenv("OPENAI_MODEL_VISION", "gpt-5.4")
    assert config.resolve_model("OPENAI_MODEL_VISION", "gpt-6.1-sol") == "gpt-5.4"
