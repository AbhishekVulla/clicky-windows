"""Clicky Windows orchestrator — wires all 7 building blocks into the PTT loop.

One sequential pipeline worker thread per PTT press, cancel-on-re-press.
Matches Clicky's Swift Task pattern (CompanionManager.swift lines 490-720).

Threading rule: only pyqtSignal crosses thread boundaries. Worker thread
NEVER calls overlay methods directly.

Top-to-bottom order (so `python -m app` works):
    1. Module docstring
    2. Imports
    3. Constants + sentence splitter
    4. get_foreground_app() ctypes helper
    5. ClickyApp(QObject) orchestrator class
    6. __main__ block
"""
from __future__ import annotations

import ctypes
import os
import re
import signal
import sys
import threading
from ctypes import wintypes

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from ai import create_ai_client
from debug_log import DebugSession
from capture import capture_all_screens, set_dpi_awareness, unscale_claude_coords
from config import (
    ANTHROPIC_API_KEY,
    ASSEMBLYAI_API_KEY,
    CARTESIA_API_KEY,
    MODEL_ID,
)
from hotkey import PushToTalkHotkey
from memory import MemoryStore
from overlay import OverlayController
from stt import AssemblyAIStreamingSTT
from tts import CartesiaSonicTTS


# --- Constants + sentence splitter --------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?]\s")

_MAX_HISTORY_EXCHANGES = 10


def flush_sentences(buffer: str) -> tuple[list[str], str]:
    """Split buffer into complete sentences and leftover.

    Returns (list_of_complete_sentences, remaining_buffer).
    Splits on .!? followed by whitespace. The system prompt tells Claude
    to avoid abbreviations like 'e.g.' so false splits are rare.
    """
    sentences: list[str] = []
    while (m := _SENTENCE_END_RE.search(buffer)):
        end = m.end()
        sentences.append(buffer[:end].strip())
        buffer = buffer[end:]
    return sentences, buffer


# --- Foreground app detection -------------------------------------------------

