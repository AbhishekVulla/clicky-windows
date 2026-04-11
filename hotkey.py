"""Clicky Windows global push-to-talk hotkey (Ctrl+Shift+Space).

Installs a low-level Windows keyboard hook via pynput in OBSERVE-ONLY mode
(`suppress=False`) and fires on_press / on_release callbacks when the
Ctrl+Shift+Space combo becomes active / breaks.

Why Ctrl+Shift+Space and not Alt+Space (the ergonomic 2-finger combo we'd
prefer): the previous Alt+Space implementation used `pynput.Listener(suppress=True)`
to block the Windows title-bar menu from opening. That broke the user's
entire keyboard globally because pynput's suppress flag is all-or-nothing —
it cannot suppress ONE specific combo. Web research (NVDA Issue #3472, Qt
Forum, Microsoft docs) confirmed that making Alt+Space work cleanly on
Windows is an 8-12 hour project involving Win32 RegisterHotKey + GetAsyncKeyState
polling + AutoHotkey-style masking-Ctrl tricks with fragile edge cases.
Ctrl+Shift+Space has NO default Windows OS behavior, so we just observe it
without suppressing anything — global typing works normally.

See DECISIONS.md 2026-04-12 entry "Ctrl+Shift+Space over Alt+Space — pynput
suppress=True is globally destructive" for the full rationale + research
findings. Phase 1.5 / Phase 2 may upgrade to Win32 RegisterHotKey-based
Alt+Space via a new subclass of PushToTalkHotkey — the abstract interface
makes this a drop-in swap without touching app.py.

Minor conflict: VS Code uses Ctrl+Shift+Space for "Trigger Parameter Hints"
(not IntelliSense — IntelliSense is Ctrl+Space which would be FAR worse, per
DECISIONS.md "Alt+Space hotkey, NEVER Ctrl+Space"). Small UX regression,
acceptable for Phase 1.

Clicky (macOS) uses ctrl+option modifier-only via a CGEvent LISTEN-ONLY tap
(observes but does NOT consume keys). Our `suppress=False` approach is the
Windows equivalent — same "observe but don't consume" philosophy.

Callbacks run on the pynput listener thread, NOT the Qt main thread. Phase 1
caller (this module's __main__) just prints. Step 7 app.py will wire them to
pyqtSignal.emit which is thread-safe by design -- Qt marshals across threads.

File order (so `py -3.13 -m hotkey` works):
    1. Module docstring
    2. Imports
    3. HotkeyState enum
    4. PushToTalkHotkey class
    5. __main__ block LAST
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Callable, Optional

from pynput import keyboard


# --- State enum --------------------------------------------------------------

class HotkeyState(Enum):
    """Two-state push-to-talk state machine.

    IDLE:      waiting for the user to hold all 3 keys of Ctrl+Shift+Space.
               No recording active.
    RECORDING: Ctrl AND Shift AND Space are ALL currently held. Audio capture
               is live. On any of the 3 being released, transition back to
               IDLE and fire on_release.
    """

    IDLE = "idle"
    RECORDING = "recording"


# --- PushToTalkHotkey --------------------------------------------------------

class PushToTalkHotkey:
    """Global Ctrl+Shift+Space push-to-talk hotkey, non-suppressing.

    Tracks Ctrl, Shift, Space key-down state independently so any of the 6
    possible press orders transitions IDLE -> RECORDING when all 3 are held.
    Any release of any of the 3 while in RECORDING immediately fires
    on_release() and returns to IDLE, clearing all 3 flags. This matches
    real-world PTT UX: the moment the combo breaks, stop recording.

    Thread model: pynput installs a low-level Windows keyboard hook on its
    own thread and invokes our handlers from that thread. Callers' on_press /
    on_release run on the pynput listener thread. Phase 1 (__main__) just
    prints, which is thread-safe. Step 7 app.py wires to pyqtSignal.emit
    which marshals across threads for free.

    A small threading.Lock guards the state flags because the listener thread
    fires handlers serially BUT start()/stop() can be called from the main
    thread concurrently with handler execution.

    suppress=False is DELIBERATE and load-bearing: pynput's suppress flag is
    global (all-or-nothing), and we only want to observe Ctrl+Shift+Space,
    not block every other key on the system. Ctrl+Shift+Space has no default
    Windows behavior, so observe-only works.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        listener_class=None,
    ) -> None:
        """Wire the hotkey to caller callbacks.

        Args:
            on_press:       fired once when Ctrl+Shift+Space combo becomes
                            active (all 3 keys held). Runs on pynput listener
                            thread.
            on_release:     fired once when the combo is broken by releasing
                            any of the 3 keys while RECORDING. Listener thread.
            listener_class: DI hook for tests -- factory for building the
                            keyboard listener. Defaults to pynput.keyboard.Listener
                            at construction time so tests can inject MagicMock.
        """
        self._on_press_cb = on_press
        self._on_release_cb = on_release
        self._listener_class = listener_class or keyboard.Listener

        self._lock = threading.Lock()
        self._ctrl_down: bool = False
        self._shift_down: bool = False
        self._space_down: bool = False
        self._state: HotkeyState = HotkeyState.IDLE

        self._listener = None  # set in start(), cleared in stop()

    @property
    def state(self) -> HotkeyState:
        """Current state machine position. Thread-safe read."""
        with self._lock:
            return self._state

    # --- public lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Install the low-level Windows keyboard hook with suppress=False.

        Idempotent: calling start() twice is a no-op after the first.
        The listener runs on its own thread; this returns immediately.

        suppress=False is DELIBERATE -- we observe key events but do NOT
        consume them. Ctrl+Shift+Space has no default Windows OS behavior
        (unlike Alt+Space which opens the title-bar menu), so we don't need
        to block it. This preserves global typing. Changing to suppress=True
        would block ALL keys globally due to pynput's all-or-nothing suppress
        semantics.
        """
        with self._lock:
            if self._listener is not None:
                return  # already started -- idempotent

            self._listener = self._listener_class(
                on_press=self._handle_press,
                on_release=self._handle_release,
                suppress=False,
            )
            self._listener.start()

    def stop(self) -> None:
        """Uninstall the hook and release the listener thread. Idempotent."""
        listener = None
        with self._lock:
            if self._listener is None:
                return  # already stopped -- idempotent
            listener = self._listener
            self._listener = None
        # Call stop() outside the lock so pynput can join its own thread
        # without deadlocking on a handler that's mid-flight waiting for us.
        try:
            listener.stop()
        except Exception:
            # Best-effort: if pynput's teardown raises (e.g. already stopped
            # internally), don't bubble it up -- stop() is idempotent.
            pass

    # --- key-event handlers (invoked by pynput on listener thread) ----------

    def _is_ctrl(self, key) -> bool:
        """Treat Ctrl, Ctrl_L, and Ctrl_R all as the ctrl modifier.

        pynput fires Key.ctrl_l on left-Ctrl press and Key.ctrl on some
        systems. Lumping them together avoids split-brain state where
        Ctrl_L pressed and Ctrl_R released would leave _ctrl_down stuck True.
        The RECORDING-release path clears all 3 flags defensively.
        """
        return key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r)

    def _is_shift(self, key) -> bool:
        """Treat Shift, Shift_L, and Shift_R all as the shift modifier."""
        return key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r)

    def _is_space(self, key) -> bool:
        """Space is a single constant in pynput (no left/right variants)."""
        return key == keyboard.Key.space

    def _handle_press(self, key) -> Optional[bool]:
        """Low-level key-down handler called by pynput on its listener thread.

        Sets the appropriate _down flag. If all 3 flags are True AND state is
        IDLE, transitions to RECORDING and fires on_press() exactly once.
        Order-independent: any of the 6 possible key-down sequences works.
        """
        fire_press = False
        with self._lock:
            if self._is_ctrl(key):
                self._ctrl_down = True
            elif self._is_shift(key):
                self._shift_down = True
            elif self._is_space(key):
                self._space_down = True
            # Non-hotkey keys: ignored, no state change, no flag touched.

            # Check if the combo is now complete.
            if (self._state == HotkeyState.IDLE
                    and self._ctrl_down
                    and self._shift_down
                    and self._space_down):
                self._state = HotkeyState.RECORDING
                fire_press = True

        if fire_press:
            # Fire callbacks OUTSIDE the lock so a slow on_press doesn't
            # block concurrent state reads from other threads.
            self._on_press_cb()
        return None  # pynput convention: None = propagate (we're suppress=False anyway)

    def _handle_release(self, key) -> Optional[bool]:
        """Low-level key-up handler called by pynput on its listener thread.

        If RECORDING AND the released key is ctrl/shift/space: fire on_release
        once, clear all 3 flags, return to IDLE. Otherwise just clear the
        flag for this specific released key (if it's one of the 3).
        """
        fire_release = False
        with self._lock:
            is_hotkey_key = (self._is_ctrl(key)
                             or self._is_shift(key)
                             or self._is_space(key))

            if self._state == HotkeyState.RECORDING and is_hotkey_key:
                # Any of the 3 released while RECORDING: end the recording.
                fire_release = True
                self._state = HotkeyState.IDLE
                self._ctrl_down = False
                self._shift_down = False
                self._space_down = False
            else:
                # IDLE path: clear the flag for this specific key, if applicable.
                if self._is_ctrl(key):
                    self._ctrl_down = False
                elif self._is_shift(key):
                    self._shift_down = False
                elif self._is_space(key):
                    self._space_down = False

        if fire_release:
            self._on_release_cb()
        return None


# --- Manual verification entry point ----------------------------------------

if __name__ == "__main__":
    # Run: py -3.13 -m hotkey
    # Hold Ctrl+Shift+Space to trigger PRESSED, release any of the 3 for RELEASED.
    # CRITICAL: verify typing in other apps still works (suppress=False).
    import time

    print("=" * 70)
    print("Clicky Windows -- hotkey.py manual verification")
    print("=" * 70)
    print("\nInstructions:")
    print("  1. Hold Ctrl+Shift+Space -- you should see >>> PRESSED within 50ms")
    print("  2. Release any of the 3 keys -- you should see >>> RELEASED within 50ms")
    print("  3. Open another window (Notepad) and type 'hello world' normally --")
    print("     typing MUST work (suppress=False: observe but never consume keys)")
    print("  4. In VS Code, Ctrl+Shift+Space will also trigger Parameter Hints --")
    print("     minor known conflict, acceptable for Phase 1")
    print("  5. Ctrl+C in this terminal to quit")
    print()

    hk = PushToTalkHotkey(
        on_press=lambda: print("  >>> PRESSED (recording started)"),
        on_release=lambda: print("  >>> RELEASED (recording stopped)"),
    )
    hk.start()
    print("Listener started. Waiting for Ctrl+Shift+Space...\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        hk.stop()
        print("\nListener stopped. Exiting.")
