"""Unit tests for capture.py.

All tests are mock-based. Zero real-hardware dependency. Green in <2s.
Covers: pick_resolution, resize_for_claude, unscale_claude_coords,
monitor_containing, set_dpi_awareness. See
docs/superpowers/plans/2026-04-11-capture.md for the full test plan.
"""

import pytest


def test_capture_module_importable():
    import capture  # noqa: F401


# --- pick_resolution ---------------------------------------------------------

class TestPickResolution:
    """Tests for capture.pick_resolution.

    Mirrors Clicky's ElementLocationDetector.swift bestComputerUseResolution()
    logic: pick the candidate whose aspect ratio is closest to the source's.
    """

    def test_16_10_exact_match(self):
        """2880x1800 (16:10) -> 1280x800. User's MacBook-class display."""
        from capture import pick_resolution
        assert pick_resolution(2880, 1800) == (1280, 800)

    def test_16_9_laptop(self):
        """1920x1080 (16:9) -> 1366x768. Most generic laptops / externals."""
        from capture import pick_resolution
        assert pick_resolution(1920, 1080) == (1366, 768)

    def test_4_3_legacy(self):
        """1024x768 (4:3) -> 1024x768. Exact match for legacy displays."""
        from capture import pick_resolution
        assert pick_resolution(1024, 768) == (1024, 768)

    def test_ultrawide_falls_back_to_closest(self):
        """3440x1440 (21:9 ~= 2.389) -> 1366x768 (1.779). Closest available."""
        from capture import pick_resolution
        assert pick_resolution(3440, 1440) == (1366, 768)

    def test_square_picks_4_3(self):
        """1000x1000 (1.0) -> 1024x768 (1.333). 4:3 is closest of the three."""
        from capture import pick_resolution
        assert pick_resolution(1000, 1000) == (1024, 768)

    def test_invalid_dimensions_raise(self):
        """Zero or negative dimensions must raise ValueError."""
        from capture import pick_resolution
        with pytest.raises(ValueError):
            pick_resolution(0, 800)
        with pytest.raises(ValueError):
            pick_resolution(1280, -1)


# --- resize_for_claude -------------------------------------------------------

class TestResizeForClaude:
    """Tests for capture.resize_for_claude.

    Verifies LANCZOS resize produces exact target dimensions and returns
    scale factors usable for later unscaling Claude's coordinates.
    """

    def test_exact_pixel_dims_2880x1800_to_1280x800(self):
        """Resize returns an image with size == (target_w, target_h) exactly."""
        from PIL import Image
        from capture import resize_for_claude
        source = Image.new("RGB", (2880, 1800), color=(0, 0, 0))
        resized, sx, sy = resize_for_claude(source, 1280, 800)
        assert resized.size == (1280, 800)
        assert sx == 2880 / 1280
        assert sy == 1800 / 800

    def test_uniform_scale_16_10(self):
        """16:10 source to 16:10 target -> uniform scale, sx == sy."""
        from PIL import Image
        from capture import resize_for_claude
        source = Image.new("RGB", (2880, 1800), color=(255, 255, 255))
        _, sx, sy = resize_for_claude(source, 1280, 800)
        assert sx == pytest.approx(2.25)
        assert sy == pytest.approx(2.25)
        assert sx == sy

    def test_non_uniform_scale_when_aspect_mismatch(self):
        """If source aspect != target aspect, sx != sy and math still works."""
        from PIL import Image
        from capture import resize_for_claude
        source = Image.new("RGB", (1920, 1200), color=(128, 128, 128))  # 16:10
        resized, sx, sy = resize_for_claude(source, 1366, 768)  # 16:9 target
        assert resized.size == (1366, 768)
        assert sx == pytest.approx(1920 / 1366)
        assert sy == pytest.approx(1200 / 768)
        assert sx != sy

    def test_invalid_target_dims_raise(self):
        """Zero or negative target dimensions must raise ValueError."""
        from PIL import Image
        from capture import resize_for_claude
        source = Image.new("RGB", (1280, 800))
        with pytest.raises(ValueError):
            resize_for_claude(source, 0, 800)
        with pytest.raises(ValueError):
            resize_for_claude(source, 1280, -5)


# --- unscale_claude_coords ---------------------------------------------------

