"""Unit tests for overlay.py.

All tests are mock-based. Zero real Qt runtime or real HWND dependency.
Green in <2s. Covers: _CLICKTHROUGH_FLAGS constant sanity, physical_to_local_logical,
screen_for_monitor, OverlayController._overlay_for_screen lookup. See
docs/superpowers/plans/2026-04-11-overlay.md (or the source plan at
~/.claude/plans/streamed-tumbling-sunbeam.md) for the full test plan.

What's NOT tested here (manual verification gate only):
- Actual Win32 click-through behavior (needs real HWND)
- OverlayWindow Qt widget transparency, visibility, animation smoothness
- Focus stealing / taskbar entry absence
- Real multi-monitor spanning
All of the above are verified by `py -3.13 -m overlay` in Task 9.
"""

def test_overlay_module_importable():
    import overlay  # noqa: F401


# --- _CLICKTHROUGH_FLAGS constant -------------------------------------------

class TestClickthroughFlagsConstant:
    """Sanity test for the Win32 ex-style OR bit pattern.

    This is the one ceremony test we keep for overlay.py because a typo in
    the Win32 constants (e.g., WS_EX_LAYERED = 0x00080000 vs 0x00800000)
    would silently break click-through without raising any error. The bit
    pattern is load-bearing and deserves a test that catches drift.
    """

    def test_clickthrough_flags_bit_pattern(self):
        """WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW"""
        from overlay import _CLICKTHROUGH_FLAGS
        expected = 0x00080000 | 0x00000020 | 0x00000008 | 0x08000000 | 0x00000080
        assert _CLICKTHROUGH_FLAGS == expected
        assert _CLICKTHROUGH_FLAGS == 0x080800A8


# --- Mock helpers ------------------------------------------------------------

class _MockRect:
    """Test double for QRect with the fields physical_to_local_logical needs.

    Only implements left(), top(), width(), height() because that's all the
    pure-math functions touch. Using a real QRect would require a QApplication.
    """

    def __init__(self, x: int, y: int, w: int, h: int) -> None:
        self._x = x
        self._y = y
        self._w = w
        self._h = h

    def left(self) -> int:
        return self._x

    def top(self) -> int:
        return self._y

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h


class _MockScreen:
    """Test double for QScreen with the methods overlay.py's pure functions
    need: geometry() returning a QRect-like object (DIP coords) and
    devicePixelRatio() returning a float.

    Using a real QScreen would require a QApplication instance which needs
    a real display — defeats the "mock-based, no real hardware" test target.
    """

    def __init__(self, x: int, y: int, w: int, h: int, ratio: float,
                 name: str = "Mock") -> None:
        self._geom = _MockRect(x, y, w, h)
        self._ratio = ratio
        self._name = name

    def geometry(self) -> _MockRect:
        return self._geom

    def devicePixelRatio(self) -> float:
        return self._ratio

    def name(self) -> str:
        return self._name


# --- physical_to_local_logical -----------------------------------------------

class TestPhysicalToLocalLogical:
    """Tests for overlay.physical_to_local_logical.

    Verifies the physical-pixel -> Qt-logical-DIP conversion PER SCREEN
    (not using a cached global ratio). Covers 100% DPI, 200% DPI (user's
    actual machine), and mixed-DPI multi-monitor scenarios.
    """

    def test_100pct_dpi_primary_at_origin(self):
        """ratio=1.0, screen at (0,0,1920,1080) -> physical == logical."""
        from overlay import physical_to_local_logical
        screen = _MockScreen(x=0, y=0, w=1920, h=1080, ratio=1.0)
        assert physical_to_local_logical(500, 300, screen) == (500, 300)

    def test_200pct_dpi_primary_at_origin(self):
        """User's machine: 2880x1800 physical, 200% DPI, DIP (0,0,1440,900).
        Physical (1440, 900) -> local (720, 450) — dead center.
        Matches Step 1 verified coordinate math."""
        from overlay import physical_to_local_logical
        screen = _MockScreen(x=0, y=0, w=1440, h=900, ratio=2.0)
        assert physical_to_local_logical(1440, 900, screen) == (720, 450)

    def test_100pct_secondary_with_offset(self):
        """Secondary monitor at DIP (2880, 0) 1920x1080 @ 100%.
        Physical left = 2880*1 = 2880. Physical (3880, 500) -> local (1000, 500)."""
        from overlay import physical_to_local_logical
        screen = _MockScreen(x=2880, y=0, w=1920, h=1080, ratio=1.0)
        assert physical_to_local_logical(3880, 500, screen) == (1000, 500)

    def test_200pct_secondary_with_offset(self):
        """Secondary at DIP (1440, 0), 960x600 @ 200%.
        Physical left = 1440*2 = 2880. Physical (3880, 1100):
          local_phys_x = 3880 - 2880 = 1000, local_phys_y = 1100 - 0 = 1100
          local_log_x = 1000 / 2 = 500, local_log_y = 1100 / 2 = 550"""
        from overlay import physical_to_local_logical
        screen = _MockScreen(x=1440, y=0, w=960, h=600, ratio=2.0)
        assert physical_to_local_logical(3880, 1100, screen) == (500, 550)


