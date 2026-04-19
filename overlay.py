"""Clicky Windows transparent click-through pointer overlay.

Per-monitor `OverlayWindow(QWidget)` overlays routed by `OverlayController`.
Each overlay covers exactly one physical monitor in DIP (logical) coords.
A blue animated pointer is drawn via `QPainter.paintEvent` and moved by
`QPropertyAnimation` on a `pyqtProperty`. Click-through is enforced by
Win32 extended window styles applied via ctypes AFTER `QWidget.show()`.

See docs/superpowers/plans/2026-04-11-overlay.md (or the source plan at
~/.claude/plans/streamed-tumbling-sunbeam.md) for the full design rationale,
research findings, and the "islands-of-screens" DPI gotcha that drove the
per-monitor architecture decision.

Responsibility boundary:
- THIS MODULE lives in Space A (physical pixels from capture.py) and
  Space B (Qt logical/DIP pixels). It owns the math that maps A -> B
  per-screen via devicePixelRatio().
- capture.py owns Space A -> Space C (Claude declared resolution).
- app.py owns threading and calls OverlayController methods from the
  main Qt thread only (PyQt6 is not thread-safe).

Top-to-bottom order (so `python -m overlay` works):
    1. Module docstring
    2. Imports
    3. Win32 constants (_GWL_EXSTYLE, _WS_EX_*, _SWP_*, _HWND_TOPMOST,
       _CLICKTHROUGH_FLAGS)
    4. apply_clickthrough_styles(hwnd) ctypes helper
    5. screen_for_monitor(monitor, screens) pure function
    6. physical_to_local_logical(x, y, screen) pure function
    7. OverlayWindow(QWidget) class
    8. OverlayController class
    9. __main__ block for manual click-through verification
"""
from __future__ import annotations

import ctypes
import math
from itertools import cycle

from enum import Enum, auto

from PyQt6.QtCore import (
    QPoint,
    QPointF,
    QTimer,
    QVariantAnimation,
    Qt,
)
from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen, QPolygonF, QScreen
from PyQt6.QtWidgets import QWidget


class _OverlayState(Enum):
    IDLE = auto()
    POINTING = auto()
    HIDDEN = auto()


# --- Path A Task 8: Quadratic bezier flight arc math -------------------------
#
# Ports farzaa/clicky leanring-buddy/OverlayWindow.swift:491-568 with ONE
# deliberate deviation: no tangent rotation. Our cursor is a tip-anchored
# polygon (commit a775c55 replaced ball with cursor polygon) — the tip IS
# the pointer, so we keep it pointing at the target throughout flight
# instead of rotating along the tangent like Clicky's isosceles triangle.

def _bezier_position(
    t: float,
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
) -> tuple[float, float]:
    """Quadratic Bezier: B(t) = (1-t)²·P0 + 2(1-t)t·P1 + t²·P2."""
    one_minus = 1.0 - t
    x = one_minus * one_minus * p0[0] + 2.0 * one_minus * t * p1[0] + t * t * p2[0]
    y = one_minus * one_minus * p0[1] + 2.0 * one_minus * t * p1[1] + t * t * p2[1]
    return (x, y)


def _smoothstep(t: float) -> float:
    """Hermite smoothstep: 3t² - 2t³. Eases in and out for natural motion."""
    return t * t * (3.0 - 2.0 * t)


def _flight_duration_s(distance_px: float) -> float:
    """Distance-scaled flight duration, clamped to [0.6s, 1.4s].
    Ports OverlayWindow.swift:509."""
    return max(0.6, min(distance_px / 800.0, 1.4))


def _scale_pulse(linear_t: float) -> float:
    """Sine scale pulse: 1.0 at endpoints → 1.3 at midpoint. Not eased —
    runs on LINEAR progress (not smoothstep'd) so the peak lands dead-center.
    Ports OverlayWindow.swift:567."""
    return 1.0 + math.sin(linear_t * math.pi) * 0.3


# --- Path A Task 9: Waveform widget (LISTENING state visual) -----------------
#
# Ports farzaa/clicky leanring-buddy/OverlayWindow.swift:705-743 verbatim.
# While PTT is held, the cursor polygon hides and this 5-bar waveform renders
# at the cursor position. Bar heights are driven by mic RMS (from stt.py
# Task 7) × a profile curve + an independent sine idle-pulse so bars are
# never fully flat. Rendered at ~36 fps via QTimer.

_WAVEFORM_BAR_COUNT = 5
_WAVEFORM_BAR_PROFILE: tuple[float, ...] = (0.4, 0.7, 1.0, 0.7, 0.4)
"""Per-bar amplitude multiplier. Center bar (idx 2) scales by 1.0 = taller.
Edges (idx 0, 4) scale by 0.4 = shorter. Matches Clicky's visual rhythm."""
_WAVEFORM_BASE_HEIGHT = 3.0  # minimum px — bars are never fully flat
_WAVEFORM_MAX_REACTIVE = 10.0  # max extra px from audio-driven component
_WAVEFORM_IDLE_PULSE_AMP = 1.5  # max extra px from independent sine pulse


