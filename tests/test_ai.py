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


# --- GeminiClient -------------------------------------------------------------

class TestGeminiClient:
    """Tests for ai.GeminiClient using DI-mocked openai factory.

    Mirrors the DI-mock pattern from TestAnthropicClient. Zero real network.
    """

    def _make_client(self, mocker):
        """Build GeminiClient with a mock openai.OpenAI instance.

        Returns (client, mock_openai_instance, mock_openai_cls) so tests can
        assert on both the constructor call and the chat.completions mock.
        """
        from ai import GeminiClient
        mock_openai_instance = mocker.MagicMock(name="openai_client")
        mock_openai_cls = mocker.patch("ai.OpenAI", return_value=mock_openai_instance)
        client = GeminiClient(
            api_key="test-key",
            model_id="google/gemini-3-flash-preview",
            base_url="https://openrouter.ai/api/v1",
        )
        return client, mock_openai_instance, mock_openai_cls

    def test_construction_uses_openai_sdk(self, mocker):
        client, mock_instance, mock_cls = self._make_client(mocker)
        mock_cls.assert_called_once_with(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            timeout=60.0,
        )
        assert client.model_id == "google/gemini-3-flash-preview"

    def test_ask_stream_builds_openai_messages_with_image_url(self, mocker):
        from PIL import Image
        client, mock_instance, _ = self._make_client(mocker)

        # Mock the streaming iterator — ask_stream must not consume it here,
        # just build the request.
        fake_stream = mocker.MagicMock(name="openai_stream")
        mock_instance.chat.completions.create.return_value = fake_stream

        img = Image.new("RGB", (100, 60), color="white")
        label = "primary focus (image dimensions: 100x60 pixels)"

        client.ask_stream(
            images=[(img, label)],
            transcript="where is the save button",
            history=[],
        )

        # Assert create() called with OpenAI-shaped messages.
        mock_instance.chat.completions.create.assert_called_once()
        kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "google/gemini-3-flash-preview"
        assert kwargs["stream"] is True
        assert kwargs["max_tokens"] == 1024

        messages = kwargs["messages"]
        # First message: system prompt.
        assert messages[0]["role"] == "system"
        assert "clicky" in messages[0]["content"].lower()
        # Second message: user with image_url + text blocks.
        user_msg = messages[1]
        assert user_msg["role"] == "user"
        assert isinstance(user_msg["content"], list)
        blocks = user_msg["content"]
        assert blocks[0]["type"] == "image_url"
        assert blocks[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert blocks[1]["type"] == "text"
        assert blocks[1]["text"] == label
        assert blocks[2]["type"] == "text"
        assert blocks[2]["text"] == "where is the save button"

    def test_ask_stream_converts_history_content_blocks_to_plain_strings(self, mocker):
        """History is stored in Anthropic format (list of content blocks).
        OpenAI API expects plain string content for assistant/user turns.
        GeminiClient must convert — concatenate all text blocks."""
        from PIL import Image
        client, mock_instance, _ = self._make_client(mocker)
        fake_stream = mocker.MagicMock()
        mock_instance.chat.completions.create.return_value = fake_stream

        history = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is html"},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "html is the skeleton of a web page."},
                ],
            },
        ]
        img = Image.new("RGB", (100, 60))
        client.ask_stream(
            images=[(img, "screen 1")],
            transcript="what about css",
            history=history,
        )

        kwargs = mock_instance.chat.completions.create.call_args.kwargs
        messages = kwargs["messages"]
        # Expected shape: [system, history_user, history_assistant, new_user]
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "what is html"}
        assert messages[2] == {
            "role": "assistant",
            "content": "html is the skeleton of a web page.",
        }
        assert messages[3]["role"] == "user"
        # New user turn is still list-of-blocks (has image).
        assert isinstance(messages[3]["content"], list)

    def test_streaming_yields_deltas_and_parses_point_tag(self, mocker):
        from ai import _GeminiStreamingResponse, PointParseResult

        def make_chunk(text):
            chunk = mocker.MagicMock()
            chunk.choices = [mocker.MagicMock()]
            chunk.choices[0].delta.content = text
            return chunk

        fake_chunks = [
            make_chunk("click the save button. "),
            make_chunk("[POINT:640,400:save button]"),
        ]
        def fake_iterator_gen():
            for c in fake_chunks:
                yield c
        fake_iterator = fake_iterator_gen()

        wrapper = _GeminiStreamingResponse(fake_iterator)
        with wrapper as stream:
            deltas = list(stream.text_deltas())
            result = stream.final_result()

        assert deltas == ["click the save button. ", "[POINT:640,400:save button]"]
        assert isinstance(result, PointParseResult)
        assert result.spoken_text == "click the save button."
        assert result.coordinate == (640, 400)
        assert result.element_label == "save button"

    def test_streaming_empty_delta_chunks_are_skipped(self, mocker):
        """Some OpenAI streaming chunks have delta.content=None (e.g. role-only
        chunk at start, finish_reason chunk at end). Must not crash."""
        from ai import _GeminiStreamingResponse

        def make_chunk(text):
            chunk = mocker.MagicMock()
            chunk.choices = [mocker.MagicMock()]
            chunk.choices[0].delta.content = text
            return chunk

        fake_chunks = [
            make_chunk(None),       # role-only start chunk
            make_chunk("hello. "),
            make_chunk(None),       # finish chunk
            make_chunk("[POINT:none]"),
        ]
        def fake_iterator_gen():
            for c in fake_chunks:
                yield c
        fake_iterator = fake_iterator_gen()

        wrapper = _GeminiStreamingResponse(fake_iterator)
        with wrapper as stream:
            deltas = list(stream.text_deltas())
            result = stream.final_result()

        assert deltas == ["hello. ", "[POINT:none]"]
        assert result.coordinate is None
        assert result.spoken_text == "hello."

    def test_streaming_no_choices_chunk_is_tolerated(self, mocker):
        """OpenRouter occasionally sends keepalive chunks with choices=[].
        Iterator must skip, not crash."""
        from ai import _GeminiStreamingResponse

        def make_chunk_with_choices(text):
            chunk = mocker.MagicMock()
            chunk.choices = [mocker.MagicMock()]
            chunk.choices[0].delta.content = text
            return chunk

        def make_chunk_empty():
            chunk = mocker.MagicMock()
            chunk.choices = []
            return chunk

        fake_chunks = [
            make_chunk_empty(),
            make_chunk_with_choices("ok."),
        ]
        def fake_iterator_gen():
            for c in fake_chunks:
                yield c
        fake_iterator = fake_iterator_gen()

        wrapper = _GeminiStreamingResponse(fake_iterator)
        with wrapper as stream:
            deltas = list(stream.text_deltas())

        assert deltas == ["ok."]