# --- screen_for_monitor ------------------------------------------------------

class TestScreenForMonitor:
    """Tests for overlay.screen_for_monitor.

    Matches a capture.py monitor dict (physical pixel bounds) against a
    list of QScreen-like objects (DIP coords + devicePixelRatio). Verifies
    per-screen metadata matching and the no-match fallback path.
    """

    def test_single_monitor_exact_match(self):
        """User's machine: 2880x1800 physical, 200% DPI, DIP (0,0,1440,900).
        mss monitor dict has physical bounds; screen_for_monitor must find it."""
        from overlay import screen_for_monitor
        screens = [_MockScreen(x=0, y=0, w=1440, h=900, ratio=2.0, name="Primary")]
        monitor = {"left": 0, "top": 0, "width": 2880, "height": 1800}
        result = screen_for_monitor(monitor, screens)
        assert result is screens[0]

    def test_multi_monitor_primary_match(self):
        """Two monitors: laptop (200%) + external (100%). monitor dict for
        the laptop should resolve to the first screen."""
        from overlay import screen_for_monitor
        laptop = _MockScreen(x=0, y=0, w=1440, h=900, ratio=2.0, name="Laptop")
        external = _MockScreen(x=1440, y=0, w=1920, h=1080, ratio=1.0, name="External")
        screens = [laptop, external]
        monitor = {"left": 0, "top": 0, "width": 2880, "height": 1800}
        result = screen_for_monitor(monitor, screens)
        assert result is laptop
        assert result.name() == "Laptop"

    def test_multi_monitor_secondary_match(self):
        """Same two monitors. monitor dict for the external at physical
        (2880, 0, 1920, 1080) resolves to the external screen."""
        from overlay import screen_for_monitor
        laptop = _MockScreen(x=0, y=0, w=1440, h=900, ratio=2.0, name="Laptop")
        external = _MockScreen(x=1440, y=0, w=1920, h=1080, ratio=1.0, name="External")
        screens = [laptop, external]
        monitor = {"left": 1440, "top": 0, "width": 1920, "height": 1080}
        result = screen_for_monitor(monitor, screens)
        assert result is external
        assert result.name() == "External"

    def test_no_match_falls_back_to_first(self):
        """If the monitor dict doesn't match any screen (stale mss state,
        screen reconfigured mid-session, etc.), fall back to screens[0]."""
        from overlay import screen_for_monitor
        laptop = _MockScreen(x=0, y=0, w=1440, h=900, ratio=2.0, name="Laptop")
        external = _MockScreen(x=1440, y=0, w=1920, h=1080, ratio=1.0, name="External")
        screens = [laptop, external]
        # Monitor that doesn't match either screen
        monitor = {"left": 9999, "top": 9999, "width": 1, "height": 1}
        result = screen_for_monitor(monitor, screens)
        assert result is laptop  # first in the list


# --- OverlayController lookup ------------------------------------------------

class _MockOverlayWindow:
    """Test double for OverlayWindow that records show/apply calls without
    touching Qt internals. OverlayController's DI parameters let us inject
    this in place of the real OverlayWindow constructor."""

    def __init__(self, screen) -> None:
        self.screen_name = screen.name()
        self._shown = False
        self._clickthrough_applied = False

    def show(self) -> None:
        self._shown = True

    def hide(self) -> None:
        self._shown = False

    def apply_win32_clickthrough(self) -> None:
        self._clickthrough_applied = True

    def animate_pointer_to(self, x: int, y: int) -> None:  # noqa: ARG002
        pass


# --- Cursor polygon geometry --------------------------------------------------

class TestCursorPolygonGeometry:
    """Tests for the _CURSOR_VERTICES constant used by OverlayWindow.paintEvent."""

    def test_tip_is_at_origin(self):
        """First vertex (the tip) must be (0, 0) so the tip anchors at pointer_pos."""
        from overlay import _CURSOR_VERTICES
        assert _CURSOR_VERTICES[0] == (0, 0)

    def test_all_vertices_within_bounding_box(self):
        """All vertices must be non-negative (tip is top-left anchor)
        and within a reasonable bounding box (~30x30 pixels)."""
        from overlay import _CURSOR_VERTICES
        for dx, dy in _CURSOR_VERTICES:
            assert 0 <= dx <= 30, f"dx={dx} out of bounds"
            assert 0 <= dy <= 30, f"dy={dy} out of bounds"