def _waveform_bar_height(bar_index: int, audio_level: float, phase_seconds: float) -> float:
    """Compute a single bar's height in px.

    Formula (port of OverlayWindow.swift:728-740):
        normalized = max(audio_level - 0.008, 0)
        eased = (min(normalized * 2.85, 1))^0.76
        reactive = eased * 10 * profile[bar_index]
        idle_pulse = (sin(phase * 3.6 + bar_index * 0.35) + 1) / 2 * 1.5
        height = 3 + reactive + idle_pulse

    - The 0.008 dead zone prevents flickering on near-silent chunks.
    - The 2.85× boost + 0.76 power curve make quiet speech visually punchy
      without saturating on loud speech.
    - The per-bar phase offset (0.35 rad) gives a subtle wave pattern even
      at silence.

    Args:
        bar_index: 0..4. Must be within _WAVEFORM_BAR_PROFILE bounds.
        audio_level: RMS-derived level in [0, 1] (from stt.py's on_audio_level).
        phase_seconds: elapsed time since widget startup (drives the idle pulse).

    Returns:
        Bar height in pixels, in range ~[3, 14.5].
    """
    normalized_level = max(audio_level - 0.008, 0.0)
    eased = pow(min(normalized_level * 2.85, 1.0), 0.76)
    reactive = eased * _WAVEFORM_MAX_REACTIVE * _WAVEFORM_BAR_PROFILE[bar_index]
    animation_phase = phase_seconds * 3.6 + bar_index * 0.35
    idle_pulse = (math.sin(animation_phase) + 1.0) / 2.0 * _WAVEFORM_IDLE_PULSE_AMP
    return _WAVEFORM_BASE_HEIGHT + reactive + idle_pulse

# --- Cursor polygon shape ----------------------------------------------------

_CURSOR_VERTICES = [
    (0, 0),       # tip (anchor point — lands on the target coordinate)
    (0, 24),      # left edge down
    (5, 19),      # notch inward
    (10, 28),     # lower-right barb tip
    (13, 26),     # barb right edge
    (8, 17),      # barb back up to body
    (16, 17),     # body right edge (widest point)
]
"""Blue cursor shape. Tip at (0,0) anchors on the target coordinate.
Translucent dodger-blue fill with thin dark outline.
"""

_CURSOR_FOLLOW_LERP = 0.15
"""Spring interpolation factor for cursor following. Each frame, cursor moves
15% of the remaining distance toward the target. Lower = smoother/laggier.
0.15 gives a natural 'buddy following you' feel — like a puppy trotting after you.
"""


# --- Win32 constants ---------------------------------------------------------

_GWL_EXSTYLE = -20
"""SetWindowLongW index for the extended window style field."""

_WS_EX_LAYERED = 0x00080000
"""Required for WS_EX_TRANSPARENT to function on top-level windows."""
_WS_EX_TRANSPARENT = 0x00000020
"""The actual click-through flag (only works on layered windows)."""
_WS_EX_TOPMOST = 0x00000008
"""Always-on-top. Redundant with Qt.WindowStaysOnTopHint but harmless."""
_WS_EX_NOACTIVATE = 0x08000000
"""Prevents focus theft when the overlay receives any event."""
_WS_EX_TOOLWINDOW = 0x00000080
"""Hides the window from the taskbar and Alt-Tab list."""

_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
"""Forces WM_NCCALCSIZE so style changes take effect immediately."""
_HWND_TOPMOST = -1

_CLICKTHROUGH_FLAGS = (
    _WS_EX_LAYERED
    | _WS_EX_TRANSPARENT
    | _WS_EX_TOPMOST
    | _WS_EX_NOACTIVATE
    | _WS_EX_TOOLWINDOW
)
"""OR of all ex-styles to apply to overlay windows after show().

Bit pattern should be 0x080800A8. The test in test_overlay.py guards
against silent drift in the individual constants.
"""


# --- Win32 click-through helper ----------------------------------------------

def apply_clickthrough_styles(hwnd: int) -> None:
    """Apply Win32 extended window styles for click-through + no-taskbar
    + no-focus-theft on an existing top-level window.

    MUST be called AFTER QWidget.show() so the HWND exists. Reads the
    current GWL_EXSTYLE via GetWindowLongW, ORs in _CLICKTHROUGH_FLAGS
    (NEVER overwrites -- that would wipe Qt's own flags), then calls
    SetWindowLongW and forces the style change to take effect via
    SetWindowPos with SWP_FRAMECHANGED.

    This is the core of the click-through mechanism on Windows 11.
    Without SWP_FRAMECHANGED the new styles don't take effect until the
    window is resized or moved.

    Raises:
        RuntimeError: if SetWindowLongW returns 0, indicating the Win32
            call failed. Error details from ctypes.WinError() are included.
            This catches silent click-through breakage that would otherwise
            leave the user with no diagnostic signal.

    Args:
        hwnd: native window handle from int(QWidget.winId()).
    """
    user32 = ctypes.windll.user32
    current = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    new_style = current | _CLICKTHROUGH_FLAGS
    result = user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, new_style)
    # SetWindowLongW returns the previous value on success, 0 on failure.
    # Previous value could legitimately be 0 if no ex-styles were set yet,
    # so we also check GetLastError. In practice current != 0 (Qt sets
    # some ex-styles) so a 0 return is always a failure.
    if result == 0 and current != 0:
        raise RuntimeError(
            f"SetWindowLongW failed for HWND {hwnd}: {ctypes.WinError()}"
        )
    user32.SetWindowPos(
        hwnd,
        _HWND_TOPMOST,
        0, 0, 0, 0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    )


# --- Pure coordinate math ----------------------------------------------------