class TestUnscaleClaudeCoords:
    """Tests for capture.unscale_claude_coords.

    This is the function that maps Claude's returned (x, y) in the
    declared resolution space back to physical pixels in virtual desktop
    space. Must clamp out-of-bounds Claude coords BEFORE scaling.
    """

    def test_uniform_scale_zero_offset(self):
        """Claude (640, 400) x 2.25 + (0,0) -> physical (1440, 900)."""
        from capture import unscale_claude_coords
        x, y = unscale_claude_coords(
            claude_x=640, claude_y=400,
            scale_x=2.25, scale_y=2.25,
            monitor_left=0, monitor_top=0,
            target_w=1280, target_h=800,
        )
        assert (x, y) == (1440, 900)

    def test_monitor_offset_applied(self):
        """Claude (100, 100) x 2.0 + (1920, 0) -> physical (2120, 200).
        Simulates a secondary monitor to the right of primary."""
        from capture import unscale_claude_coords
        x, y = unscale_claude_coords(
            claude_x=100, claude_y=100,
            scale_x=2.0, scale_y=2.0,
            monitor_left=1920, monitor_top=0,
            target_w=1280, target_h=800,
        )
        assert (x, y) == (2120, 200)

    def test_clamps_negative_claude_coords(self):
        """Claude (-50, 100) -> clamped to (0, 100), then x 2.25 -> (0, 225)."""
        from capture import unscale_claude_coords
        x, y = unscale_claude_coords(
            claude_x=-50, claude_y=100,
            scale_x=2.25, scale_y=2.25,
            monitor_left=0, monitor_top=0,
            target_w=1280, target_h=800,
        )
        assert (x, y) == (0, 225)

    def test_clamps_overflow_claude_coords(self):
        """Claude (1500, 900) in 1280x800 space -> clamped to (1279, 799)
        then x 2.25 -> (2877, 1797)."""
        from capture import unscale_claude_coords
        x, y = unscale_claude_coords(
            claude_x=1500, claude_y=900,
            scale_x=2.25, scale_y=2.25,
            monitor_left=0, monitor_top=0,
            target_w=1280, target_h=800,
        )
        # Clamp: max allowed is target - 1 (so 1279, 799)
        # Scale: 1279 * 2.25 = 2877.75 -> int(2877.75) = 2877
        #        799 * 2.25 = 1797.75 -> int(1797.75) = 1797
        assert (x, y) == (2877, 1797)


# --- monitor_containing / list_monitors --------------------------------------

class TestMonitorContaining:
    """Tests for capture.monitor_containing.

    Pure function over a list of mss-style monitor dicts. No mocking needed
    since we pass the monitors list explicitly.
    """

    def test_point_inside_primary_monitor(self):
        """Cursor at (500, 500) on a 2880x1800 monitor at (0,0) -> returns it."""
        from capture import monitor_containing
        monitors = [
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
        ]
        result = monitor_containing(500, 500, monitors)
        assert result == monitors[0]

    def test_dead_zone_falls_back_to_primary(self):
        """Cursor at (-9999, -9999) with no containing monitor -> primary."""
        from capture import monitor_containing
        monitors = [
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
            {"left": 2880, "top": 0, "width": 1920, "height": 1080},
        ]
        result = monitor_containing(-9999, -9999, monitors)
        assert result == monitors[0]

    def test_point_on_secondary_monitor(self):
        """Cursor at (3500, 500) -> on the secondary monitor at (2880, 0)."""
        from capture import monitor_containing
        monitors = [
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
            {"left": 2880, "top": 0, "width": 1920, "height": 1080},
        ]
        result = monitor_containing(3500, 500, monitors)
        assert result == monitors[1]

    def test_point_at_exact_edge(self):
        """Right edge is exclusive. Point at left + width - 1 is inside;
        point at left + width is outside."""
        from capture import monitor_containing
        monitors = [
            {"left": 0, "top": 0, "width": 100, "height": 100},
            {"left": 100, "top": 0, "width": 100, "height": 100},
        ]
        # (99, 50) is inside monitor 0 (rightmost pixel of mon 0)
        assert monitor_containing(99, 50, monitors) == monitors[0]
        # (100, 50) is inside monitor 1 (leftmost pixel of mon 1)
        assert monitor_containing(100, 50, monitors) == monitors[1]


