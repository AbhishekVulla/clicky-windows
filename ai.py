"""Clicky Windows vision+LLM layer.

Every provider streams a spoken answer about the screenshots and ends it with
a [POINT:x,y:label] tag, parsed here. This follows Clicky's shipping path:
ClaudeAPI.analyzeImageStreaming + CompanionManager.parsePointingCoordinates.

Clients: AnthropicClient (Claude), GeminiClient (the OpenAI-compatible base,
used for Gemini and OpenRouter), OpenAIVisionClient (OpenAI) and OllamaClient
(local). create_ai_client picks one from the model ID and the key's prefix.

Responsibility boundary:
- THIS MODULE lives in Space C (the screenshot's declared resolution). It
  returns coordinates unclamped in that space.
- capture.unscale_claude_coords() maps Space C -> Space A (physical pixels).
- app.py owns threading and calls this module from worker threads.
"""
from __future__ import annotations

import base64
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO
from typing import Iterator

import httpx
from anthropic import Anthropic
from anthropic import DefaultHttpxClient as AnthropicHttpxClient
from openai import DefaultHttpxClient as OpenAIHttpxClient
from openai import OpenAI
from PIL import Image

from ollama_health import model_family


# --- Constants ----------------------------------------------------------------

_CLICKY_SYSTEM_PROMPT = """\
you're clicky, a friendly always-on companion that lives in the user's system tray on windows. the user just spoke to you via push-to-talk and you can see their screen(s). your reply will be spoken aloud via text-to-speech, so write the way you'd actually talk. this is an ongoing conversation — you remember everything they've said before.

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
- the user is on windows. name keyboard shortcuts with windows keys (control, alt, shift, the windows key), never command or option.
- if you receive multiple screen images, the one labeled "primary focus" is where the cursor is — prioritize that one but reference others if relevant.

element pointing:
you have a small blue cursor that can fly to and point at things on screen. use it whenever pointing would genuinely help the user — if they're asking how to do something, looking for a menu, trying to find a button, or need help navigating an app, point at the relevant element. err on the side of pointing rather than not pointing, because it makes your help way more useful and concrete.

don't point at things when it would be pointless — like if the user asks a general knowledge question, or the conversation has nothing to do with what's on screen, or you'd just be pointing at something obvious they're already looking at. but if there's a specific UI element, menu, button, or area on screen that's relevant to what you're helping with, point at it.

when you point, append a coordinate tag at the very end of your response, AFTER your spoken text. the screenshot images are labeled with their pixel dimensions. use those dimensions as the coordinate space. the origin (0,0) is the top-left corner of the image. x increases rightward, y increases downward.

format: [POINT:x,y:label] where x,y are integer pixel coordinates in the screenshot's coordinate space, and label is a short 1-3 word description of the element (like "search bar" or "save button"). if the element is on the cursor's screen you can omit the screen number. if the element is on a DIFFERENT screen, append :screenN where N is the screen number from the image label (e.g. :screen2). this is important — without the screen number, the cursor will point at the wrong place.

if pointing wouldn't help, append [POINT:none].

examples:
- user asks how to keep the header row visible in excel: "open the view tab up top, then hit freeze panes and pick freeze top row. [POINT:310,62:view tab]"
- user asks what html is: "html stands for hypertext markup language, it's basically the skeleton of every web page. curious how it connects to the css you're looking at? [POINT:none]"
- user asks how to commit in vs code: "see the source control icon in the left sidebar? click it, type a message, and press control enter to commit. [POINT:24,148:source control]"
- element is on screen 2 (not where cursor is): "that's over on your other monitor — see the terminal window? [POINT:400,300:terminal:screen2]"\
"""

# Local Qwen-VL models were trained to answer "locate X" with a box in their own
# JSON format, and they point well when asked that way; asked for a [POINT] tag
# at the end of a spoken answer, they get the place wrong. So they get the
# persona without its pointing section, and the box request goes right after
# the question, where a small model keeps hold of it. No coordinate space is
# named: each family uses its own (see _ollama_box_style). They are sent only
# the cursor's screen, since a box is read against one image, and the rule
# about several screens is swapped for one that says so.
_CLICKY_BOX_SYSTEM_PROMPT = "\n".join(
    "- you only see the screen the cursor is on. if what they ask about isn't on it, "
    "tell them to move the cursor to that screen and ask again."
    if line.startswith("- if you receive multiple screen images") else line
    for line in _CLICKY_SYSTEM_PROMPT.split("element pointing:")[0].rstrip().splitlines()
) + "\n"
_BOX_REQUEST = (
    "\n\nanswer in one or two short spoken sentences. then, on a new line, give the "
    'on-screen element i should click as json: {"bbox_2d": [x1, y1, x2, y2], "label": "..."}. '
    "leave the json out if nothing on screen helps."
)

_CLICKY_ANNOTATION_SYSTEM_PROMPT = """\
you're clicky, a patient tutor that teaches by drawing on the user's screen on windows. the user just spoke to you via push-to-talk and you can see their screen(s). your reply is spoken aloud via text-to-speech, so write the way you'd actually talk — all lowercase, casual, warm, one or two short sentences (longer only if they ask you to go deeper). write for the ear: no lists, no markdown, no symbols that sound weird read aloud.

instead of only pointing with the cursor, you can DRAW on the screen to teach — circle the exact thing you mean, draw an arrow from a mistake to where it should go, underline a term, or write a short correction. use drawing whenever it makes your help more concrete: finding a button, explaining a step, showing a mistake on the user's work.

how to draw: explain in spoken words first, then append shape tags at the very END of your reply, after your spoken text. the screenshot images are labeled with their pixel dimensions — use those dimensions as the coordinate space. the origin (0,0) is the top-left corner of the image, x increases rightward, y increases downward. point at the SPECIFIC element, never a vague region. never read coordinates aloud. only annotate the screen labeled "primary focus" (the one with the cursor) — do NOT emit shape tags for elements on any other screen.

shape tags (use as many as help, only what helps):
- [CIRCLE:x,y,r:label] — circle the exact element. (x,y) is the center, r is the radius in pixels, label is a 1-3 word description.
- [ARROW:x1,y1->x2,y2] — an arrow from (x1,y1) to (x2,y2), e.g. from the mistake to where it should go.
- [UNDERLINE:x,y,w] — underline a term or formula. (x,y) is the left end, w is the width in pixels.
- [LABEL:x,y:text] — write a short correction or hint on the screen at (x,y).

if nothing on screen is relevant (a purely conceptual question), just speak with no tags.

examples:
- user asks why their answer is wrong: "you applied the power rule but missed the chain rule here — you need to multiply by the derivative of the inside. [CIRCLE:340,210,28:missing step][ARROW:340,210->410,260]"
- user asks where to save: "hit the save button up in the top left of the toolbar. [CIRCLE:48,30,18:save]"
- user asks what a binary tree is (nothing on screen): "it's a structure where each node has at most two children, a left and a right. great for fast lookups."\
"""
"""Annotation-mode system prompt (draw-on-screen). Same
dimension-labeled coordinate-space contract as the cursor [POINT] prompt
(Farza CompanionManager.swift:566), extended from a single point to four
shape tags parsed by annotations.parse_annotations. Used when
config.ANNOTATION_MODE == 'on'."""

