"""Unit tests for ai.py.

All tests are mock-based. Zero real-API dependency. Green in <2s.
Covers: parse_point_tag, image_to_base64_jpeg, parse_response_text,
AIClient abstract, AnthropicClient.ask_stream, AnthropicClient.ask.
"""

import pytest


def test_ai_module_importable():
    import ai  # noqa: F401


# --- parse_point_tag ----------------------------------------------------------

class TestParsePointTag:
    """Tests for ai.parse_point_tag — Python port of Clicky's
    CompanionManager.parsePointingCoordinates regex parser."""

    def test_happy_path_with_label(self):
        from ai import parse_point_tag
        result = parse_point_tag(
            "click the save button up top. [POINT:640,400:save button]"
        )
        assert result.spoken_text == "click the save button up top."
        assert result.coordinate == (640, 400)
        assert result.element_label == "save button"
        assert result.screen_number is None

    def test_point_none(self):
        from ai import parse_point_tag
        result = parse_point_tag(
            "html stands for hypertext markup language. [POINT:none]"
        )
        assert result.spoken_text == "html stands for hypertext markup language."
        assert result.coordinate is None
        assert result.element_label is None

    def test_no_tag_at_all(self):
        from ai import parse_point_tag
        result = parse_point_tag("just a plain response with no tag")
        assert result.spoken_text == "just a plain response with no tag"
        assert result.coordinate is None

    def test_with_screen_number(self):
        from ai import parse_point_tag
        result = parse_point_tag(
            "that's on your other monitor. [POINT:400,300:terminal:screen2]"
        )
        assert result.coordinate == (400, 300)
        assert result.element_label == "terminal"
        assert result.screen_number == 2

    def test_without_label(self):
        from ai import parse_point_tag
        result = parse_point_tag("look here. [POINT:100,200]")
        assert result.coordinate == (100, 200)
        assert result.element_label is None

    def test_trailing_whitespace_stripped(self):
        from ai import parse_point_tag
        result = parse_point_tag("check this. [POINT:50,60:button]  \n")
        assert result.coordinate == (50, 60)
        assert result.spoken_text == "check this."

    def test_malformed_tag_returns_no_coordinate(self):
        from ai import parse_point_tag
        result = parse_point_tag("broken tag [POINT:garbage]")
        assert result.coordinate is None
        assert "broken tag [POINT:garbage]" in result.spoken_text

    def test_coordinates_with_spaces(self):
        from ai import parse_point_tag
        result = parse_point_tag("here. [POINT:640 , 400:btn]")
        assert result.coordinate == (640, 400)

    def test_screen_number_without_label(self):
        """[POINT:x,y:screen2] must parse screen_number=2, not label='screen2'."""
        from ai import parse_point_tag
        result = parse_point_tag("over there. [POINT:400,300:screen2]")
        assert result.coordinate == (400, 300)
        assert result.element_label is None
        assert result.screen_number == 2


# --- image_to_base64_jpeg ----------------------------------------------------

class TestImageToBase64Jpeg:
    """Tests for ai.image_to_base64_jpeg."""

    def test_returns_ascii_string(self):
        from PIL import Image
        from ai import image_to_base64_jpeg
        img = Image.new("RGB", (100, 100), color=(200, 100, 50))
        result = image_to_base64_jpeg(img)
        assert isinstance(result, str)
        result.encode("ascii")

    def test_is_valid_base64_jpeg(self):
        import base64
        from PIL import Image
        from ai import image_to_base64_jpeg
        img = Image.new("RGB", (100, 100), color=(0, 255, 0))
        result = image_to_base64_jpeg(img)
        decoded = base64.b64decode(result)
        assert decoded[:3] == b"\xff\xd8\xff"

    def test_respects_quality_param(self):
        from PIL import Image
        from ai import image_to_base64_jpeg
        img = Image.new("RGB", (400, 400))
        pixels = img.load()
        for x in range(400):
            for y in range(400):
                pixels[x, y] = ((x * 3) % 256, (y * 5) % 256, ((x + y) * 7) % 256)
        high_q = image_to_base64_jpeg(img, quality=95)
        low_q = image_to_base64_jpeg(img, quality=20)
        assert len(low_q) < len(high_q)


