"""Clicky Windows vision+LLM layer.

AIClient abstract base + AnthropicClient concrete implementation using
Claude Computer Use API beta. Mirrors Clicky's ElementLocationDetector.swift
verbatim -- same system prompt, same tool JSON, same anthropic-beta header.

See docs/superpowers/specs/2026-04-11-ai-design.md for the design spec.
See docs/superpowers/plans/2026-04-11-ai.md for the implementation plan.

Responsibility boundary:
- THIS MODULE lives in Space C (Claude's declared resolution). It returns
  coordinates unclamped in that space.
- capture.unscale_claude_coords() maps Space C -> Space A (physical pixels).
- app.py owns threading and calls this module from worker threads.

Top-to-bottom order (so `python -m ai` works):
    1. Module docstring
    2. Imports
    3. Helper functions (build_user_prompt, image_to_base64_jpeg,
       parse_tool_use_coordinates, parse_response_text)
    4. AIClient abstract base class
    5. AnthropicClient concrete class
    6. __main__ block for manual live-API verification
"""
from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from io import BytesIO

from anthropic import Anthropic
from PIL import Image


# --- Constants (mirror Clicky's ElementLocationDetector.swift verbatim) ------

_CLICKY_MAX_TOKENS = 256
"""Matches Clicky's max_tokens budget. Room for the click action + a 2-3
sentence natural-language explanation that fits our TTS latency budget."""

_CLICKY_TIMEOUT_SECONDS = 15.0
"""Mirrors Clicky's timeoutIntervalForRequest: 15. If Claude doesn't respond
in 15s, the user has already given up -- fail fast, don't hang the UI."""


# --- Helper functions --------------------------------------------------------

def build_user_prompt(transcript: str) -> str:
    """Inject transcript into Clicky's verbatim 3-line system-prompt template.

    The template and wording are byte-for-byte from
    farzaa/clicky/leanring-buddy/ElementLocationDetector.swift (verified via
    gh api on 2026-04-11). Do not reword -- tests assert three anchor phrases
    verbatim and Computer Use training was done against this exact wording.
    """
    return (
        f'The user asked this question while looking at their screen: "{transcript}"\n'
        "\n"
        "Look at the screenshot. If there is a specific UI element (button, link, "
        "menu item, text field, icon, etc.) that the user should interact with or "
        "is asking about, click on that element.\n"
        "\n"
        'If the question is purely conceptual (e.g., "what does HTML mean?") and '
        "there's no specific element to point to, just respond with text saying "
        '"no specific element".'
    )


def image_to_base64_jpeg(img: Image.Image, quality: int = 85) -> str:
    """Encode a PIL image to a base64-ASCII JPEG string for the Claude API.

    Pattern matches Clicky's approach: encode as JPEG (~150 KB at 1280x800)
    to keep the payload small, then base64-encode for JSON transport.

    Args:
        img: source PIL Image in any mode.
        quality: JPEG quality 1-100 (default 85 matches Clicky).

    Returns:
        str -- ASCII base64-encoded JPEG bytes, ready to plug into the
        Anthropic image content block's `source.data` field.
    """
    buf = BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _get(obj, key, default=None):
    """Dual-access helper: works on both dict-shaped test mocks and
    anthropic SDK objects (via attribute access).

    The Anthropic Python SDK returns Message objects with .content as a list
    of TextBlock/ToolUseBlock objects, accessed via attributes (block.type).
    Test mocks use plain dicts (block["type"]). This helper lets parser
    functions accept either shape without duplicating the code.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def parse_tool_use_coordinates(response) -> list[tuple[int, int]]:
    """Extract (x, y) pairs from Claude's tool_use content blocks.

    Iterates response.content (or response["content"]), filters for
    type == "tool_use" + input.action == "left_click", extracts
    input.coordinate as (int, int). Returns [] if no matching block exists
    (conceptual question -- Claude returned text-only).

    Coordinates are in Claude's declared-resolution space (Space C).
    Caller uses capture.unscale_claude_coords() to map to physical pixels.
    """
    coords: list[tuple[int, int]] = []
    content = _get(response, "content", []) or []
    for block in content:
        if _get(block, "type") != "tool_use":
            continue
        input_data = _get(block, "input", {}) or {}
        if _get(input_data, "action") != "left_click":
            continue
        coordinate = _get(input_data, "coordinate", []) or []
        if len(coordinate) == 2:
            coords.append((int(coordinate[0]), int(coordinate[1])))
    return coords


def parse_response_text(response) -> str:
    """Concatenate all text-type content blocks into a single string for TTS.

    Dual-access compatible (dict mocks or SDK objects). Used by the caller
    to feed Claude's natural-language response to pyttsx3. Joins multiple
    text blocks with a single space, strips leading/trailing whitespace.
    Returns empty string if no text blocks exist.
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


