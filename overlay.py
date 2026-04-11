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
from itertools import cycle

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
    pyqtProperty,
)
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen, QScreen
from PyQt6.QtWidgets import QWidget


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

        # QPropertyAnimation drives the pointer via the pointerPos property.
        # 400ms linear matches config.POINTER_ANIMATION_MS. Bezier easing
        # is Phase 2 polish (see DECISIONS.md).
        self._animation = QPropertyAnimation(self, b"pointerPos")
        self._animation.setDuration(400)
        self._animation.setEasingCurve(QEasingCurve.Type.Linear)

    def paintEvent(self, _event) -> None:
        """Draw a blue circle at the current pointer position.

        Qt requires the `event` parameter in the method signature even
        though we don't use it; the underscore prefix signals "intentionally
        unused" (PEP 8 convention) without needing a linter suppression.
        """
        if not self._pointer_visible:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor(30, 144, 255))  # dodger blue
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        radius = 20
        painter.drawEllipse(self._pointer_pos, radius, radius)

    def animate_pointer_to(self, local_logical_x: int, local_logical_y: int) -> None:
        """Start a 400ms linear animation from current pointer position to target.

        Args:
            local_logical_x: within-screen logical DIP x (from physical_to_local_logical)
            local_logical_y: within-screen logical DIP y
        """
        target = QPoint(local_logical_x, local_logical_y)
        self._animation.stop()
        self._animation.setStartValue(self._pointer_pos)
        self._animation.setEndValue(target)
        self._pointer_visible = True
        self._animation.start()

    # Qt property wiring for QPropertyAnimation
    def _get_pointer_pos(self) -> QPoint:
        return self._pointer_pos

    def _set_pointer_pos(self, pos: QPoint) -> None:
        self._pointer_pos = pos
        self.update()  # trigger a paintEvent

    pointerPos = pyqtProperty(QPoint, _get_pointer_pos, _set_pointer_pos)

    def apply_win32_clickthrough(self) -> None:
        """Apply Win32 ex-styles for click-through. MUST be called after show()."""
        hwnd = int(self.winId())
        apply_clickthrough_styles(hwnd)


# --- Controller --------------------------------------------------------------

class OverlayController:
    """Manages one OverlayWindow per physical monitor.

    Created once at app startup. app.py calls point_at() with physical
    pixel coordinates from Claude + the CaptureResult.monitor dict, and
    the controller routes to the right overlay via screen_for_monitor +
    physical_to_local_logical. hide_for_capture()/show_after_capture()
    operate on all overlays atomically so Claude never sees our pointer
    in its screenshot.

    Dependency injection: overlay_factory and screens are injectable so
    tests can substitute mocks without instantiating real QWidgets or
    requiring a QApplication. In production both default to the real
    OverlayWindow class and QGuiApplication.screens() respectively.
    """

    def __init__(
        self,
        overlay_factory=None,
        screens: list[QScreen] | None = None,
    ) -> None:
        """Construct one overlay per physical monitor.

        Args:
            overlay_factory: callable taking a QScreen and returning an
                OverlayWindow-like object. Defaults to OverlayWindow. Tests
                inject a mock factory to avoid real QWidget instantiation.
            screens: list of QScreens to create overlays for. Defaults to
                QGuiApplication.screens(). Tests inject a list of mock
                screens to avoid needing a running QApplication.
        """
        if overlay_factory is None:
            overlay_factory = OverlayWindow
        if screens is None:
            screens = QGuiApplication.screens()
        self.overlays: list[OverlayWindow] = []
        for qscreen in screens:
            overlay = overlay_factory(qscreen)
            overlay.show()
            overlay.apply_win32_clickthrough()
            self.overlays.append(overlay)

    def point_at(
        self,
        physical_x: int,
        physical_y: int,
        monitor: dict,
    ) -> None:
        """Route the animated pointer to the overlay covering `monitor`.

        Args:
            physical_x: virtual-desktop physical pixel x (output of
                capture.unscale_claude_coords).
            physical_y: virtual-desktop physical pixel y.
            monitor: mss-style monitor dict from CaptureResult.monitor.
        """
        screens = QGuiApplication.screens()
        target_screen = screen_for_monitor(monitor, screens)
        target_overlay = self._overlay_for_screen(target_screen)
        if target_overlay is None:
            # Fallback: primary overlay (first in list)
            if not self.overlays:
                return
            target_overlay = self.overlays[0]
        local_x, local_y = physical_to_local_logical(
            physical_x, physical_y, target_screen
        )
        target_overlay.animate_pointer_to(local_x, local_y)

    def _overlay_for_screen(self, screen: QScreen) -> OverlayWindow | None:
        """Find the overlay whose screen_name matches the target screen's name.

        Pure lookup function, testable without Qt runtime. Returns None
        if no overlay matches (controller was constructed with an
        incompatible screen list).
        """
        target_name = screen.name()
        for overlay in self.overlays:
            if overlay.screen_name == target_name:
                return overlay
        return None

    def hide_for_capture(self) -> None:
        """Hide ALL overlays before mss.grab() so Claude doesn't see our
        pointer in the screenshot. Must be called from the main Qt thread."""
        for overlay in self.overlays:
            overlay.hide()

    def show_after_capture(self) -> None:
        """Re-show ALL overlays after capture completes. Also re-applies
        the Win32 ex-styles in case Qt reset them during show/hide."""
        for overlay in self.overlays:
            overlay.show()
            overlay.apply_win32_clickthrough()


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
    print("  1. Blue circle pointer visible, animates smoothly through 5 positions")
    print("  2. Clicks PASS THROUGH to apps underneath (try clicking desktop icons)")
    print("  3. No taskbar entry for overlay")
    print("  4. Overlay doesn't steal focus from the active app")
    print("  5. Pointer lands on plausible screen positions (corners, center)")
    print("\nClose with Ctrl+C in this terminal or close the Python process.")
    sys.exit(app.exec())
