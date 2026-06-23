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
        # system= is now a list-of-blocks carrying cache_control (Path A Task 3).
        assert call_kwargs["system"] == [
            {
                "type": "text",
                "text": _CLICKY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
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

        assert fake_client.messages.stream.call_args.kwargs["system"] == [
            {
                "type": "text",
                "text": "custom prompt",
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def test_ask_stream_system_prompt_uses_cache_control(self, mocker):
        """System prompt must be a list-of-blocks with cache_control: ephemeral.

        Saves ~50-100ms TTFT per turn after first cache hit (OpenRouter passes
        Anthropic-native cache_control through for anthropic/* routes).
        """
        from PIL import Image
        from ai import AnthropicClient, _CLICKY_SYSTEM_PROMPT

        fake_anthropic_class = mocker.patch("ai.Anthropic")
        fake_client = fake_anthropic_class.return_value
        fake_stream = mocker.MagicMock()
        fake_stream.text_stream = iter([])
        fake_stream.get_final_text.return_value = "ok [POINT:none]"
        fake_stream_mgr = mocker.MagicMock()
        fake_stream_mgr.__enter__ = mocker.MagicMock(return_value=fake_stream)
        fake_stream_mgr.__exit__ = mocker.MagicMock(return_value=False)
        fake_client.messages.stream.return_value = fake_stream_mgr

        client = AnthropicClient(api_key="test-key", model_id="anthropic/claude-sonnet-4-6")
        img = Image.new("RGB", (1280, 800))
        with client.ask_stream(
            images=[(img, "label")], transcript="what's this", history=[],
        ) as stream:
            list(stream.text_deltas())

        system_arg = fake_client.messages.stream.call_args.kwargs["system"]
        assert isinstance(system_arg, list), (
            "Expected system= to be a list of content blocks (required for "
            f"cache_control), got {type(system_arg)}"
        )
        assert len(system_arg) == 1
        assert system_arg[0]["type"] == "text"
        assert system_arg[0]["text"] == _CLICKY_SYSTEM_PROMPT
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

    def test_ask_stream_memory_prefix_gets_cache_control(self, mocker):
        """Memory-context prefix of user transcript must be split into its own
        cached text block; the current transcript stays uncached.

        Avoids the full-context-caching latency paradox (arxiv 2601.06007) —
        we only cache the stable prefix, never per-turn dynamic content.
        """
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

        transcript_with_memory = (
            "[context from past sessions — use silently, don't summarize or reference it:]\n"
            "User asked about freeze panes in Excel yesterday.\n\n"
            "how do I hide gridlines"
        )

        client = AnthropicClient(api_key="test-key", model_id="anthropic/claude-sonnet-4-6")
        img = Image.new("RGB", (1280, 800))
        with client.ask_stream(
            images=[(img, "label")], transcript=transcript_with_memory, history=[],
        ) as stream:
            list(stream.text_deltas())

        content = fake_client.messages.stream.call_args.kwargs["messages"][-1]["content"]
        # Find the memory-context text block
        memory_block = next(
            (b for b in content
             if b.get("type") == "text" and "context from past sessions" in b.get("text", "")),
            None,
        )
        assert memory_block is not None, (
            "Memory-context block not found in user message content"
        )
        assert memory_block.get("cache_control") == {"type": "ephemeral"}, (
            "Memory-context block must have cache_control: ephemeral"
        )
        # And the current transcript must be a SEPARATE block without cache_control
        current_block = next(
            (b for b in content
             if b.get("type") == "text" and "hide gridlines" in b.get("text", "")),
            None,
        )
        assert current_block is not None
        assert "cache_control" not in current_block, (
            "Current-turn transcript must NOT be cached (dynamic per turn)"
        )

    def test_ask_stream_with_kb_content_appends_second_system_block(self, mocker):
        """When kb_content is provided, system_blocks must have TWO entries:
        the persona (block 1) and the KB block (block 2), both cache_control:
        ephemeral. KB block's text must contain the marker prefix + the
        kb_app_name (with .exe stripped) + the raw KB content."""
        from PIL import Image
        from ai import AnthropicClient, _CLICKY_SYSTEM_PROMPT

        fake_anthropic_class = mocker.patch("ai.Anthropic")
        fake_client = fake_anthropic_class.return_value
        fake_stream = mocker.MagicMock()
        fake_stream.text_stream = iter([])
        fake_stream.get_final_text.return_value = "ok [POINT:none]"
        fake_stream_mgr = mocker.MagicMock()
        fake_stream_mgr.__enter__ = mocker.MagicMock(return_value=fake_stream)
        fake_stream_mgr.__exit__ = mocker.MagicMock(return_value=False)
        fake_client.messages.stream.return_value = fake_stream_mgr

        client = AnthropicClient(
            api_key="test-key", model_id="anthropic/claude-sonnet-4-6"
        )
        img = Image.new("RGB", (1280, 800))
        with client.ask_stream(
            images=[(img, "label")],
            transcript="how do I plot YM vs density",
            history=[],
            kb_content="# Granta EduPack KB\n\nPlot via Chart > Add...",
            kb_app_name="edupack.exe",
        ) as stream:
            list(stream.text_deltas())

        system_arg = fake_client.messages.stream.call_args.kwargs["system"]
        assert isinstance(system_arg, list)
        assert len(system_arg) == 2, (
            "Expected 2 system blocks (persona + KB), "
            f"got {len(system_arg)}: {[b.get('text', '')[:50] for b in system_arg]}"
        )
        # Block 1 = persona (unchanged)
        assert system_arg[0]["text"] == _CLICKY_SYSTEM_PROMPT
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}
        # Block 2 = KB injection
        kb_block = system_arg[1]
        assert kb_block["type"] == "text"
        assert kb_block["cache_control"] == {"type": "ephemeral"}
        assert "app knowledge base" in kb_block["text"]
        assert "edupack" in kb_block["text"], (
            "Display name (kb_app_name with .exe stripped) must appear "
            "in the marker"
        )
        assert ".exe" not in kb_block["text"].split("\n\n")[0], (
            "The .exe suffix should be stripped from the prose marker"
        )
        assert "# Granta EduPack KB" in kb_block["text"], (
            "Raw KB markdown body must be present"
        )
        assert "Plot via Chart > Add..." in kb_block["text"]

    def test_ask_stream_without_kb_content_keeps_one_system_block(self, mocker):
        """When kb_content is empty (default), system_blocks must have only
        the persona block — no second KB block. This is the 'Claude already
        knows that software' path."""
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

        client = AnthropicClient(
            api_key="test-key", model_id="anthropic/claude-sonnet-4-6"
        )
        img = Image.new("RGB", (1280, 800))
        # Call with NO kb_content / kb_app_name — should default to empty
        with client.ask_stream(
            images=[(img, "label")], transcript="hello", history=[],
        ) as stream:
            list(stream.text_deltas())

        system_arg = fake_client.messages.stream.call_args.kwargs["system"]
        assert isinstance(system_arg, list)
        assert len(system_arg) == 1, (
            "Expected 1 system block (persona only) when no kb_content, "
            f"got {len(system_arg)}"
        )
        # Sanity: persona block has no KB marker
        assert "app knowledge base" not in system_arg[0]["text"]


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

    def test_ask_stream_with_kb_content_concats_into_system_string(self, mocker):
        """Gemini via OpenRouter OpenAI-compat doesn't support multiple
        system blocks or cache_control breakpoints, so kb_content must
        concat onto the system prompt as a plain string. The marker
        prefix + display_name (kb_app_name minus .exe) + raw KB body
        must all appear in messages[0]['content']."""
        from PIL import Image
        client, mock_instance, _ = self._make_client(mocker)

        fake_stream = mocker.MagicMock(name="openai_stream")
        mock_instance.chat.completions.create.return_value = fake_stream

        img = Image.new("RGB", (100, 60), color="white")
        client.ask_stream(
            images=[(img, "label")],
            transcript="how do I plot YM vs density",
            history=[],
            kb_content="# Granta EduPack KB\n\nUse Chart > Add to plot.",
            kb_app_name="edupack.exe",
        )

        kwargs = mock_instance.chat.completions.create.call_args.kwargs
        system_msg = kwargs["messages"][0]
        assert system_msg["role"] == "system"
        # System content must have BOTH the persona prompt AND the KB
        # marker + body concatenated.
        content = system_msg["content"]
        assert "clicky" in content.lower(), "persona prompt must be preserved"
        assert "app knowledge base" in content, "KB marker must be present"
        assert "edupack" in content, (
            "Display name (kb_app_name with .exe stripped) must appear"
        )
        # Ensure the .exe suffix didn't leak into the prose marker line.
        marker_line = next(
            (line for line in content.splitlines()
             if "you are helping the user with" in line),
            "",
        )
        assert ".exe" not in marker_line, (
            "The .exe suffix should be stripped from the prose marker"
        )
        assert "# Granta EduPack KB" in content, "raw KB markdown body required"
        assert "Chart > Add" in content

    def test_ask_stream_without_kb_content_uses_plain_system_prompt(self, mocker):
        """Empty kb_content (default) → system message is just the
        persona prompt. No marker, no KB content."""
        from PIL import Image
        client, mock_instance, _ = self._make_client(mocker)

        fake_stream = mocker.MagicMock(name="openai_stream")
        mock_instance.chat.completions.create.return_value = fake_stream

        img = Image.new("RGB", (100, 60), color="white")
        client.ask_stream(
            images=[(img, "label")], transcript="hello", history=[],
        )

        kwargs = mock_instance.chat.completions.create.call_args.kwargs
        system_msg = kwargs["messages"][0]
        assert "app knowledge base" not in system_msg["content"], (
            "No KB marker should appear when kb_content is empty"
        )

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
        # 'cohere/' is a genuinely unsupported prefix (openai/ became valid in v0.3.0).
        with pytest.raises(ValueError) as excinfo:
            create_ai_client(model_id="cohere/command-r", api_key="test-key")
        msg = str(excinfo.value)
        assert "cohere/command-r" in msg
        assert "anthropic/" in msg
        assert "google/" in msg
        assert "openai/" in msg  # openai/ now listed as supported

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

    def test_anthropic_with_openrouter_key_auto_routes_to_openrouter(self, mocker):
        """sk-or-v1-* OpenRouter key prefix triggers OpenRouter base URL
        even when no explicit base_url is passed. Closes the bundled-EXE
        401 bug where .env isn't loaded so ANTHROPIC_BASE_URL env var
        is missing — without this fallback the Anthropic SDK defaults
        to api.anthropic.com which rejects OpenRouter-namespaced keys.
        """
        mock_anthropic = mocker.patch("ai.Anthropic")
        from ai import create_ai_client
        create_ai_client(
            model_id="anthropic/claude-sonnet-4-6",
            api_key="sk-or-v1-d16e1d434a7808ef8fc276b09213983270952aae",
        )
        kwargs = mock_anthropic.call_args.kwargs
        assert kwargs.get("base_url") == "https://openrouter.ai/api", (
            "OpenRouter sk-or- key must auto-route to openrouter.ai endpoint"
        )

    def test_anthropic_with_direct_key_does_not_set_base_url(self, mocker):
        """sk-ant-* direct Anthropic keys must NOT trigger any base_url
        override — they're valid for Anthropic SDK's default endpoint
        (api.anthropic.com). Auto-routing is opt-in via prefix."""
        mock_anthropic = mocker.patch("ai.Anthropic")
        from ai import create_ai_client
        create_ai_client(
            model_id="anthropic/claude-sonnet-4-6",
            api_key="sk-ant-api03-real-anthropic-key",
        )
        kwargs = mock_anthropic.call_args.kwargs
        # Anthropic SDK gets called without base_url — uses its default.
        assert "base_url" not in kwargs, (
            "Direct Anthropic keys must not have base_url overridden"
        )

    def test_explicit_base_url_overrides_openrouter_auto_detect(self, mocker):
        """If caller passes an explicit base_url, the auto-detect logic
        must NOT override it — explicit choice wins. This protects
        against edge cases like staging environments or proxy services."""
        mock_anthropic = mocker.patch("ai.Anthropic")
        from ai import create_ai_client
        create_ai_client(
            model_id="anthropic/claude-sonnet-4-6",
            api_key="sk-or-v1-test-key",  # would auto-route...
            base_url="https://my-proxy.example.com",  # ...but explicit wins
        )
        kwargs = mock_anthropic.call_args.kwargs
        assert kwargs.get("base_url") == "https://my-proxy.example.com"


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


# --- OllamaClient (v0.2.0 local LLM support) --------------------------------

class TestOllamaClient:
    """Tests for ai.OllamaClient — local LLM via Ollama /api/chat.

    Mirrors the DI-mock pattern from TestGeminiClient: mock httpx.Client,
    drive a fake streaming JSON-per-line response, assert on request shape
    and emitted text deltas.

    Interface contract (must match AnthropicClient + GeminiClient):
        ask_stream(images=[(PIL.Image, label)], transcript, history, ...)
        returns a context manager with .text_deltas() generator + .final_result()
        method returning PointParseResult.
    """

    def _make_client_with_stream(self, mocker, json_lines):
        """Build OllamaClient whose httpx stream yields the given JSON-encoded lines.

        json_lines: list of dicts. Each dict is JSON-serialized + bytes-encoded
        into the fake response's iter_lines() output. Last dict should have
        {"done": True} to terminate cleanly.
        """
        from ai import OllamaClient

        # Mock the response object yielded by httpx.Client.stream() context manager
        mock_response = mocker.MagicMock(name="ollama_response")
        mock_response.status_code = 200
        mock_response.raise_for_status = mocker.MagicMock()

        # iter_lines() yields the encoded JSON lines
        import json as _json
        encoded_lines = [_json.dumps(d).encode("utf-8") for d in json_lines]
        mock_response.iter_lines = mocker.MagicMock(return_value=iter(encoded_lines))

        # Context manager wrapping the response
        mock_stream_cm = mocker.MagicMock(name="stream_cm")
        mock_stream_cm.__enter__ = mocker.MagicMock(return_value=mock_response)
        mock_stream_cm.__exit__ = mocker.MagicMock(return_value=None)

        # httpx.Client() instance with .stream() method
        mock_httpx_instance = mocker.MagicMock(name="httpx_client")
        mock_httpx_instance.__enter__ = mocker.MagicMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__exit__ = mocker.MagicMock(return_value=None)
        mock_httpx_instance.stream = mocker.MagicMock(return_value=mock_stream_cm)

        # Patch httpx.Client at the call site
        mocker.patch("ai.httpx.Client", return_value=mock_httpx_instance)

        client = OllamaClient(host="http://localhost:11434", model_id="ollama/llama3.2-vision")
        return client, mock_httpx_instance, mock_response

    def test_construction_stores_host_and_strips_ollama_prefix(self, mocker):
        from ai import OllamaClient
        client = OllamaClient(host="http://localhost:11434", model_id="ollama/llama3.2-vision")
        # Internally strips "ollama/" prefix because Ollama API wants just the model name
        assert client.model_id == "llama3.2-vision"
        assert client.host == "http://localhost:11434"

    def test_construction_strips_trailing_slash_from_host(self, mocker):
        from ai import OllamaClient
        client = OllamaClient(host="http://localhost:11434/", model_id="ollama/llama3.2-vision")
        assert client.host == "http://localhost:11434"

    def test_construction_keeps_bare_model_name_unchanged(self, mocker):
        """If user sets MODEL_ID=llama3.2-vision (bare, no prefix), keep as-is."""
        from ai import OllamaClient
        client = OllamaClient(host="http://localhost:11434", model_id="llama3.2-vision")
        assert client.model_id == "llama3.2-vision"

    def test_ask_stream_returns_context_manager_with_text_deltas(self, mocker):
        """Happy path: streaming yields the model's text chunks via text_deltas()."""
        from PIL import Image

        client, mock_httpx, _ = self._make_client_with_stream(mocker, [
            {"message": {"content": "click "}, "done": False},
            {"message": {"content": "the save "}, "done": False},
            {"message": {"content": "button."}, "done": False},
            {"message": {"content": ""}, "done": True},
        ])

        img = Image.new("RGB", (100, 60), color="white")
        with client.ask_stream(
            images=[(img, "primary focus")],
            transcript="where is save",
            history=[],
        ) as stream:
            deltas = list(stream.text_deltas())

        assert "".join(deltas) == "click the save button."

    def test_ask_stream_sends_base64_images_in_user_message(self, mocker):
        """Verify request payload contains base64-encoded JPEG in user.images."""
        from PIL import Image

        client, mock_httpx, _ = self._make_client_with_stream(mocker, [
            {"message": {"content": "ok"}, "done": True},
        ])

        img = Image.new("RGB", (100, 60), color="white")
        with client.ask_stream(
            images=[(img, "primary focus (image dimensions: 100x60 pixels)")],
            transcript="point at something",
            history=[],
        ) as stream:
            list(stream.text_deltas())

        # httpx.Client.stream(...) was called with the right payload
        mock_httpx.stream.assert_called_once()
        call_args = mock_httpx.stream.call_args
        method = call_args.args[0] if call_args.args else call_args.kwargs.get("method")
        url = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("url")
        payload = call_args.kwargs.get("json", {})

        assert method == "POST"
        assert url == "http://localhost:11434/api/chat"
        assert payload["model"] == "llama3.2-vision"   # ollama/ prefix stripped
        assert payload["stream"] is True

        messages = payload["messages"]
        # First message: Clicky system prompt
        assert messages[0]["role"] == "system"
        assert "clicky" in messages[0]["content"].lower()
        # Last message: user with content text + images list (base64-encoded JPEGs)
        user_msg = messages[-1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "point at something"
        assert "images" in user_msg
        assert len(user_msg["images"]) == 1
        # First (and only) image: base64 string, not bytes
        assert isinstance(user_msg["images"][0], str)
        # Sanity: looks like base64 (alphanumeric + / + =)
        import re as _re
        assert _re.match(r"^[A-Za-z0-9+/=]+$", user_msg["images"][0])

    def test_ask_stream_404_raises_friendly_runtime_error(self, mocker):
        """If Ollama returns 404, error message should tell user to ollama pull."""
        from ai import OllamaClient
        from PIL import Image

        mock_response = mocker.MagicMock(name="ollama_404")
        mock_response.status_code = 404
        mock_response.raise_for_status = mocker.MagicMock()

        mock_stream_cm = mocker.MagicMock()
        mock_stream_cm.__enter__ = mocker.MagicMock(return_value=mock_response)
        mock_stream_cm.__exit__ = mocker.MagicMock(return_value=None)

        mock_httpx_instance = mocker.MagicMock()
        mock_httpx_instance.__enter__ = mocker.MagicMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__exit__ = mocker.MagicMock(return_value=None)
        mock_httpx_instance.stream = mocker.MagicMock(return_value=mock_stream_cm)
        mocker.patch("ai.httpx.Client", return_value=mock_httpx_instance)

        client = OllamaClient(host="http://localhost:11434", model_id="ollama/qwen2.5-vl")
        img = Image.new("RGB", (50, 50))
        with pytest.raises(RuntimeError) as exc_info:
            with client.ask_stream(
                images=[(img, "x")],
                transcript="?",
                history=[],
            ) as stream:
                list(stream.text_deltas())

        msg = str(exc_info.value)
        assert "qwen2.5-vl" in msg
        assert "ollama pull" in msg.lower()

    def test_ask_stream_skips_empty_content_chunks(self, mocker):
        """Ollama emits metadata-only chunks with empty content — don't yield empties."""
        from PIL import Image

        client, _, _ = self._make_client_with_stream(mocker, [
            {"message": {"content": ""}, "done": False},     # skip — empty
            {"message": {"content": "hi"}, "done": False},
            {"message": {"content": ""}, "done": True},      # skip + stop
        ])

        img = Image.new("RGB", (50, 50))
        with client.ask_stream(
            images=[(img, "x")],
            transcript="?",
            history=[],
        ) as stream:
            deltas = list(stream.text_deltas())

        assert deltas == ["hi"]

    def test_ask_stream_final_result_parses_point_tag(self, mocker):
        """After streaming, final_result() returns PointParseResult with [POINT:x,y] extracted."""
        from PIL import Image

        client, _, _ = self._make_client_with_stream(mocker, [
            {"message": {"content": "click the save button. "}, "done": False},
            {"message": {"content": "[POINT:640,400:save]"}, "done": True},
        ])

        img = Image.new("RGB", (100, 60))
        with client.ask_stream(
            images=[(img, "primary focus")],
            transcript="where is save",
            history=[],
        ) as stream:
            list(stream.text_deltas())
            result = stream.final_result()

        assert result.coordinate == (640, 400)
        assert result.element_label == "save"
        assert result.spoken_text == "click the save button."

    def test_ask_stream_history_converted_to_plain_strings(self, mocker):
        """History stored in Anthropic content-block format must be flattened
        to plain strings for Ollama's OpenAI-style messages array."""
        from PIL import Image

        client, mock_httpx, _ = self._make_client_with_stream(mocker, [
            {"message": {"content": "ok"}, "done": True},
        ])

        history = [
            {"role": "user", "content": [{"type": "text", "text": "what is html"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "the skeleton."}]},
        ]
        img = Image.new("RGB", (100, 60))
        with client.ask_stream(
            images=[(img, "x")],
            transcript="what about css",
            history=history,
        ) as stream:
            list(stream.text_deltas())

        payload = mock_httpx.stream.call_args.kwargs["json"]
        messages = payload["messages"]
        # system + 2 history + 1 new user = 4 messages
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "what is html"}
        assert messages[2] == {"role": "assistant", "content": "the skeleton."}
        assert messages[3]["role"] == "user"
        assert messages[3]["content"] == "what about css"

    def test_ask_stream_with_kb_content_concats_into_system(self, mocker):
        """Ollama doesn't support multi-block cache_control; concat KB into system."""
        from PIL import Image

        client, mock_httpx, _ = self._make_client_with_stream(mocker, [
            {"message": {"content": "ok"}, "done": True},
        ])

        img = Image.new("RGB", (50, 50))
        with client.ask_stream(
            images=[(img, "x")],
            transcript="how do I plot Young's modulus",
            history=[],
            kb_content="GrantaEduPack docs: use the Chart Stage menu...",
            kb_app_name="edupack.exe",
        ) as stream:
            list(stream.text_deltas())

        payload = mock_httpx.stream.call_args.kwargs["json"]
        system_msg = payload["messages"][0]
        assert system_msg["role"] == "system"
        # System prompt + KB block concatenated
        assert "clicky" in system_msg["content"].lower()
        assert "edupack" in system_msg["content"].lower()
        assert "GrantaEduPack docs" in system_msg["content"]


# --- Regression tests for superpowers:code-reviewer findings (v0.2.0) -------

class TestOllamaClientReviewerFixes:
    """Regression tests for the 3 blockers caught by superpowers:code-reviewer
    on 2026-06-05 (the v0.2.0 sprint pre-handoff review)."""

    def test_blocker1_closes_httpx_client_when_stream_raises(self, mocker):
        """BLOCKER 1 regression: if httpx.Client.stream() raises (Ollama down,
        DNS failure, ECONNREFUSED), the previously-opened httpx_client must
        be closed — caller's `with` block never enters, so __exit__ won't
        fire. Without the fix, every Ollama-unreachable interaction would
        leak a connection pool.
        """
        from ai import OllamaClient, _OllamaStreamingResponse
        from PIL import Image

        # Mock httpx.Client that raises on .stream()
        mock_httpx_instance = mocker.MagicMock()
        mock_httpx_instance.__enter__ = mocker.MagicMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__exit__ = mocker.MagicMock(return_value=None)
        mock_httpx_instance.stream = mocker.MagicMock(side_effect=ConnectionError("Connection refused"))
        mocker.patch("ai.httpx.Client", return_value=mock_httpx_instance)

        client = OllamaClient(host="http://localhost:11434", model_id="ollama/x")
        img = Image.new("RGB", (50, 50))
        with pytest.raises(ConnectionError):
            with client.ask_stream(images=[(img, "x")], transcript="?", history=[]) as stream:
                # Never reached — __enter__ raises
                pass

        # The crucial assertion: __exit__ WAS called on the httpx client to
        # release the connection pool. Without the BLOCKER 1 fix this would
        # be 0 (leak).
        assert mock_httpx_instance.__exit__.call_count == 1

    def test_blocker1_closes_httpx_client_when_raise_for_status_raises(self, mocker):
        """BLOCKER 1 regression sibling: if response.raise_for_status() raises
        (non-2xx, non-404 — e.g. 500 Internal Server Error), the httpx client
        must still be closed before re-raising."""
        from ai import OllamaClient
        from PIL import Image

        mock_response = mocker.MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = mocker.MagicMock(
            side_effect=RuntimeError("500 Internal Server Error")
        )

        mock_stream_cm = mocker.MagicMock()
        mock_stream_cm.__enter__ = mocker.MagicMock(return_value=mock_response)
        mock_stream_cm.__exit__ = mocker.MagicMock(return_value=None)

        mock_httpx_instance = mocker.MagicMock()
        mock_httpx_instance.__enter__ = mocker.MagicMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__exit__ = mocker.MagicMock(return_value=None)
        mock_httpx_instance.stream = mocker.MagicMock(return_value=mock_stream_cm)
        mocker.patch("ai.httpx.Client", return_value=mock_httpx_instance)

        client = OllamaClient(host="http://localhost:11434", model_id="ollama/x")
        img = Image.new("RGB", (50, 50))
        with pytest.raises(RuntimeError, match="500"):
            with client.ask_stream(images=[(img, "x")], transcript="?", history=[]) as stream:
                pass

        # Both the stream context AND the httpx client must be cleaned up.
        assert mock_stream_cm.__exit__.call_count == 1
        assert mock_httpx_instance.__exit__.call_count == 1

    def test_blocker3_final_result_safe_after_text_deltas_raises(self, mocker):
        """BLOCKER 3 regression: if text_deltas() raises mid-stream (Ollama
        crash, network drop), final_result() must NOT re-enter the iterator
        and re-raise — it must return parse_point_tag of whatever was
        accumulated before the failure (graceful degradation).
        """
        from ai import OllamaClient
        from PIL import Image

        # Build a stream that yields 2 valid chunks then raises ReadError-like
        def failing_iter_lines():
            yield b'{"message":{"content":"partial "},"done":false}'
            yield b'{"message":{"content":"response"},"done":false}'
            raise ConnectionError("network drop mid-stream")

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = mocker.MagicMock()
        mock_response.iter_lines = mocker.MagicMock(return_value=failing_iter_lines())

        mock_stream_cm = mocker.MagicMock()
        mock_stream_cm.__enter__ = mocker.MagicMock(return_value=mock_response)
        mock_stream_cm.__exit__ = mocker.MagicMock(return_value=None)

        mock_httpx_instance = mocker.MagicMock()
        mock_httpx_instance.__enter__ = mocker.MagicMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__exit__ = mocker.MagicMock(return_value=None)
        mock_httpx_instance.stream = mocker.MagicMock(return_value=mock_stream_cm)
        mocker.patch("ai.httpx.Client", return_value=mock_httpx_instance)

        client = OllamaClient(host="http://localhost:11434", model_id="ollama/x")
        img = Image.new("RGB", (50, 50))
        with client.ask_stream(images=[(img, "x")], transcript="?", history=[]) as stream:
            # Consume deltas — will raise mid-stream
            collected = []
            try:
                for delta in stream.text_deltas():
                    collected.append(delta)
            except ConnectionError:
                pass  # Expected
            # The crucial assertion: final_result() must NOT re-raise the
            # ConnectionError. It must return a PointParseResult with what
            # was accumulated before the failure. Without BLOCKER 3 fix
            # this would re-iterate text_deltas() and re-raise.
            result = stream.final_result()
            # Accumulated text was "partial response" (no [POINT:x,y] tag)
            assert result.coordinate is None
            assert "partial" in result.spoken_text
            assert "response" in result.spoken_text


# --- create_ai_client factory: Ollama dispatch (v0.2.0) -----------------------

class TestCreateAIClientOllama:
    """Extends TestCreateAIClient: factory now dispatches ollama/* prefix
    (and bare llama*/qwen*/llava* names) to OllamaClient."""

    def test_routes_ollama_prefix_to_ollama_client(self, mocker):
        from ai import create_ai_client, OllamaClient
        mocker.patch("ai.httpx")  # don't make real HTTP calls
        client = create_ai_client(
            model_id="ollama/llama3.2-vision",
            api_key="",   # no key needed for local Ollama
            ollama_host="http://localhost:11434",
        )
        assert isinstance(client, OllamaClient)
        assert client.model_id == "llama3.2-vision"  # prefix stripped

    def test_routes_bare_llama_prefix_to_ollama_client(self, mocker):
        """If MODEL_ID=llama3.2-vision (Bitshank-style bare name), route to Ollama."""
        from ai import create_ai_client, OllamaClient
        mocker.patch("ai.httpx")
        client = create_ai_client(
            model_id="llama3.2-vision",
            api_key="",
            ollama_host="http://localhost:11434",
        )
        assert isinstance(client, OllamaClient)

    def test_routes_bare_qwen_prefix_to_ollama_client(self, mocker):
        from ai import create_ai_client, OllamaClient
        mocker.patch("ai.httpx")
        client = create_ai_client(
            model_id="qwen2.5-vl",
            api_key="",
            ollama_host="http://localhost:11434",
        )
        assert isinstance(client, OllamaClient)

    def test_routes_bare_llava_prefix_to_ollama_client(self, mocker):
        from ai import create_ai_client, OllamaClient
        mocker.patch("ai.httpx")
        client = create_ai_client(
            model_id="llava:13b",
            api_key="",
            ollama_host="http://localhost:11434",
        )
        assert isinstance(client, OllamaClient)

    def test_ollama_routing_uses_default_host_when_not_passed(self, mocker):
        from ai import create_ai_client, OllamaClient
        mocker.patch("ai.httpx")
        client = create_ai_client(
            model_id="ollama/llama3.2-vision",
            api_key="",
            # ollama_host omitted — should fall back to default
        )
        assert isinstance(client, OllamaClient)
        assert client.host == "http://localhost:11434"

    def test_ollama_routing_does_not_break_existing_anthropic_dispatch(self, mocker):
        """Regression check: Anthropic dispatch still works alongside new Ollama branch."""
        from ai import create_ai_client, AnthropicClient
        mocker.patch("ai.Anthropic")
        client = create_ai_client(
            model_id="anthropic/claude-sonnet-4-6",
            api_key="sk-ant-test",
        )
        assert isinstance(client, AnthropicClient)


class TestOpenAIVisionClient:
    """v0.3.0: OpenAIVisionClient (GPT-4o via OpenAI native API) is a thin
    subclass of GeminiClient — same OpenAI-chat-completions shape, just
    OpenAI's endpoint instead of OpenRouter. Strips the 'openai/' routing
    prefix so the API gets the bare model name."""

    def test_strips_openai_prefix_from_model_id(self, mocker):
        from ai import OpenAIVisionClient
        mocker.patch("ai.OpenAI")
        client = OpenAIVisionClient(api_key="sk-proj-test", model_id="openai/gpt-4o")
        assert client.model_id == "gpt-4o"  # prefix stripped

    def test_keeps_bare_model_id_unchanged(self, mocker):
        from ai import OpenAIVisionClient
        mocker.patch("ai.OpenAI")
        client = OpenAIVisionClient(api_key="sk-proj-test", model_id="gpt-4o")
        assert client.model_id == "gpt-4o"

    def test_constructs_openai_sdk_with_default_endpoint(self, mocker):
        """base_url=None → OpenAI SDK uses its default (api.openai.com),
        NOT OpenRouter (that's GeminiClient's job)."""
        from ai import OpenAIVisionClient
        mock_openai = mocker.patch("ai.OpenAI")
        OpenAIVisionClient(api_key="sk-proj-test", model_id="openai/gpt-4o")
        kwargs = mock_openai.call_args.kwargs
        assert kwargs["api_key"] == "sk-proj-test"
        assert kwargs.get("base_url") is None

    def test_inherits_ask_stream_from_gemini_client(self):
        """OpenAIVisionClient reuses GeminiClient.ask_stream verbatim —
        same OpenAI chat.completions streaming shape. Confirms the subclass
        doesn't override it."""
        from ai import OpenAIVisionClient, GeminiClient
        assert OpenAIVisionClient.ask_stream is GeminiClient.ask_stream


class TestCreateAIClientOpenAI:
    """Factory dispatch for the 'openai/' prefix → OpenAIVisionClient."""

    def test_routes_openai_prefix_to_vision_client(self, mocker):
        from ai import create_ai_client, OpenAIVisionClient
        mocker.patch("ai.OpenAI")
        client = create_ai_client(model_id="openai/gpt-4o", api_key="sk-proj-test")
        assert isinstance(client, OpenAIVisionClient)
        assert client.model_id == "gpt-4o"  # prefix stripped

    def test_openai_routing_does_not_break_anthropic(self, mocker):
        """Regression: Anthropic dispatch still works alongside the new branch."""
        from ai import create_ai_client, AnthropicClient
        mocker.patch("ai.Anthropic")
        client = create_ai_client(model_id="anthropic/claude-sonnet-4-6", api_key="sk-ant-test")
        assert isinstance(client, AnthropicClient)

    def test_openai_routing_does_not_break_ollama(self, mocker):
        from ai import create_ai_client, OllamaClient
        mocker.patch("ai.httpx")
        client = create_ai_client(model_id="ollama/llava:7b", api_key="")
        assert isinstance(client, OllamaClient)
