"""Two-stage grid-locator for pixel-pointing with weak vision LLMs.

Used by OllamaClient (and any future vision LLM that can't reliably return
pixel coordinates) to derive a (x, y) pointing target from a screenshot
plus a natural-language query.

Algorithm (per Bitshank-2338/clicky-windows MIT, ai/universal_locator.py):

    Stage 1 (coarse, 12x8 = 96 cells):
        - Draw a numbered red grid overlay on the screenshot.
        - Ask the LLM: "Which numbered cell contains <target>?"
        - LLM picks 1-96.

    Stage 2 (fine, 6x6 = 36 sub-cells in 3x3 region around the pick):
        - Crop a 3x3-cell region around the chosen Stage-1 cell.
        - Draw a 6x6 sub-grid on the crop.
        - Ask again: "Which sub-cell contains <target>?"
        - LLM picks 1-36 within the crop.

    Final: Map the centre of the chosen sub-cell back to original screen
    pixels, then to logical Qt coordinates.

Accuracy: ~25-50px on a 1080p screen. Adequate for buttons, menu items,
links, icons. Less precise than Claude's native [POINT:x,y] tag (~5px)
but works with ANY vision-capable model including local Ollama (llama3.2-
vision, qwen2.5-vl, llava).

The LLM doesn't need to return pixel coordinates — it just picks a number
1-96 (then 1-36) from a labeled grid. Even weak models that fail at raw
pixel-grounding can do this reliably.

Public API:
    locate_via_grid(llm_client, screenshot_jpeg_b64, original_size,
                    physical_size, physical_origin, dpi_scale, query)
        -> tuple[int, int] | None

Returns coordinates in LOGICAL screen pixels (same space as QCursor.pos()),
ready to feed directly into the overlay pointer. Returns None when the
query is conceptual (no UI element to point at) or the LLM couldn't pick
a cell.
"""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Optional

from PIL import Image, ImageDraw, ImageFont


# --- Tunables (locked, mirror Bitshank-2338) ----------------------------------

STAGE1_COLS = 12
STAGE1_ROWS = 8
STAGE2_COLS = 6
STAGE2_ROWS = 6
ZOOM_RADIUS_CELLS = 1   # Stage-2 zoom region = (1 + 2*ZOOM_RADIUS) cells wide
MAX_INFERENCE_WIDTH = 1280


# --- Grid drawing -------------------------------------------------------------

