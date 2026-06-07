"""Unit tests for ollama_health.py (v0.2.1 — Issue #1 fix D).

Tests cover:
- detect_ollama_version happy path (200 + valid JSON)
- detect_ollama_version failure modes (network, non-200, malformed JSON)
- check_model_compatibility incompatible case (warning returned)
- check_model_compatibility compatible case (None returned)
- check_model_compatibility edge cases (None version, unknown model, prefix-stripped model name)
"""
from __future__ import annotations

import httpx


class TestDetectOllamaVersion:
    def test_returns_version_string_on_healthy_ollama(self, mocker):
        from ollama_health import detect_ollama_version

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"version": "0.4.7"}

        mock_client = mocker.MagicMock()
        mock_client.__enter__ = mocker.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mocker.MagicMock(return_value=None)
        mock_client.get = mocker.MagicMock(return_value=mock_response)

        mocker.patch("ollama_health.httpx.Client", return_value=mock_client)

        result = detect_ollama_version("http://localhost:11434")
        assert result == "0.4.7"

    def test_strips_trailing_slash_from_host(self, mocker):
        """detect_ollama_version('http://localhost:11434/') should hit
        '/api/version', not '//api/version'."""
        from ollama_health import detect_ollama_version

        captured = {}

        def fake_get(url):
            captured["url"] = url
            m = mocker.MagicMock()
            m.status_code = 200
            m.json.return_value = {"version": "0.4.0"}
            return m

        mock_client = mocker.MagicMock()
        mock_client.__enter__ = mocker.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mocker.MagicMock(return_value=None)
        mock_client.get = mocker.MagicMock(side_effect=fake_get)

        mocker.patch("ollama_health.httpx.Client", return_value=mock_client)

        detect_ollama_version("http://localhost:11434/")
        assert captured["url"] == "http://localhost:11434/api/version"

    def test_returns_none_on_network_error(self, mocker):
        """ConnectError (Ollama not running) → None, not a crash."""
        from ollama_health import detect_ollama_version

        mock_client = mocker.MagicMock()
        mock_client.__enter__ = mocker.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mocker.MagicMock(return_value=None)
        mock_client.get = mocker.MagicMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        mocker.patch("ollama_health.httpx.Client", return_value=mock_client)

        assert detect_ollama_version("http://localhost:11434") is None

    def test_returns_none_on_non_200_status(self, mocker):
        from ollama_health import detect_ollama_version

        mock_response = mocker.MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "internal"}

        mock_client = mocker.MagicMock()
        mock_client.__enter__ = mocker.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mocker.MagicMock(return_value=None)
        mock_client.get = mocker.MagicMock(return_value=mock_response)

        mocker.patch("ollama_health.httpx.Client", return_value=mock_client)

        assert detect_ollama_version("http://localhost:11434") is None

    def test_returns_none_on_malformed_json(self, mocker):
        from ollama_health import detect_ollama_version

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not JSON")

        mock_client = mocker.MagicMock()
        mock_client.__enter__ = mocker.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mocker.MagicMock(return_value=None)
        mock_client.get = mocker.MagicMock(return_value=mock_response)

        mocker.patch("ollama_health.httpx.Client", return_value=mock_client)

        assert detect_ollama_version("http://localhost:11434") is None

    def test_returns_none_when_version_field_missing(self, mocker):
        """200 + valid JSON but no 'version' key → None (don't pretend
        to know what Ollama is running)."""
        from ollama_health import detect_ollama_version

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}

        mock_client = mocker.MagicMock()
        mock_client.__enter__ = mocker.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mocker.MagicMock(return_value=None)
        mock_client.get = mocker.MagicMock(return_value=mock_response)

        mocker.patch("ollama_health.httpx.Client", return_value=mock_client)

        assert detect_ollama_version("http://localhost:11434") is None


class TestCheckModelCompatibility:
    def test_warns_when_model_needs_newer_ollama(self):
        """0.3.14 is below 0.4.0 threshold → warning. Note: reporter
        wrote 'Ollama 0.30.4' in the issue but that semver-parses to
        (0, 30, 4) which is > (0, 4, 0) — the actual versions where
        mllama is unsupported are the 0.3.x line."""
        from ollama_health import check_model_compatibility

        warning = check_model_compatibility("llama3.2-vision", "0.3.14")
        assert warning is not None
        assert "llama3.2-vision" in warning
        assert "0.4.0" in warning  # minimum
        assert "0.3.14" in warning  # what user has

    def test_no_warning_when_model_compatible_with_ollama(self):
        from ollama_health import check_model_compatibility

        assert check_model_compatibility("llama3.2-vision", "0.4.7") is None
        assert check_model_compatibility("llama3.2-vision", "0.5.0") is None

    def test_no_warning_for_llava(self):
        """llava:7b has no recorded minimum — compatible with all
        Ollama versions that have any vision."""
        from ollama_health import check_model_compatibility

        assert check_model_compatibility("llava:7b", "0.1.30") is None
        assert check_model_compatibility("llava:7b", "0.30.4") is None

    def test_no_warning_when_ollama_version_unknown(self):
        """None version means we couldn't detect → no warning (don't
        guess)."""
        from ollama_health import check_model_compatibility

        assert check_model_compatibility("llama3.2-vision", None) is None

    def test_no_warning_for_unknown_model(self):
        """Custom model name we don't have a minimum for → no warning."""
        from ollama_health import check_model_compatibility

        assert (
            check_model_compatibility("my-custom-vision-model:latest", "0.3.0")
            is None
        )

    def test_strips_ollama_prefix_from_model_name(self):
        """Model name 'ollama/llama3.2-vision' should be normalized
        to 'llama3.2-vision' for the lookup."""
        from ollama_health import check_model_compatibility

        warning = check_model_compatibility(
            "ollama/llama3.2-vision", "0.3.14"
        )
        assert warning is not None
        assert "llama3.2-vision" in warning

    def test_no_warning_when_version_unparseable(self):
        """Weird version strings (dev builds, hashes) → no warning
        rather than guess."""
        from ollama_health import check_model_compatibility

        # Empty / nonsense
        assert (
            check_model_compatibility("llama3.2-vision", "asdf-not-a-version")
            is None
        )


class TestParseVersion:
    def test_parses_standard_dotted_decimal(self):
        from ollama_health import _parse_version

        assert _parse_version("0.4.7") == (0, 4, 7)
        assert _parse_version("1.2.3") == (1, 2, 3)
        assert _parse_version("0.30.4") == (0, 30, 4)

    def test_strips_suffix_at_first_non_digit(self):
        from ollama_health import _parse_version

        # Dev builds with suffixes
        assert _parse_version("0.4.0-rc1") == (0, 4, 0)
        assert _parse_version("0.5.0+dev") == (0, 5, 0)

    def test_returns_none_for_garbage(self):
        from ollama_health import _parse_version

        assert _parse_version("") is None
        assert _parse_version("not-a-version") is None
        assert _parse_version("asdf") is None
