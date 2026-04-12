"""Screen capture + cursor + DPI + resolution picking for Clicky Windows.

Produces Claude-ready images (exact target dimensions) with metadata
for coordinate unscaling. See docs/superpowers/plans/2026-04-11-capture.md
for the architecture overview and three coordinate spaces diagram.

Responsibility boundary:
- THIS MODULE lives in Space A (physical pixels) and Space C (Claude's
  declared resolution). It owns the math that maps between them.
- overlay.py owns Space B (Qt logical pixels) and the physical->logical
  conversion via DPI scaling.
- app.py owns threading and calls this module from worker threads.

Nothing in this module is hardcoded to a specific monitor; everything is
detected at runtime via mss, ctypes, and the three candidate resolutions
in config.py.

Top-to-bottom order (so `python -m capture` works):
    1. Imports
    2. Win32 constants + _POINT struct
    3. CaptureResult dataclass
    4. Pure functions (pick_resolution, resize_for_claude, unscale_claude_coords)
    5. OS-wrapper functions (set_dpi_awareness, get_cursor_position,
       list_monitors, monitor_containing)
    6. Private capture helper (_capture_monitor)
    7. Composite: capture_active_screen()
    8. __main__ block for manual verification
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass

import mss
from PIL import Image, ImageDraw

from config import CANDIDATE_RESOLUTIONS


# --- Win32 constants ---------------------------------------------------------

_PROCESS_PER_MONITOR_DPI_AWARE_V2 = 2
"""SetProcessDpiAwareness value for per-monitor v2 (Win10 1703+)."""


class _POINT(ctypes.Structure):
    """Win32 POINT struct for GetCursorPos."""
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# --- Result dataclass --------------------------------------------------------

@dataclass
class CaptureResult:
    """All the metadata needed to send a screenshot to Claude and later
    map Claude's returned coordinates back to the user's physical screen.

    Attributes:
        image: PIL Image, already resized to exact (target_width, target_height).
        target_width: width declared to Claude's vision call.
        target_height: height declared to Claude's vision call.
        monitor: mss-style dict with 'left', 'top', 'width', 'height' in
            physical pixel virtual-desktop coords.
        cursor_physical: (x, y) of the cursor in physical pixels at capture time.
        scale_x: physical monitor width / target_width (for unscaling).
        scale_y: physical monitor height / target_height (for unscaling).
    """
    image: Image.Image
    target_width: int
    target_height: int
    monitor: dict
    cursor_physical: tuple[int, int]
    scale_x: float
    scale_y: float


@dataclass
class LabeledCapture:
    """Extended capture result with a label string for Claude's multi-screen context.

    Mirrors Clicky's CompanionScreenCaptureUtility.captureAllScreensAsJPEG()
    output shape: each captured screen gets a label like
    "screen 1 of 2 — cursor is on this screen (primary focus) (image dimensions: 1280x800 pixels)".

    Attributes:
        image: PIL Image, already resized to exact (target_width, target_height).
        label: human-readable label for Claude's context (includes pixel dims).
        is_cursor_screen: True if the cursor was on this monitor at capture time.
        monitor: mss-style dict with 'left', 'top', 'width', 'height'.
        target_width: width declared to Claude's vision call.
        target_height: height declared to Claude's vision call.
        scale_x: physical monitor width / target_width (for unscaling).
        scale_y: physical monitor height / target_height (for unscaling).
    """
    image: Image.Image
    label: str
    is_cursor_screen: bool
    monitor: dict
    target_width: int
    target_height: int
    scale_x: float
    scale_y: float


# --- Pure functions ----------------------------------------------------------

def pick_resolution(width: int, height: int) -> tuple[int, int]:
    """Pick the closest-aspect-ratio resolution from CANDIDATE_RESOLUTIONS.

    Mirrors Clicky's CompanionScreenCaptureUtility.swift resolution logic.
    The goal is to avoid distortion when resizing a monitor's image
    down to a Claude-friendly resolution. Picking a resolution whose
    aspect ratio is closest to the source minimizes stretching and preserves
    X/Y coordinate accuracy.

    Args:
        width: source width in pixels (must be > 0).
        height: source height in pixels (must be > 0).

    Returns:
        (target_w, target_h) tuple from config.CANDIDATE_RESOLUTIONS.

    Raises:
        ValueError: if width <= 0 or height <= 0.
    """
    if width <= 0 or height <= 0:
        raise ValueError(
            f"width and height must be positive, got ({width}, {height})"
        )
    source_ratio = width / height
    best = min(
        CANDIDATE_RESOLUTIONS,
        key=lambda res: abs(source_ratio - (res[0] / res[1])),
    )
    return best


def resize_for_claude(
    img: Image.Image,
    target_w: int,
    target_h: int,
) -> tuple[Image.Image, float, float]:
    """Resize a PIL image to exact (target_w, target_h) using LANCZOS.

    Computes the scale factors needed to later unscale Claude's returned
    coordinates back to the source image's pixel space:
        physical_x = claude_x * scale_x
        physical_y = claude_y * scale_y

    Args:
        img: source PIL Image in any mode.
        target_w: target width in pixels (must be > 0).
        target_h: target height in pixels (must be > 0).

    Returns:
        (resized_img, scale_x, scale_y) where:
            resized_img.size == (target_w, target_h) exactly
            scale_x = img.width / target_w
            scale_y = img.height / target_h

    Raises:
        ValueError: if target_w <= 0 or target_h <= 0.
    """
    if target_w <= 0 or target_h <= 0:
        raise ValueError(
            f"target dims must be positive, got ({target_w}, {target_h})"
        )
    scale_x = img.width / target_w
    scale_y = img.height / target_h
    resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    return resized, scale_x, scale_y


def unscale_claude_coords(
    claude_x: int,
    claude_y: int,
    scale_x: float,
    scale_y: float,
    monitor_left: int,
    monitor_top: int,
    target_w: int,
    target_h: int,
) -> tuple[int, int]:
    """Map Claude's (x, y) in declared resolution space -> physical pixels
    in virtual desktop coords.

    Clamps claude_x / claude_y to [0, target_w - 1] and [0, target_h - 1]
    BEFORE scaling. Claude occasionally returns slightly out-of-bounds
    coordinates; Clicky's ElementLocationDetector.swift clamps for the same
    reason.

    Args:
        claude_x: x coordinate Claude returned, in declared resolution space.
        claude_y: y coordinate Claude returned, in declared resolution space.
        scale_x: physical width / target_w (from resize_for_claude).
        scale_y: physical height / target_h (from resize_for_claude).
        monitor_left: monitor's x offset in virtual desktop coords.
        monitor_top: monitor's y offset in virtual desktop coords.
        target_w: the declared width we sent to Claude (for clamping).
        target_h: the declared height we sent to Claude (for clamping).

    Returns:
        (physical_x, physical_y) in virtual desktop pixel coordinates,
        ready for the overlay's physical->Qt-logical conversion.
    """
    clamped_x = max(0, min(claude_x, target_w - 1))
    clamped_y = max(0, min(claude_y, target_h - 1))
    physical_x = monitor_left + int(clamped_x * scale_x)
    physical_y = monitor_top + int(clamped_y * scale_y)
    return physical_x, physical_y


# --- OS wrappers -------------------------------------------------------------

def set_dpi_awareness() -> bool:
    """Set per-monitor v2 DPI awareness for this process.

    Windows only allows DPI awareness to be set once per process. If another
    library (PyQt6, tkinter, a previous call from us) already set it, the
    call returns E_ACCESSDENIED (-2147024891) and this function returns
    False without raising. Either way, per-monitor v2 is active.

    Returns:
        True if we set it successfully just now, False if it was already set.
    """
    try:
        hresult = ctypes.windll.shcore.SetProcessDpiAwareness(
            _PROCESS_PER_MONITOR_DPI_AWARE_V2
        )
        return hresult == 0
    except OSError:
        return False


def get_cursor_position() -> tuple[int, int]:
    """Return cursor (x, y) in physical pixels (virtual desktop coords).

    Requires set_dpi_awareness() to have been called first; otherwise on
    multi-monitor / mixed-DPI setups the returned coords are in a
    DPI-virtualized space and wrong.
    """
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def list_monitors() -> list[dict]:
    """Return all physical monitors as mss-style dicts.

    mss's monitors[0] is a virtual-desktop union of every monitor; we skip it
    and return only the real physical monitors. Each dict has 'left', 'top',
    'width', 'height' keys.
    """
    with mss.mss() as sct:
        return [dict(m) for m in sct.monitors[1:]]


def monitor_containing(x: int, y: int, monitors: list[dict]) -> dict:
    """Return the monitor whose rectangle contains the point (x, y).

    The rectangle is half-open: [left, left + width) x [top, top + height).
    If no monitor contains the point (dead zone between monitors, rare but
    possible), falls back to the first monitor in the list (primary).

    Args:
        x: point x in virtual desktop physical pixels.
        y: point y in virtual desktop physical pixels.
        monitors: list of mss-style monitor dicts (first = primary).

    Returns:
        The containing monitor dict, or monitors[0] as fallback.
    """
    for m in monitors:
        if (m["left"] <= x < m["left"] + m["width"]
                and m["top"] <= y < m["top"] + m["height"]):
            return m
    return monitors[0]


# --- Composite ---------------------------------------------------------------

def _capture_monitor(monitor: dict) -> Image.Image:
    """Grab a monitor's pixels via mss and return as a PIL RGB image.

    mss returns BGRA data; we convert to RGB here so downstream code doesn't
    have to worry about channel order.
    """
    with mss.mss() as sct:
        sct_img = sct.grab(monitor)
        return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")


def capture_active_screen() -> CaptureResult:
    """Capture the monitor currently under the cursor, resize for Claude.

    Composite of the other functions in this module. Everything is
    runtime-detected: number of monitors, sizes, DPI, cursor position,
    aspect ratio, scale factors. Works on any monitor configuration.

    Returns:
        A CaptureResult with the resized image and all metadata needed
        to later unscale Claude's coordinates back to physical pixels.
    """
    set_dpi_awareness()  # Idempotent; safe to call every capture.
    cursor_x, cursor_y = get_cursor_position()
    monitors = list_monitors()
    monitor = monitor_containing(cursor_x, cursor_y, monitors)
    raw_img = _capture_monitor(monitor)
    target_w, target_h = pick_resolution(raw_img.width, raw_img.height)
    resized, scale_x, scale_y = resize_for_claude(raw_img, target_w, target_h)
    return CaptureResult(
        image=resized,
        target_width=target_w,
        target_height=target_h,
        monitor=monitor,
        cursor_physical=(cursor_x, cursor_y),
        scale_x=scale_x,
        scale_y=scale_y,
    )


def capture_all_screens() -> list[LabeledCapture]:
    """Capture all connected monitors, resize each for Claude, return sorted
    cursor-screen-first with labeled metadata.

    Mirrors Clicky's CompanionScreenCaptureUtility.captureAllScreensAsJPEG():
    - Captures every physical monitor (not just the cursor screen)
    - Labels each with "primary focus" marker + pixel dimensions
    - Sorts so the cursor's screen is always first

    Returns:
        list[LabeledCapture] sorted with is_cursor_screen=True first.
        On a single-monitor machine, the list has len==1.
    """
    set_dpi_awareness()
    cursor_x, cursor_y = get_cursor_position()
    monitors = list_monitors()
    cursor_monitor = monitor_containing(cursor_x, cursor_y, monitors)
    total = len(monitors)

    results: list[LabeledCapture] = []
    for i, mon in enumerate(monitors, start=1):
        raw_img = _capture_monitor(mon)
        target_w, target_h = pick_resolution(raw_img.width, raw_img.height)
        resized, scale_x, scale_y = resize_for_claude(raw_img, target_w, target_h)

        is_cursor = (mon == cursor_monitor)
        if is_cursor:
            focus = "cursor is on this screen (primary focus)"
        else:
            focus = "secondary screen"

        label = (
            f"screen {i} of {total} — {focus} "
            f"(image dimensions: {target_w}x{target_h} pixels)"
        )

        results.append(LabeledCapture(
            image=resized,
            label=label,
            is_cursor_screen=is_cursor,
            monitor=mon,
            target_width=target_w,
            target_height=target_h,
            scale_x=scale_x,
            scale_y=scale_y,
        ))

    results.sort(key=lambda c: (not c.is_cursor_screen,))
    return results


# --- Manual verification entry point ----------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("Clicky Windows -- capture.py manual verification")
    print("=" * 70)

    result = capture_active_screen()

    print("\nMonitor detected:")
    print(f"  position:      ({result.monitor['left']}, {result.monitor['top']})")
    print(f"  physical size: {result.monitor['width']} x {result.monitor['height']} px")

    print("\nCursor position (physical pixels, virtual desktop):")
    print(f"  ({result.cursor_physical[0]}, {result.cursor_physical[1]})")

    print("\nResolution picked for Claude (closest aspect-ratio match):")
    print(f"  {result.target_width} x {result.target_height}")

    print("\nScale factors (physical / target, for unscaling Claude coords):")
    print(f"  scale_x = {result.scale_x:.4f}")
    print(f"  scale_y = {result.scale_y:.4f}")

    print("\nResized image dimensions (what Claude will see):")
    print(f"  {result.image.size}")
    assert result.image.size == (result.target_width, result.target_height), \
        "resize did not produce exact target dimensions"

    demo_claude = (640, 400)
    demo_physical = unscale_claude_coords(
        claude_x=demo_claude[0], claude_y=demo_claude[1],
        scale_x=result.scale_x, scale_y=result.scale_y,
        monitor_left=result.monitor["left"], monitor_top=result.monitor["top"],
        target_w=result.target_width, target_h=result.target_height,
    )
    print("\nDemo unscale:")
    print(
        f"  if Claude says 'click at {demo_claude}' in "
        f"{result.target_width}x{result.target_height} space,"
    )
    print(f"  that maps to physical pixel {demo_physical} on your monitor.")

    # Draw a red crosshair on the debug image at the cursor's position.
    # Screenshot APIs don't capture the OS cursor layer, so without this
    # the image can't be used to visually verify cursor position accuracy.
    # Mapping: cursor_physical is in virtual-desktop physical pixels.
    # Convert to monitor-local physical, then divide by scale factors
    # to get the position in the resized 1280x800-ish image.
    cursor_local_x = result.cursor_physical[0] - result.monitor["left"]
    cursor_local_y = result.cursor_physical[1] - result.monitor["top"]
    marker_x = int(cursor_local_x / result.scale_x)
    marker_y = int(cursor_local_y / result.scale_y)

    # Clamp marker to image bounds (cursor may be momentarily off-screen
    # during fast mouse movement).
    marker_x = max(0, min(marker_x, result.target_width - 1))
    marker_y = max(0, min(marker_y, result.target_height - 1))

    debug_image = result.image.copy()
    draw = ImageDraw.Draw(debug_image)
    # Crosshair: two perpendicular lines, 20 px each way, 3 px wide, red.
    line_len = 20
    line_color = (255, 0, 0)
    line_width = 3
    draw.line(
        [(marker_x - line_len, marker_y), (marker_x + line_len, marker_y)],
        fill=line_color, width=line_width,
    )
    draw.line(
        [(marker_x, marker_y - line_len), (marker_x, marker_y + line_len)],
        fill=line_color, width=line_width,
    )
    # Ring around the center so it's visually distinct from content lines.
    ring_radius = 8
    draw.ellipse(
        [(marker_x - ring_radius, marker_y - ring_radius),
         (marker_x + ring_radius, marker_y + ring_radius)],
        outline=line_color, width=line_width,
    )

    print("\nCursor marker on debug image:")
    print(
        f"  cursor_physical ({result.cursor_physical[0]}, "
        f"{result.cursor_physical[1]}) - monitor ({result.monitor['left']}, "
        f"{result.monitor['top']}) = local ({cursor_local_x}, {cursor_local_y})"
    )
    print(
        f"  local ({cursor_local_x}, {cursor_local_y}) / scale "
        f"({result.scale_x:.4f}, {result.scale_y:.4f}) = "
        f"image ({marker_x}, {marker_y})"
    )

    debug_path = "debug_capture.jpg"
    debug_image.save(debug_path, "JPEG", quality=85)
    print(f"\nSaved: {debug_path}")
    print("  Open this file - the red crosshair shows where Python thinks")
    print("  the cursor was. It should match the actual cursor location")
    print("  visually (same button, same window, same pixel neighborhood).")

    print("\n" + "=" * 70)
    print("Manual verification checklist:")
    print("  1. debug_capture.jpg shows your current desktop (not blank/garbled)")
    print("  2. Red crosshair in the image matches where the mouse actually was")
    print("  3. Cursor coords printed are plausible (e.g. Start button = small")
    print("     x, large y; top-right X button = large x, small y)")
    print("  4. Resolution picked matches your aspect ratio")
    print("  5. Scale factors are reasonable for your monitor")
    print("=" * 70)