def get_foreground_app() -> tuple[str, str]:
    """Return (app_name, window_title) of the foreground window via ctypes.

    app_name is the .exe basename (e.g. 'EXCEL.EXE').
    window_title is the full title bar text.
    Returns ('unknown', '') if detection fails.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ("unknown", "")

    length = user32.GetWindowTextLengthW(hwnd)
    title_buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title_buf, length + 1)
    window_title = title_buf.value

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    app_name = "unknown"
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if handle:
        try:
            exe_buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            kernel32.QueryFullProcessImageNameW(
                handle, 0, exe_buf, ctypes.byref(size)
            )
            app_name = os.path.basename(exe_buf.value) or "unknown"
        finally:
            kernel32.CloseHandle(handle)

    return (app_name, window_title)


# --- ClickyApp orchestrator ---------------------------------------------------

class ClickyApp(QObject):
    """Main orchestrator. Owns all services + signals + worker lifecycle."""

    sig_pressed = pyqtSignal()
    sig_released = pyqtSignal()
    sig_hide_overlay = pyqtSignal()
    sig_show_overlay = pyqtSignal()
    sig_point_at = pyqtSignal(int, int, dict)
    sig_record_memory = pyqtSignal(str, str, str, str, list)

    def __init__(
        self,
        ai_client=None,
        stt_client=None,
        tts_client=None,
        memory_store=None,
        overlay_controller=None,
        hotkey_instance=None,
    ) -> None:
        super().__init__()

        self._ai = ai_client or create_ai_client(
            model_id=MODEL_ID,
            api_key=ANTHROPIC_API_KEY,
        )
        self._stt = stt_client or AssemblyAIStreamingSTT(
            api_key=ASSEMBLYAI_API_KEY
        )
        self._tts = tts_client or CartesiaSonicTTS(api_key=CARTESIA_API_KEY)
        self._memory = memory_store or MemoryStore()
        self._overlay = overlay_controller
        self._hotkey = hotkey_instance

        self._history: list[dict] = []
        self._cancel_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._current_app: str = "unknown"
        self._current_title: str = ""

        self.sig_pressed.connect(self._handle_press)
        self.sig_released.connect(self._handle_release)
        self.sig_hide_overlay.connect(self._on_hide_overlay)
        self.sig_show_overlay.connect(self._on_show_overlay)
        self.sig_point_at.connect(self._on_point_at)
        self.sig_record_memory.connect(self._on_record_memory)

    def start(self) -> None:
        """Initialize overlay + hotkey and begin listening.

        Hotkey callbacks fire on the pynput listener thread, so they emit
        sig_pressed/sig_released which Qt marshals to _handle_press/_handle_release
        on the main thread. This is the pyqtSignal-only thread crossing rule.
        """
        if self._overlay is None:
            self._overlay = OverlayController()
        if self._hotkey is None:
            self._hotkey = PushToTalkHotkey(
                on_press=lambda: self.sig_pressed.emit(),
                on_release=lambda: self.sig_released.emit(),
            )
        self._hotkey.start()
        _log("Listening for Ctrl+Alt+Space...")

    def stop(self) -> None:
        """Clean shutdown of all services."""
        if self._hotkey:
            self._hotkey.stop()
        self._cancel_event.set()
        self._tts.stop()
        self._stt.disconnect()
        _log("Shutdown complete.")

    # --- Hotkey handlers (called on Qt main thread via pyqtSignal) ---

    def _handle_press(self) -> None:
        """Hotkey pressed: kill TTS + start recording + capture foreground app."""
        import time
        _log("PRESS handler START")
        t0 = time.time()
        self._tts.stop()
        # Check if TTS thread actually died
        tts_thread = self._tts._current_thread
        tts_alive = tts_thread.is_alive() if tts_thread else False
        _log(f"  tts.stop() called, old thread alive={tts_alive}")
        self._current_app, self._current_title = get_foreground_app()
        _log(f"  app: {self._current_app}")
        try:
            self._stt.start_recording()
            _log(f"  start_recording() in {(time.time()-t0)*1000:.0f}ms")
        except RuntimeError as exc:
            _log(f"ERROR: STT start failed — {exc}")
            return

    def _handle_release(self) -> None:
        """Hotkey released: cancel previous worker, spawn new pipeline."""
        import time
        _log(f"RELEASE handler START (Qt main thread)")
        if self._worker_thread and self._worker_thread.is_alive():
            _log("  cancelling previous worker + stopping TTS")
            self._cancel_event.set()
            self._tts.stop()

        self._cancel_event = threading.Event()

        self._worker_thread = threading.Thread(
            target=self._pipeline_worker,
            args=(
                self._current_app,
                self._current_title,
                self._cancel_event,
            ),
            daemon=True,
            name="clicky-pipeline",
        )
        self._worker_thread.start()

    # --- Pipeline worker (runs on worker thread) ---

    def _pipeline_worker(
        self,
        app_name: str,
        window_title: str,
        cancel: threading.Event,
    ) -> None:
        """Sequential pipeline: STT → capture → recall → stream → TTS → overlay."""
        dbg = DebugSession.start(app_name, window_title)
        try:
            if cancel.is_set():
                return

            dbg.log("STT: calling stop_recording()...")
            transcript = self._stt.stop_recording()
            dbg.log(f"STT: {self._stt._chunk_count} chunks forwarded to AssemblyAI")
            dbg.log(f"STT: latest_partial before ForceEndpoint: {self._stt._latest_partial!r}")
            dbg.log(f"STT: final transcript ({len(transcript)} chars): {transcript!r}")
            _log(f"Transcript: {transcript!r}")
            if not transcript.strip():
                dbg.log("NO SPEECH DETECTED — skipping interaction")
                _log("No speech detected, skipping.")
                return

            if cancel.is_set():
                return

            dbg.log("CAPTURE: hiding overlay + capturing screens...")
            self.sig_hide_overlay.emit()
            threading.Event().wait(0.05)
            captures = capture_all_screens()
            self.sig_show_overlay.emit()
            dbg.log(f"CAPTURE: {len(captures)} screen(s)")
            for i, c in enumerate(captures):
                dbg.log(f"  screen[{i}]: {c.target_width}x{c.target_height}, "
                        f"scale=({c.scale_x:.2f}, {c.scale_y:.2f}), "
                        f"monitor={c.monitor}, cursor={c.is_cursor_screen}")
                dbg.save_screenshot(c.image, f"screenshot_{i}.jpg")

            if cancel.is_set():
                return

            memory_context = self._memory.recall(app_name)
            dbg.log(f"MEMORY: recalled {len(memory_context)} chars for {app_name}")

            user_text = transcript
            if memory_context:
                user_text = (
                    f"[context from past sessions — use silently, don't summarize or reference it:]\n"
                    f"{memory_context}\n\n"
                    f"{transcript}"
                )

            images = [(c.image, c.label) for c in captures]
            cursor_capture = captures[0]

            if cancel.is_set():
                return

            dbg.log("CLAUDE: streaming started...")
            _log("Asking Claude...")
            with self._ai.ask_stream(
                images=images,
                transcript=user_text,
                history=self._history,
            ) as stream:
                for delta in stream.text_deltas():
                    if cancel.is_set():
                        return

                result = stream.final_result()

            if cancel.is_set():
                return

            dbg.log(f"CLAUDE: done ({len(result.spoken_text)} chars)")
            dbg.log(f"CLAUDE: spoken_text: {result.spoken_text!r}")
            dbg.log(f"CLAUDE: coordinate={result.coordinate}, label={result.element_label!r}, screen={result.screen_number}")

            if result.spoken_text:
                dbg.log("TTS: calling speak()...")
                self._tts.speak(result.spoken_text)

            if cancel.is_set():
                return

            _log(f"Response: {result.spoken_text[:80]}...")

            if result.coordinate:
                x_claude, y_claude = result.coordinate
                screen_num = result.screen_number

                # Save screenshot with red marker at Claude's coordinate
                dbg.save_screenshot(
                    cursor_capture.image,
                    "screenshot_with_marker.jpg",
                    coordinate=(x_claude, y_claude),
                )

                target_capture = cursor_capture
                if screen_num is not None:
                    for c in captures:
                        if f"screen{screen_num}" in c.label.replace(" ", ""):
                            target_capture = c
                            break

                phys_x, phys_y = unscale_claude_coords(
                    claude_x=x_claude,
                    claude_y=y_claude,
                    scale_x=target_capture.scale_x,
                    scale_y=target_capture.scale_y,
                    monitor_left=target_capture.monitor["left"],
                    monitor_top=target_capture.monitor["top"],
                    target_w=target_capture.target_width,
                    target_h=target_capture.target_height,
                )
                dbg.log(f"COORDS: claude=({x_claude},{y_claude}) -> physical=({phys_x},{phys_y})")
                dbg.log(f"COORDS: scale=({target_capture.scale_x:.2f},{target_capture.scale_y:.2f}), "
                        f"monitor_offset=({target_capture.monitor['left']},{target_capture.monitor['top']})")
                self.sig_point_at.emit(phys_x, phys_y, target_capture.monitor)
            else:
                dbg.log("COORDS: no coordinate returned (text-only response)")

            pointer_targets = []
            if result.coordinate:
                pointer_targets.append(result.coordinate)

            self.sig_record_memory.emit(
                app_name,
                window_title,
                transcript,
                result.spoken_text,
                pointer_targets,
            )

            self._history.append({
                "role": "user",
                "content": [{"type": "text", "text": transcript}],
            })
            self._history.append({
                "role": "assistant",
                "content": [{"type": "text", "text": result.spoken_text}],
            })
            if len(self._history) > _MAX_HISTORY_EXCHANGES * 2:
                self._history = self._history[-(
                    _MAX_HISTORY_EXCHANGES * 2
                ):]

            dbg.log("DONE — interaction complete")

        except Exception as exc:
            if not cancel.is_set():
                dbg.log(f"ERROR: {type(exc).__name__}: {exc}")
                _log(f"ERROR: Pipeline failed — {type(exc).__name__}: {exc}")
        finally:
            dbg.close()

    # --- Signal slot handlers (run on Qt main thread) ---

    def _on_hide_overlay(self) -> None:
        if self._overlay:
            self._overlay.hide_for_capture()

    def _on_show_overlay(self) -> None:
        if self._overlay:
            self._overlay.show_after_capture()

    def _on_point_at(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        if self._overlay:
            self._overlay.point_at(physical_x, physical_y, monitor)

    def _on_record_memory(
        self,
        app_name: str,
        window_title: str,
        question: str,
        response: str,
        pointer_targets: list,
    ) -> None:
        try:
            self._memory.record(
                app_name=app_name,
                window_title=window_title,
                user_question=question,
                claude_response=response,
                pointer_targets=pointer_targets,
            )
        except Exception as exc:
            _log(f"ERROR: Memory record failed — {exc}")


_T0 = __import__("time").time()


def _log(msg: str) -> None:
    """Print a log line with millisecond-precision elapsed time."""
    import time
    elapsed = (time.time() - _T0) * 1000
    ts = time.strftime("%H:%M:%S")
    print(f"[clicky {ts} +{elapsed:.0f}ms] {msg}", flush=True)


# --- Manual entry point -------------------------------------------------------

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    print("=" * 70)
    print("Clicky Windows — push-to-talk AI buddy")
    print("=" * 70)

    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not ASSEMBLYAI_API_KEY:
        missing.append("ASSEMBLYAI_API_KEY")
    if not CARTESIA_API_KEY:
        missing.append("CARTESIA_API_KEY")
    if missing:
        print(f"\nERROR: Missing API keys in .env: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in your keys.")
        sys.exit(1)

    set_dpi_awareness()
    qt_app = QApplication(sys.argv)

    clicky = ClickyApp()

    _log("Pre-opening mic + WebSocket (one-time startup cost)...")
    try:
        clicky._stt.connect()
        clicky._stt.on_partial_transcript(
            lambda text: print(f"[stt partial] {text}", flush=True)
        )
    except RuntimeError as exc:
        print(f"\nERROR: {exc}")
        sys.exit(1)

    clicky.start()

    def _shutdown(*_args):
        _log("Shutting down...")
        clicky.stop()
        qt_app.quit()

    signal.signal(signal.SIGINT, _shutdown)

    _log(f"Model: {MODEL_ID}")
    _log("Listening for Ctrl+Alt+Space... (Ctrl+C to quit)")

    sys.exit(qt_app.exec())
