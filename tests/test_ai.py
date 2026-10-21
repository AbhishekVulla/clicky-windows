"""Unit tests for ai.py.

All tests are mock-based. Zero real-API dependency. Green in <2s.
Covers: parse_point_tag, image_to_base64_jpeg, the AIClient base, and each
provider client's ask_stream and ask.
"""

import pytest


def test_ai_module_importable():
    import ai  # noqa: F401


# --- parse_point_tag ----------------------------------------------------------

class TestParsePointTag:
    """Tests for ai.parse_point_tag, the Python port of Clicky's
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

    @pytest.mark.parametrize("text", ["click reset. [POINT:20,30", "click reset. [POINT:", "click reset. [point:20"])
    def test_a_cut_off_tag_is_never_spoken(self, text):
        from ai import parse_point_tag
        result = parse_point_tag(text)
        assert (result.spoken_text, result.coordinate) == ("click reset.", None)

    def test_the_word_point_in_brackets_is_still_spoken(self):
        from ai import parse_point_tag
        assert parse_point_tag("type [point into the search field.").spoken_text == "type [point into the search field."

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
        # system= is a list-of-blocks carrying cache_control.
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

    def test_user_turn_carries_no_cache_breakpoint(self, mocker):
        """The screenshot changes every turn, so a breakpoint at or after it is a
        cache write that is never read. Memory and question go in as one block,
        intact even when the memory itself has blank lines."""
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
            "They also asked about charts.\n\n"
            "how do I hide gridlines"
        )

        client = AnthropicClient(api_key="test-key", model_id="anthropic/claude-haiku-5-5")
        img = Image.new("RGB", (1280, 800))
        with client.ask_stream(
            images=[(img, "label")], transcript=transcript_with_memory, history=[],
        ) as stream:
            list(stream.text_deltas())

        content = fake_client.messages.stream.call_args.kwargs["messages"][-1]["content"]
        assert not any("cache_control" in b for b in content)
        assert transcript_with_memory in [b["text"] for b in content if b.get("type") == "text"]

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
        the persona block, no second KB block. This is the 'Claude already
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
        # Call with NO kb_content / kb_app_name; should default to empty
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
            http_client=client._http,
        )
        assert client.model_id == "google/gemini-3-flash-preview"

    def test_ask_stream_builds_openai_messages_with_image_url(self, mocker):
        from PIL import Image
        client, mock_instance, _ = self._make_client(mocker)

        # Mock the streaming iterator. ask_stream must not consume it here,
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
        GeminiClient must convert by concatenating all text blocks."""
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
    """Tests for ai.create_ai_client, which routes model_id prefix to right subclass."""

    def test_routes_anthropic_prefix_to_anthropic_client(self, mocker):
        from ai import create_ai_client, AnthropicClient
        mocker.patch("ai.Anthropic")  # don't construct real SDK
        client = create_ai_client(
            model_id="anthropic/claude-sonnet-4-6",
            api_key="test-key",
        )
        assert isinstance(client, AnthropicClient)
        assert client.model_id == "claude-sonnet-4-6"

    def test_routes_claude_prefix_to_anthropic_client(self, mocker):
        from ai import create_ai_client, AnthropicClient
        mocker.patch("ai.Anthropic")
        client = create_ai_client(
            model_id="claude-sonnet-4-6",  # bare Anthropic ID (non-OpenRouter)
            api_key="test-key",
        )
        assert isinstance(client, AnthropicClient)

    def test_routes_google_prefix_to_gemini_via_openrouter(self, mocker):
        """sk-or- OpenRouter key -> OpenRouter endpoint, keeps the google/ slug."""
        from ai import create_ai_client, GeminiClient
        mock_openai = mocker.patch("ai.OpenAI")
        client = create_ai_client(
            model_id="google/gemini-3-flash-preview",
            api_key="sk-or-v1-testkey",
        )
        assert isinstance(client, GeminiClient)
        assert client.model_id == "google/gemini-3-flash-preview"
        assert "openrouter" in mock_openai.call_args.kwargs["base_url"]

    def test_routes_google_prefix_to_native_gemini_with_direct_key(self, mocker):
        """A non-sk-or key (direct Google AI Studio key) routes to
        Google's native OpenAI-compat endpoint with the 'google/' prefix
        stripped, so a user with no OpenRouter account can paste a Google key."""
        from ai import create_ai_client, GeminiClient
        mock_openai = mocker.patch("ai.OpenAI")
        client = create_ai_client(
            model_id="google/gemini-3.1-pro-preview",
            api_key="AIzaSyDirectGoogleKey",
        )
        assert isinstance(client, GeminiClient)
        assert client.model_id == "gemini-3.1-pro-preview"  # prefix stripped
        assert "generativelanguage.googleapis.com" in mock_openai.call_args.kwargs["base_url"]

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
        # 'cohere/' is a genuinely unsupported prefix.
        with pytest.raises(ValueError) as excinfo:
            create_ai_client(model_id="cohere/command-r", api_key="test-key")
        msg = str(excinfo.value)
        assert "cohere/command-r" in msg
        assert "anthropic/" in msg
        assert "google/" in msg
        assert "openai/" in msg  # openai/ is listed as supported

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
        even when no explicit base_url is passed. The bundled EXE doesn't
        load .env, so the ANTHROPIC_BASE_URL env var is missing; without
        this fallback the Anthropic SDK defaults to api.anthropic.com,
        which rejects OpenRouter-namespaced keys with a 401.
        """
        mock_anthropic = mocker.patch("ai.Anthropic")
        from ai import create_ai_client
        create_ai_client(
            model_id="anthropic/claude-sonnet-4-6",
            api_key="sk-or-v1-fake-test-key",
        )
        kwargs = mock_anthropic.call_args.kwargs
        assert kwargs.get("base_url") == "https://openrouter.ai/api", (
            "OpenRouter sk-or- key must auto-route to openrouter.ai endpoint"
        )

    def test_anthropic_with_direct_key_does_not_set_base_url(self, mocker):
        """sk-ant-* direct Anthropic keys must NOT trigger any base_url
        override: they're valid for Anthropic SDK's default endpoint
        (api.anthropic.com). Auto-routing is opt-in via prefix."""
        mock_anthropic = mocker.patch("ai.Anthropic")
        from ai import create_ai_client
        create_ai_client(
            model_id="anthropic/claude-sonnet-4-6",
            api_key="sk-ant-api03-real-anthropic-key",
        )
        kwargs = mock_anthropic.call_args.kwargs
        # Anthropic SDK gets called without base_url, so it uses its default.
        assert "base_url" not in kwargs, (
            "Direct Anthropic keys must not have base_url overridden"
        )

    def test_explicit_base_url_overrides_openrouter_auto_detect(self, mocker):
        """If caller passes an explicit base_url, the auto-detect logic
        must NOT override it: explicit choice wins. This protects
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
        # Gemini answers per-mille: 500,500 is the centre of a 1280x800 shot.
        client, _ = self._make_client_with_stream(
            mocker, ["click save. ", "[POINT:500,500:save button]"]
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
        """Edge case: history turn contains image blocks alongside text.
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
        """Chunk boundaries are arbitrary, so a tag can arrive split across
        chunks. Parsing runs on the accumulated text."""
        client, _ = self._make_client_with_stream(
            mocker,
            ["click save. [POI", "NT:500,", "500:save button]"],
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
        """Provider errors (401, 402, 404) are wrapped with a diagnostic
        RuntimeError that names the model and points at the likely causes."""
        from ai import GeminiClient
        mock_openai_instance = mocker.MagicMock(name="openai_client")
        mocker.patch("ai.OpenAI", return_value=mock_openai_instance)
        mock_openai_instance.chat.completions.create.side_effect = (
            ConnectionError("404: model gemini-4-argon not available")
        )
        client = GeminiClient(
            api_key="test-key",
            model_id="google/gemini-4-argon",
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
        assert "google/gemini-4-argon" in msg
        assert "key in Settings" in msg
        assert "not available" in msg  # the provider's own reason is kept
        # Original should be chained for debugging.
        assert isinstance(exc_info.value.__cause__, ConnectionError)


# --- OllamaClient (local LLM support) ---------------------------------------

class TestOllamaClient:
    """Tests for ai.OllamaClient: local LLM via Ollama /api/chat.

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
        """Ollama emits metadata-only chunks with empty content; don't yield empties."""
        from PIL import Image

        client, _, _ = self._make_client_with_stream(mocker, [
            {"message": {"content": ""}, "done": False},     # skip: empty
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


# --- OllamaClient failure-path regressions ----------------------------------

class TestOllamaClientReviewerFixes:
    """OllamaClient must release its connection pool on every failure path
    and degrade gracefully when the stream dies mid-response."""

    def test_blocker1_closes_httpx_client_when_stream_raises(self, mocker):
        """If httpx.Client.stream() raises (Ollama down, DNS failure,
        ECONNREFUSED), the already-opened httpx_client must be closed: the
        caller's `with` block never enters, so __exit__ won't fire. Otherwise
        every Ollama-unreachable interaction leaks a connection pool.
        """
        from ai import OllamaClient
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
            with client.ask_stream(images=[(img, "x")], transcript="?", history=[]):
                # Never reached: __enter__ raises
                pass

        # The crucial assertion: __exit__ WAS called on the httpx client to
        # release the connection pool. A count of 0 means a leak.
        assert mock_httpx_instance.__exit__.call_count == 1

    def test_blocker1_closes_httpx_client_when_raise_for_status_raises(self, mocker):
        """Sibling of the stream() case: if response.raise_for_status() raises
        (non-2xx, non-404, e.g. 500 Internal Server Error), the httpx client
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
            with client.ask_stream(images=[(img, "x")], transcript="?", history=[]):
                pass

        # Both the stream context AND the httpx client must be cleaned up.
        assert mock_stream_cm.__exit__.call_count == 1
        assert mock_httpx_instance.__exit__.call_count == 1

    def test_blocker3_final_result_safe_after_text_deltas_raises(self, mocker):
        """If text_deltas() raises mid-stream (Ollama crash, network drop),
        final_result() must NOT re-enter the iterator and re-raise. It must
        return parse_point_tag of whatever was accumulated before the failure
        (graceful degradation).
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
            # Consume deltas; will raise mid-stream
            collected = []
            try:
                for delta in stream.text_deltas():
                    collected.append(delta)
            except ConnectionError:
                pass  # Expected
            # The crucial assertion: final_result() must NOT re-iterate
            # text_deltas() and re-raise the ConnectionError. It must return a
            # PointParseResult with what was accumulated before the failure.
            result = stream.final_result()
            # Accumulated text was "partial response" (no [POINT:x,y] tag)
            assert result.coordinate is None
            assert "partial" in result.spoken_text
            assert "response" in result.spoken_text


# --- create_ai_client factory: Ollama dispatch --------------------------------

class TestCreateAIClientOllama:
    """Extends TestCreateAIClient: factory dispatches ollama/* prefix
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
        """If MODEL_ID=llama3.2-vision (bare model name), route to Ollama."""
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
            # ollama_host omitted: should fall back to default
        )
        assert isinstance(client, OllamaClient)
        assert client.host == "http://localhost:11434"

    def test_ollama_routing_does_not_break_existing_anthropic_dispatch(self, mocker):
        """Regression check: Anthropic dispatch still works alongside the Ollama branch."""
        from ai import create_ai_client, AnthropicClient
        mocker.patch("ai.Anthropic")
        client = create_ai_client(
            model_id="anthropic/claude-sonnet-4-6",
            api_key="sk-ant-test",
        )
        assert isinstance(client, AnthropicClient)


class TestOpenAIVisionClient:
    """OpenAIVisionClient (OpenAI native API) is a thin subclass of
    GeminiClient: same OpenAI-chat-completions shape, just
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
        """OpenAIVisionClient reuses GeminiClient.ask_stream verbatim
        (same OpenAI chat.completions streaming shape). Confirms the subclass
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
        """Regression: Anthropic dispatch still works alongside the OpenAI branch."""
        from ai import create_ai_client, AnthropicClient
        mocker.patch("ai.Anthropic")
        client = create_ai_client(model_id="anthropic/claude-sonnet-4-6", api_key="sk-ant-test")
        assert isinstance(client, AnthropicClient)

    def test_openai_routing_does_not_break_ollama(self, mocker):
        from ai import create_ai_client, OllamaClient
        mocker.patch("ai.httpx")
        client = create_ai_client(model_id="ollama/llava:7b", api_key="")
        assert isinstance(client, OllamaClient)

    def test_openai_with_openrouter_key_routes_to_openrouter(self, mocker):
        """Dual routing: an sk-or- key for OpenAI points at OpenRouter
        and KEEPS the namespaced openai/ slug (OpenRouter expects it). Token
        param is the classic max_tokens (OpenRouter normalizes it)."""
        from ai import create_ai_client, OpenAIVisionClient
        mock_openai = mocker.patch("ai.OpenAI")
        client = create_ai_client(model_id="openai/gpt-5.4", api_key="sk-or-v1-test")
        assert isinstance(client, OpenAIVisionClient)
        assert client.model_id == "openai/gpt-5.4"  # slug kept for OpenRouter
        assert "openrouter.ai" in mock_openai.call_args.kwargs["base_url"]
        assert client._max_tokens_param == "max_tokens"

    def test_openai_with_direct_key_hits_openai_native(self, mocker):
        """A direct OpenAI key strips the prefix, leaves base_url None
        (api.openai.com), and uses max_completion_tokens (gpt-5 requirement)."""
        from ai import create_ai_client, OpenAIVisionClient
        mock_openai = mocker.patch("ai.OpenAI")
        client = create_ai_client(model_id="openai/gpt-5.4", api_key="sk-proj-direct")
        assert isinstance(client, OpenAIVisionClient)
        assert client.model_id == "gpt-5.4"  # prefix stripped for native API
        assert mock_openai.call_args.kwargs.get("base_url") is None
        assert client._max_tokens_param == "max_completion_tokens"


class TestAnnotationPrompt:
    """Draw-on-screen annotation system prompt."""

    def test_annotation_prompt_has_four_shape_tags(self):
        from ai import _CLICKY_ANNOTATION_SYSTEM_PROMPT as p
        for tag in ("[ARROW:", "[CIRCLE:", "[UNDERLINE:", "[LABEL:"):
            assert tag in p, f"annotation prompt missing {tag}"

    def test_annotation_prompt_uses_image_dimension_coordinate_space(self):
        """Farza's accuracy contract: tell the model the screenshot pixel
        dimensions and ask for coords in that space."""
        from ai import _CLICKY_ANNOTATION_SYSTEM_PROMPT as p
        assert "dimension" in p.lower()
        assert "pixel" in p.lower()
        assert "0,0" in p or "origin" in p.lower()


def test_create_ai_client_routes_gemini_3_5_flash():
    """A google/* model id routes to GeminiClient (OpenRouter)."""
    from ai import GeminiClient, create_ai_client
    c = create_ai_client(model_id="google/gemini-3.5-flash", api_key="sk-or-test")
    assert isinstance(c, GeminiClient)


class TestAnthropicModelIdPerEndpoint:
    """api.anthropic.com takes bare model IDs; OpenRouter takes anthropic/ slugs."""

    def _sent_model(self, mocker, api_key, model_id="anthropic/claude-sonnet-4-6"):
        from PIL import Image
        mock_anthropic = mocker.patch("ai.Anthropic")
        from ai import create_ai_client
        client = create_ai_client(model_id=model_id, api_key=api_key)
        client.ask_stream(
            images=[(Image.new("RGB", (8, 8)), "label")], transcript="hi", history=[],
        )
        return mock_anthropic.return_value.messages.stream.call_args.kwargs["model"]

    def test_direct_key_sends_bare_model_id(self, mocker):
        assert self._sent_model(mocker, "sk-ant-api03-test") == "claude-sonnet-4-6"

    def test_openrouter_key_keeps_namespaced_slug(self, mocker):
        assert self._sent_model(mocker, "sk-or-v1-test") == "anthropic/claude-sonnet-4-6"

    def test_bare_model_id_on_direct_key_is_unchanged(self, mocker):
        assert self._sent_model(mocker, "sk-ant-api03-test", "claude-opus-4-8") == "claude-opus-4-8"


class TestGeminiPerMilleCoords:
    """Gemini answers on a 0-1000 grid; everything downstream works in screenshot pixels."""

    def _final(self, mocker, model_id, text, sizes=((1280, 800),), labels=None):
        from PIL import Image
        from ai import create_ai_client
        chunk = mocker.MagicMock()
        chunk.choices = [mocker.MagicMock()]
        chunk.choices[0].delta.content = text
        mocker.patch("ai.OpenAI").return_value.chat.completions.create.return_value = iter([chunk])
        client = create_ai_client(model_id=model_id, api_key="sk-or-v1-test")
        labels = labels or [f"screen {i + 1} of {len(sizes)}" for i in range(len(sizes))]
        images = [(Image.new("RGB", s), label) for s, label in zip(sizes, labels)]
        with client.ask_stream(images=images, transcript="where?", history=[]) as stream:
            for _ in stream.text_deltas():
                pass
            return stream.final_result()

    def test_gemini_point_lands_in_pixels(self, mocker):
        r = self._final(mocker, "google/gemini-4-argon", "up top. [POINT:325,68:eco audit]")
        assert r.coordinate == (416, 54)

    def test_other_models_are_left_alone(self, mocker):
        r = self._final(mocker, "openai/gpt-6.1-sol", "up top. [POINT:325,68:eco audit]")
        assert r.coordinate == (325, 68)

    def test_point_on_another_screen_uses_that_screens_size(self, mocker):
        r = self._final(mocker, "google/gemini-4-argon", "there. [POINT:500,500:menu:screen2]",
                        sizes=((1280, 800), (1024, 768)))
        assert r.coordinate == (512, 384)
        assert r.screen_number == 2

    def test_screen_number_follows_the_label_not_the_cursor_first_order(self, mocker):
        # Cursor on screen 2, so screen 2's capture is sent first.
        r = self._final(mocker, "google/gemini-4-argon", "there. [POINT:500,500:menu:screen1]",
                        sizes=((1024, 768), (1280, 800)),
                        labels=["screen 2 of 2 — cursor is on this screen (primary focus)",
                                "screen 1 of 2 — secondary screen"])
        assert r.coordinate == (640, 400)
        assert r.screen_number == 1

    def test_draw_tags_convert_but_label_text_does_not(self, mocker):
        r = self._final(mocker, "google/gemini-4-argon",
                        "look [CIRCLE:500,500,50:x][ARROW:0,0->1000,1000]"
                        "[UNDERLINE:100,200,300][LABEL:250,750:3 + 4 = 7]")
        assert "[CIRCLE:640,400,64:x]" in r.spoken_text
        assert "[ARROW:0,0->1280,800]" in r.spoken_text
        assert "[UNDERLINE:128,160,384]" in r.spoken_text
        assert "[LABEL:320,600:3 + 4 = 7]" in r.spoken_text


class TestTokenUsage:
    """Each turn reports provider token counts for the debug log."""

    def test_anthropic_stream_reports_usage(self, mocker):
        from types import SimpleNamespace
        from PIL import Image
        from ai import AnthropicClient

        fake_client = mocker.patch("ai.Anthropic").return_value
        fake_stream = mocker.MagicMock()
        fake_stream.text_stream = iter(["ok [POINT:none]"])
        fake_stream.get_final_message.return_value.usage = SimpleNamespace(
            input_tokens=1500, output_tokens=40,
            cache_read_input_tokens=1100, cache_creation_input_tokens=0,
        )
        mgr = mocker.MagicMock()
        mgr.__enter__ = mocker.MagicMock(return_value=fake_stream)
        mgr.__exit__ = mocker.MagicMock(return_value=False)
        fake_client.messages.stream.return_value = mgr

        client = AnthropicClient(api_key="sk-ant-test", model_id="claude-haiku-5-5")
        with client.ask_stream(images=[(Image.new("RGB", (8, 8)), "s")], transcript="hi", history=[]) as stream:
            list(stream.text_deltas())
            stream.final_result()
            assert stream.usage() == {"input": 1500, "output": 40, "cache_read": 1100, "cache_write": 0}

    def test_openai_compatible_stream_reports_usage(self, mocker):
        from types import SimpleNamespace
        from PIL import Image
        from ai import create_ai_client

        text = mocker.MagicMock()
        text.choices = [mocker.MagicMock()]
        text.choices[0].delta.content = "ok [POINT:none]"
        text.usage = None
        tail = SimpleNamespace(choices=[], usage=SimpleNamespace(
            prompt_tokens=1300, completion_tokens=35,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        ))
        create = mocker.patch("ai.OpenAI").return_value.chat.completions.create
        create.return_value = iter([text, tail])

        client = create_ai_client(model_id="openai/gpt-6.1-sol", api_key="sk-or-v1-test")
        with client.ask_stream(images=[(Image.new("RGB", (8, 8)), "s")], transcript="hi", history=[]) as stream:
            list(stream.text_deltas())
            stream.final_result()
            assert stream.usage() == {"input": 1300, "output": 35, "cache_read": 0, "cache_write": 0}
        assert create.call_args.kwargs["stream_options"] == {"include_usage": True}


class TestThinkingOff:
    """Voice turns skip hidden thinking where the model allows it: faster first
    token, same hits on the pointing set."""

    def _anthropic_kwargs(self, mocker, model_id):
        from PIL import Image
        from ai import create_ai_client
        mock = mocker.patch("ai.Anthropic")
        client = create_ai_client(model_id=model_id, api_key="sk-or-v1-test")
        client.ask_stream(images=[(Image.new("RGB", (8, 8)), "s")], transcript="hi", history=[])
        return mock.return_value.messages.stream.call_args.kwargs

    def _openai_kwargs(self, mocker, model_id, api_key="sk-proj-test"):
        from PIL import Image
        from ai import create_ai_client
        create = mocker.patch("ai.OpenAI").return_value.chat.completions.create
        create.return_value = iter([])
        client = create_ai_client(model_id=model_id, api_key=api_key)
        client.ask_stream(images=[(Image.new("RGB", (8, 8)), "s")], transcript="hi", history=[])
        return create.call_args.kwargs

    def test_haiku_5_5_does_not_think(self, mocker):
        assert self._anthropic_kwargs(mocker, "anthropic/claude-haiku-5-5")["thinking"] == {"type": "disabled"}

    def test_sonnet_5_5_thinks_only_between_tools(self, mocker):
        assert self._anthropic_kwargs(mocker, "anthropic/claude-sonnet-5.5")["thinking"] == {"type": "between_tools"}

    def test_models_without_an_off_switch_get_no_thinking_param(self, mocker):
        assert "thinking" not in self._anthropic_kwargs(mocker, "anthropic/claude-opus-5-5")

    def test_gpt_6_1_sol_reasons_low(self, mocker):
        assert self._openai_kwargs(mocker, "openai/gpt-6.1-sol")["reasoning_effort"] == "low"

    def test_gpt_6_luna_does_not_reason(self, mocker):
        assert self._openai_kwargs(mocker, "openai/gpt-6-luna", "sk-or-v1-test")["reasoning_effort"] == "none"

    def test_other_models_get_no_reasoning_param(self, mocker):
        assert "reasoning_effort" not in self._openai_kwargs(mocker, "openai/gpt-4o")
        assert "reasoning_effort" not in self._openai_kwargs(mocker, "google/gemini-4-argon", "sk-or-v1-test")


class TestWarmUp:
    """warm() opens the provider connection while the user is still talking."""

    def test_anthropic_client_warms_its_own_api_origin(self, mocker):
        from ai import create_ai_client
        http = mocker.MagicMock()
        mocker.patch("ai.AnthropicHttpxClient", return_value=http)
        sdk = mocker.patch("ai.Anthropic")
        sdk.return_value.base_url = "https://openrouter.ai/api/"
        client = create_ai_client(model_id="anthropic/claude-haiku-5-5", api_key="sk-or-v1-test")
        client.warm()
        assert sdk.call_args.kwargs["http_client"] is http
        assert http.head.call_args.args[0] == "https://openrouter.ai/api/"

    def test_openai_compatible_client_warms_its_own_api_origin(self, mocker):
        from ai import create_ai_client
        http = mocker.MagicMock()
        mocker.patch("ai.OpenAIHttpxClient", return_value=http)
        sdk = mocker.patch("ai.OpenAI")
        sdk.return_value.base_url = "https://api.openai.com/v1/"
        client = create_ai_client(model_id="openai/gpt-6.1-sol", api_key="sk-proj-test")
        client.warm()
        assert sdk.call_args.kwargs["http_client"] is http
        assert http.head.call_args.args[0] == "https://api.openai.com/v1/"

    def test_warm_never_raises(self, mocker):
        import httpx
        from ai import create_ai_client
        http = mocker.MagicMock()
        http.head.side_effect = httpx.ConnectError("offline")
        mocker.patch("ai.AnthropicHttpxClient", return_value=http)
        mocker.patch("ai.Anthropic")
        create_ai_client(model_id="anthropic/claude-haiku-5-5", api_key="sk-ant-test").warm()


class TestWindowsPrompt:
    """The prompts describe Windows, so answers don't come back in Mac terms."""

    def test_no_mac_only_references(self):
        from ai import _CLICKY_ANNOTATION_SYSTEM_PROMPT, _CLICKY_SYSTEM_PROMPT
        for prompt in (_CLICKY_SYSTEM_PROMPT, _CLICKY_ANNOTATION_SYSTEM_PROMPT):
            lowered = prompt.lower()
            for mac in ("menu bar", "final cut", "xcode", "command option", "finder"):
                assert mac not in lowered, mac

    def test_shortcuts_use_windows_keys(self):
        from ai import _CLICKY_ANNOTATION_SYSTEM_PROMPT, _CLICKY_SYSTEM_PROMPT
        for prompt in (_CLICKY_SYSTEM_PROMPT, _CLICKY_ANNOTATION_SYSTEM_PROMPT):
            assert "windows" in prompt.lower()