# --- create_ai_client factory ------------------------------------------------

class TestCreateAIClient:
    """Tests for ai.create_ai_client — routes model_id prefix to right subclass."""

    def test_routes_anthropic_prefix_to_anthropic_client(self, mocker):
        from ai import create_ai_client, AnthropicClient
        mocker.patch("ai.Anthropic")  # don't construct real SDK
        client = create_ai_client(
            model_id="anthropic/claude-sonnet-4-6",
            api_key="test-key",
        )
        assert isinstance(client, AnthropicClient)
        assert client.model_id == "anthropic/claude-sonnet-4-6"

    def test_routes_claude_prefix_to_anthropic_client(self, mocker):
        from ai import create_ai_client, AnthropicClient
        mocker.patch("ai.Anthropic")
        client = create_ai_client(
            model_id="claude-sonnet-4-6",  # bare Anthropic ID (non-OpenRouter)
            api_key="test-key",
        )
        assert isinstance(client, AnthropicClient)

    def test_routes_google_prefix_to_gemini_client(self, mocker):
        from ai import create_ai_client, GeminiClient
        mocker.patch("ai.OpenAI")
        client = create_ai_client(
            model_id="google/gemini-3-flash-preview",
            api_key="test-key",
        )
        assert isinstance(client, GeminiClient)
        assert client.model_id == "google/gemini-3-flash-preview"

    def test_routes_gemini_prefix_to_gemini_client(self, mocker):
        from ai import create_ai_client, GeminiClient
        mocker.patch("ai.OpenAI")
        client = create_ai_client(
            model_id="gemini-3-flash-preview",  # bare Google ID
            api_key="test-key",
        )
        assert isinstance(client, GeminiClient)

    def test_unknown_prefix_raises_value_error(self):
        from ai import create_ai_client
        with pytest.raises(ValueError) as excinfo:
            create_ai_client(model_id="openai/gpt-4o", api_key="test-key")
        msg = str(excinfo.value)
        assert "openai/gpt-4o" in msg
        assert "anthropic/" in msg
        assert "google/" in msg

    def test_base_url_override_forwarded_to_anthropic_client(self, mocker):
        """Tier 2.2 fix: base_url override must reach AnthropicClient, not be silently dropped."""
        mock_anthropic = mocker.patch("ai.Anthropic")
        from ai import create_ai_client
        create_ai_client(
            model_id="anthropic/claude-sonnet-4-6",
            api_key="test-key",
            base_url="https://staging.openrouter.ai/api",
        )
        # Anthropic SDK must receive the custom base_url (not silently drop it).
        call_kwargs = mock_anthropic.call_args.kwargs
        assert call_kwargs.get("base_url") == "https://staging.openrouter.ai/api"