def _load_font(size: int) -> ImageFont.ImageFont:
    """Best-effort font loader. Falls back to PIL default if no system font."""
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _draw_grid(img: Image.Image, cols: int, rows: int) -> Image.Image:
    """Overlay a numbered red grid on `img`. Returns a new RGB image of same size.

    Cell labels go top-left of each cell, white text on a red filled rectangle
    background, sized proportional to cell dimensions.
    """
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    w, h = base.size
    cell_w = w / cols
    cell_h = h / rows

    line_color = (255, 0, 0, 200)
    label_bg = (255, 0, 0, 220)
    label_fg = (255, 255, 255, 255)

    # Vertical grid lines (skip image borders)
    for c in range(1, cols):
        x = int(c * cell_w)
        draw.line([(x, 0), (x, h)], fill=line_color, width=1)
    # Horizontal grid lines
    for r in range(1, rows):
        y = int(r * cell_h)
        draw.line([(0, y), (w, y)], fill=line_color, width=1)

    # Cell number labels, font scaled to cell size
    font_size = max(12, min(28, int(min(cell_w, cell_h) / 3.5)))
    font = _load_font(font_size)

    n = 1
    for r in range(rows):
        for c in range(cols):
            cx = int(c * cell_w) + 2
            cy = int(r * cell_h) + 2
            label = str(n)
            try:
                bbox = draw.textbbox((cx, cy), label, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            except Exception:
                tw, th = font_size * len(label) // 2, font_size
            draw.rectangle(
                [(cx - 1, cy - 1), (cx + tw + 4, cy + th + 4)],
                fill=label_bg,
            )
            draw.text((cx + 2, cy), label, fill=label_fg, font=font)
            n += 1

    return Image.alpha_composite(base, overlay).convert("RGB")


def _img_to_jpeg_b64(img: Image.Image, quality: int = 85) -> str:
    """Encode PIL image as base64 JPEG string (no data: prefix)."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --- Cell-number parsing ------------------------------------------------------

_PARSE_RE = re.compile(r"\b(\d{1,5})\b")


def _parse_cell_number(text: str, max_n: int) -> Optional[int]:
    """Extract a cell number 0..max_n from a free-form LLM reply.

    Returns:
        0     - LLM signaled "no specific UI element" (conceptual question)
        1..N  - Valid cell pick
        None  - No valid number could be extracted

    Strategy:
        1. Try parsing first JSON-shaped {"cell": N} object in the text.
        2. Otherwise scan integers in order, return first 0..max_n match.
    """
    # JSON-shaped answer (preferred — system prompt asks for this).
    json_match = re.search(r"\{[^{}]*\}", text, flags=re.DOTALL)
    if json_match:
        try:
            obj = json.loads(json_match.group(0))
            for key in ("cell", "number", "n", "answer"):
                if key in obj:
                    try:
                        n = int(obj[key])
                        if 0 <= n <= max_n:
                            return n
                    except (ValueError, TypeError):
                        continue
        except json.JSONDecodeError:
            pass

    # Fallback: scan integers in order, take first valid one
    for tok in _PARSE_RE.findall(text):
        try:
            n = int(tok)
            if 0 <= n <= max_n:
                return n
        except ValueError:
            continue

    return None


# --- LLM call -----------------------------------------------------------------

def _ask_grid_pick(
    llm_client,
    img_b64: str,
    target: str,
    max_n: int,
    debug_log: Optional[callable] = None,
) -> Optional[int]:
    """Send annotated screenshot + question to LLM, parse cell number from reply.

    Calls llm_client.ask_stream(images=[(PIL.Image, label)], transcript, history=[])
    which returns a context manager with .text_deltas() generator.

    We accumulate the streamed reply and run _parse_cell_number on it. We
    short-circuit reading the stream once the reply exceeds 400 chars — the
    expected answer is a tiny JSON object, no need to consume more.

    Returns:
        int 0..max_n  - LLM picked this cell (0 = "no UI element / conceptual")
        None          - LLM call failed (transport error, parse error, timeout)
                        OR LLM reply didn't contain a parseable number

    Args:
        debug_log: optional callable taking a single str arg. If provided,
            emits a diagnostic message DISTINGUISHING transport failures
            (network error, image decoding, llm exception) from model
            uncertainty (unparseable reply or out-of-range cell number).
            Without this, all None returns looked the same to the caller —
            a real bug (Ollama down) was indistinguishable from "model
            said cell 0". Caught by codex adversarial review 2026-06-05.
    """
    prompt = (
        f"You are looking at a screenshot with a red numbered grid overlay. "
        f"Cells are numbered 1 to {max_n}, left-to-right, top-to-bottom.\n\n"
        f'The user asked: "{target}"\n\n'
        f"Identify the SINGLE numbered cell that most precisely contains the "
        f"UI element the user is asking about (button, link, menu item, icon, "
        f"text field, etc.).\n\n"
        f'Respond with ONLY this JSON, nothing else:  {{"cell": <number>}}\n\n'
        f"If there's no specific UI element to point at (purely conceptual "
        f'question), respond exactly:  {{"cell": 0}}'
    )

    # Decode b64 back into a PIL Image for the AIClient.ask_stream() interface.
    try:
        img_bytes = base64.b64decode(img_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as exc:
        if debug_log is not None:
            debug_log(
                f"GRID-LOCATOR: image-decode failure "
                f"({type(exc).__name__}: {exc}) — returning None"
            )
        return None

    chunks: list[str] = []
    try:
        with llm_client.ask_stream(
            images=[(img, "grid-annotated screenshot")],
            transcript=prompt,
            history=[],
        ) as stream:
            for chunk in stream.text_deltas():
                chunks.append(chunk)
                if sum(len(c) for c in chunks) > 400:
                    break
    except Exception as exc:
        # Transport / network / model-not-pulled / etc. Distinguish from
        # "LLM returned valid but unparseable text" downstream.
        if debug_log is not None:
            debug_log(
                f"GRID-LOCATOR: LLM call failed "
                f"({type(exc).__name__}: {exc}) — transport or model error, "
                f"returning None"
            )
        return None

    parsed = _parse_cell_number("".join(chunks), max_n)
    if parsed is None and debug_log is not None:
        # LLM responded but reply was unparseable. Distinguishable from
        # transport failure (above) and from cell-0 (returned as 0).
        reply_preview = "".join(chunks)[:200]
        debug_log(
            f"GRID-LOCATOR: LLM replied but reply unparseable "
            f"(no cell number 0-{max_n} found in: {reply_preview!r}) — "
            f"returning None"
        )
    return parsed


# --- Main entry ---------------------------------------------------------------

def locate_via_grid(
    *,
    llm_client,
    screenshot_jpeg_b64: str,
    original_size: tuple[int, int],
    physical_size: tuple[int, int],
    physical_origin: tuple[int, int] = (0, 0),
    dpi_scale: float = 1.0,
    query: str,
    debug_log: Optional[callable] = None,
) -> Optional[tuple[int, int]]:
    """Two-stage grid-locator. Returns (x, y) in LOGICAL Qt screen coords, or None.

    Args:
        llm_client: object with ``.ask_stream(images=[(PIL.Image, label)],
            transcript=str, history=[])`` method (e.g. OllamaClient,
            AnthropicClient).
        screenshot_jpeg_b64: base64-encoded JPEG of the screenshot.
        original_size: (width, height) of the JPEG as captured.
        physical_size: (width, height) of the physical monitor in screen pixels.
        physical_origin: (left, top) of the physical monitor in virtual-desktop
            coords. Default (0, 0) for single-monitor / primary.
        dpi_scale: e.g. 2.25 for a 225% Windows DPI display. Used to convert
            physical px to logical Qt coords (which is what QCursor.pos()
            and the overlay's point_at() expect).
        query: the user's natural-language question (e.g. "where's the save
            button?").

    Returns:
        (x, y) tuple in logical Qt coords if both stages succeed.
        None if the LLM signals the question is conceptual (cell 0), the LLM
        can't pick a cell, or anything else goes wrong (graceful fail).
    """
    # Decode + (optionally) downscale to inference size.
    raw_bytes = base64.b64decode(screenshot_jpeg_b64)
    full_img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    fw, fh = full_img.size

    scale_w = MAX_INFERENCE_WIDTH / fw if fw > MAX_INFERENCE_WIDTH else 1.0
    iw = int(fw * scale_w)
    ih = int(fh * scale_w)
    infer_img = (
        full_img.resize((iw, ih), Image.Resampling.LANCZOS)
        if scale_w != 1.0
        else full_img
    )

    # --- Stage 1: coarse 12x8 grid ------------------------------------------
    s1_img = _draw_grid(infer_img, STAGE1_COLS, STAGE1_ROWS)
    s1_b64 = _img_to_jpeg_b64(s1_img, quality=80)

    s1_max = STAGE1_COLS * STAGE1_ROWS
    s1_pick = _ask_grid_pick(llm_client, s1_b64, query, s1_max, debug_log=debug_log)
    if s1_pick is None or s1_pick == 0:
        return None

    # Convert cell number → row/col (1-indexed, row-major).
    idx = s1_pick - 1
    s1_row = idx // STAGE1_COLS
    s1_col = idx % STAGE1_COLS
    cell_w = iw / STAGE1_COLS
    cell_h = ih / STAGE1_ROWS

    # Stage-2 crop region: ZOOM_RADIUS cells in each direction.
    c0 = max(0, s1_col - ZOOM_RADIUS_CELLS)
    r0 = max(0, s1_row - ZOOM_RADIUS_CELLS)
    c1 = min(STAGE1_COLS - 1, s1_col + ZOOM_RADIUS_CELLS)
    r1 = min(STAGE1_ROWS - 1, s1_row + ZOOM_RADIUS_CELLS)

    crop_left = int(c0 * cell_w)
    crop_top = int(r0 * cell_h)
    crop_right = int((c1 + 1) * cell_w)
    crop_bottom = int((r1 + 1) * cell_h)

    crop = infer_img.crop((crop_left, crop_top, crop_right, crop_bottom))

    # --- Stage 2: fine 6x6 grid on the cropped region -----------------------
    # Optionally upscale the crop so grid labels are crisp at vision-LLM
    # resolution (helps weaker models like llava).
    target_crop_w = max(crop.size[0], 768)
    if crop.size[0] < target_crop_w:
        scale = target_crop_w / crop.size[0]
        crop = crop.resize(
            (target_crop_w, int(crop.size[1] * scale)),
            Image.Resampling.LANCZOS,
        )

    s2_img = _draw_grid(crop, STAGE2_COLS, STAGE2_ROWS)
    s2_b64 = _img_to_jpeg_b64(s2_img, quality=85)

    s2_max = STAGE2_COLS * STAGE2_ROWS
    s2_pick = _ask_grid_pick(llm_client, s2_b64, query, s2_max, debug_log=debug_log)

    if s2_pick is None or s2_pick == 0:
        # Fall back to centre of Stage-1 cell (still better than nothing).
        infer_x = (s1_col + 0.5) * cell_w
        infer_y = (s1_row + 0.5) * cell_h
    else:
        s2_idx = s2_pick - 1
        s2_row = s2_idx // STAGE2_COLS
        s2_col = s2_idx % STAGE2_COLS
        s2_cell_w = (crop_right - crop_left) / STAGE2_COLS
        s2_cell_h = (crop_bottom - crop_top) / STAGE2_ROWS
        infer_x = crop_left + (s2_col + 0.5) * s2_cell_w
        infer_y = crop_top + (s2_row + 0.5) * s2_cell_h

    # --- Coord transform: infer-image -> original JPEG -> physical -> logical
    jpeg_x = infer_x / scale_w
    jpeg_y = infer_y / scale_w

    orig_w, orig_h = original_size
    phys_w, phys_h = physical_size
    phys_left, phys_top = physical_origin

    px = jpeg_x / orig_w * phys_w
    py = jpeg_y / orig_h * phys_h

    vx = px + phys_left
    vy = py + phys_top

    s = dpi_scale if dpi_scale > 0 else 1.0
    return (int(round(vx / s)), int(round(vy / s)))