class TestOverlayControllerLookup:
    """Tests for OverlayController._overlay_for_screen (name-based lookup).

    Uses dependency injection: OverlayController accepts an overlay_factory
    and a screens list so tests can avoid instantiating real QWidgets or
    calling QGuiApplication.screens() (which would require a QApplication).
    """

    def test_finds_overlay_by_screen_name(self):
        """After constructing with a list of mock screens, each mock overlay's
        screen_name should be looked up correctly."""
        from overlay import OverlayController
        laptop = _MockScreen(x=0, y=0, w=1440, h=900, ratio=2.0, name="Laptop")
        external = _MockScreen(x=1440, y=0, w=1920, h=1080, ratio=1.0, name="External")
        controller = OverlayController(
            overlay_factory=_MockOverlayWindow,
            screens=[laptop, external],
        )
        assert len(controller.overlays) == 2
        # Verify the controller called show + apply_win32_clickthrough on each
        for overlay in controller.overlays:
            assert overlay._shown is True
            assert overlay._clickthrough_applied is True
        # Lookup by screen
        result = controller._overlay_for_screen(laptop)
        assert result is not None
        assert result.screen_name == "Laptop"
        result = controller._overlay_for_screen(external)
        assert result is not None
        assert result.screen_name == "External"

    def test_returns_none_when_no_match(self):
        """If the lookup screen's name matches no overlay, return None."""
        from overlay import OverlayController
        laptop = _MockScreen(x=0, y=0, w=1440, h=900, ratio=2.0, name="Laptop")
        controller = OverlayController(
            overlay_factory=_MockOverlayWindow,
            screens=[laptop],
        )
        # Query with an unrelated screen
        other = _MockScreen(x=9999, y=9999, w=1, h=1, ratio=1.0, name="Unknown")
        result = controller._overlay_for_screen(other)
        assert result is None


# --- OverlayController lifecycle --------------------------------------------

class TestOverlayControllerLifecycle:
    """Tests for hide_for_capture() / show_after_capture() on OverlayController.

    These methods protect the screenshot-integrity invariant -- if Claude
    ever sees our pointer in the screenshot, it'll try to point at its own
    pointer, creating an infinite feedback loop. The tests verify that:
    - hide_for_capture() hides ALL overlays (not just one)
    - show_after_capture() shows ALL overlays AND re-applies Win32 click-through
      (because Qt occasionally resets the ex-styles during show/hide cycles)
    """

    def test_hide_for_capture_hides_all_overlays(self):
        """hide_for_capture() must call hide() on every overlay in the list."""
        from overlay import OverlayController
        laptop = _MockScreen(x=0, y=0, w=1440, h=900, ratio=2.0, name="Laptop")
        external = _MockScreen(x=1440, y=0, w=1920, h=1080, ratio=1.0, name="External")
        controller = OverlayController(
            overlay_factory=_MockOverlayWindow,
            screens=[laptop, external],
        )
        # Sanity: after construction all overlays are shown
        for overlay in controller.overlays:
            assert overlay._shown is True

        controller.hide_for_capture()

        # Every overlay should now be hidden
        for overlay in controller.overlays:
            assert overlay._shown is False, (
                f"overlay for {overlay.screen_name} was not hidden by hide_for_capture()"
            )

    def test_show_after_capture_shows_and_reapplies_clickthrough(self):
        """show_after_capture() must re-show every overlay AND re-apply the
        Win32 click-through ex-styles (because Qt can reset them during
        show/hide). The _clickthrough_applied flag on the mock captures both
        the initial apply and the re-apply."""
        from overlay import OverlayController
        laptop = _MockScreen(x=0, y=0, w=1440, h=900, ratio=2.0, name="Laptop")
        external = _MockScreen(x=1440, y=0, w=1920, h=1080, ratio=1.0, name="External")
        controller = OverlayController(
            overlay_factory=_MockOverlayWindow,
            screens=[laptop, external],
        )
        # Simulate the full capture lifecycle
        controller.hide_for_capture()
        # Reset the clickthrough flag so we can detect the re-apply
        for overlay in controller.overlays:
            overlay._clickthrough_applied = False

        controller.show_after_capture()

        # Every overlay should be visible again AND have click-through re-applied
        for overlay in controller.overlays:
            assert overlay._shown is True, (
                f"overlay for {overlay.screen_name} was not re-shown"
            )
            assert overlay._clickthrough_applied is True, (
                f"overlay for {overlay.screen_name} did not re-apply clickthrough "
                f"after show_after_capture() -- Qt may have reset ex-styles"
            )