def screen_for_monitor(monitor: dict, screens: list[QScreen]) -> QScreen:
    """Find the QScreen whose physical geometry matches a capture.py monitor dict.

    capture.py produces CaptureResult.monitor = {"left": phys_x, "top": phys_y,
    "width": phys_w, "height": phys_h} where all fields are in virtual-desktop
    physical pixel coordinates (from mss). QScreen.geometry() returns coords
    in Qt's DIP (logical) space. We compare by converting each QScreen's DIP
    dimensions to physical via its per-screen devicePixelRatio().

    Args:
        monitor: mss-style dict with 'left', 'top', 'width', 'height' keys
            (all values in physical pixels).
        screens: list of QScreen-compatible objects, each with geometry() ->
            QRect-like (DIP coords) and devicePixelRatio() -> float. In tests
            this is a list of _MockScreen duck-types, not real QScreens.

    Returns:
        The QScreen whose physical bounds match the monitor dict. Falls back
        to screens[0] (primary) if no match is found -- this can happen if
        mss state is stale or the monitor config changed mid-session.
    """
    target_w = monitor["width"]
    target_h = monitor["height"]
    target_left = monitor["left"]
    target_top = monitor["top"]
    for screen in screens:
        ratio = screen.devicePixelRatio()
        geom = screen.geometry()
        phys_w = int(geom.width() * ratio)
        phys_h = int(geom.height() * ratio)
        phys_left = int(geom.left() * ratio)
        phys_top = int(geom.top() * ratio)
        if (phys_w == target_w
                and phys_h == target_h
                and phys_left == target_left
                and phys_top == target_top):
            return screen
    return screens[0]


def physical_to_local_logical(
    physical_x: int,
    physical_y: int,
    screen: QScreen,
) -> tuple[int, int]:
    """Map a physical-pixel point (Space A) to within-screen logical DIP
    coords (Space B) inside the target QScreen's local coordinate system.

    Returns (local_x, local_y) where (0, 0) is the screen's top-left in
    the overlay widget's coordinate system. The per-monitor architecture
    means we never need global virtual-desktop coordinates -- each overlay
    lives in its own screen's local space.

    Critical: uses the PER-SCREEN devicePixelRatio(). Do NOT cache a
    global ratio. Mixed-DPI setups (e.g., laptop at 200% + external
    monitor at 100%) have different ratios per screen, and using the
    wrong one would land the pointer in the wrong place on one of them.

    Args:
        physical_x: virtual-desktop physical pixel x (from capture.py).
        physical_y: virtual-desktop physical pixel y.
        screen: QScreen-compatible object with geometry() returning a
            QRect-like (DIP coords) and devicePixelRatio() returning a float.

    Returns:
        (local_log_x, local_log_y) integer tuple in the screen's local
        logical coordinate space, ready to pass to QWidget.move or the
        pointer animation target.
    """
    ratio = screen.devicePixelRatio()
    geom = screen.geometry()
    screen_phys_left = int(geom.left() * ratio)
    screen_phys_top = int(geom.top() * ratio)
    local_phys_x = physical_x - screen_phys_left
    local_phys_y = physical_y - screen_phys_top
    local_log_x = int(local_phys_x / ratio)
    local_log_y = int(local_phys_y / ratio)
    return local_log_x, local_log_y


# --- Overlay window ----------------------------------------------------------