# --- set_dpi_awareness / get_cursor_position --------------------------------

class TestSetDpiAwareness:
    """Tests for capture.set_dpi_awareness.

    Idempotency matters: Windows returns E_ACCESSDENIED if the process
    already set DPI awareness (e.g., via PyQt6 or a previous call). Our
    function must swallow that and not raise.
    """

    def test_first_call_returns_bool(self):
        """First call returns a bool without raising."""
        from capture import set_dpi_awareness
        result = set_dpi_awareness()
        assert isinstance(result, bool)

    def test_second_call_does_not_raise(self):
        """Calling twice must not raise (Windows returns E_ACCESSDENIED)."""
        from capture import set_dpi_awareness
        set_dpi_awareness()
        # Second call must not raise:
        set_dpi_awareness()


# --- capture_all_screens ------------------------------------------------------

class TestCaptureAllScreens:
    """Tests for capture.capture_all_screens using mocked OS functions."""

    def test_single_monitor_returns_one_labeled_capture(self, mocker):
        from PIL import Image
        from capture import capture_all_screens, LabeledCapture

        mocker.patch("capture.set_dpi_awareness")
        mocker.patch("capture.get_cursor_position", return_value=(500, 400))
        mocker.patch("capture.list_monitors", return_value=[
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
        ])
        fake_img = Image.new("RGB", (2880, 1800), color=(100, 100, 100))
        mocker.patch("capture._capture_monitor", return_value=fake_img)

        results = capture_all_screens()
        assert len(results) == 1
        assert isinstance(results[0], LabeledCapture)
        assert results[0].is_cursor_screen is True
        assert "primary focus" in results[0].label
        assert "1280x800" in results[0].label or "1024x768" in results[0].label or "1366x768" in results[0].label

    def test_two_monitors_sorted_cursor_first(self, mocker):
        from PIL import Image
        from capture import capture_all_screens

        mocker.patch("capture.set_dpi_awareness")
        mocker.patch("capture.get_cursor_position", return_value=(3500, 500))
        mocker.patch("capture.list_monitors", return_value=[
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
            {"left": 2880, "top": 0, "width": 1920, "height": 1080},
        ])
        fake_img_primary = Image.new("RGB", (2880, 1800))
        fake_img_secondary = Image.new("RGB", (1920, 1080))
        mocker.patch("capture._capture_monitor", side_effect=[
            fake_img_primary, fake_img_secondary,
        ])

        results = capture_all_screens()
        assert len(results) == 2
        assert results[0].is_cursor_screen is True
        assert results[1].is_cursor_screen is False
        assert "primary focus" in results[0].label
        assert "secondary screen" in results[1].label

    def test_labels_contain_pixel_dimensions(self, mocker):
        from PIL import Image
        from capture import capture_all_screens

        mocker.patch("capture.set_dpi_awareness")
        mocker.patch("capture.get_cursor_position", return_value=(500, 400))
        mocker.patch("capture.list_monitors", return_value=[
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
        ])
        fake_img = Image.new("RGB", (2880, 1800))
        mocker.patch("capture._capture_monitor", return_value=fake_img)

        results = capture_all_screens()
        assert "image dimensions:" in results[0].label
        assert "pixels" in results[0].label

    def test_scale_factors_correct(self, mocker):
        from PIL import Image
        from capture import capture_all_screens

        mocker.patch("capture.set_dpi_awareness")
        mocker.patch("capture.get_cursor_position", return_value=(500, 400))
        mocker.patch("capture.list_monitors", return_value=[
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
        ])
        fake_img = Image.new("RGB", (2880, 1800))
        mocker.patch("capture._capture_monitor", return_value=fake_img)

        results = capture_all_screens()
        r = results[0]
        assert abs(r.scale_x - 2880 / r.target_width) < 0.001
        assert abs(r.scale_y - 1800 / r.target_height) < 0.001


class TestGetCursorPosition:
    """Tests for capture.get_cursor_position. Smoke tests only — real value
    depends on where the mouse is at test time, so we only check shape."""

    def test_returns_tuple_of_two_ints(self):
        """Must return (int, int)."""
        from capture import get_cursor_position
        result = get_cursor_position()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(c, int) for c in result)