_POINT_TAG_RE = re.compile(
    r"\[POINT:(?:none|(\d+)\s*,\s*(\d+)(?::(?!screen\d)([^\]:\s][^\]:]*?))?(?::screen(\d+))?)\]\s*$"
)
"""Regex for Clicky's [POINT:x,y:label(:screenN)?] coordinate tag.

Python port of CompanionManager.parsePointingCoordinates
(CompanionManager.swift:784-828).
"""

# A [POINT tag the stream cut off before its closing bracket.
_CUT_OFF_POINT_RE = re.compile(r"\s*\[point(?::[^\]]*)?$", re.IGNORECASE)

_CLICKY_MAX_TOKENS = 1024
"""Token budget matching Clicky's analyzeImageStreaming call."""

_KB_SYSTEM_PREFIX_TEMPLATE = (
    "app knowledge base:\n"
    "you are helping the user with {app_name}. here is reference "
    "documentation that you should treat as authoritative:\n\n"
)
"""Marker prefix prepended to user-uploaded KB content before injection
into the system prompt as a SECOND cache_control block. Caller (app.py
_pipeline_worker → ask_stream's kb_content kwarg) supplies the raw
markdown body; ask_stream formats this prefix in front and adds the
ephemeral cache breakpoint. Per-app cache hit on subsequent turns within
the same app session; cache miss on app switch (acceptable since each
KB read is the dominant cost anyway). Empty kb_content means no second
block: Claude proceeds with vision + memory only (the 'Claude already
knows that software' path)."""


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
    The spoken_text field has the tag removed so TTS never reads it aloud,
    including a tag the stream cut off before its closing bracket.
    """
    match = _POINT_TAG_RE.search(text)
    if not match:
        return PointParseResult(
            spoken_text=_CUT_OFF_POINT_RE.sub("", text).strip(),
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


# Hidden thinking delays the first spoken word. Where a model lets it be turned
# off or down, Clicky does: same hits on the pointing set, earlier first token.
_ANTHROPIC_THINKING = {
    "claude-haiku-5-5": {"type": "disabled"},
    "claude-sonnet-5-5": {"type": "between_tools"},  # "disabled" is a 400 on Sonnet 5.5
}
_OPENAI_REASONING_EFFORT = {
    "gpt-6-luna": "none",
    "gpt-6.1-sol": "low",  # Sol has no "none"
}


def _bare_model(model_id: str) -> str:
    return model_id.split("/", 1)[-1]


def _warm(http, url) -> None:
    """Open a pooled connection to ``url``'s origin so the next request skips
    the TCP and TLS handshake. Any HTTP status will do; failures are ignored."""
    try:
        http.head(str(url), timeout=5)
    except Exception:
        pass


_COORD_TAG_RE = re.compile(r"\[\s*(POINT|ARROW|CIRCLE|UNDERLINE|LABEL)\s*:([^\]]*)\]", re.IGNORECASE)
_NUM_PREFIX_RE = re.compile(r"\s*\d+(?:\s*(?:,|->)\s*\d+)*")
_NUM_RE = re.compile(r"\d+")
_SCREEN_LABEL_RE = re.compile(r"\s*screen\s+(\d+)", re.IGNORECASE)
# Leading numeric fields of each tag: x/y are positions, l is a length (scales with width).
_COORD_FIELDS = {
    "POINT": "xy", "ARROW": "xyxy", "CIRCLE": "xyl", "UNDERLINE": "xyl", "LABEL": "xy",
}


def per_mille_to_pixels(
    text: str,
    sizes: list[tuple[int, int]],
    screens: dict[int, tuple[int, int]] | None = None,
) -> str:
    """Rewrite tag coordinates from a 0-1000 grid to screenshot pixels.

    Drawing tags are always on the cursor screen (sizes[0]). A POINT's
    :screenN uses ``screens[N]``, keyed by the number in each capture's label
    because that is how the pointer is routed; the cursor screen is sent
    first, so list position is not the screen number. Label text is never
    touched.
    """
    if not sizes:
        return text

    def rewrite(m: re.Match) -> str:
        tag, body = m.group(1).upper(), m.group(2)
        w, h = sizes[0]
        screen = re.search(r":\s*screen(\d+)\s*$", body)
        if tag == "POINT" and screen and screens and int(screen.group(1)) in screens:
            w, h = screens[int(screen.group(1))]
        prefix = _NUM_PREFIX_RE.match(body)
        if not prefix:
            return m.group(0)
        kinds = iter(_COORD_FIELDS[tag])

        def scale(n: re.Match) -> str:
            kind = next(kinds, None)
            if kind is None:
                return n.group(0)
            return str(round(int(n.group(0)) * (h if kind == "y" else w) / 1000))

        head = m.group(0)[: m.start(2) - m.start(0)]
        return head + _NUM_RE.sub(scale, prefix.group(0)) + body[prefix.end():] + "]"

    return _COORD_TAG_RE.sub(rewrite, text)


class _JsonObject(list):
    """A decoded JSON object kept as its (key, value) pairs, so a repeated key
    keeps every value in the order written."""


_JSON = json.JSONDecoder(object_pairs_hook=_JsonObject)
_JSON_OPEN_RE = re.compile(r"[\[{]")
_FENCE_OPEN_RE = re.compile(r"```\w*\s*$")
_FENCE_CLOSE_RE = re.compile(r"\s*```")
# A JSON string, closed or running to the end of a cut-off answer.
_JSON_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*(?:"|\\?$)')
# What is left of JSON once its strings are removed: brackets, separators,
# numbers, literals and fences. A word of prose, or a full stop, fails it.
_JSON_RESIDUE_RE = re.compile(
    r"(?:```\w*|true|false|null|NaN|-?Infinity|-?\d+(?:\.\d*)?(?:[eE][+-]?\d*)?|[\s\[\]{}:,])*"
)
# Read straight from box JSON that won't decode.
_WRITTEN_BOX_RE = re.compile(r'"bbox_2d"\s*:\s*(\[[^\[\]]*\])')
_WRITTEN_LABEL_RE = re.compile(r'"label"\s*:\s*("(?:[^"\\]|\\.)*")')
_WRITTEN_ARRAY_RE = re.compile(r"\[[^\[\]]*\]")
# An object opening with a key, alone or in a list: '{"' or '[{"'.
_OBJECT_START_RE = re.compile(r'\[?\s*\{\s*"')
_ANY_POINT_TAG_RE = re.compile(_POINT_TAG_RE.pattern.removesuffix(r"\s*$"))
# Any [POINT ...] text, finished or not, to keep it out of speech.
_POINT_TEXT_RE = re.compile(r"\s*\[point(?::[^\]]*)?(?:\]|$)", re.IGNORECASE)
# No screen is wider than this; beyond it a "coordinate" is noise.
_MAX_COORD = 100_000


def _ollama_box_style(model_id: str) -> str | None:
    """How a local model writes a box when asked in its own format: Qwen3-VL on
    a 0-1000 grid, Qwen2.5-VL in pixels of the image it was given. None for
    models without a trained box format; they keep the [POINT] tag."""
    family = model_family(model_id)
    if family.startswith("qwen3-vl"):
        return "grid"
    if family.startswith(("qwen2.5vl", "qwen2.5-vl")):
        return "pixels"
    return None


def _is_box(value) -> bool:
    """Four usable numbers: finite and no bigger than a screen."""
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(
            isinstance(n, (int, float)) and not isinstance(n, bool) and abs(n) <= _MAX_COORD
            for n in value
        )
    )


def _boxes(value, bare: bool):
    """Each box in a decoded answer, in order, with the label beside it: every
    "bbox_2d" value, and when bare, any [x1, y1, x2, y2] as well. A "bbox_2d"
    that isn't four numbers yields None, so the JSON still counts as a box."""
    if isinstance(value, _JsonObject):
        label = next((v for k, v in value if k == "label" and isinstance(v, str)), None)
        for key, item in value:
            if key == "bbox_2d":
                yield (item if _is_box(item) else None), label
            else:
                yield from _boxes(item, bare)
    elif isinstance(value, list):
        if bare and _is_box(value):
            yield value, None
        else:
            for item in value:
                yield from _boxes(item, bare)