class OverlayWindow(QWidget):
    """One transparent click-through overlay for a single QScreen.

    Responsibilities:
    - Cover exactly one physical monitor with a frameless transparent window
    - Paint a blue animated pointer via QPainter in paintEvent
    - Expose a pointerPos pyqtProperty so QPropertyAnimation can drive it
    - Apply Win32 click-through ex-styles via ctypes after show()

    The per-monitor architecture (see DECISIONS.md 2026-04-11 "Per-monitor
    overlays instead of virtual-desktop-spanning") means each OverlayWindow
    operates entirely in its own screen's local DIP coordinate space. No
    global virtual-desktop coordinates are ever used here -- that's the
    whole point of the architectural reversal from CLAUDE.md's original
    "spans full virtual desktop" wording.

    Thread safety: PyQt6 is NOT thread-safe. All methods must be called
    from the main Qt thread only. app.py enforces this via pyqtSignal
    cross-thread communication.
    """

    def __init__(self, screen: QScreen) -> None:
        """Construct the overlay window for a given QScreen.

        Args:
            screen: QScreen for this overlay to cover. Production uses real
                QScreens from QGuiApplication.screens(); tests never call
                this constructor directly (they use _MockOverlayWindow via
                OverlayController dependency injection).
        """
        super().__init__()

        # Qt window flags: frameless, always-on-top, Tool (no taskbar entry)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        # Attribute-based transparency -- NOT stylesheet. Stylesheet
        # transparency is the #1 flicker source on Win 11 per forum.qt.io.
        # Also: do NOT setWindowOpacity(<1.0), that forces Qt's own layered
        # path and overrides the Win32 ex-styles we apply later.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Cover this screen exactly. QScreen.geometry() returns DIP coords,
        # which is what setGeometry expects -- no conversion needed.
        self.setGeometry(screen.geometry())
        self.screen_name = screen.name()  # used by OverlayController._overlay_for_screen

        # Pointer state
        self._pointer_pos = QPoint(0, 0)
        self._pointer_visible = False

        # Visual state flags — gates cursor polygon paint + widget positions.
        # Only one of these can be true at a time (Farza's verbatim state
        # machine — cursor polygon never coexists with waveform or spinner).
        self._waveform_visible = False
        self._spinner_visible = False
        self._waveform_widget = None  # lazy-created on first show_waveform()
        self._spinner_widget = None   # lazy-created on first show_spinner()

        # Bezier flight animation (Path A Task 8). Replaces the old linear
        # QPropertyAnimation with Farza's quadratic-bezier-arc + smoothstep +
        # scale-pulse. Port of OverlayWindow.swift:491-568 (no tangent rotation
        # — our cursor is tip-anchored; the tip stays on target through flight).
        self._flight_anim = QVariantAnimation(self)
        self._flight_anim.setStartValue(0.0)
        self._flight_anim.setEndValue(1.0)
        self._flight_anim.valueChanged.connect(self._on_flight_value)
        self._flight_p0: tuple[float, float] = (0.0, 0.0)
        self._flight_p1: tuple[float, float] = (0.0, 0.0)
        self._flight_p2: tuple[float, float] = (0.0, 0.0)
        self._flight_scale: float = 1.0

    def paintEvent(self, _event) -> None:
        """Draw a blue arrow cursor polygon at the current pointer position.

        The tip vertex (0,0 in _CURSOR_VERTICES) is anchored at pointer_pos
        so point_at(x,y) puts the tip exactly on the target UI element.

        During FLYING state, self._flight_scale rises to 1.3 at mid-flight and
        returns to 1.0 on landing. We scale around the tip so the tip keeps
        tracking the Bezier curve position exactly (scale around any other
        point would drift the tip).
        """
        if not self._pointer_visible:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        px, py = self._pointer_pos.x(), self._pointer_pos.y()

        # Glow: semi-transparent blue circle behind the cursor
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(30, 144, 255, 35))
        painter.drawEllipse(QPointF(px + 5, py + 10), 22, 22)

        # Apply mid-flight scale pulse around the tip (0, 0 in cursor space).
        # painter.translate + scale + translate is standard Qt pattern for
        # scaling around a specific point.
        if self._flight_scale != 1.0:
            painter.save()
            painter.translate(float(px), float(py))
            painter.scale(self._flight_scale, self._flight_scale)
            painter.translate(-float(px), -float(py))

        # Cursor polygon
        painter.setBrush(QColor(30, 144, 255, 200))  # dodger blue, more opaque
        painter.setPen(QPen(QColor(40, 40, 40, 100), 1))
        poly = QPolygonF([
            QPointF(px + dx, py + dy) for dx, dy in _CURSOR_VERTICES
        ])
        painter.drawPolygon(poly)

        if self._flight_scale != 1.0:
            painter.restore()

    def animate_pointer_to(self, local_logical_x: int, local_logical_y: int) -> None:
        """Fly the pointer along a quadratic Bezier arc to (x, y).

        Ports farzaa/clicky OverlayWindow.swift:491-568 verbatim with ONE
        deliberate deviation: no tangent rotation. Our cursor is a tip-anchored
        polygon — the tip IS the pointer, so it keeps pointing at the target
        through flight instead of rotating along the tangent.

        Curve: P0=current pointer, P1=midpoint lifted up by min(dist*0.2, 80px),
        P2=target. Duration = clamp(distance/800 s, 0.6s, 1.4s). Smoothstep
        eases progress before bezier interpolation. Scale pulse 1.0→1.3→1.0
        applied on LINEAR progress (not eased) so the peak lands mid-arc.

        Args:
            local_logical_x: within-screen logical DIP x (from physical_to_local_logical)
            local_logical_y: within-screen logical DIP y
        """
        start_x = float(self._pointer_pos.x())
        start_y = float(self._pointer_pos.y())
        end_x = float(local_logical_x)
        end_y = float(local_logical_y)
        dx, dy = end_x - start_x, end_y - start_y
        distance = math.hypot(dx, dy)

        mid_x = (start_x + end_x) / 2.0
        mid_y = (start_y + end_y) / 2.0
        arc_height = min(distance * 0.2, 80.0)

        self._flight_p0 = (start_x, start_y)
        self._flight_p1 = (mid_x, mid_y - arc_height)
        self._flight_p2 = (end_x, end_y)

        duration_ms = int(_flight_duration_s(distance) * 1000.0)

        self._flight_anim.stop()
        self._flight_anim.setDuration(duration_ms)
        self._flight_anim.setStartValue(0.0)
        self._flight_anim.setEndValue(1.0)
        self._pointer_visible = True
        self._flight_anim.start()

    def _on_flight_value(self, linear_t) -> None:
        """QVariantAnimation.valueChanged callback: interpolate bezier + pulse.

        linear_t is Qt's raw interpolated value 0.0..1.0. We apply smoothstep
        BEFORE the bezier sample (eased position) but use LINEAR t for the
        scale pulse (peak lands at true midpoint, not eased midpoint).
        """
        t = float(linear_t)
        eased_t = _smoothstep(t)
        x, y = _bezier_position(eased_t, self._flight_p0, self._flight_p1, self._flight_p2)
        self._pointer_pos = QPoint(int(x), int(y))
        self._flight_scale = _scale_pulse(t)
        # On completion, snap to P2 and reset scale (defensive — Qt sometimes
        # emits valueChanged(1.0) slightly early and we want exact landing).
        if t >= 0.9999:
            self._pointer_pos = QPoint(int(self._flight_p2[0]), int(self._flight_p2[1]))
            self._flight_scale = 1.0
        self.update()

    def apply_win32_clickthrough(self) -> None:
        """Apply Win32 ex-styles for click-through. MUST be called after show()."""
        hwnd = int(self.winId())
        apply_clickthrough_styles(hwnd)

    # --- Waveform + Spinner widgets (LISTENING / THINKING states) --------
    #
    # Both widgets position themselves at self._pointer_pos every follow-tick
    # (matches Clicky's .position(cursorPosition) binding — verified from
    # farzaa/clicky OverlayWindow.swift:326-329 + 411-438, the widgets follow
    # the OS cursor at 60Hz, they DO NOT stay pinned at press-time position).
    #
    # show_waveform / show_spinner just create the widget + flip a visibility
    # flag. The 60Hz _on_follow_tick drives their positions.

    def show_waveform(self) -> None:
        """Enter LISTENING state: show waveform widget, hide cursor polygon.
        Widget position is driven by _on_follow_tick (tracks mouse at 60Hz).
        """
        if getattr(self, "_waveform_widget", None) is None:
            self._waveform_widget = WaveformWidget(self)
        self._waveform_widget.show()
        self._waveform_visible = True
        self._pointer_visible = False  # cursor polygon hides during LISTENING
        self.update()

    def hide_waveform(self) -> None:
        """Exit LISTENING state. Does NOT restore cursor visibility — caller
        is expected to transition into THINKING (show_spinner) or IDLE (tick
        will re-show the cursor when no waveform/spinner is active)."""
        if getattr(self, "_waveform_widget", None) is not None:
            self._waveform_widget.hide()
        self._waveform_visible = False
        self.update()

    def show_spinner(self) -> None:
        """Enter THINKING state: show rotating arc, keep cursor hidden.
        Position tracks cursor via _on_follow_tick, same as waveform."""
        if getattr(self, "_spinner_widget", None) is None:
            self._spinner_widget = SpinnerWidget(self)
        self._spinner_widget.show()
        self._spinner_visible = True
        self._pointer_visible = False
        self.update()

    def hide_spinner(self) -> None:
        """Exit THINKING state. Cursor will reappear via _on_follow_tick when
        no widget is active, OR via point_at() setting _pointer_visible=True
        right before the bezier arc starts."""
        if getattr(self, "_spinner_widget", None) is not None:
            self._spinner_widget.hide()
        self._spinner_visible = False
        self.update()

    def set_audio_level(self, level: float) -> None:
        """Forward audio level to the waveform widget (no-op if not shown yet)."""
        if getattr(self, "_waveform_widget", None) is not None:
            self._waveform_widget.set_audio_level(level)