# --- GeminiClient additional coverage (post-review gaps) --------------------

class TestGeminiClientExtraCoverage:
    """Coverage for review-flagged gaps: ask() batch wrapper, empty images,
    image-block history turns, empty history content, split-chunk POINT tags,
    error wrapping with diagnostic message."""

    def _make_client_with_stream(self, mocker, stream_text_list):
        """Helper: build GeminiClient whose streaming iterator yields given texts."""
        from ai import GeminiClient
        mock_openai_instance = mocker.MagicMock(name="openai_client")
        mocker.patch("ai.OpenAI", return_value=mock_openai_instance)

        def gen():
            for t in stream_text_list:
                chunk = mocker.MagicMock()
                chunk.choices = [mocker.MagicMock()]
                chunk.choices[0].delta.content = t
                yield chunk

        mock_openai_instance.chat.completions.create.return_value = gen()
        client = GeminiClient(
            api_key="test-key",
            model_id="google/gemini-3-flash-preview",
            base_url="https://openrouter.ai/api/v1",
        )
        return client, mock_openai_instance

    def test_ask_batch_wrapper_returns_dict_with_coordinate(self, mocker):
        from PIL import Image
        client, _ = self._make_client_with_stream(
            mocker, ["click save. ", "[POINT:640,400:save button]"]
        )
        img = Image.new("RGB", (1280, 800))
        result = client.ask(
            image=img,
            transcript="where is save",
            history=[],
            declared_w=1280,
            declared_h=800,
        )
        assert result["text"] == "click save."
        assert result["points"] == [{"x": 640, "y": 400, "label": "save button"}]

    def test_ask_batch_wrapper_text_only_response(self, mocker):
        from PIL import Image
        client, _ = self._make_client_with_stream(
            mocker, ["html is the skeleton. [POINT:none]"]
        )
        img = Image.new("RGB", (1280, 800))
        result = client.ask(
            image=img,
            transcript="what is html",
            history=[],
            declared_w=1280,
            declared_h=800,
        )
        assert result["text"] == "html is the skeleton."
        assert result["points"] == []

    def test_ask_stream_with_empty_images_list(self, mocker):
        """Defensive: empty images list must produce a valid OpenAI request
        with only a text block for the transcript. OpenRouter would 400 on
        empty content, but a transcript-only request is valid."""
        client, mock_instance = self._make_client_with_stream(mocker, [])
        client.ask_stream(
            images=[],
            transcript="what time is it",
            history=[],
        )
        kwargs = mock_instance.chat.completions.create.call_args.kwargs
        user_msg = kwargs["messages"][1]
        assert user_msg["role"] == "user"
        # Should contain exactly one text block for the transcript.
        assert user_msg["content"] == [{"type": "text", "text": "what time is it"}]

    def test_history_with_non_text_blocks_drops_them(self, mocker):
        """Phase 2 edge case: history turn contains image blocks alongside text.
        GeminiClient must extract only text blocks, drop non-text blocks."""
        from PIL import Image
        client, mock_instance = self._make_client_with_stream(mocker, [])
        history = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "data": "..."}},
                    {"type": "text", "text": "what is this"},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "it's a cat."},
                ],
            },
        ]
        img = Image.new("RGB", (100, 60))
        client.ask_stream(
            images=[(img, "screen 1")],
            transcript="and what else",
            history=history,
        )
        messages = mock_instance.chat.completions.create.call_args.kwargs["messages"]
        # [system, history_user_text_only, history_assistant, new_user]
        assert messages[1] == {"role": "user", "content": "what is this"}
        assert messages[2] == {"role": "assistant", "content": "it's a cat."}

    def test_history_with_empty_content_is_skipped(self, mocker):
        """Tier 2.3 fix: history turns with no text content must be dropped,
        not sent as content:"" (which OpenRouter rejects)."""
        from PIL import Image
        client, mock_instance = self._make_client_with_stream(mocker, [])
        history = [
            {"role": "user", "content": []},  # empty content list
            {"role": "assistant", "content": [{"type": "text", "text": ""}]},  # only empty strings
            {"role": "user", "content": [{"type": "text", "text": "real question"}]},
        ]
        img = Image.new("RGB", (100, 60))
        client.ask_stream(
            images=[(img, "s")],
            transcript="follow-up",
            history=history,
        )
        messages = mock_instance.chat.completions.create.call_args.kwargs["messages"]
        # Empty turns dropped; only the real one should appear.
        # [system, real_user_turn, new_user]
        assert len(messages) == 3
        assert messages[1] == {"role": "user", "content": "real question"}

    def test_split_point_tag_across_chunks_still_parses(self, mocker):
        """OpenRouter chunk boundaries are arbitrary. [POINT:640,400:save] might
        arrive as ["[POI", "NT:640,", "400:save]"]. parse_point_tag operates on
        the accumulated string so this should still work."""
        client, _ = self._make_client_with_stream(
            mocker,
            ["click save. [POI", "NT:640,", "400:save button]"],
        )
        from PIL import Image
        img = Image.new("RGB", (1280, 800))
        result = client.ask(
            image=img,
            transcript="where is save",
            history=[],
            declared_w=1280,
            declared_h=800,
        )
        assert result["text"] == "click save."
        assert result["points"] == [{"x": 640, "y": 400, "label": "save button"}]

    def test_request_failure_raises_runtime_error_with_diagnostic(self, mocker):
        """Tier 2.1 fix: OpenRouter errors (401, 402, 404) must be wrapped with
        a diagnostic RuntimeError that points at the likely causes."""
        from ai import GeminiClient
        mock_openai_instance = mocker.MagicMock(name="openai_client")
        mocker.patch("ai.OpenAI", return_value=mock_openai_instance)
        mock_openai_instance.chat.completions.create.side_effect = (
            ConnectionError("404: model gemini-3-flash-preview not available")
        )
        client = GeminiClient(
            api_key="test-key",
            model_id="google/gemini-3-flash-preview",
            base_url="https://openrouter.ai/api/v1",
        )
        from PIL import Image
        img = Image.new("RGB", (100, 60))
        with pytest.raises(RuntimeError) as exc_info:
            client.ask_stream(
                images=[(img, "s")],
                transcript="anything",
                history=[],
            )
        msg = str(exc_info.value)
        assert "gemini-3-flash-preview" in msg
        assert "OpenRouter" in msg
        assert "preview" in msg.lower() or "gemini-2.5-flash" in msg.lower()
        # Original should be chained for debugging.
        assert isinstance(exc_info.value.__cause__, ConnectionError)