def _with_fence(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen a JSON span to the code fence around it, if there is one."""
    fence = _FENCE_OPEN_RE.search(text, 0, start)
    if fence:
        start = fence.start()
        close = _FENCE_CLOSE_RE.match(text, end)
        if close:
            end = close.end()
    return start, end


def _only_json(text: str) -> bool:
    """Whether text holds nothing but JSON, fences and [POINT] tags."""
    return bool(_JSON_RESIDUE_RE.fullmatch(_JSON_STRING_RE.sub("", _POINT_TEXT_RE.sub("", text))))


def _written_boxes(broken: str) -> list:
    """The first box still whole in JSON that won't decode, with its label
    when that reads: a "bbox_2d" value, else any [x1, y1, x2, y2]."""
    label = _WRITTEN_LABEL_RE.search(broken)
    try:
        label = json.loads(label.group(1)) if label else None
    except ValueError:
        label = None
    keyed = [written.group(1) for written in _WRITTEN_BOX_RE.finditer(broken)]
    for written in keyed or _WRITTEN_ARRAY_RE.findall(_JSON_STRING_RE.sub('""', broken)):
        try:
            box = json.loads(written)
        except ValueError:
            continue
        if _is_box(box):
            return [(box, label if keyed else None)]
    return []


def _broken_box_end(text: str, start: int) -> int | None:
    """End of box JSON that won't decode for a reason other than being cut off,
    such as a stray quote or a bad escape in its label: an object opening
    with a key and holding "bbox_2d", on one line or inside a code fence. It
    ends at its last closing bracket, so a sentence after it is still spoken."""
    if not _OBJECT_START_RE.match(text, start):
        return None
    stop = text.find("```" if _FENCE_OPEN_RE.search(text, 0, start) else "\n", start)
    stop = len(text) if stop < 0 else stop
    if '"bbox_2d"' not in text[start:stop]:
        return None
    close = max(text.rfind("}", start, stop), text.rfind("]", start, stop))
    return close + 1 if close >= 0 else stop


def _answer_json(text: str) -> tuple[list[tuple[int, int]], list]:
    """The spans of a local model's JSON in its answer, and the boxes in them.
    JSON counts when it holds a "bbox_2d" key, sits in a code fence, or holds
    [x1, y1, x2, y2] with only JSON after it; so does box JSON that won't
    decode because the stream cut it off or the model wrote it badly. A
    bracket, brace or backtick in a sentence is none of these, and neither is
    JSON the sentence is talking about."""
    spans, boxes, pos = [], [], 0
    while (opening := _JSON_OPEN_RE.search(text, pos)):
        start = opening.start()
        pos = start + 1
        if _POINT_TEXT_RE.match(text, start):
            continue
        try:
            value, end = _JSON.raw_decode(text, start)
            last = _only_json(text[end:])
            found = list(_boxes(value, bare=False)) or (list(_boxes(value, bare=True)) if last else [])
        except (ValueError, RecursionError):
            fragment = text[start:]
            if re.search(r'["\d]', fragment) and _only_json(fragment):
                spans.append(_with_fence(text, start, len(text)))
                boxes += _written_boxes(fragment)
                break
            end = _broken_box_end(text, start)
            if end is not None:
                spans.append(_with_fence(text, start, end))
                boxes += _written_boxes(text[start:end])
                pos = end
            continue
        span = _with_fence(text, start, end)
        if found or span[0] != start:
            spans.append(span)
            boxes += found
            pos = end
    return spans, boxes


def parse_box_answer(text: str, style: str, image_size: tuple[int, int]) -> PointParseResult:
    """Split a local model's answer into speech and a point. Its JSON is never
    spoken, whatever the shape (fenced, nested, a list, escaped, cut off), nor
    is [POINT] text; everything else is, symbols included. The point is the
    centre of the first box, or the [POINT] tag's when there is no box."""
    spans, boxes = _answer_json(text)
    pieces, last = [], 0
    for start, end in spans:
        pieces.append(text[last:start])
        last = end
    pieces.append(text[last:])
    speech = " ".join(p.strip() for p in pieces if p.strip())
    spoken = _POINT_TEXT_RE.sub("", speech).strip()
    tag = _ANY_POINT_TAG_RE.search(text)
    box, label = next(((b, lbl) for b, lbl in boxes if b), (None, None))
    if box:
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if style == "grid":
            cx, cy = cx * image_size[0] / 1000, cy * image_size[1] / 1000
        return PointParseResult(spoken_text=spoken, coordinate=(round(cx), round(cy)),
                                element_label=label, screen_number=None)
    if tag and tag.group(1) is not None:
        return PointParseResult(
            spoken_text=spoken,
            coordinate=(int(tag.group(1)), int(tag.group(2))),
            element_label=tag.group(3),
            screen_number=int(tag.group(4)) if tag.group(4) else None,
        )
    return PointParseResult(spoken_text=spoken, coordinate=None, element_label=None, screen_number=None)


def image_to_base64_jpeg(img: Image.Image, quality: int = 85) -> str:
    """Encode a PIL image to a base64-ASCII JPEG string for the Claude API."""
    buf = BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --- AIClient abstract base ---------------------------------------------------

class AIClient(ABC):
    """Abstract base for vision+LLM providers.

    Each provider is a subclass: AnthropicClient, GeminiClient,
    OpenAIVisionClient, OllamaClient.
    """

    def warm(self) -> None:
        """Open the provider connection ahead of a request. Never raises."""

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
    """Claude via plain vision streaming + [POINT:x,y:label].

    Matches Clicky's shipping path: ClaudeAPI.analyzeImageStreaming +
    CompanionManager.parsePointingCoordinates. NOT the Computer Use API beta:
    in Clicky that lives in ElementLocationDetector.swift, which is dead code
    (0 refs).
    """

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str | None = None,
    ) -> None:
        self._http = AnthropicHttpxClient()
        kwargs: dict = {"api_key": api_key, "timeout": 60.0, "http_client": self._http}
        if base_url is not None:
            kwargs["base_url"] = base_url
        self.client = Anthropic(**kwargs)
        # api.anthropic.com takes bare IDs (claude-haiku-5-5); OpenRouter takes
        # the anthropic/ slug. Keep the slug only when routing through OpenRouter.
        via_openrouter = bool(base_url) and "openrouter.ai" in base_url
        self.model_id = model_id if via_openrouter else model_id.removeprefix("anthropic/")
        self._thinking = _ANTHROPIC_THINKING.get(_bare_model(model_id).replace(".", "-"))

    def warm(self) -> None:
        _warm(self._http, self.client.base_url)

    def ask_stream(
        self,
        images: list[tuple[Image.Image, str]],
        transcript: str,
        history: list[dict],
        system_prompt: str = _CLICKY_SYSTEM_PROMPT,
        max_tokens: int = _CLICKY_MAX_TOKENS,
        kb_content: str = "",
        kb_app_name: str = "",
    ):
        """Open a streaming Claude call, return a context manager.

        Args:
            images: list of (PIL Image, label string) tuples, one per screen.
                Sorted cursor-screen-first by capture_all_screens(). Each
                becomes an image content block + a text label block in the
                user message. Matches Clicky's analyzeImageStreaming(images:
                [(Data, String)], ...) shape.
            transcript: user's voice question (raw STT output).
            history: prior turns in Anthropic SDK message format.
            system_prompt: persona + pointing instructions.
            max_tokens: token budget (1024 default, matches Clicky).
            kb_content: optional curated KB markdown body (from
                kb.recall). If non-empty, injected as a SECOND
                cache_control system block alongside the persona block.
                Empty (default) → only the persona block is sent.
            kb_app_name: sanitized .exe basename used to format the KB
                injection marker (e.g. "edupack.exe" → display "edupack").
                Ignored when kb_content is empty.

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

        # No breakpoint in this turn: the screenshot above changes every time,
        # so caching anything at or after it would be a write that never hits.
        content_blocks.append({"type": "text", "text": transcript})

        new_user_turn = {"role": "user", "content": content_blocks}

        # Cache the system prompt (largest stable text block, ~1500 chars).
        # OpenRouter passes Anthropic-native cache_control through for
        # anthropic/* routes per openrouter.ai/docs/guides/best-practices/prompt-caching.
        system_blocks = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        # Optional second cache_control block for user-uploaded curated KB
        # (kb.recall result). Per-app cache: hit within same app session,
        # miss on app switch. Anthropic's 4-block limit accommodates this
        # plus the user-message memory prefix block.
        if kb_content:
            display_name = kb_app_name.removesuffix(".exe") or "this software"
            kb_text = (
                _KB_SYSTEM_PREFIX_TEMPLATE.format(app_name=display_name)
                + kb_content
            )
            system_blocks.append({
                "type": "text",
                "text": kb_text,
                "cache_control": {"type": "ephemeral"},
            })

        extra = {"thinking": self._thinking} if self._thinking else {}
        sdk_stream_mgr = self.client.messages.stream(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[*history, new_user_turn],
            **extra,
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

    def usage(self) -> dict:
        """Token counts the provider reported for this turn ({} if unavailable)."""
        try:
            u = self._sdk_stream.get_final_message().usage
        except Exception:
            return {}
        return {
            "input": u.input_tokens,
            "output": u.output_tokens,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }


# --- Gemini (OpenAI-compatible endpoint) -------------------------------------

class GeminiClient(AIClient):
    """Gemini via the OpenAI Python SDK: OpenRouter's endpoint for an sk-or-
    key, Google's native OpenAI-compatible endpoint otherwise.
    OpenAIVisionClient reuses it with OpenAI's endpoint.

    Response parsing (parse_point_tag) is identical: both Claude and Gemini
    emit [POINT:x,y:label] per the verbatim Clicky system prompt. The only
    differences vs AnthropicClient are request shape (OpenAI chat.completions
    format instead of Anthropic messages format) and image block format
    (image_url with data URL instead of base64 source block).

    History assumption: we convert Anthropic content-block format to plain
    strings by concatenating text blocks. Non-text blocks in history are
    dropped. History only contains text blocks, so this is safe; revisit it
    if history ever carries images.
    """

    # OpenRouter accepts the classic `max_tokens` for every model + normalizes
    # it. A provider's NATIVE endpoint may need a different param (OpenAI's
    # gpt-5-series requires `max_completion_tokens`), so subclasses override
    # this; it is used only on the native (non-OpenRouter) path.
    _native_token_param: str = "max_tokens"

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        # OpenRouter wants the NAMESPACED slug (google/..., openai/...); a
        # provider's native OpenAI-compatible endpoint wants the BARE name, so
        # strip the prefix. Detect which by the base_url.
        via_openrouter = bool(base_url) and "openrouter.ai" in base_url
        if via_openrouter:
            self.model_id = model_id
        else:
            self.model_id = model_id.split("/", 1)[1] if "/" in model_id else model_id
        self._http = OpenAIHttpxClient()
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, http_client=self._http)
        self._max_tokens_param = (
            "max_tokens" if via_openrouter else self._native_token_param
        )
        # Gemini grounds on a 0-1000 grid per axis (its trained format), even
        # when the label gives the image size in pixels.
        self._per_mille = "gemini" in model_id.lower()
        self._reasoning_effort = _OPENAI_REASONING_EFFORT.get(_bare_model(model_id))

    def warm(self) -> None:
        _warm(self._http, self.client.base_url)

    def ask_stream(
        self,
        images: list[tuple[Image.Image, str]],
        transcript: str,
        history: list[dict],
        system_prompt: str = _CLICKY_SYSTEM_PROMPT,
        max_tokens: int = _CLICKY_MAX_TOKENS,
        kb_content: str = "",
        kb_app_name: str = "",
    ):
        """Open a streaming Gemini call. Returns a context manager with the
        same interface as AnthropicClient.ask_stream().

        Builds OpenAI-shaped messages:
            - System prompt goes as messages[0] role=system. If
              ``kb_content`` is non-empty, the KB block is concatenated
              onto the system prompt (Gemini via OpenAI-compat doesn't
              support multiple system blocks or cache_control breakpoints,
              so caching is best-effort via OpenRouter's prompt-caching
              auto-detection).
            - History is converted from Anthropic content-block format to
              OpenAI plain-string content (text blocks are concatenated)
            - Current user turn gets image_url + text blocks

        Usage is identical to AnthropicClient:
            with client.ask_stream(images, transcript, history) as stream:
                for delta in stream.text_deltas():
                    ...
                result = stream.final_result()
        """
        user_content: list[dict] = []
        for img, label in images:
            base64_jpeg = image_to_base64_jpeg(img)
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_jpeg}",
                },
            })
            user_content.append({"type": "text", "text": label})
        user_content.append({"type": "text", "text": transcript})

        # Concat KB into system prompt for Gemini (no native multi-block
        # support via OpenAI-compat endpoint).
        full_system = system_prompt
        if kb_content:
            display_name = kb_app_name.removesuffix(".exe") or "this software"
            full_system = (
                system_prompt
                + "\n\n"
                + _KB_SYSTEM_PREFIX_TEMPLATE.format(app_name=display_name)
                + kb_content
            )

        openai_messages: list[dict] = [
            {"role": "system", "content": full_system}
        ]
        for turn in history:
            text_parts = []
            for block in turn.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            # Skip turns with no text content (image-only or malformed
            # turns): OpenRouter rejects empty content.
            if not any(p.strip() for p in text_parts):
                continue
            openai_messages.append({
                "role": turn["role"],
                "content": " ".join(text_parts),
            })
        openai_messages.append({"role": "user", "content": user_content})

        extra = {"reasoning_effort": self._reasoning_effort} if self._reasoning_effort else {}
        try:
            sdk_iterator = self.client.chat.completions.create(
                model=self.model_id,
                messages=openai_messages,
                stream=True,
                stream_options={"include_usage": True},
                **{self._max_tokens_param: max_tokens},
                **extra,
            )
        except Exception as exc:
            raise RuntimeError(
                f"AI request failed (model={self.model_id!r}). "
                "Diagnostic checklist:\n"
                "  1. Is the key in Settings valid and funded?\n"
                "  2. Is this model available on your account?\n"
                "  3. Is your internet connection up?\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc
        sizes = screens = None
        if self._per_mille:
            sizes = [img.size for img, _ in images]
            screens = {
                int(m.group(1)): img.size
                for img, label in images
                if (m := _SCREEN_LABEL_RE.match(label))
            }
        return _GeminiStreamingResponse(sdk_iterator, per_mille_sizes=sizes, per_mille_screens=screens)

    def ask(
        self,
        image: Image.Image,
        transcript: str,
        history: list[dict],
        declared_w: int,
        declared_h: int,
    ) -> dict:
        """Batch wrapper for parity with AnthropicClient.ask()."""
        label = f"primary focus (image dimensions: {declared_w}x{declared_h} pixels)"
        with self.ask_stream([(image, label)], transcript, history) as stream:
            for _ in stream.text_deltas():
                pass
            result = stream.final_result()
        points = []
        if result.coordinate:
            x, y = result.coordinate
            points.append({"x": x, "y": y, "label": result.element_label or ""})
        return {"text": result.spoken_text, "points": points}


class _GeminiStreamingResponse:
    """Wraps OpenAI SDK streaming iterator to match AnthropicClient's
    _StreamingResponse public interface (context manager + text_deltas() +
    final_result()). Consumers of ask_stream() don't need to know which
    client is behind it.
    """

    def __init__(self, sdk_iterator, per_mille_sizes=None, per_mille_screens=None):
        self._sdk_iterator = sdk_iterator
        self._accumulated = ""
        self._deltas_exhausted = False
        self._per_mille_sizes = per_mille_sizes
        self._per_mille_screens = per_mille_screens
        self._usage = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._sdk_iterator.close()
        except Exception:
            pass
        return False  # don't swallow exceptions

    def text_deltas(self) -> Iterator[str]:
        for chunk in self._sdk_iterator:
            if getattr(chunk, "usage", None):
                self._usage = chunk.usage  # the final chunk carries the counts
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                self._accumulated += delta
                yield delta
        self._deltas_exhausted = True

    def final_result(self) -> PointParseResult:
        if not self._deltas_exhausted:
            for _ in self.text_deltas():
                pass
        text = self._accumulated
        if self._per_mille_sizes:
            text = per_mille_to_pixels(text, self._per_mille_sizes, self._per_mille_screens)
        return parse_point_tag(text)

    def usage(self) -> dict:
        """Token counts the provider reported for this turn ({} if unavailable)."""
        u = self._usage
        if u is None:
            return {}
        details = getattr(u, "prompt_tokens_details", None)
        return {
            "input": u.prompt_tokens,
            "output": u.completion_tokens,
            "cache_read": (getattr(details, "cached_tokens", 0) or 0) if details else 0,
            "cache_write": 0,
        }


# --- OpenAIVisionClient (OpenAI native API) ----------------------------------

class OpenAIVisionClient(GeminiClient):
    """OpenAI vision model via the OpenAI Python SDK pointed at OpenAI's
    native endpoint (api.openai.com).

    Reuses GeminiClient's ``ask_stream`` / ``ask`` / ``_GeminiStreamingResponse``
    verbatim: both are OpenAI-chat-completions-shaped, and only the endpoint
    and key differ. Emits ``[POINT:x,y:label]`` per the same Clicky system
    prompt.

    gpt-5.4 is pixel-accurate at grounding (5px off Claude on the EduPack
    target; 85.4% ScreenSpot-Pro). It returns a precise ``[POINT]`` tag directly,
    so app.py's grid-locator auto-skips (it only fires when no tag is present).
    The grid-locator is for weaker models such as gpt-4o (74px off) and
    Ollama's local vision models, both still selectable via env override.

    Routed via the ``openai/`` MODEL_ID prefix in create_ai_client(). The
    GPT-Realtime speech-to-speech path is separate (realtime.py); its pixel
    pass also goes through this client (see app.py).
    """

    # gpt-5-series rejects `max_tokens` on OpenAI's NATIVE endpoint with a 400
    # and requires `max_completion_tokens`. Via OpenRouter, max_tokens works (the
    # base class handles that). Used only on the native path.
    _native_token_param: str = "max_completion_tokens"

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str | None = None,
    ) -> None:
        # Same dual routing as the GeminiClient base: OpenRouter keeps the
        # namespaced openai/ slug; a direct OpenAI key (base_url=None →
        # api.openai.com) strips to the bare model name. create_ai_client picks
        # base_url by key prefix via _provider_base_url.
        super().__init__(api_key=api_key, model_id=model_id, base_url=base_url)


# --- OllamaClient (local LLM support) ----------------------------------------

class OllamaClient(AIClient):
    """Local LLM via Ollama server (https://ollama.com).

    Speaks Ollama's /api/chat streaming protocol over HTTP. Accepts multi-image
    screenshots passed as base64-encoded JPEGs inline in the user message's
    ``images`` field (Ollama-specific extension to the OpenAI-style messages
    array).

    **Pointing:** Qwen-VL models are asked for their own {"bbox_2d": [...]}
    box (see _CLICKY_BOX_SYSTEM_PROMPT) and point well that way. Other local
    models rarely return usable [POINT:x,y:label] tags, so app.py runs a
    grid-locator fallback (locator.py) after the answer when no point came
    back and the question was directional.

    Public interface matches AnthropicClient + GeminiClient: ``ask_stream(...)``
    returns a context manager with .text_deltas() generator + .final_result()
    returning PointParseResult. app.py's _pipeline_worker doesn't need to know
    which client is behind it.
    """

    def __init__(self, host: str, model_id: str) -> None:
        # Strip optional 'ollama/' prefix: the Ollama API wants the bare model name.
        if model_id.lower().startswith("ollama/"):
            self.model_id = model_id[len("ollama/"):]
        else:
            self.model_id = model_id
        self.host = host.rstrip("/")
        self.box_style = _ollama_box_style(self.model_id)

    def ask_stream(
        self,
        images: list[tuple[Image.Image, str]],
        transcript: str,
        history: list[dict],
        system_prompt: str = _CLICKY_SYSTEM_PROMPT,
        max_tokens: int = _CLICKY_MAX_TOKENS,
        kb_content: str = "",
        kb_app_name: str = "",
    ):
        """Open a streaming Ollama /api/chat call. Returns a context manager
        with the same interface as AnthropicClient.ask_stream().

        Builds Ollama-shaped messages:
            - System prompt as messages[0] role=system. KB block concat'd onto
              system prompt (Ollama doesn't support multi-block cache_control).
            - History flattened from Anthropic content-block format to plain
              strings (Ollama uses OpenAI-style string content).
            - Current user turn: {role: user, content: transcript, images: [b64...]}
              with base64-encoded JPEG screenshots as a list of strings (Ollama's
              extension to the standard message shape).

        Usage:
            with client.ask_stream(images, transcript, history) as stream:
                for delta in stream.text_deltas():
                    ...
                result = stream.final_result()
        """
        ask_for_box = self.box_style is not None and system_prompt is _CLICKY_SYSTEM_PROMPT
        if ask_for_box:
            system_prompt = _CLICKY_BOX_SYSTEM_PROMPT
        # Concat KB into system prompt (no multi-block support on Ollama).
        full_system = system_prompt
        if kb_content:
            display_name = kb_app_name.removesuffix(".exe") or "this software"
            full_system = (
                system_prompt
                + "\n\n"
                + _KB_SYSTEM_PREFIX_TEMPLATE.format(app_name=display_name)
                + kb_content
            )

        # Build OpenAI-style messages array.
        ollama_messages: list[dict] = [
            {"role": "system", "content": full_system}
        ]

        # Flatten history (Anthropic content-blocks → plain strings).
        for turn in history:
            text_parts: list[str] = []
            content = turn.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
            elif isinstance(content, str):
                text_parts.append(content)
            # Skip empty turns (image-only or malformed): Ollama rejects empty content.
            if not any(p.strip() for p in text_parts):
                continue
            ollama_messages.append({
                "role": turn["role"],
                "content": " ".join(text_parts),
            })

        # Current user turn: transcript as content, screenshots as images list
        user_turn: dict = {"role": "user", "content": transcript + (_BOX_REQUEST if ask_for_box else "")}
        if ask_for_box:
            images = images[:1]  # the cursor's screen; the box is read against it
        if images:
            user_turn["images"] = [
                image_to_base64_jpeg(img) for img, _label in images
            ]
        ollama_messages.append(user_turn)

        payload = {
            "model": self.model_id,
            "messages": ollama_messages,
            "stream": True,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0.7,
            },
        }

        return _OllamaStreamingResponse(
            host=self.host,
            payload=payload,
            model_for_errors=self.model_id,
            box_style=self.box_style if ask_for_box else None,
            image_size=images[0][0].size if images else (0, 0),
        )

    def ask(
        self,
        image: Image.Image,
        transcript: str,
        history: list[dict],
        declared_w: int,
        declared_h: int,
    ) -> dict:
        """Batch wrapper for parity with AnthropicClient.ask() / GeminiClient.ask()."""
        label = f"primary focus (image dimensions: {declared_w}x{declared_h} pixels)"
        with self.ask_stream([(image, label)], transcript, history) as stream:
            for _ in stream.text_deltas():
                pass
            result = stream.final_result()
        points = []
        if result.coordinate:
            x, y = result.coordinate
            points.append({"x": x, "y": y, "label": result.element_label or ""})
        return {"text": result.spoken_text, "points": points}


class _OllamaStreamingResponse:
    """Wraps httpx streaming response from Ollama /api/chat to match
    AnthropicClient + GeminiClient's _StreamingResponse public interface
    (context manager + text_deltas() + final_result()). Consumers of
    ask_stream() don't need to know which client is behind it.

    Each instance opens its own httpx.Client + stream context on __enter__
    (so the network call doesn't fire until the caller actually wants to
    stream). __exit__ closes both.
    """

    def __init__(
        self,
        host: str,
        payload: dict,
        model_for_errors: str,
        box_style: str | None = None,
        image_size: tuple[int, int] = (0, 0),
    ) -> None:
        self._host = host
        self._payload = payload
        self._model_for_errors = model_for_errors
        self._box_style = box_style
        self._image_size = image_size
        self._httpx_client = None
        self._stream_cm = None
        self._response = None
        self._accumulated = ""
        self._deltas_exhausted = False

    def __enter__(self):
        # Open the httpx client first. If any subsequent open-step raises,
        # we MUST close this client: the caller's `with` block never gets
        # entered, so __exit__ won't fire. Without the try/except below the
        # client would leak its connection pool every time Ollama is
        # unreachable (DNS failure, ECONNREFUSED, server crash, etc).
        # Reading a new screenshot can take minutes on a laptop without a strong
        # GPU, so allow that; Ollama not running at all still fails in seconds.
        self._httpx_client = httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0))
        self._httpx_client.__enter__()
        try:
            self._stream_cm = self._httpx_client.stream(
                "POST",
                f"{self._host}/api/chat",
                json=self._payload,
            )
            self._response = self._stream_cm.__enter__()

            # Friendly error for the most common Ollama mistake: model not pulled
            if self._response.status_code == 404:
                # Close before raising to release the connection
                try:
                    self._stream_cm.__exit__(None, None, None)
                finally:
                    self._httpx_client.__exit__(None, None, None)
                    self._httpx_client = None
                    self._stream_cm = None
                raise RuntimeError(
                    f"Ollama doesn't have '{self._model_for_errors}' installed. "
                    f"Run: ollama pull {self._model_for_errors}"
                )
            self._response.raise_for_status()
        except BaseException:
            # Any failure between httpx_client open and successful
            # raise_for_status (network error, 5xx, etc.): close the
            # client we just opened, null the slot so __exit__ is a no-op,
            # re-raise so the caller sees the original exception.
            if self._stream_cm is not None:
                try:
                    self._stream_cm.__exit__(None, None, None)
                except Exception:
                    pass
                self._stream_cm = None
            try:
                self._httpx_client.__exit__(None, None, None)
            except Exception:
                pass
            self._httpx_client = None
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._stream_cm is not None:
                self._stream_cm.__exit__(exc_type, exc_val, exc_tb)
        finally:
            if self._httpx_client is not None:
                self._httpx_client.__exit__(exc_type, exc_val, exc_tb)
        return False  # don't swallow exceptions

    def text_deltas(self) -> Iterator[str]:
        """Yield progressive text chunks from Ollama's JSON-per-line stream.

        Each line is a JSON object like {"message": {"content": "..."}, "done": false}.
        We accumulate the .message.content fields and yield non-empty chunks.

        On socket failure mid-stream (Ollama crash, network drop), httpx raises
        ReadError / RemoteProtocolError. We MUST mark deltas_exhausted=True in
        a finally block so that a subsequent final_result() call doesn't re-
        enter this iterator and re-raise the same exception, instead returning
        whatever was accumulated before the failure (graceful degradation:
        the user gets a partial response with parse_point_tag falling back to
        no coordinate).
        """
        import json as _json
        try:
            for line in self._response.iter_lines():
                if not line:
                    continue
                # iter_lines() in httpx returns str by default in newer
                # versions, bytes in older. Handle both.
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                try:
                    data = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    self._accumulated += chunk
                    yield chunk
                if data.get("done"):
                    break
        finally:
            # ALWAYS set this, even on exception. Prevents final_result()
            # from re-entering this generator and re-raising the same error.
            self._deltas_exhausted = True

    def final_result(self) -> PointParseResult:
        """Parse the accumulated text for a [POINT:x,y:label] tag.

        Safe to call even if text_deltas() raised mid-stream: we just parse
        whatever was accumulated before the failure (may be empty string,
        in which case parse_point_tag returns PointParseResult with no
        coordinate and empty spoken_text). The pipeline worker handles
        empty results gracefully.
        """
        if not self._deltas_exhausted:
            # text_deltas() guarantees _deltas_exhausted=True even on failure,
            # so this re-entry can only happen if the caller never consumed
            # any deltas. Consume them now (best-effort: if iter_lines
            # raises, our own try/finally above flips the flag so we don't
            # loop forever).
            try:
                for _ in self.text_deltas():
                    pass
            except Exception:
                # Already accumulated whatever streamed in; parse_point_tag
                # below will return a PointParseResult with no coordinate.
                pass
        if self._box_style:
            return parse_box_answer(self._accumulated, self._box_style, self._image_size)
        return parse_point_tag(self._accumulated)

    def usage(self) -> dict:
        """Local models have no billed tokens."""
        return {}


# --- Factory: route model_id prefix to right AIClient subclass ---------------

# Bare-name prefixes that route to OllamaClient (in addition to ollama/* prefix).
# These match common local-model name conventions Ollama uses.
_OLLAMA_BARE_PREFIXES = ("llama", "qwen", "llava", "mistral", "phi", "gemma")


def _provider_base_url(
    api_key: str,
    base_url: str | None,
    openrouter_url: str,
    native_url: str | None = None,
) -> str | None:
    """Shared endpoint decision for every LLM provider. An explicit base_url
    wins; else an OpenRouter (sk-or-) key routes to openrouter_url; else the
    provider's native endpoint (native_url, or None = the SDK's own default).

    Every branch in create_ai_client calls this, so no provider can 'forget'
    OpenRouter routing and send an sk-or- key to its native endpoint, which
    answers 401.
    """
    if base_url is not None:
        return base_url
    if api_key and api_key.startswith("sk-or-"):
        return openrouter_url
    return native_url


def create_ai_client(
    model_id: str,
    api_key: str,
    base_url: str | None = None,
    ollama_host: str | None = None,
) -> AIClient:
    """Route to AnthropicClient, GeminiClient, or OllamaClient based on model_id prefix.

    This is THE BYOK abstraction. Users change MODEL_ID in .env, app.py calls
    this factory, and the right SDK routes the request. No app.py logic
    depends on which model family is active.

    Args:
        model_id: OpenRouter-style model ID. Prefix determines client:
            'anthropic/...' or 'claude...'      → AnthropicClient (anthropic SDK)
            'google/...' or 'gemini...'         → GeminiClient (openai SDK)
            'ollama/...' or bare 'llama*' /     → OllamaClient (httpx → local server)
                'qwen*' / 'llava*' / 'mistral*' /
                'phi*' / 'gemma*'
            Other prefixes raise ValueError with an actionable message.
        api_key: API key. Ignored for Ollama (local server, unauthenticated).
            For Anthropic, OpenAI and Gemini: the provider's own key, or one
            OpenRouter (sk-or-) key, which routes the call through OpenRouter.
        base_url: Optional override for cloud providers' API endpoints. Testing
            hook; production leaves it None so each client uses its SDK's
            default. Ignored by OllamaClient (use ``ollama_host`` instead).
        ollama_host: Optional override for Ollama server URL. Defaults to
            ``http://localhost:11434`` (Ollama's out-of-the-box binding).
            Only used when dispatching to OllamaClient.

    Returns:
        A concrete AIClient subclass ready for .ask_stream() calls.

    Raises:
        ValueError: if model_id prefix is not recognized. Error message lists
            the supported prefixes and hints how to add a new provider.
    """
    mid = model_id.lower()

    # Ollama dispatch FIRST: `llama*` and `qwen*` prefixes are unambiguous local
    # (no cloud provider ships them under those bare names).
    if mid.startswith("ollama/") or any(
        mid.startswith(p) for p in _OLLAMA_BARE_PREFIXES
    ):
        return OllamaClient(
            host=ollama_host or "http://localhost:11434",
            model_id=model_id,
        )

    if mid.startswith("anthropic/") or mid.startswith("claude"):
        # Auto-route OpenRouter keys (sk-or-v1-*) to OpenRouter's
        # Anthropic-compat endpoint when no explicit base_url given.
        # Bundled Clicky.exe has cwd outside the repo, so .env doesn't
        # load and ANTHROPIC_BASE_URL env var is unset. Without this
        # fallback the SDK defaults to api.anthropic.com and Anthropic
        # rejects the OpenRouter-namespaced key with 401 invalid x-api-key.
        # Direct Anthropic keys (sk-ant-*) leave base_url=None so the
        # SDK uses its default api.anthropic.com endpoint, where those
        # keys are valid.
        base_url = _provider_base_url(api_key, base_url, "https://openrouter.ai/api")
        return AnthropicClient(
            api_key=api_key, model_id=model_id, base_url=base_url,
        )
    if mid.startswith("google/") or mid.startswith("gemini"):
        from config import OPENROUTER_BASE_URL
        # sk-or- key → OpenRouter (GeminiClient keeps the google/ slug); a
        # direct Google AI Studio key → Google's native OpenAI-compat endpoint
        # (GeminiClient strips the google/ prefix). One OpenRouter key OR a
        # direct Google key both work.
        base_url = _provider_base_url(
            api_key, base_url, OPENROUTER_BASE_URL,
            native_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        return GeminiClient(api_key=api_key, model_id=model_id, base_url=base_url)
    # OpenAI: 'openai/gpt-4o' etc. The GPT-Realtime speech-to-speech path
    # uses LLM_PROVIDER='openai-realtime' and is handled separately in app.py
    # (realtime.py), NOT through this factory, so 'gpt-realtime*' should
    # never reach here as a vision MODEL_ID.
    if mid.startswith("openai/"):
        from config import OPENROUTER_BASE_URL
        # sk-or- key → OpenRouter (OpenAIVisionClient keeps the openai/ slug); a
        # direct OpenAI key → base_url None so the SDK uses api.openai.com (the
        # client strips the openai/ prefix + uses max_completion_tokens there).
        base_url = _provider_base_url(api_key, base_url, OPENROUTER_BASE_URL)
        return OpenAIVisionClient(
            api_key=api_key,
            model_id=model_id,
            base_url=base_url,
        )
    raise ValueError(
        f"Unsupported MODEL_ID prefix: {model_id!r}. "
        f"Supported prefixes: 'anthropic/...' (or 'claude...'), "
        f"'google/...' (or 'gemini...'), 'openai/...', 'ollama/...' (or bare "
        f"'llama*'/'qwen*'/'llava*'/'mistral*'/'phi*'/'gemma*'). "
        f"To add a new provider, subclass AIClient in ai.py and extend "
        f"create_ai_client() with a new branch."
    )


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

    print("\n\nSpoken text (tag stripped):")
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