# --- parse_response_text -----------------------------------------------------

class TestParseResponseText:
    """Tests for ai.parse_response_text. Extracts text content for TTS."""

    def test_single_text_block(self):
        from ai import parse_response_text
        fake_response = {
            "content": [
                {"type": "text", "text": "The Save button is in the top-left."},
            ]
        }
        assert parse_response_text(fake_response) == "The Save button is in the top-left."

    def test_multiple_text_blocks_joined(self):
        from ai import parse_response_text
        fake_response = {
            "content": [
                {"type": "text", "text": "Part one."},
                {"type": "text", "text": "Part two."},
            ]
        }
        assert parse_response_text(fake_response) == "Part one. Part two."


# --- AIClient abstract base --------------------------------------------------

class TestAIClient:

    def test_aiclient_is_abstract(self):
        from ai import AIClient
        with pytest.raises(TypeError):
            AIClient()  # type: ignore[abstract]


# --- AnthropicClient.ask_stream -----------------------------------------------

class TestAnthropicClientAskStream:
    """Tests for AnthropicClient.ask_stream using a mocked SDK."""

    def test_ask_stream_calls_sdk_with_correct_args(self, mocker):
        from PIL import Image
        from ai import AnthropicClient, _CLICKY_SYSTEM_PROMPT

        fake_anthropic_class = mocker.patch("ai.Anthropic")
        fake_client = fake_anthropic_class.return_value

        # Mock the stream context manager chain
        fake_stream = mocker.MagicMock()
        fake_stream.text_stream = iter(["hello ", "world"])
        fake_stream.get_final_text.return_value = "hello world [POINT:none]"
        fake_stream_mgr = mocker.MagicMock()
        fake_stream_mgr.__enter__ = mocker.MagicMock(return_value=fake_stream)
        fake_stream_mgr.__exit__ = mocker.MagicMock(return_value=False)
        fake_client.messages.stream.return_value = fake_stream_mgr

        client = AnthropicClient(api_key="test-key", model_id="claude-test")
        img = Image.new("RGB", (1280, 800), color=(100, 100, 100))
        images = [(img, "primary focus (image dimensions: 1280x800 pixels)")]

        with client.ask_stream(
            images=images, transcript="how do I save",
            history=[],
        ) as stream:
            deltas = list(stream.text_deltas())
            result = stream.final_result()

        assert deltas == ["hello ", "world"]
        assert result.coordinate is None

        call_kwargs = fake_client.messages.stream.call_args.kwargs
        assert call_kwargs["model"] == "claude-test"
        assert call_kwargs["max_tokens"] == 1024
        assert call_kwargs["system"] == _CLICKY_SYSTEM_PROMPT
        assert "tools" not in call_kwargs
        assert "extra_headers" not in call_kwargs

        messages = call_kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert content[0]["type"] == "image"
        assert content[1]["type"] == "text"
        assert "1280x800" in content[1]["text"]
        assert content[2]["type"] == "text"
        assert "how do I save" in content[2]["text"]

    def test_ask_stream_prepends_history(self, mocker):
        from PIL import Image
        from ai import AnthropicClient

        fake_anthropic_class = mocker.patch("ai.Anthropic")
        fake_client = fake_anthropic_class.return_value

        fake_stream = mocker.MagicMock()
        fake_stream.text_stream = iter([])
        fake_stream.get_final_text.return_value = "ok [POINT:none]"
        fake_stream_mgr = mocker.MagicMock()
        fake_stream_mgr.__enter__ = mocker.MagicMock(return_value=fake_stream)
        fake_stream_mgr.__exit__ = mocker.MagicMock(return_value=False)
        fake_client.messages.stream.return_value = fake_stream_mgr

        history = [
            {"role": "user", "content": [{"type": "text", "text": "prior q"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "prior a"}]},
        ]

        client = AnthropicClient(api_key="test-key", model_id="claude-test")
        img = Image.new("RGB", (1280, 800))
        images = [(img, "primary focus (image dimensions: 1280x800 pixels)")]

        with client.ask_stream(
            images=images, transcript="next q", history=history,
        ) as stream:
            list(stream.text_deltas())

        messages = fake_client.messages.stream.call_args.kwargs["messages"]
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "user"

    def test_ask_stream_custom_system_prompt(self, mocker):
        from PIL import Image
        from ai import AnthropicClient

        fake_anthropic_class = mocker.patch("ai.Anthropic")
        fake_client = fake_anthropic_class.return_value

        fake_stream = mocker.MagicMock()
        fake_stream.text_stream = iter([])
        fake_stream.get_final_text.return_value = "hi [POINT:none]"
        fake_stream_mgr = mocker.MagicMock()
        fake_stream_mgr.__enter__ = mocker.MagicMock(return_value=fake_stream)
        fake_stream_mgr.__exit__ = mocker.MagicMock(return_value=False)
        fake_client.messages.stream.return_value = fake_stream_mgr

        client = AnthropicClient(api_key="test-key", model_id="claude-test")
        img = Image.new("RGB", (1280, 800))
        images = [(img, "primary focus (image dimensions: 1280x800 pixels)")]

        with client.ask_stream(
            images=images, transcript="test", history=[],
            system_prompt="custom prompt",
        ) as stream:
            list(stream.text_deltas())

        assert fake_client.messages.stream.call_args.kwargs["system"] == "custom prompt"