# --- Spinner widget (THINKING state) -----------------------------------------
#
# Ports farzaa/clicky leanring-buddy/OverlayWindow.swift:749-773 (the
# BlueCursorSpinnerView). Shown between hotkey RELEASE and Claude returning
# a coordinate. Same visual vocabulary as Clicky's macOS shipping buddy.

_SPINNER_PERIOD_S = 0.8
"""Full-rotation period. Matches Clicky's linear(duration: 0.8) forever-repeat."""

_SPINNER_ARC_START_DEG = 54.0   # 0.15 * 360
_SPINNER_ARC_SPAN_DEG = 252.0   # (0.85 - 0.15) * 360  → 70% of full circle

_SPINNER_WIDGET_SIZE = 28  # px — leaves room around the 14px arc for stroke + glow
_SPINNER_ARC_DIAMETER = 14.0
_SPINNER_STROKE_WIDTH = 2.5


def _spinner_angle_deg(elapsed_s: float) -> float:
    """Linear-rotation angle in degrees, wrapping at full period (0.8s).

    Returns angle in [0, 360). Pure function (no Qt), easy to unit test.
    """
    return (elapsed_s / _SPINNER_PERIOD_S * 360.0) % 360.0


class SpinnerWidget(QWidget):
    """14×14 rotating arc, shown during THINKING state (release → coord).

    Port of Clicky's BlueCursorSpinnerView. The arc covers 70% of a circle
    (trimmed 15% at top + 15% at bottom to match Farza's ``.trim(from: 0.15,
    to: 0.85)``). Rotates continuously, 0.8s per full rotation.

    Rendered via QPainter on a transparent, mouse-transparent QWidget. The
    widget size is larger than the arc so the stroke + anti-aliasing don't
    clip at the edges.

    Thread safety: show()/hide() are Qt-main-thread only (called via
    pyqtSignal slots in app.py). The timer ticks on the main thread too.
    """

    WIDGET_SIZE = _SPINNER_WIDGET_SIZE
    UPDATE_INTERVAL_MS = 28  # ~36 fps — same cadence as waveform for consistency

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.WIDGET_SIZE, self.WIDGET_SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        import time as _t
        self._phase_start = _t.time()

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(self.UPDATE_INTERVAL_MS)

    def _tick(self) -> None:
        self.update()

    def paintEvent(self, _event) -> None:
        """Draw a rotating 70% arc in dodger blue + subtle outer glow."""
        import time as _t
        from PyQt6.QtCore import QRectF as _QRectF

        elapsed = _t.time() - self._phase_start
        angle = _spinner_angle_deg(elapsed)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Center the arc in the widget + rotate by `angle` around the center.
        cx = cy = self.WIDGET_SIZE / 2.0
        painter.translate(cx, cy)
        painter.rotate(angle)
        painter.translate(-cx, -cy)

        # Outer glow — a faint circle slightly larger than the arc.
        glow_pen = QPen(QColor(30, 144, 255, 90))
        glow_pen.setWidthF(_SPINNER_STROKE_WIDTH + 2.0)
        glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(glow_pen)
        arc_rect = _QRectF(
            (self.WIDGET_SIZE - _SPINNER_ARC_DIAMETER) / 2.0,
            (self.WIDGET_SIZE - _SPINNER_ARC_DIAMETER) / 2.0,
            _SPINNER_ARC_DIAMETER,
            _SPINNER_ARC_DIAMETER,
        )
        # QPainter.drawArc uses 1/16-degree units.
        painter.drawArc(
            arc_rect,
            int(_SPINNER_ARC_START_DEG * 16),
            int(_SPINNER_ARC_SPAN_DEG * 16),
        )

        # Main arc — fully opaque dodger blue.
        main_pen = QPen(QColor(30, 144, 255, 220))
        main_pen.setWidthF(_SPINNER_STROKE_WIDTH)
        main_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(main_pen)
        painter.drawArc(
            arc_rect,
            int(_SPINNER_ARC_START_DEG * 16),
            int(_SPINNER_ARC_SPAN_DEG * 16),
        )


