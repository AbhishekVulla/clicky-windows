"""Unit tests for hotkey.py.

All tests are mock-based with zero real keyboard hook installation.
Mirrors the DI-mock pattern from tests/test_overlay.py: PushToTalkHotkey
accepts a `listener_class` parameter so tests inject a MagicMock factory
instead of pynput.keyboard.Listener.

The hotkey is Ctrl+Alt+Space (all 3 must be held for RECORDING; any
release of any of the 3 ends it). suppress=False means the listener
observes but never consumes keys — global typing still works.

Pivoted from Ctrl+Shift+Space to Ctrl+Alt+Space on 2026-04-12 evening
after empirically confirming Ctrl+Shift+Space conflicts with Excel +
Google Sheets "Select entire worksheet" binding. See DECISIONS.md
2026-04-12 (evening) "Ctrl+Alt+Space replaces Ctrl+Shift+Space" for
the full rationale including research-backed rejection of Fn+Space.

What's NOT tested here (manual verification gate only):
- Actual Windows low-level hook installation
- suppress=False actually NOT blocking global typing (the whole point of
  the earlier Alt+Space -> Ctrl+Shift+Space pivot from suppress=True)
- Excel "Select entire worksheet" NOT firing on Ctrl+Alt+Space (the whole
  point of THIS pivot from Ctrl+Shift+Space)
- Callback latency (<50ms)
- Windows window menu NOT opening on the combo (only Alt+Space alone opens it)
All of the above are verified by `py -3.13 -m hotkey`.
"""
from unittest.mock import MagicMock

from pynput.keyboard import Key


def test_hotkey_module_importable():
    import hotkey  # noqa: F401


class TestStateMachine:
    """Tests for the IDLE <-> RECORDING state-machine transitions.

    Each test constructs a fresh PushToTalkHotkey with MagicMock listener_class
    (never called here -- we don't invoke start()), then calls _handle_press /
    _handle_release directly to drive the transitions the listener thread
    would normally trigger. This isolates the state logic from pynput itself.

    The combo is Ctrl+Alt+Space: all 3 must be held together to transition
    to RECORDING. Any release of any of the 3 while RECORDING ends it.
    """

    def _make_hk(self):
        """Helper: fresh PushToTalkHotkey with mock callbacks + mock listener."""
        from hotkey import PushToTalkHotkey
        on_press = MagicMock()
        on_release = MagicMock()
        hk = PushToTalkHotkey(
            on_press=on_press,
            on_release=on_release,
            listener_class=MagicMock(),
        )
        return hk, on_press, on_release

    def test_ctrl_alt_space_in_order_fires_on_press(self):
        """Typical order: Ctrl, then Alt, then Space. on_press fires once
        on the space-down transition (the last key of the combo), state
        becomes RECORDING."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.ctrl)
        assert on_press.call_count == 0
        assert hk.state == HotkeyState.IDLE

        hk._handle_press(Key.alt)
        assert on_press.call_count == 0
        assert hk.state == HotkeyState.IDLE

        hk._handle_press(Key.space)
        assert on_press.call_count == 1
        assert on_release.call_count == 0
        assert hk.state == HotkeyState.RECORDING

    def test_space_then_ctrl_then_alt_also_fires_on_press(self):
        """Reverse order: Space first, then Ctrl, then Alt. Still valid --
        all 3 down means RECORDING, regardless of press order. Tests the
        order-independence of the state machine."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.space)
        assert on_press.call_count == 0
        assert hk.state == HotkeyState.IDLE

        hk._handle_press(Key.ctrl)
        assert on_press.call_count == 0
        assert hk.state == HotkeyState.IDLE

        hk._handle_press(Key.alt)
        assert on_press.call_count == 1
        assert hk.state == HotkeyState.RECORDING

    def test_alt_gr_is_treated_as_alt(self):
        """AltGr (Alt_Gr) on international keyboards must be recognized as
        the alt modifier. Without this, international users pressing
        Ctrl+AltGr+Space would see no Clicky response."""
        from hotkey import HotkeyState
        hk, on_press, _ = self._make_hk()

        hk._handle_press(Key.ctrl)
        hk._handle_press(Key.alt_gr)
        hk._handle_press(Key.space)
        assert on_press.call_count == 1
        assert hk.state == HotkeyState.RECORDING

    def test_release_space_while_recording_fires_on_release(self):
        """From RECORDING, releasing Space must fire on_release exactly once
        and return to IDLE. All 3 flags cleared per spec."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        # Drive into RECORDING
        hk._handle_press(Key.ctrl)
        hk._handle_press(Key.alt)
        hk._handle_press(Key.space)
        assert hk.state == HotkeyState.RECORDING

        hk._handle_release(Key.space)
        assert on_release.call_count == 1
        assert hk.state == HotkeyState.IDLE
        # All 3 flags cleared per spec
        assert hk._ctrl_down is False
        assert hk._alt_down is False
        assert hk._space_down is False

    def test_release_ctrl_while_recording_also_fires_on_release(self):
        """Releasing Ctrl (not Space) while RECORDING also ends recording.
        Any of the 3 keys going up is enough to break the combo."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.ctrl)
        hk._handle_press(Key.alt)
        hk._handle_press(Key.space)
        assert hk.state == HotkeyState.RECORDING

        hk._handle_release(Key.ctrl)
        assert on_release.call_count == 1
        assert hk.state == HotkeyState.IDLE

    def test_ctrl_alone_does_nothing(self):
        """Pressing Ctrl alone from IDLE: no callbacks, state stays IDLE,
        but _ctrl_down flag goes True so a subsequent Alt+Space can
        complete the combo."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.ctrl)
        assert on_press.call_count == 0
        assert on_release.call_count == 0
        assert hk.state == HotkeyState.IDLE
        assert hk._ctrl_down is True
        assert hk._alt_down is False
        assert hk._space_down is False

    def test_ctrl_alt_without_space_does_not_fire(self):
        """Pressing Ctrl+Alt but NOT Space should NOT fire on_press --
        the combo is incomplete without Space. Guards against the bug
        where the state machine accidentally transitions on 2 of 3 keys."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.ctrl)
        hk._handle_press(Key.alt)
        assert on_press.call_count == 0
        assert hk.state == HotkeyState.IDLE
        assert hk._ctrl_down is True
        assert hk._alt_down is True
        assert hk._space_down is False

    def test_non_hotkey_key_ignored(self):
        """Pressing an unrelated key (Enter) from IDLE: nothing happens at all.
        No callbacks, no state change, no flag set. The state machine only
        cares about Ctrl, Alt, and Space."""
        from hotkey import HotkeyState
        hk, on_press, on_release = self._make_hk()

        hk._handle_press(Key.enter)
        assert on_press.call_count == 0
        assert on_release.call_count == 0
        assert hk.state == HotkeyState.IDLE
        assert hk._ctrl_down is False
        assert hk._alt_down is False
        assert hk._space_down is False


