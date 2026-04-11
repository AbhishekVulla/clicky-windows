"""Unit tests for ai.py.

All tests are mock-based. Zero real-API dependency. Green in <2s.
Covers: build_user_prompt, image_to_base64_jpeg, parse_tool_use_coordinates,
parse_response_text, AIClient abstract, AnthropicClient.ask. See
docs/superpowers/plans/2026-04-11-ai.md for the full test plan.
"""

import pytest


def test_ai_module_importable():
    import ai  # noqa: F401


# --- build_user_prompt -------------------------------------------------------

class TestBuildUserPrompt:
    """Tests for ai.build_user_prompt.

    The prompt text is copied verbatim from Clicky's ElementLocationDetector.swift
    (verified via gh api on 2026-04-11). Any drift is a bug because Computer
    Use training was done against this exact wording.
    """

    def test_prompt_contains_verbatim_clicky_template(self):
        """Three anchor phrases must appear exactly."""
        from ai import build_user_prompt
        result = build_user_prompt("how do I save this file")
        assert "looking at their screen" in result
        assert "If there is a specific UI element" in result
        assert "no specific element" in result

    def test_prompt_substitutes_transcript(self):
        """User transcript is quoted into the first line."""
        from ai import build_user_prompt
        result = build_user_prompt("how do I save this file")
        assert '"how do I save this file"' in result

    def test_prompt_handles_empty_transcript(self):
        """Empty transcript produces an empty-quoted prompt, does not crash."""
        from ai import build_user_prompt
        result = build_user_prompt("")
        assert '""' in result
        assert "looking at their screen" in result


# --- image_to_base64_jpeg ----------------------------------------------------

class TestImageToBase64Jpeg:
    """Tests for ai.image_to_base64_jpeg.

    Verifies PIL -> JPEG -> base64 ASCII conversion. The decoded bytes must
    start with the JPEG SOI marker b'\\xff\\xd8\\xff'. Quality parameter
    must affect output size.
    """

    def test_returns_ascii_string(self):
        """Result is str, not bytes. Must be safe to put in a JSON payload."""
        from PIL import Image
        from ai import image_to_base64_jpeg
        img = Image.new("RGB", (100, 100), color=(200, 100, 50))
        result = image_to_base64_jpeg(img)
        assert isinstance(result, str)
        # ASCII-only (base64 chars)
        result.encode("ascii")  # should not raise

    def test_is_valid_base64_jpeg(self):
        """Decoded bytes must start with JPEG SOI marker b'\\xff\\xd8\\xff'."""
        import base64
        from PIL import Image
        from ai import image_to_base64_jpeg
        img = Image.new("RGB", (100, 100), color=(0, 255, 0))
        result = image_to_base64_jpeg(img)
        decoded = base64.b64decode(result)
        assert decoded[:3] == b"\xff\xd8\xff", (
            f"expected JPEG SOI marker, got {decoded[:3]!r}"
        )

    def test_respects_quality_param(self):
        """Lower quality yields smaller output."""
        from PIL import Image
        from ai import image_to_base64_jpeg
        # Use a larger image with color variance so quality actually matters.
        img = Image.new("RGB", (400, 400))
        pixels = img.load()
        for x in range(400):
            for y in range(400):
                pixels[x, y] = ((x * 3) % 256, (y * 5) % 256, ((x + y) * 7) % 256)
        high_q = image_to_base64_jpeg(img, quality=95)
        low_q = image_to_base64_jpeg(img, quality=20)
        assert len(low_q) < len(high_q), (
            f"quality=20 should be smaller than quality=95, "
            f"got {len(low_q)} vs {len(high_q)}"
        )


# --- parse_tool_use_coordinates ----------------------------------------------