# --- Waveform widget -----------------------------------------------------------

class WaveformWidget(QWidget):
    """5-bar audio-level waveform rendered via QPainter at ~36 fps.

    Ports farzaa/clicky leanring-buddy/OverlayWindow.swift:705-743. During
    PTT hold, this widget shows at the cursor position (OverlayWindow hides
    the cursor polygon). Bar heights come from _waveform_bar_height() using
    the RMS level set via set_audio_level() + an independent idle-pulse sine
    so bars are never fully flat.

    Thread safety: set_audio_level may be called from any thread (it just
    assigns a float). The timer-driven update() runs on the Qt main thread.
    Rendering runs on the main thread via paintEvent.
    """

    BAR_WIDTH = 2
    BAR_SPACING = 2
    WIDGET_HEIGHT = 18  # px — slightly taller than cursor for visibility
    WIDGET_WIDTH = _WAVEFORM_BAR_COUNT * BAR_WIDTH + (_WAVEFORM_BAR_COUNT - 1) * BAR_SPACING  # = 18
    UPDATE_INTERVAL_MS = 28  # ~36 fps, matches Farza's 1/36s cadence

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.WIDGET_WIDTH, self.WIDGET_HEIGHT)
        # Transparent bg + mouse-transparent (clicks pass through to apps below).
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self._audio_level: float = 0.0
        import time as _t
        self._phase_start = _t.time()

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(self.UPDATE_INTERVAL_MS)

    def set_audio_level(self, level: float) -> None:
        """Update live audio level (called from app.py via pyqtSignal)."""
        self._audio_level = max(0.0, min(float(level), 1.0))

    def _tick(self) -> None:
        """Trigger a repaint on each timer tick — bars redraw at ~36 fps."""
        self.update()

    def paintEvent(self, _event) -> None:
        """Draw the 5 vertical bars centered vertically in the widget."""
        from PyQt6.QtCore import QRectF as _QRectF  # local import: type only used here
        import time as _t

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        # Same dodger blue as cursor polygon
        painter.setBrush(QColor(30, 144, 255, 220))

        phase = _t.time() - self._phase_start
        for i in range(_WAVEFORM_BAR_COUNT):
            bar_h = _waveform_bar_height(i, self._audio_level, phase)
            x = i * (self.BAR_WIDTH + self.BAR_SPACING)
            y = (self.WIDGET_HEIGHT - bar_h) / 2.0
            painter.drawRoundedRect(
                _QRectF(float(x), float(y), float(self.BAR_WIDTH), float(bar_h)),
                1.5, 1.5,
            )


# --- Controller --------------------------------------------------------------