class TestStartLifecycle:
    """Tests for start() listener construction via injected listener_class."""

    def test_start_creates_listener_with_suppress_false(self):
        """start() must instantiate the injected listener_class exactly once
        with suppress=False (load-bearing: pynput's suppress is all-or-nothing
        global, so suppress=True would disable ALL typing globally -- the
        exact bug that forced the original Alt+Space -> Ctrl+Shift+Space
        pivot, and by extension the current Ctrl+Shift+Space -> Ctrl+Alt+Space
        pivot inherits the same suppress=False model).

        on_press and on_release handlers must be wired to self._handle_press
        / self._handle_release."""
        from hotkey import PushToTalkHotkey
        fake_listener_instance = MagicMock()
        fake_listener_class = MagicMock(return_value=fake_listener_instance)

        hk = PushToTalkHotkey(
            on_press=MagicMock(),
            on_release=MagicMock(),
            listener_class=fake_listener_class,
        )
        hk.start()

        assert fake_listener_class.call_count == 1
        kwargs = fake_listener_class.call_args.kwargs
        assert kwargs["suppress"] is False, (
            "suppress MUST be False for Ctrl+Alt+Space -- pynput suppress=True "
            "is globally destructive and blocks all typing. See DECISIONS.md "
            "2026-04-12 (evening) entry 'Ctrl+Alt+Space replaces Ctrl+Shift+Space'."
        )
        assert kwargs["on_press"] == hk._handle_press
        assert kwargs["on_release"] == hk._handle_release
        fake_listener_instance.start.assert_called_once()

    def test_start_is_idempotent(self):
        """Calling start() twice must only construct one listener. Prevents
        double-hook installation which pynput would silently accept but
        would then deliver every key event twice."""
        from hotkey import PushToTalkHotkey
        fake_listener_instance = MagicMock()
        fake_listener_class = MagicMock(return_value=fake_listener_instance)

        hk = PushToTalkHotkey(
            on_press=MagicMock(),
            on_release=MagicMock(),
            listener_class=fake_listener_class,
        )
        hk.start()
        hk.start()

        assert fake_listener_class.call_count == 1
        assert fake_listener_instance.start.call_count == 1