# --- AnthropicClient.ask (batch wrapper) --------------------------------------

class TestAnthropicClientAsk:
    """Tests for the batch ask() wrapper."""

    def test_ask_returns_parsed_dict_with_coordinate(self, mocker):
        from PIL import Image
        from ai import AnthropicClient

        fake_anthropic_class = mocker.patch("ai.Anthropic")
        fake_client = fake_anthropic_class.return_value

        fake_stream = mocker.MagicMock()
        fake_stream.text_stream = iter(["save is top-left. [POINT:450,80:save button]"])
        fake_stream.get_final_text.return_value = "save is top-left. [POINT:450,80:save button]"
        fake_stream_mgr = mocker.MagicMock()
        fake_stream_mgr.__enter__ = mocker.MagicMock(return_value=fake_stream)
        fake_stream_mgr.__exit__ = mocker.MagicMock(return_value=False)
        fake_client.messages.stream.return_value = fake_stream_mgr

        client = AnthropicClient(api_key="test-key", model_id="claude-test")
        img = Image.new("RGB", (1280, 800))
        result = client.ask(
            image=img, transcript="test", history=[],
            declared_w=1280, declared_h=800,
        )

        assert result["text"] == "save is top-left."
        assert len(result["points"]) == 1
        assert result["points"][0]["x"] == 450
        assert result["points"][0]["y"] == 80
        assert result["points"][0]["label"] == "save button"

    def test_ask_returns_empty_points_on_point_none(self, mocker):
        from PIL import Image
        from ai import AnthropicClient

        fake_anthropic_class = mocker.patch("ai.Anthropic")
        fake_client = fake_anthropic_class.return_value

        fake_stream = mocker.MagicMock()
        fake_stream.text_stream = iter(["no element. [POINT:none]"])
        fake_stream.get_final_text.return_value = "no element. [POINT:none]"
        fake_stream_mgr = mocker.MagicMock()
        fake_stream_mgr.__enter__ = mocker.MagicMock(return_value=fake_stream)
        fake_stream_mgr.__exit__ = mocker.MagicMock(return_value=False)
        fake_client.messages.stream.return_value = fake_stream_mgr

        client = AnthropicClient(api_key="test-key", model_id="claude-test")
        img = Image.new("RGB", (1280, 800))
        result = client.ask(
            image=img, transcript="what is html", history=[],
            declared_w=1280, declared_h=800,
        )

        assert result["text"] == "no element."
        assert result["points"] == []
