"""Clicky Windows vision+LLM layer.

AIClient abstract base + AnthropicClient concrete implementation using
plain vision streaming with a [POINT:x,y:label] coordinate tag regex parser.
Matches Clicky's actual shipping path: ClaudeAPI.analyzeImageStreaming +
CompanionManager.parsePointingCoordinates (verified line-by-line via gh api
on 2026-04-12). See DECISIONS.md 2026-04-12 (evening 3) for the research pass.

Responsibility boundary:
- THIS MODULE lives in Space C (Claude's declared resolution). It returns
  coordinates unclamped in that space.
- capture.unscale_claude_coords() maps Space C -> Space A (physical pixels).
- app.py owns threading and calls this module from worker threads.

Top-to-bottom order (so `python -m ai` works):
    1. Module docstring
    2. Imports
    3. Constants (_CLICKY_SYSTEM_PROMPT, _POINT_TAG_RE, _CLICKY_MAX_TOKENS)
    4. PointParseResult dataclass
    5. Pure functions (parse_point_tag, image_to_base64_jpeg, _get,
       parse_response_text)
    6. AIClient abstract base class
    7. AnthropicClient concrete class
    8. __main__ block for manual live-API verification
"""
from __future__ import annotations

import base64
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO
from typing import Iterator

from anthropic import Anthropic
from PIL import Image


# --- Constants ----------------------------------------------------------------

_CLICKY_SYSTEM_PROMPT = """\
you're clicky, a friendly always-on companion that lives in the user's menu bar. the user just spoke to you via push-to-talk and you can see their screen(s). your reply will be spoken aloud via text-to-speech, so write the way you'd actually talk. this is an ongoing conversation — you remember everything they've said before.

rules:
- default to one or two sentences. be direct and dense. BUT if the user asks you to explain more, go deeper, or elaborate, then go all out — give a thorough, detailed explanation with no length limit.
- all lowercase, casual, warm. no emojis.
- write for the ear, not the eye. short sentences. no lists, bullet points, markdown, or formatting — just natural speech.
- don't use abbreviations or symbols that sound weird read aloud. write "for example" not "e.g.", spell out small numbers.
- if the user's question relates to what's on their screen, reference specific things you see.
- if the screenshot doesn't seem relevant to their question, just answer the question directly.
- you can help with anything — coding, writing, general knowledge, brainstorming.
- never say "simply" or "just".
- don't read out code verbatim. describe what the code does or what needs to change conversationally.
- focus on giving a thorough, useful explanation. don't end with simple yes/no questions like "want me to explain more?" or "should i show you?" — those are dead ends that force the user to just say yes.
- instead, when it fits naturally, end by planting a seed — mention something bigger or more ambitious they could try, a related concept that goes deeper, or a next-level technique that builds on what you just explained. make it something worth coming back for, not a question they'd just nod to. it's okay to not end with anything extra if the answer is complete on its own.
- if you receive multiple screen images, the one labeled "primary focus" is where the cursor is — prioritize that one but reference others if relevant.

element pointing:
you have a small blue cursor that can fly to and point at things on screen. use it whenever pointing would genuinely help the user — if they're asking how to do something, looking for a menu, trying to find a button, or need help navigating an app, point at the relevant element. err on the side of pointing rather than not pointing, because it makes your help way more useful and concrete.

don't point at things when it would be pointless — like if the user asks a general knowledge question, or the conversation has nothing to do with what's on screen, or you'd just be pointing at something obvious they're already looking at. but if there's a specific UI element, menu, button, or area on screen that's relevant to what you're helping with, point at it.

when you point, append a coordinate tag at the very end of your response, AFTER your spoken text. the screenshot images are labeled with their pixel dimensions. use those dimensions as the coordinate space. the origin (0,0) is the top-left corner of the image. x increases rightward, y increases downward.

format: [POINT:x,y:label] where x,y are integer pixel coordinates in the screenshot's coordinate space, and label is a short 1-3 word description of the element (like "search bar" or "save button"). if the element is on the cursor's screen you can omit the screen number. if the element is on a DIFFERENT screen, append :screenN where N is the screen number from the image label (e.g. :screen2). this is important — without the screen number, the cursor will point at the wrong place.

if pointing wouldn't help, append [POINT:none].

examples:
- user asks how to color grade in final cut: "you'll want to open the color inspector — it's right up in the top right area of the toolbar. click that and you'll get all the color wheels and curves. [POINT:1100,42:color inspector]"
- user asks what html is: "html stands for hypertext markup language, it's basically the skeleton of every web page. curious how it connects to the css you're looking at? [POINT:none]"
- user asks how to commit in xcode: "see that source control menu up top? click that and hit commit, or you can use command option c as a shortcut. [POINT:285,11:source control]"
- element is on screen 2 (not where cursor is): "that's over on your other monitor — see the terminal window? [POINT:400,300:terminal:screen2]"\
"""