class TestParseToolUseCoordinates:
    """Tests for ai.parse_tool_use_coordinates.

    Uses dict-shaped mocks that mirror anthropic.types.Message.content shape.
    The dual-access pattern (getattr for objects, .get for dicts) means the
    same function body handles both test mocks and real SDK responses.
    """

    def test_single_tool_use_returns_one_coordinate(self):
        """One left_click block with coordinate [640, 400] -> [(640, 400)]."""
        from ai import parse_tool_use_coordinates
        fake_response = {
            "content": [
                {"type": "text", "text": "The Save button is top-left."},
                {
                    "type": "tool_use", "id": "t1", "name": "computer",
                    "input": {"action": "left_click", "coordinate": [640, 400]},
                },
            ]
        }
        assert parse_tool_use_coordinates(fake_response) == [(640, 400)]

    def test_multiple_tool_uses_returned_in_order(self):
        """Two left_click blocks -> both returned in content order."""
        from ai import parse_tool_use_coordinates
        fake_response = {
            "content": [
                {
                    "type": "tool_use", "id": "t1", "name": "computer",
                    "input": {"action": "left_click", "coordinate": [100, 200]},
                },
                {"type": "text", "text": "and here"},
                {
                    "type": "tool_use", "id": "t2", "name": "computer",
                    "input": {"action": "left_click", "coordinate": [300, 400]},
                },
            ]
        }
        assert parse_tool_use_coordinates(fake_response) == [(100, 200), (300, 400)]

    def test_no_tool_use_returns_empty(self):
        """Text-only response (conceptual question) -> empty list."""
        from ai import parse_tool_use_coordinates
        fake_response = {
            "content": [
                {"type": "text", "text": "no specific element"},
            ]
        }
        assert parse_tool_use_coordinates(fake_response) == []

    def test_ignores_non_left_click_actions(self):
        """Non-left_click tool_use blocks (type, scroll, etc.) are skipped."""
        from ai import parse_tool_use_coordinates
        fake_response = {
            "content": [
                {
                    "type": "tool_use", "id": "t1", "name": "computer",
                    "input": {"action": "type", "text": "hello"},
                },
                {
                    "type": "tool_use", "id": "t2", "name": "computer",
                    "input": {"action": "scroll", "direction": "down"},
                },
                {
                    "type": "tool_use", "id": "t3", "name": "computer",
                    "input": {"action": "left_click", "coordinate": [500, 600]},
                },
            ]
        }
        assert parse_tool_use_coordinates(fake_response) == [(500, 600)]


# --- parse_response_text -----------------------------------------------------

class TestParseResponseText:
    """Tests for ai.parse_response_text. Extracts text content for TTS."""

    def test_single_text_block(self):
        """One text block -> returns its text."""
        from ai import parse_response_text
        fake_response = {
            "content": [
                {"type": "text", "text": "The Save button is in the top-left."},
            ]
        }
        assert parse_response_text(fake_response) == "The Save button is in the top-left."

    def test_multiple_text_blocks_joined(self):
        """Two text blocks -> joined with space, stripped."""
        from ai import parse_response_text
        fake_response = {
            "content": [
                {"type": "text", "text": "Part one."},
                {"type": "tool_use", "input": {"action": "left_click", "coordinate": [0, 0]}},
                {"type": "text", "text": "Part two."},
            ]
        }
        assert parse_response_text(fake_response) == "Part one. Part two."


# --- AIClient abstract base --------------------------------------------------

class TestAIClient:
    """Tests for ai.AIClient abstract base class."""

    def test_aiclient_is_abstract(self):
        """AIClient() must raise TypeError because ask is abstract."""
        from ai import AIClient
        with pytest.raises(TypeError):
            AIClient()  # type: ignore[abstract]


# --- AnthropicClient.ask -----------------------------------------------------