# --- AIClient abstract base --------------------------------------------------

class AIClient(ABC):
    """Abstract base for vision+LLM providers.

    Phase 1: AnthropicClient (Computer Use API beta, Anthropic-direct).
    Phase 2: OpenRouterClient (vision-tag regex fallback -- OpenRouter can't
    proxy Computer Use beta). See DECISIONS.md entry "Provider abstraction
    from day 1" for the rationale.
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

        Args:
            image: PIL image already resized to (declared_w, declared_h).
            transcript: user's voice question (Whisper output).
            history: prior turns in Anthropic SDK message format
                (list of dicts with 'role' and 'content' keys).
            declared_w: image width in pixels, passed to Computer Use tool.
            declared_h: image height in pixels, passed to Computer Use tool.

        Returns:
            dict with keys:
              - 'text': str (for TTS)
              - 'points': list of {'x': int, 'y': int, 'label': str}
                (label is empty in Phase 1; Computer Use tool doesn't emit labels)
        """
        ...


# --- Concrete Anthropic implementation ---------------------------------------

class AnthropicClient(AIClient):
    """Phase 1 implementation using the Anthropic Python SDK + Computer Use API beta.

    Mirrors farzaa/clicky/leanring-buddy/ElementLocationDetector.swift verbatim:
    - Same `computer_20251124` tool type
    - Same `anthropic-beta: computer-use-2025-11-24` header
    - Same 3-line system prompt (via build_user_prompt)
    - Same max_tokens=256 budget
    - Same 15s timeout

    Do not deviate. Clicky has thousands of real users hitting these exact
    parameters; we inherit that validation for free. See
    docs/superpowers/specs/2026-04-11-ai-design.md for the 10 locked design
    decisions behind this implementation.
    """

    def __init__(self, api_key: str, model_id: str) -> None:
        """Construct an Anthropic SDK client.

        Args:
            api_key: Anthropic API key (from .env via config.ANTHROPIC_API_KEY).
            model_id: Claude model id (e.g. "claude-sonnet-4-6" from config.MODEL_ID).
        """
        self.client = Anthropic(api_key=api_key, timeout=_CLICKY_TIMEOUT_SECONDS)
        self.model_id = model_id

    def ask(
        self,
        image: Image.Image,
        transcript: str,
        history: list[dict],
        declared_w: int,
        declared_h: int,
    ) -> dict:
        """Call Claude Computer Use API beta, return parsed text + points.

        Coordinates in the returned points are in Claude's declared-resolution
        space (Space C), unclamped. Caller uses capture.unscale_claude_coords()
        to map to physical pixels (Space A).
        """
        base64_jpeg = image_to_base64_jpeg(image)
        user_prompt = build_user_prompt(transcript)

        new_user_turn = {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64_jpeg,
                    },
                },
                {"type": "text", "text": user_prompt},
            ],
        }

        response = self.client.messages.create(
            model=self.model_id,
            max_tokens=_CLICKY_MAX_TOKENS,
            tools=[
                {
                    "type": "computer_20251124",
                    "name": "computer",
                    "display_width_px": declared_w,
                    "display_height_px": declared_h,
                }
            ],
            extra_headers={"anthropic-beta": "computer-use-2025-11-24"},
            messages=[*history, new_user_turn],
        )

        text = parse_response_text(response)
        coords = parse_tool_use_coordinates(response)
        points = [{"x": x, "y": y, "label": ""} for (x, y) in coords]

        return {"text": text, "points": points}


# --- Manual live-API verification entry point -------------------------------

if __name__ == "__main__":
    # Manual live-API acceptance gate. Run: py -3.13 -m ai
    # Requires ANTHROPIC_API_KEY in .env and debug_capture.jpg in cwd.
    from config import ANTHROPIC_API_KEY, MODEL_ID

    print("=" * 70)
    print("Clicky Windows -- ai.py manual live-API verification")
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

    result = client.ask(
        image=test_image,
        transcript=transcript,
        history=[],
        declared_w=test_image.width,
        declared_h=test_image.height,
    )

    print("\nResponse text:")
    print(f"  {result['text']!r}")
    print(f"\nPoints ({len(result['points'])}):")
    for p in result["points"]:
        in_bounds = (
            0 <= p["x"] < test_image.width and 0 <= p["y"] < test_image.height
        )
        print(f"  ({p['x']}, {p['y']}) in-bounds={in_bounds}")

    print("\n" + "=" * 70)
    print("Manual verification checklist:")
    print("  1. Response text is non-empty and references something visible")
    print("     in debug_capture.jpg")
    print("  2. >= 1 point returned (unless Claude says 'no specific element')")
    print("  3. All points are in-bounds of the image dimensions")
    print("  4. The returned coordinate lands on a plausible UI element")
    print("=" * 70)