_POINT_TAG_RE = re.compile(
    r"\[POINT:(?:none|(\d+)\s*,\s*(\d+)(?::(?!screen\d)([^\]:\s][^\]:]*?))?(?::screen(\d+))?)\]\s*$"
)
"""Regex for Clicky's [POINT:x,y:label(:screenN)?] coordinate tag.

Python port of CompanionManager.parsePointingCoordinates
(leanring-buddy/CompanionManager.swift:784-828).
"""

_CLICKY_MAX_TOKENS = 1024
"""Token budget matching Clicky's analyzeImageStreaming call."""


# --- PointParseResult ---------------------------------------------------------

@dataclass
class PointParseResult:
    """Result of parsing the [POINT:...] tag from Claude's response text."""
    spoken_text: str
    coordinate: tuple[int, int] | None
    element_label: str | None
    screen_number: int | None


# --- Pure functions -----------------------------------------------------------

def parse_point_tag(text: str) -> PointParseResult:
    """Extract coordinate from a trailing [POINT:x,y:label] tag and strip it.

    Returns PointParseResult with coordinate=None on [POINT:none] or no match.
    The spoken_text field has the tag removed so TTS never reads it aloud.
    """
    match = _POINT_TAG_RE.search(text)
    if not match:
        return PointParseResult(
            spoken_text=text.strip(),
            coordinate=None,
            element_label=None,
            screen_number=None,
        )

    spoken = _POINT_TAG_RE.sub("", text).strip()

    if match.group(1) is None:
        return PointParseResult(
            spoken_text=spoken,
            coordinate=None,
            element_label=None,
            screen_number=None,
        )

    x, y = int(match.group(1)), int(match.group(2))
    label = match.group(3)
    screen = int(match.group(4)) if match.group(4) else None

    return PointParseResult(
        spoken_text=spoken,
        coordinate=(x, y),
        element_label=label,
        screen_number=screen,
    )