class TestAnthropicClient:
    """Tests for ai.AnthropicClient.ask using a mocked Anthropic SDK client."""

    def _fake_response(self):
        """A dict-shaped fake that parse_tool_use_coordinates +
        parse_response_text can both parse successfully."""
        return {
            "content": [
                {"type": "text", "text": "The Save button is top-left."},
                {
                    "type": "tool_use", "id": "t1", "name": "computer",
                    "input": {"action": "left_click", "coordinate": [450, 80]},
                },
            ]
        }

    def test_ask_calls_sdk_with_correct_args(self, mocker):
        """Verify the SDK call has the exact Computer Use beta shape."""
        from PIL import Image
        from ai import AnthropicClient

        fake_anthropic_class = mocker.patch("ai.Anthropic")
        fake_client = fake_anthropic_class.return_value
        fake_client.messages.create.return_value = self._fake_response()

        client = AnthropicClient(api_key="test-key", model_id="claude-test-model")
        img = Image.new("RGB", (1280, 800), color=(100, 100, 100))
        client.ask(
            image=img,
            transcript="how do I save",
            history=[],
            declared_w=1280,
            declared_h=800,
        )

        fake_client.messages.create.assert_called_once()
        call_kwargs = fake_client.messages.create.call_args.kwargs

        # Model from constructor
        assert call_kwargs["model"] == "claude-test-model"
        # max_tokens matches Clicky
        assert call_kwargs["max_tokens"] == 256
        # Tool JSON matches Computer Use beta
        assert len(call_kwargs["tools"]) == 1
        tool = call_kwargs["tools"][0]
        assert tool["type"] == "computer_20251124"
        assert tool["name"] == "computer"
        assert tool["display_width_px"] == 1280
        assert tool["display_height_px"] == 800
        # Beta header
        assert call_kwargs["extra_headers"] == {
            "anthropic-beta": "computer-use-2025-11-24"
        }
        # Messages shape: [new user turn]
        messages = call_kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert len(content) == 2
        # First block: image
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        assert content[0]["source"]["media_type"] == "image/jpeg"
        assert isinstance(content[0]["source"]["data"], str)
        # Second block: text with transcript quoted
        assert content[1]["type"] == "text"
        assert '"how do I save"' in content[1]["text"]

    def test_ask_returns_parsed_dict_shape(self, mocker):
        """Return value has 'text' and 'points' keys; points have x/y/label."""
        from PIL import Image
        from ai import AnthropicClient

        fake_anthropic_class = mocker.patch("ai.Anthropic")
        fake_client = fake_anthropic_class.return_value
        fake_client.messages.create.return_value = self._fake_response()

        client = AnthropicClient(api_key="test-key", model_id="claude-test-model")
        img = Image.new("RGB", (1280, 800))
        result = client.ask(
            image=img, transcript="test", history=[],
            declared_w=1280, declared_h=800,
        )

        assert "text" in result
        assert isinstance(result["text"], str)
        assert result["text"] == "The Save button is top-left."

        assert "points" in result
        assert len(result["points"]) == 1
        point = result["points"][0]
        assert point["x"] == 450
        assert point["y"] == 80
        assert point["label"] == ""

    def test_ask_prepends_history(self, mocker):
        """When history has 2 prior turns, final messages has 3 entries."""
        from PIL import Image
        from ai import AnthropicClient

        fake_anthropic_class = mocker.patch("ai.Anthropic")
        fake_client = fake_anthropic_class.return_value
        fake_client.messages.create.return_value = self._fake_response()

        history = [
            {"role": "user", "content": [{"type": "text", "text": "prior q"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "prior a"}]},
        ]

        client = AnthropicClient(api_key="test-key", model_id="claude-test-model")
        img = Image.new("RGB", (1280, 800))
        client.ask(
            image=img, transcript="next q", history=history,
            declared_w=1280, declared_h=800,
        )

        messages = fake_client.messages.create.call_args.kwargs["messages"]
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[0]["content"][0]["text"] == "prior q"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"][0]["text"] == "prior a"
        assert messages[2]["role"] == "user"
        # Latest user turn has image + text, text contains new transcript
        latest_content = messages[2]["content"]
        assert latest_content[0]["type"] == "image"
        assert '"next q"' in latest_content[1]["text"]