class OverlayController:
    """Manages one OverlayWindow per physical monitor + cursor following.

    State machine:
    - IDLE: 16ms timer polls QCursor.pos(), cursor follows mouse with offset
    - POINTING: timer stopped, animation drives cursor to Claude's target,
      3s dwell, then fly back to mouse, resume IDLE
    - HIDDEN: timer stopped, overlays hidden for screen capture

    Phase 1 = always-visible mode only. Cursor visible from launch.

    Dependency injection: overlay_factory, screens, and cursor_pos_fn are
    injectable so tests can substitute mocks without real QWidgets.
    """

    _FOLLOW_OFFSET_X = 35
    _FOLLOW_OFFSET_Y = 25
    _DWELL_MS = 3000
    _FOLLOW_INTERVAL_MS = 16

    def __init__(
        self,
        overlay_factory=None,
        screens: list[QScreen] | None = None,
        cursor_pos_fn=None,
    ) -> None:
        if overlay_factory is None:
            overlay_factory = OverlayWindow
        if screens is None:
            screens = QGuiApplication.screens()
        self._cursor_pos_fn = cursor_pos_fn or QCursor.pos

        self.overlays: list[OverlayWindow] = []
        for qscreen in screens:
            overlay = overlay_factory(qscreen)
            overlay.show()
            overlay.apply_win32_clickthrough()
            self.overlays.append(overlay)

        self._state = _OverlayState.IDLE
        self._pointing_overlay: OverlayWindow | None = None

        self._follow_timer = QTimer()
        self._follow_timer.setInterval(self._FOLLOW_INTERVAL_MS)
        self._follow_timer.timeout.connect(self._on_follow_tick)
        self._follow_timer.start()

    def _on_follow_tick(self) -> None:
        """Poll cursor position and lerp the buddy cursor toward it.

        Instead of snapping directly to the mouse position (which looks like
        teleporting), each frame moves 15% of the remaining distance. This
        creates a smooth 'buddy following you' feel — the cursor lazily
        drifts toward your mouse like a puppy trotting after you.
        """
        if self._state != _OverlayState.IDLE:
            return
        global_pos = self._cursor_pos_fn()
        screen = QGuiApplication.screenAt(global_pos)
        if screen is None:
            return
        target_overlay = self._overlay_for_screen(screen)
        if target_overlay is None:
            return
        for ov in self.overlays:
            if ov is not target_overlay:
                ov._pointer_visible = False
                ov.update()

        local = target_overlay.mapFromGlobal(global_pos)
        target_x = local.x() + self._FOLLOW_OFFSET_X
        target_y = local.y() + self._FOLLOW_OFFSET_Y

        current_x = target_overlay._pointer_pos.x()
        current_y = target_overlay._pointer_pos.y()

        dx = target_x - current_x
        dy = target_y - current_y
        dist_sq = dx * dx + dy * dy

        if dist_sq < 4:
            new_x, new_y = target_x, target_y
        else:
            step_x = int(dx * _CURSOR_FOLLOW_LERP)
            step_y = int(dy * _CURSOR_FOLLOW_LERP)
            if dx != 0 and step_x == 0:
                step_x = 1 if dx > 0 else -1
            if dy != 0 and step_y == 0:
                step_y = 1 if dy > 0 else -1
            new_x = current_x + step_x
            new_y = current_y + step_y

        target_overlay._pointer_pos = QPoint(new_x, new_y)

        # Visibility gating: waveform (LISTENING) and spinner (THINKING) hide
        # the cursor polygon; otherwise cursor polygon is visible. Widgets are
        # repositioned to the new cursor position so they track the OS mouse
        # at 60Hz (matches Clicky's .position(cursorPosition) binding per
        # OverlayWindow.swift:326-329 + 411-438).
        wf_widget = getattr(target_overlay, "_waveform_widget", None)
        sp_widget = getattr(target_overlay, "_spinner_widget", None)

        if getattr(target_overlay, "_waveform_visible", False) and wf_widget is not None:
            wf_widget.move(
                int(new_x - wf_widget.width() // 2),
                int(new_y - wf_widget.height() // 2),
            )
            target_overlay._pointer_visible = False
        elif getattr(target_overlay, "_spinner_visible", False) and sp_widget is not None:
            sp_widget.move(
                int(new_x - sp_widget.width() // 2),
                int(new_y - sp_widget.height() // 2),
            )
            target_overlay._pointer_visible = False
        else:
            target_overlay._pointer_visible = True

        target_overlay.update()

    def point_at(
        self,
        physical_x: int,
        physical_y: int,
        monitor: dict,
    ) -> None:
        """Fly the cursor from current position to Claude's target coordinate."""
        self._follow_timer.stop()
        self._state = _OverlayState.POINTING

        screens = QGuiApplication.screens()
        target_screen = screen_for_monitor(monitor, screens)
        target_overlay = self._overlay_for_screen(target_screen)
        if target_overlay is None:
            if not self.overlays:
                return
            target_overlay = self.overlays[0]

        self._pointing_overlay = target_overlay
        local_x, local_y = physical_to_local_logical(
            physical_x, physical_y, target_screen
        )
        target_overlay._pointer_visible = True
        target_overlay.animate_pointer_to(local_x, local_y)
        target_overlay._flight_anim.finished.connect(self._on_point_animation_finished)

    def _on_point_animation_finished(self) -> None:
        """After arriving at target, dwell 3s then fly back to mouse."""
        if self._pointing_overlay:
            self._pointing_overlay._flight_anim.finished.disconnect(
                self._on_point_animation_finished
            )
        QTimer.singleShot(self._DWELL_MS, self._fly_back)

    def _fly_back(self) -> None:
        """Animate the cursor back to the current mouse position."""
        if self._state == _OverlayState.HIDDEN:
            return
        if self._pointing_overlay is None:
            self._resume_idle()
            return
        global_pos = self._cursor_pos_fn()
        local = self._pointing_overlay.mapFromGlobal(global_pos)
        target = QPoint(
            local.x() + self._FOLLOW_OFFSET_X,
            local.y() + self._FOLLOW_OFFSET_Y,
        )
        self._pointing_overlay._flight_anim.finished.connect(self._on_return_finished)
        self._pointing_overlay.animate_pointer_to(target.x(), target.y())

    def _on_return_finished(self) -> None:
        """Return flight complete — resume mouse following."""
        if self._pointing_overlay:
            self._pointing_overlay._flight_anim.finished.disconnect(
                self._on_return_finished
            )
        self._pointing_overlay = None
        self._resume_idle()

    def _resume_idle(self) -> None:
        if self._state == _OverlayState.HIDDEN:
            return
        self._state = _OverlayState.IDLE
        self._follow_timer.start()

    def _overlay_for_screen(self, screen: QScreen) -> OverlayWindow | None:
        target_name = screen.name()
        for overlay in self.overlays:
            if overlay.screen_name == target_name:
                return overlay
        return None

    def hide_for_capture(self) -> None:
        """Hide ALL overlays + stop timer for screen capture."""
        self._follow_timer.stop()
        if self._pointing_overlay and self._pointing_overlay._flight_anim.state() == QVariantAnimation.State.Running:
            self._pointing_overlay._flight_anim.stop()
            try:
                self._pointing_overlay._flight_anim.finished.disconnect()
            except TypeError:
                pass
        self._state = _OverlayState.HIDDEN
        for overlay in self.overlays:
            overlay._pointer_visible = False
            overlay.hide()

    def show_after_capture(self) -> None:
        """Re-show ALL overlays + restart cursor following."""
        for overlay in self.overlays:
            overlay.show()
            overlay.apply_win32_clickthrough()
        self._pointing_overlay = None
        self._state = _OverlayState.IDLE
        self._follow_timer.start()

    # --- Waveform + Spinner delegation (called by app.py state machine) ----
    #
    # Position is driven by _on_follow_tick, NOT by show_waveform/show_spinner
    # args. The monitor arg is retained for multi-monitor routing: the widget
    # is created on the OverlayWindow whose screen the cursor is on AT PRESS/
    # RELEASE time. If the cursor crosses monitors mid-hold, the widget stays
    # on its original monitor (known limitation, deferred as future work —
    # see ROADMAP.md "Future visual + TTS refinements").

    def show_waveform(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        """Show waveform on the overlay containing (physical_x, physical_y).
        Called by app.py on hotkey PRESS."""
        target_overlay = self._pick_overlay_for_point(physical_x, physical_y, monitor)
        if target_overlay is not None and hasattr(target_overlay, "show_waveform"):
            target_overlay.show_waveform()

    def hide_waveform(self) -> None:
        """Hide waveform on all overlays. app.py calls this on hotkey RELEASE."""
        for overlay in self.overlays:
            if hasattr(overlay, "hide_waveform"):
                overlay.hide_waveform()

    def show_spinner(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        """Show spinner (THINKING state) on the overlay containing the cursor.
        Called by app.py on hotkey RELEASE, immediately after hide_waveform."""
        target_overlay = self._pick_overlay_for_point(physical_x, physical_y, monitor)
        if target_overlay is not None and hasattr(target_overlay, "show_spinner"):
            target_overlay.show_spinner()

    def hide_spinner(self) -> None:
        """Hide spinner on all overlays. Called by app.py when:
        - Claude returns a coordinate (just before sig_point_at → bezier fires)
        - Text-only response path (no coordinate)
        - Pipeline error / cancel paths (don't leave spinner spinning)
        - Top of _handle_press (clear stale from prior interaction)"""
        for overlay in self.overlays:
            if hasattr(overlay, "hide_spinner"):
                overlay.hide_spinner()

    def set_audio_level(self, level: float) -> None:
        """Forward audio level to ALL overlays (only the one with a showing
        waveform widget renders — others are no-ops)."""
        for overlay in self.overlays:
            if hasattr(overlay, "set_audio_level"):
                overlay.set_audio_level(level)

    def _pick_overlay_for_point(
        self, physical_x: int, physical_y: int, monitor: dict,
    ):
        """Route a physical-pixel point to the right OverlayWindow.
        Returns None if no overlay exists (empty screens list)."""
        screens = QGuiApplication.screens()
        target_screen = screen_for_monitor(monitor, screens)
        target = self._overlay_for_screen(target_screen)
        if target is None and self.overlays:
            target = self.overlays[0]
        return target


# --- Manual verification entry point ----------------------------------------

if __name__ == "__main__":
    # Manual click-through verification. Run: py -3.13 -m overlay
    #
    # Opens one overlay per physical monitor and animates a blue pointer
    # through 5 positions (4 corners + center) of the primary overlay,
    # cycling every 1.5 seconds. User confirms the 5-point checklist below
    # by watching the overlay and trying to click on apps underneath.
    import sys

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from capture import set_dpi_awareness

    set_dpi_awareness()  # Idempotent if already set by PyQt6

    print("=" * 70)
    print("Clicky Windows -- overlay.py manual click-through verification")
    print("=" * 70)

    app = QApplication(sys.argv)
    controller = OverlayController()

    print(f"\nCreated {len(controller.overlays)} overlay(s):")
    for i, overlay in enumerate(controller.overlays):
        geom = overlay.geometry()
        print(
            f"  [{i}] screen={overlay.screen_name} "
            f"geometry=({geom.x()}, {geom.y()}, {geom.width()}, {geom.height()}) DIP"
        )

    # Build a 5-point test pattern for the primary overlay:
    # top-left, top-right, bottom-right, bottom-left, center
    primary = controller.overlays[0]
    primary_geom = primary.geometry()
    primary_w = primary_geom.width()
    primary_h = primary_geom.height()
    margin = 100  # DIP
    test_positions = [
        (margin, margin),
        (primary_w - margin, margin),
        (primary_w - margin, primary_h - margin),
        (margin, primary_h - margin),
        (primary_w // 2, primary_h // 2),
    ]

    # itertools.cycle gives us an infinite iterator over the positions
    # without any mutable external state. Cleaner than a [0] counter.
    _positions_iter = cycle(test_positions)

    def _animate_next() -> None:
        x, y = next(_positions_iter)
        primary.animate_pointer_to(x, y)
        print(f"  -> pointer target: ({x}, {y}) DIP on {primary.screen_name}")

    _timer = QTimer()
    _timer.timeout.connect(_animate_next)
    _timer.start(1500)  # move every 1.5 seconds
    _animate_next()  # first position immediately

    print("\nManual verification checklist (confirm each):")
    print("  1. Blue arrow cursor visible, animates smoothly through 5 positions")
    print("  2. Clicks PASS THROUGH to apps underneath (try clicking desktop icons)")
    print("  3. No taskbar entry for overlay")
    print("  4. Overlay doesn't steal focus from the active app")
    print("  5. Pointer lands on plausible screen positions (corners, center)")
    print("\nClose with Ctrl+C in this terminal or close the Python process.")
    sys.exit(app.exec())