def image_to_base64_jpeg(img: Image.Image, quality: int = 85) -> str:
    """Encode a PIL image to a base64-ASCII JPEG string for the Claude API."""
    buf = BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _get(obj, key, default=None):
    """Dual-access helper: works on both dict-shaped test mocks and
    anthropic SDK objects (via attribute access)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def parse_response_text(response) -> str:
    """Concatenate all text-type content blocks into a single string.

    Dual-access compatible (dict mocks or SDK objects). Used by the batch
    ask() wrapper to extract the full text from a non-streaming response.
    """
    content = _get(response, "content", []) or []
    texts: list[str] = []
    for block in content:
        if _get(block, "type") != "text":
            continue
        text = _get(block, "text", "") or ""
        if text:
            texts.append(text)
    return " ".join(texts).strip()


# --- AIClient abstract base ---------------------------------------------------

class AIClient(ABC):
    """Abstract base for vision+LLM providers.

    Phase 1: AnthropicClient (vision-tag streaming).
    Phase 2: OpenRouterClient, GeminiClient, etc. as subclass drops.
    """

    @abstractmethod
    def ask(
        self,
        image: Image.Image,
        transcript: str,
        history: list[dict],
        declared_w: int,
        declared_h: int,
    ) -> dict:
        """Return {"text": str, "points": [{"x":int,"y":int,"label":str}]}.

        Coordinates are in Claude's declared-resolution space (Space C),
        unclamped. Caller uses capture.unscale_claude_coords() to map to
        physical pixels (Space A).
        """
        ...


# --- Concrete Anthropic implementation ----------------------------------------

class AnthropicClient(AIClient):
    """Phase 1 implementation using plain vision streaming + [POINT:x,y:label].

    Matches Clicky's actual shipping path: ClaudeAPI.analyzeImageStreaming +
    CompanionManager.parsePointingCoordinates. NOT Computer Use API beta
    (that was dead code in Clicky — ElementLocationDetector.swift, 0 refs).
    See DECISIONS.md 2026-04-12 (evening 3).
    """

    def __init__(self, api_key: str, model_id: str) -> None:
        self.client = Anthropic(api_key=api_key, timeout=60.0)
        self.model_id = model_id

    def ask_stream(
        self,
        images: list[tuple[Image.Image, str]],
        transcript: str,
        history: list[dict],
        system_prompt: str = _CLICKY_SYSTEM_PROMPT,
        max_tokens: int = _CLICKY_MAX_TOKENS,
    ):
        """Open a streaming Claude call, return a context manager.

        Args:
            images: list of (PIL Image, label string) tuples — one per screen.
                Sorted cursor-screen-first by capture_all_screens(). Each
                becomes an image content block + a text label block in the
                user message. Matches Clicky's analyzeImageStreaming(images:
                [(Data, String)], ...) shape.
            transcript: user's voice question (raw STT output).
            history: prior turns in Anthropic SDK message format.
            system_prompt: persona + pointing instructions.
            max_tokens: token budget (1024 default, matches Clicky).

        Usage:
            with client.ask_stream(images, transcript, history) as stream:
                for delta in stream.text_deltas():
                    # progressive text for sentence-level TTS chunking
                    pass
                result = stream.final_result()
                # result.spoken_text, result.coordinate, etc.
        """
        content_blocks: list[dict] = []
        for img, label in images:
            base64_jpeg = image_to_base64_jpeg(img)
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64_jpeg,
                },
            })
            content_blocks.append({"type": "text", "text": label})

        content_blocks.append({"type": "text", "text": transcript})

        new_user_turn = {"role": "user", "content": content_blocks}

        sdk_stream_mgr = self.client.messages.stream(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[*history, new_user_turn],
        )

        return _StreamingResponse(sdk_stream_mgr)

    def ask(
        self,
        image: Image.Image,
        transcript: str,
        history: list[dict],
        declared_w: int,
        declared_h: int,
    ) -> dict:
        """Batch wrapper: consumes the full stream, returns parsed dict.

        Wraps a single image into the list format ask_stream() expects.
        Backwards-compatible with the __main__ gate and test shapes.
        """
        label = f"primary focus (image dimensions: {declared_w}x{declared_h} pixels)"
        with self.ask_stream(
            [(image, label)], transcript, history
        ) as stream:
            for _ in stream.text_deltas():
                pass
            result = stream.final_result()

        points = []
        if result.coordinate:
            x, y = result.coordinate
            points.append({"x": x, "y": y, "label": result.element_label or ""})

        return {"text": result.spoken_text, "points": points}


class _StreamingResponse:
    """Wraps the SDK's MessageStreamManager for Clicky's streaming pattern."""

    def __init__(self, sdk_stream_mgr):
        self._sdk_mgr = sdk_stream_mgr
        self._sdk_stream = None
        self._accumulated = ""
        self._deltas_exhausted = False

    def __enter__(self):
        self._sdk_stream = self._sdk_mgr.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._sdk_mgr.__exit__(exc_type, exc_val, exc_tb)

    def text_deltas(self) -> Iterator[str]:
        """Yield progressive text deltas for sentence-level TTS chunking."""
        for delta in self._sdk_stream.text_stream:
            self._accumulated += delta
            yield delta
        self._deltas_exhausted = True

    def final_result(self) -> PointParseResult:
        """Parse the accumulated text for a [POINT:x,y:label] tag.

        If text_deltas() was fully exhausted, uses the accumulated text.
        Otherwise falls back to get_final_text() which blocks until the
        stream completes.
        """
        if not self._deltas_exhausted:
            self._accumulated = self._sdk_stream.get_final_text()
        return parse_point_tag(self._accumulated)


# --- Manual live-API verification entry point ---------------------------------

if __name__ == "__main__":
    from config import ANTHROPIC_API_KEY, MODEL_ID

    print("=" * 70)
    print("Clicky Windows -- ai.py manual live-API verification")
    print("  Pattern: vision-tag [POINT:x,y:label] with streaming")
    print("=" * 70)

    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY missing from .env")

    try:
        test_image = Image.open("debug_capture.jpg").convert("RGB")
    except FileNotFoundError:
        raise SystemExit(
            "debug_capture.jpg not found in current directory. "
            "Run `py -3.13 -m capture` first to generate it."
        )

    client = AnthropicClient(api_key=ANTHROPIC_API_KEY, model_id=MODEL_ID)
    transcript = (
        "what's on my screen and point at the most clickable thing "
        "for opening an application"
    )

    print(f"\nSending to Claude ({MODEL_ID})...")
    print(f"  image:      {test_image.size}")
    print(f"  transcript: {transcript!r}")
    print(f"  max_tokens: {_CLICKY_MAX_TOKENS}")

    label = f"primary focus (image dimensions: {test_image.width}x{test_image.height} pixels)"
    print("\nStreaming response:")
    with client.ask_stream(
        images=[(test_image, label)],
        transcript=transcript,
        history=[],
    ) as stream:
        for delta in stream.text_deltas():
            print(delta, end="", flush=True)
        result = stream.final_result()

    print(f"\n\nSpoken text (tag stripped):")
    print(f"  {result.spoken_text!r}")
    print(f"\nCoordinate: {result.coordinate}")
    print(f"Label:      {result.element_label}")
    print(f"Screen:     {result.screen_number}")

    if result.coordinate:
        x, y = result.coordinate
        in_bounds = 0 <= x < test_image.width and 0 <= y < test_image.height
        print(f"In bounds:  {in_bounds}")

    print("\n" + "=" * 70)
    print("Manual verification checklist:")
    print("  1. Response text is non-empty, lowercase, casual tone")
    print("  2. References specific things visible in debug_capture.jpg")
    print("  3. [POINT:x,y:label] tag present at end of response")
    print("  4. Coordinate is in-bounds and lands on a plausible UI element")
    print("  5. Spoken text has the tag stripped (safe for TTS)")
    print("=" * 70)
