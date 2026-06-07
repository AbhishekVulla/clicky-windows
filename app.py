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
import queue
import re
import signal
import sys
import threading
from ctypes import wintypes

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

import kb
from ai import OllamaClient, create_ai_client
from debug_log import DebugSession
from locator import locate_via_grid
from capture import (
    capture_all_screens,
    get_cursor_position,
    list_monitors,
    monitor_containing,
    set_dpi_awareness,
    unscale_claude_coords,
)
from config import (
    ANTHROPIC_API_KEY,
    ASSEMBLYAI_API_KEY,
    CARTESIA_API_KEY,
    MODEL_ID,
    OLLAMA_HOST,
    OLLAMA_MODEL_VISION,
    resolve_api_key,
    resolve_setting,
)
# Note: LLM_PROVIDER is intentionally NOT imported as a module-level
# constant — _resolve_llm_credentials() calls resolve_setting fresh on
# every invocation so any change the user made in the Settings dialog
# (which writes to keyring) is picked up without an app restart. The
# module-level constant would be frozen at import time.
from hotkey import PushToTalkHotkey
from memory import MemoryStore
from overlay import OverlayController
from stt import AssemblyAIStreamingSTT
from tts import CartesiaSonicTTS


# --- Constants + sentence splitter --------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?]\s")

_MAX_HISTORY_EXCHANGES = 10

_REUSE_THRESHOLD_PX = 150
"""Max cursor movement between press and release for reusing press-time
captures. Raised from 50 → 150 on 2026-04-21 after real-session logs
showed 100-150px cursor hovers were re-capturing unnecessarily.
150px = ~3cm on a 200% DPI laptop display — within 'target hover'
intent, not 'user repositioned intentionally'."""


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


# --- Grid-locator fallback (v0.2.0 Ollama pixel-pointing) --------------------

_DIRECTIONAL_QUERY_WORDS = (
    "where", "click", "show me", "find", "point", "open", "select", "press",
    "navigate", "locate", "tap", "look at", "go to",
)
"""Words/phrases that suggest the user wants Clicky to point at a UI element.
Grid-locator only fires for queries containing one of these — skips
conceptual asks like 'what is HTML' that don't have a UI target."""


def _looks_directional(query: str) -> bool:
    """True if the query contains a directional word like 'where', 'click', 'show me'.

    Used as a cheap pre-filter before firing the grid-locator (which is 2 extra
    LLM calls — expensive on local Ollama). For conceptual questions Clicky
    should just answer with TTS and not try to point anywhere.
    """
    if not query:
        return False
    q_lower = query.lower()
    return any(word in q_lower for word in _DIRECTIONAL_QUERY_WORDS)


def _maybe_locate_via_grid(
    *,
    ai_client,
    result,
    cursor_capture,
    query: str,
    dbg=None,
):
    """Grid-locator fallback for Ollama responses lacking a [POINT:x,y] tag.

    Triggers ONLY if:
        1. ai_client is OllamaClient (local vision model)
        2. result.coordinate is None (Claude/Ollama didn't emit [POINT:x,y])
        3. query is directional (contains 'where' / 'click' / 'show me' / etc.)

    Returns (phys_x, phys_y) in PHYSICAL virtual-desktop coords (matching the
    output of unscale_claude_coords) or None if any condition fails / locator
    can't find a target.

    The output is in physical coords (not logical) so the caller can pass it
    straight to overlay.sig_point_at.emit() — same convention the existing
    Claude-coordinate path uses.

    Args:
        ai_client: the active AIClient (OllamaClient / AnthropicClient / etc.)
        result: PointParseResult from stream.final_result() — used to check
            if coordinate is already set
        cursor_capture: capture.LabeledCapture for the primary screen
        query: user's transcript (the question they asked)
        dbg: optional DebugSession for logging the grid-locator outcome
    """
    if not isinstance(ai_client, OllamaClient):
        return None
    if result.coordinate is not None:
        return None
    if not _looks_directional(query):
        if dbg is not None:
            dbg.log(
                f"GRID-LOCATOR: skipped (query not directional): {query!r}"
            )
        return None

    # Convert the PIL screenshot to base64 JPEG for the locator
    import io
    import base64
    buf = io.BytesIO()
    cursor_capture.image.save(buf, format="JPEG", quality=85)
    jpeg_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    monitor = cursor_capture.monitor
    # locate_via_grid returns coords pre-divided by dpi_scale. We want
    # PHYSICAL virtual-desktop coords (matching the existing app.py pipeline),
    # so pass dpi_scale=1.0 — locator returns (vx, vy) i.e. physical coords.
    # v0.2.0 Codex MED fix: thread dbg.log into the locator so transport
    # failures (Ollama timeout, image-decode error) are distinguishable from
    # model uncertainty (cell 0 / unparseable reply) in the debug log.
    # Without this, a broken Ollama looked identical to "model said no UI
    # element" — operator couldn't tell whether to debug their Ollama setup
    # or just rephrase the question.
    phys_xy = locate_via_grid(
        llm_client=ai_client,
        screenshot_jpeg_b64=jpeg_b64,
        original_size=(cursor_capture.target_width, cursor_capture.target_height),
        physical_size=(monitor["width"], monitor["height"]),
        physical_origin=(monitor["left"], monitor["top"]),
        dpi_scale=1.0,   # We want PHYSICAL coords; overlay handles logical conversion
        query=query,
        debug_log=(dbg.log if dbg is not None else None),
    )

    if dbg is not None:
        if phys_xy is None:
            dbg.log("GRID-LOCATOR: ran but returned None (LLM unsure or conceptual)")
        else:
            dbg.log(f"GRID-LOCATOR: hit physical=({phys_xy[0]},{phys_xy[1]})")

    return phys_xy


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
    # Path A Task 10 — LISTENING-state signals + audio-level forwarding.
    sig_show_waveform = pyqtSignal(int, int, dict)
    sig_hide_waveform = pyqtSignal()
    sig_audio_level = pyqtSignal(float)
    # THINKING-state spinner (post-Path-A UX fix): shown between release and
    # Claude returning a coordinate, so the user sees feedback during the
    # ~4-7s LLM wait (instead of the cursor just sitting there).
    sig_show_spinner = pyqtSignal(int, int, dict)
    sig_hide_spinner = pyqtSignal()

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

        # v0.2.0: respect LLM_PROVIDER setting (Settings dialog dropdown).
        # _resolve_llm_credentials returns the effective model_id + api_key
        # based on whether the user picked Anthropic or Ollama in Settings.
        # Without this branch the dropdown was cosmetic — see helper docstring.
        if ai_client is None:
            _model_id, _api_key = _resolve_llm_credentials()
            ai_client = create_ai_client(
                model_id=_model_id,
                api_key=_api_key,
                ollama_host=OLLAMA_HOST,
            )
        self._ai = ai_client
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

        # Press-time capture state (Path A Task 4). Shifts capture + memory
        # recall off the release-time critical path — saves ~250ms wall-clock.
        # Release-time pipeline re-captures only if cursor moved >50px
        # (user intentionally repositioned mid-utterance).
        self._press_captures: list | None = None
        self._press_memory: str = ""
        self._press_cursor_pos: tuple[int, int] | None = None
        self._capture_thread: threading.Thread | None = None

        self.sig_pressed.connect(self._handle_press)
        self.sig_released.connect(self._handle_release)
        self.sig_hide_overlay.connect(self._on_hide_overlay)
        self.sig_show_overlay.connect(self._on_show_overlay)
        self.sig_point_at.connect(self._on_point_at)
        self.sig_record_memory.connect(self._on_record_memory)
        self.sig_show_waveform.connect(self._on_show_waveform)
        self.sig_hide_waveform.connect(self._on_hide_waveform)
        self.sig_audio_level.connect(self._on_audio_level)
        self.sig_show_spinner.connect(self._on_show_spinner)
        self.sig_hide_spinner.connect(self._on_hide_spinner)

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
        # Wire RMS audio-level → Qt-thread-safe signal → overlay waveform.
        # stt's callback runs on the portaudio thread; pyqtSignal marshals
        # to the Qt main thread where _on_audio_level calls overlay.set_audio_level.
        self._stt.on_audio_level(lambda lvl: self.sig_audio_level.emit(lvl))

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
        # Clear any stale spinner from a prior interaction (defensive — if the
        # previous pipeline errored before hide_spinner fired, we don't want
        # to leave a spinner spinning when a new PTT starts).
        self.sig_hide_spinner.emit()
        self._tts.stop()
        # Prevent TTS speaker decay from leaking into this PTT's transcript
        # (acoustic feedback loop). 200ms window tuned to real laptop-mic decay.
        # MUST be set before the chime — on cold-start the chime triggers a
        # 400-500ms numpy/sounddevice cold-path init we don't want to count
        # against the grace window start time.
        self._stt.set_tts_grace_until(time.time() + 0.200)
        # Play listening chime (async / non-blocking) so user hears instant
        # "mic is hot" feedback. First call triggers sample generation
        # (~5ms CPU) + sounddevice cold-path init (~400ms on fresh portaudio).
        # Both are one-time costs amortized across the session.
        _play_chime_async()
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

        # Kick off capture + memory recall in the background so they overlap
        # with the user speaking. Release-time pipeline uses cached result.
        self._press_captures = None
        self._press_memory = ""
        self._press_cursor_pos = get_cursor_position()
        self._capture_thread = threading.Thread(
            target=self._press_time_capture,
            args=(self._current_app,),
            daemon=True,
            name="clicky-press-capture",
        )
        self._capture_thread.start()

        # Show the LISTENING-state waveform at the cursor position (Path A Task 10).
        # cursor polygon hides; bars replace it for the duration of the utterance.
        cursor_x, cursor_y = self._press_cursor_pos
        try:
            mon = monitor_containing(cursor_x, cursor_y, list_monitors())
            if mon is not None:
                self.sig_show_waveform.emit(cursor_x, cursor_y, mon)
        except Exception as exc:
            _log(f"WARN: show_waveform dispatch failed — {exc}")

    def _press_time_capture(self, app_name: str) -> None:
        """Background thread launched at press time. Captures screens + recalls
        memory while the user is still speaking. Result stored on self for
        the release-time pipeline worker to consume.

        Invariant #3 preserved: overlay.hide_for_capture() fires BEFORE the
        mss.grab() call via sig_hide_overlay (Qt signal to main thread).
        """
        try:
            self.sig_hide_overlay.emit()
            threading.Event().wait(0.05)
            captures = capture_all_screens()
            self.sig_show_overlay.emit()
            self._press_captures = captures
            self._press_memory = self._memory.recall(app_name)
        except Exception as exc:
            _log(f"ERROR: press-time capture failed — {type(exc).__name__}: {exc}")
            self._press_captures = None  # Release-time path falls back to re-capture

    def _release_capture_worker(
        self,
        release_cursor: tuple[int, int],
        app_name: str,
        result_queue: "queue.Queue",
    ) -> None:
        """Background thread launched at hotkey release. Runs in parallel with
        stt.stop_recording() so re-capture wall-clock is hidden under STT
        finalize latency. Makes the reuse-vs-recapture decision itself to
        preserve 'no flicker on cursor-still sessions' UX — mirrors the
        serial logic in _pipeline_worker pre-refactor.

        Pushes a tuple (captures, memory_context, reason_log_str) to
        result_queue. On exception pushes (None, None, error_str) so the
        main thread's queue.get() never hangs.

        Invariant #3 preserved: overlay.hide_for_capture() fires BEFORE every
        mss.grab() via sig_hide_overlay (Qt signal to main thread).
        """
        try:
            # If press-time capture is still running, wait briefly for it to
            # finish before making the reuse decision. Avoids overlay-hide
            # collision between press-time + release-time captures.
            press_thread = self._capture_thread
            if press_thread is not None and press_thread.is_alive():
                press_thread.join(timeout=0.5)

            # Compute cursor delta at release.
            cursor_moved_px = 9999
            if self._press_cursor_pos is not None:
                dx = release_cursor[0] - self._press_cursor_pos[0]
                dy = release_cursor[1] - self._press_cursor_pos[1]
                cursor_moved_px = int((dx * dx + dy * dy) ** 0.5)

            if (
                self._press_captures is not None
                and cursor_moved_px <= _REUSE_THRESHOLD_PX
            ):
                reason = (
                    f"reusing press-time captures "
                    f"(cursor moved {cursor_moved_px}px, "
                    f"threshold {_REUSE_THRESHOLD_PX}px)"
                )
                result_queue.put(
                    (self._press_captures, self._press_memory, reason)
                )
                return

            # Re-capture path — fire invariant-preserving hide → grab → show.
            if self._press_captures is None:
                reason_suffix = "no press-time capture available"
            else:
                reason_suffix = (
                    f"cursor moved {cursor_moved_px}px > "
                    f"{_REUSE_THRESHOLD_PX}px threshold"
                )
            reason = f"re-capturing on release ({reason_suffix})"

            self.sig_hide_overlay.emit()
            threading.Event().wait(0.05)
            captures = capture_all_screens()
            self.sig_show_overlay.emit()
            memory_context = self._memory.recall(app_name)
            result_queue.put((captures, memory_context, reason))
        except Exception as exc:
            _log(
                f"ERROR: release capture worker failed — "
                f"{type(exc).__name__}: {exc}"
            )
            result_queue.put(
                (None, None, f"error: {type(exc).__name__}: {exc}")
            )

    def _handle_release(self) -> None:
        """Hotkey released: cancel previous worker, spawn new pipeline."""
        import time
        _log(f"RELEASE handler START (Qt main thread)")
        # LISTENING → THINKING transition: hide waveform, show spinner at the
        # current cursor position. Cursor polygon stays hidden while spinner
        # runs; buddy reappears when pipeline hides spinner + fires bezier.
        self.sig_hide_waveform.emit()

        # Snapshot release-time cursor synchronously. Taken here (not at
        # worker-start) so mouse motion during STT can't flip the
        # reuse-vs-recapture decision mid-flight. Reused for the spinner
        # dispatch below to avoid a redundant Win32 GetCursorPos call.
        release_cursor: tuple[int, int] | None = None
        try:
            cursor_x, cursor_y = get_cursor_position()
            release_cursor = (cursor_x, cursor_y)
            mon = monitor_containing(cursor_x, cursor_y, list_monitors())
            if mon is not None:
                self.sig_show_spinner.emit(cursor_x, cursor_y, mon)
        except Exception as exc:
            _log(f"WARN: show_spinner dispatch failed — {exc}")
        if release_cursor is None:
            release_cursor = self._press_cursor_pos or (0, 0)

        if self._worker_thread and self._worker_thread.is_alive():
            _log("  cancelling previous worker + stopping TTS")
            self._cancel_event.set()
            self._tts.stop()
            # Same 200ms grace as press — prevents aborted TTS tail from
            # contaminating the new PTT's transcript.
            self._stt.set_tts_grace_until(time.time() + 0.200)

        self._cancel_event = threading.Event()

        # Size-1 queue: capture worker pushes once, pipeline worker gets once.
        release_capture_queue: queue.Queue = queue.Queue(maxsize=1)

        # Launch capture worker BEFORE pipeline worker so it starts doing its
        # reuse-decision + potential mss.grab in parallel with stt.stop_recording.
        capture_worker_thread = threading.Thread(
            target=self._release_capture_worker,
            args=(release_cursor, self._current_app, release_capture_queue),
            daemon=True,
            name="clicky-release-capture",
        )
        capture_worker_thread.start()

        self._worker_thread = threading.Thread(
            target=self._pipeline_worker,
            args=(
                self._current_app,
                self._current_title,
                self._cancel_event,
                release_capture_queue,
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
        capture_queue: "queue.Queue",
    ) -> None:
        """Sequential pipeline: STT → capture → recall → stream → TTS → overlay.

        ``capture_queue`` is populated in parallel by
        :meth:`_release_capture_worker` (launched in ``_handle_release``
        BEFORE this thread). This thread blocks on ``stt.stop_recording()``,
        then reads the capture result from the queue. Wall-clock becomes
        ``max(STT, capture)`` instead of ``STT + capture``.
        """
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

            # Read capture result from the worker that's been running in
            # parallel with stt.stop_recording above. Timeout is 5s — far
            # above any realistic capture time (~300ms worst case) — so if
            # the worker errored silently we fail loudly instead of hanging.
            # Fallback on timeout or error: use press-time captures if
            # available, else abort pipeline.
            try:
                captures, memory_context, capture_reason = capture_queue.get(
                    timeout=5.0
                )
            except queue.Empty:
                dbg.log("CAPTURE: worker timeout after 5s — falling back to press-time")
                captures = self._press_captures
                memory_context = self._press_memory
                capture_reason = "worker timeout — press-time fallback"

            if captures is None:
                if self._press_captures is not None:
                    dbg.log(
                        f"CAPTURE: worker failed ({capture_reason}) — "
                        f"using press-time fallback"
                    )
                    captures = self._press_captures
                    memory_context = self._press_memory
                else:
                    dbg.log(
                        f"CAPTURE: worker failed and no press-time fallback "
                        f"({capture_reason}) — aborting pipeline"
                    )
                    _log("ERROR: No screenshots available for Claude, aborting.")
                    return

            dbg.log(f"CAPTURE: {capture_reason}")

            dbg.log(f"CAPTURE: {len(captures)} screen(s)")
            for i, c in enumerate(captures):
                dbg.log(f"  screen[{i}]: {c.target_width}x{c.target_height}, "
                        f"scale=({c.scale_x:.2f}, {c.scale_y:.2f}), "
                        f"monitor={c.monitor}, cursor={c.is_cursor_screen}")
                dbg.save_screenshot(c.image, f"screenshot_{i}.jpg")
            dbg.log(f"MEMORY: recalled {len(memory_context)} chars for {app_name}")

            if cancel.is_set():
                return

            user_text = transcript
            if memory_context:
                user_text = (
                    f"[context from past sessions — use silently, don't summarize or reference it:]\n"
                    f"{memory_context}\n\n"
                    f"{transcript}"
                )

            # Curated KB recall (user-uploaded per-app docs). Empty tuple
            # if no .md file exists for this app — Claude proceeds with
            # vision + memory only ("Claude already knows that software"
            # path). When present, ask_stream injects as a 2nd
            # cache_control system block (Anthropic) or concats into
            # system string (Gemini).
            #
            # Wrapped in try/except because KB files are user-controlled
            # and could be malformed (bad encoding, permission errors,
            # symlink loops, etc.). Failure here must NOT crash the
            # pipeline — Claude can still answer with vision + memory.
            try:
                kb_content, kb_app_name = kb.recall(app_name)
            except Exception as exc:
                dbg.log(
                    f"KB: read failed ({type(exc).__name__}: {exc}), "
                    f"falling back to no-KB path"
                )
                kb_content, kb_app_name = "", ""
            if kb_content:
                dbg.log(
                    f"KB: injected {len(kb_content)} chars from "
                    f"{kb_app_name}.md"
                )
            else:
                dbg.log(f"KB: no file for {app_name}, skipping")

            images = [(c.image, c.label) for c in captures]
            cursor_capture = captures[0]

            if cancel.is_set():
                return

            dbg.log("CLAUDE: streaming started...")
            _log("Asking Claude...")

            # Arm one-shot first-audible-word log. Fires on the first
            # successful sounddevice.play(samples) in the TTS playback
            # worker — closes the gap between "CLAUDE: streaming started"
            # (when we open the HTTP connection) and the actual moment
            # the user hears something. Per-interaction (slot clears
            # after firing once); next interaction re-arms.
            self._tts.arm_first_chunk_callback(
                lambda: dbg.log("TTS: first audible chunk played")
            )

            # Sentence-level TTS streaming (Path A Task 6). Flush complete
            # sentences from the buffer as each .!? boundary arrives, so TTS
            # starts on sentence 1 (~1200ms into Claude stream) instead of
            # after the full response (~3700ms). Saves ~2s perceived latency.
            #
            # Tag-safety: stop flushing the moment '[' appears in the buffer
            # (start of [POINT:x,y:label] tag). On stream close, use
            # result.spoken_text (tag-stripped) to compute + flush the tail.
            sentence_buffer = ""
            tag_started = False
            already_flushed_chars = 0

            with self._ai.ask_stream(
                images=images,
                transcript=user_text,
                history=self._history,
                kb_content=kb_content,
                kb_app_name=kb_app_name,
            ) as stream:
                for delta in stream.text_deltas():
                    if cancel.is_set():
                        return
                    sentence_buffer += delta
                    if "[" in sentence_buffer:
                        tag_started = True
                    if not tag_started:
                        sentences, sentence_buffer = flush_sentences(sentence_buffer)
                        for s in sentences:
                            if cancel.is_set():
                                return
                            self._tts.speak_sentence(s)
                            # +1 for the separator space matched by [.!?]\s
                            already_flushed_chars += len(s) + 1

                result = stream.final_result()

            if cancel.is_set():
                return

            dbg.log(f"CLAUDE: done ({len(result.spoken_text)} chars)")
            dbg.log(f"CLAUDE: spoken_text: {result.spoken_text!r}")
            dbg.log(f"CLAUDE: coordinate={result.coordinate}, label={result.element_label!r}, screen={result.screen_number}")

            # Flush the tail (everything in spoken_text that hasn't yet been
            # sent to TTS). Uses result.spoken_text because it's tag-stripped —
            # avoids ever speaking the [POINT:x,y:label] aloud.
            if result.spoken_text:
                tail = result.spoken_text[already_flushed_chars:].strip()
                if tail:
                    dbg.log(f"TTS: flushing tail ({len(tail)} chars)")
                    self._tts.speak_sentence(tail)

            if cancel.is_set():
                return

            _log(f"Response: {result.spoken_text[:80]}...")

            # v0.2.0: Grid-locator fallback for Ollama / weak vision models.
            # If Claude returned no [POINT:x,y] tag AND we're using Ollama AND
            # the query was directional, run grid-locator on the cursor
            # screenshot to derive coordinates. Returns physical virtual-desktop
            # coords (same convention as unscale_claude_coords output), or None
            # if the locator can't find a target.
            #
            # CANCEL GUARD: skip the locator entirely if cancel fired between
            # stream.final_result() and here (e.g. ESC during sentence
            # streaming). Without this, locator's 2 Ollama calls would run for
            # 5-10s on a cancelled worker and emit pointer + memory side
            # effects for an interaction the user already aborted. Caught by
            # codex adversarial review 2026-06-05 (HIGH 2).
            if cancel.is_set():
                return
            locator_phys_xy = _maybe_locate_via_grid(
                ai_client=self._ai,
                result=result,
                cursor_capture=cursor_capture,
                query=transcript,
                dbg=dbg,
            )
            # POST-LOCATOR cancel guard: if locator just ran (took seconds on
            # Ollama), the user may have hit ESC or pressed Ctrl+Alt+Space
            # again. Stop before emitting any pointer / memory side effects
            # — those would race the new pipeline and write history for an
            # interaction that no longer matters.
            if cancel.is_set():
                return

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
                # THINKING → FLYING: hide spinner BEFORE the point_at signal
                # so the overlay paints cleanly (no flicker of spinner +
                # cursor at the same time during the transition).
                self.sig_hide_spinner.emit()
                self.sig_point_at.emit(phys_x, phys_y, target_capture.monitor)
            elif locator_phys_xy is not None:
                # Grid-locator fallback (Ollama path): coords already in PHYSICAL
                # virtual-desktop space — skip unscale_claude_coords, emit directly.
                phys_x, phys_y = locator_phys_xy
                dbg.log(f"COORDS: grid-locator -> physical=({phys_x},{phys_y})")
                self.sig_hide_spinner.emit()
                self.sig_point_at.emit(phys_x, phys_y, cursor_capture.monitor)
            else:
                dbg.log("COORDS: no coordinate returned (text-only response)")
                # Text-only path: spinner still needs to go away so the buddy
                # returns to follow-cursor mode during TTS playback.
                self.sig_hide_spinner.emit()

            pointer_targets = []
            if result.coordinate:
                pointer_targets.append(result.coordinate)
            elif locator_phys_xy is not None:
                # Memory recording: store the grid-locator coords (physical
                # virtual-desktop space) so future recall can reference them
                # the same way Claude coords are referenced.
                pointer_targets.append(locator_phys_xy)

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
            # Always hide spinner on pipeline exit (success, error, cancel).
            # Prevents a stuck-spinning arc if anything above raises before
            # the normal hide_spinner emit fires.
            self.sig_hide_spinner.emit()
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

    # Path A Task 10 — LISTENING-state slot handlers (run on Qt main thread)

    def _on_show_waveform(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        if self._overlay:
            self._overlay.show_waveform(physical_x, physical_y, monitor)

    def _on_hide_waveform(self) -> None:
        if self._overlay:
            self._overlay.hide_waveform()

    def _on_audio_level(self, level: float) -> None:
        if self._overlay:
            self._overlay.set_audio_level(level)

    # Post-Path-A UX fix — THINKING-state slot handlers (Qt main thread)

    def _on_show_spinner(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        if self._overlay:
            self._overlay.show_spinner(physical_x, physical_y, monitor)

    def _on_hide_spinner(self) -> None:
        if self._overlay:
            self._overlay.hide_spinner()


_T0 = __import__("time").time()


# --- Sprint 3.8: single-instance mutex --------------------------------------
#
# Without this, double-clicking the installed shortcut spawns multiple
# Clicky.exe processes. Each installs its own pynput.Listener (suppress=False
# is observe-only — multiple listeners coexist), so one Ctrl+Alt+Space press
# fires N parallel STT->Claude->TTS pipelines. User hears N overlapping
# voices answering one question.
#
# Pattern: Win32 named mutex acquired before QApplication construction.
# Whoever wins the kernel-level CreateMutexW race holds the mutex for their
# process lifetime; second instance sees ERROR_ALREADY_EXISTS and exits.
# Same pattern Spotify, Slack, Discord, Raycast all use.

_MUTEX_NAME = "Local\\ClickyWindows-SingleInstance-v1"
"""Per-logon-session namespace (Local\\) — admin and non-admin in the same
session see the same mutex (correct), but different Windows users on the
same machine each get their own Clicky (also correct). Global\\ would
block second user on a shared RDP host — wrong for portfolio scope."""

_ERROR_ALREADY_EXISTS = 183  # winerror.h ERROR_ALREADY_EXISTS


def _acquire_single_instance_mutex(kernel32=None):
    """Try to acquire the named mutex. Returns the HANDLE (truthy int) if
    we are the first instance, ``None`` if another Clicky already owns it,
    or the string ``"fail-open"`` on rare CreateMutexW genuine failure (in
    which case caller should proceed with startup — better to risk a
    duplicate than block the user with a broken installer).

    The ``kernel32`` parameter is a DI hook for tests (pass a MagicMock).
    Production passes ``None`` and the function looks up the real
    ``ctypes.windll.kernel32`` itself, applying the explicit ``restype`` /
    ``argtypes`` signatures that prevent x64 HANDLE truncation (without
    them, ctypes defaults to ``c_int`` = 32-bit, which silently corrupts
    64-bit handles on x64 Windows).

    The returned handle MUST be retained for the process lifetime (a
    module-global reference is sufficient). The Windows kernel auto-
    releases the mutex when the process terminates — including on crash
    or Task Manager kill — so no explicit cleanup is needed at shutdown.
    """
    if kernel32 is None:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    # bInitialOwner=False — for single-instance detection we need the
    # kernel object's *existence* as a flag, not ownership/synchronization
    # semantics. Setting True would make first instance pointlessly own a
    # mutex it never releases.
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    # Note: ctypes maps c_void_p NULL to Python None (NOT integer 0). Test
    # mocks use return_value=0 for convenience; both are falsy so `not handle`
    # handles both representations safely.
    if not handle:
        # Genuine CreateMutexW failure (rare). Fail open — don't block startup.
        return "fail-open"
    # GetLastError MUST be the next Win32 call after CreateMutexW; any
    # intervening kernel32 call could clobber the thread-local last-error.
    # The `if not handle` branch above is pure Python — safe.
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        # Another Clicky owns the mutex. Close OUR handle to the same
        # kernel object (the original mutex is still held by the first
        # instance) so we don't leak.
        kernel32.CloseHandle(handle)
        return None
    return handle


# --- Path A Task 11: listening chime (lazy-built + async playback) ----------

_CHIME_SAMPLE_RATE = 44100
_CHIME_SAMPLES = None  # float32 numpy array, built on first play


def _play_chime_async() -> None:
    """Play a short 'mic is hot' chime on hotkey PRESS. Non-blocking.

    Generated in-memory the first time it's called (no asset file to manage,
    no soundfile dep). 60ms, 880Hz (A5), exponential decay envelope.
    ``sounddevice.play()`` returns immediately — audio plays through the
    portaudio output buffer while the rest of the press handler proceeds.

    Errors are swallowed — the chime is UX-only; if sounddevice / the audio
    device is unavailable, we silently skip rather than break the PTT flow.
    """
    global _CHIME_SAMPLES
    try:
        import sounddevice as _sd
        if _CHIME_SAMPLES is None:
            import numpy as _np
            duration_s = 0.060
            freq_hz = 880.0
            t = _np.linspace(0.0, duration_s,
                             int(_CHIME_SAMPLE_RATE * duration_s), endpoint=False)
            envelope = _np.exp(-t * 40.0)
            _CHIME_SAMPLES = (
                _np.sin(2.0 * _np.pi * freq_hz * t) * envelope * 0.3
            ).astype(_np.float32)
        _sd.play(_CHIME_SAMPLES, _CHIME_SAMPLE_RATE)
    except Exception:
        pass


def _log(msg: str) -> None:
    """Print a log line with millisecond-precision elapsed time."""
    import time
    elapsed = (time.time() - _T0) * 1000
    ts = time.strftime("%H:%M:%S")
    print(f"[clicky {ts} +{elapsed:.0f}ms] {msg}", flush=True)


# --- Sprint 4: TTS provider resolution --------------------------------------


def _resolve_tts_credentials() -> tuple[str, str | None]:
    """Resolve (TTS_PROVIDER, api_key_for_that_provider) at startup.

    Reads TTS_PROVIDER via config.resolve_setting (env→keyring→default)
    then resolves the right API key via config.resolve_api_key based on
    the selected provider. Returned to __main__ which dispatches via
    tts.create_tts_client(provider, api_key).
    """
    provider = resolve_setting("TTS_PROVIDER", default="cartesia")
    if provider == "elevenlabs":
        api_key = resolve_api_key("ELEVENLABS_API_KEY")
    else:
        api_key = resolve_api_key("CARTESIA_API_KEY")
    return provider, api_key


def _resolve_llm_credentials() -> tuple[str, str]:
    """Resolve (effective_model_id, api_key) at startup based on LLM_PROVIDER (v0.2.0).

    Reads LLM_PROVIDER via config.resolve_setting (env→keyring→default).
    - LLM_PROVIDER='ollama'    → returns ("ollama/<OLLAMA_MODEL_VISION>", ""),
                                  api_key empty because local Ollama is
                                  unauthenticated. create_ai_client factory
                                  routes `ollama/*` prefix to OllamaClient.
    - LLM_PROVIDER='anthropic' → returns (MODEL_ID, ANTHROPIC_API_KEY).
                                  Factory routes MODEL_ID prefix
                                  ('anthropic/...' or 'claude...') to
                                  AnthropicClient.
    - any other value          → falls back to anthropic path (forward-compat).

    Without this helper the Settings dropdown was cosmetic (caught by codex
    adversarial review 2026-06-05): LLM_PROVIDER='ollama' got persisted to
    keyring but app.py only ever read MODEL_ID, so the user's choice was
    silently ignored and AnthropicClient was always constructed with whatever
    MODEL_ID env var defaulted to.

    Note: MODEL_ID env var takes precedence over LLM_PROVIDER ONLY when
    MODEL_ID already routes to a non-Anthropic prefix (the factory dispatches
    on MODEL_ID prefix first). For the GUI-flow (user clicks Ollama in the
    dropdown), LLM_PROVIDER='ollama' is sufficient — they never need to know
    about MODEL_ID.
    """
    provider = resolve_setting("LLM_PROVIDER", default="anthropic")
    if provider == "ollama":
        # v0.2.1 (Issue #1 fix D): log detected Ollama version + warn
        # about model/version mismatches at startup. Stderr only — the
        # Settings dialog catches this case interactively. This is
        # belt-and-suspenders for users who set OLLAMA_MODEL_VISION via
        # env var and never touch the Settings UI.
        try:
            from ollama_health import (
                check_model_compatibility,
                detect_ollama_version,
            )
            version = detect_ollama_version(OLLAMA_HOST)
            if version is None:
                print(
                    f"[ollama] could not reach {OLLAMA_HOST}/api/version "
                    "— is `ollama serve` running?",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[ollama] detected version {version}, "
                    f"using model {OLLAMA_MODEL_VISION}",
                    file=sys.stderr,
                )
                warning = check_model_compatibility(OLLAMA_MODEL_VISION, version)
                if warning:
                    print(f"[ollama] WARNING: {warning}", file=sys.stderr)
        except Exception as exc:
            # Don't fail startup over a logging helper.
            print(f"[ollama] version-check skipped: {exc}", file=sys.stderr)

        # Construct an ollama/ prefixed model id so create_ai_client routes
        # correctly. api_key is empty (Ollama is unauthenticated local).
        return f"ollama/{OLLAMA_MODEL_VISION}", ""
    # anthropic (default) — preserve existing MODEL_ID + ANTHROPIC_API_KEY path
    return MODEL_ID, ANTHROPIC_API_KEY or ""


# --- Manual entry point -------------------------------------------------------

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    # Single-instance check — MUST run before QApplication construction
    # so a duplicate-launch exits fast without spinning up Qt / SDKs.
    # The handle is assigned to a __main__-module binding to keep it
    # alive for the process lifetime; Windows auto-releases on exit.
    _mutex_handle = _acquire_single_instance_mutex()
    if _mutex_handle is None:
        # Another Clicky is already running. Show a Win32 messagebox
        # (no Qt dependency) telling the user where to look, then exit
        # cleanly. MB_ICONINFORMATION = 0x40.
        ctypes.windll.user32.MessageBoxW(
            None,
            "Clicky Windows is already running.\n\n"
            "Look for the blue cursor icon in your system tray "
            "(bottom-right corner of your screen). Right-click it "
            "for the Settings and Quit menu.",
            "Clicky already running",
            0x40,
        )
        sys.exit(0)
    # _mutex_handle == "fail-open" or a real handle: proceed with startup.

    print("=" * 70)
    print("Clicky Windows — push-to-talk AI buddy")
    print("=" * 70)

    set_dpi_awareness()
    qt_app = QApplication(sys.argv)
    # App-level icon — used by Qt for any window that doesn't set its
    # own (overlay, future dialogs). Belt-and-suspenders alongside
    # clicky.spec's `icon=` (which embeds the icon as a Windows EXE
    # resource for taskbar/Alt-Tab/etc). Path resolved via __file__
    # so it works in both dev and bundled EXE.
    from pathlib import Path as _Path
    from PyQt6.QtGui import QIcon as _QIcon
    _icon_path = _Path(__file__).parent / "assets" / "clicky_tray.ico"
    if _icon_path.is_file():
        qt_app.setWindowIcon(_QIcon(str(_icon_path)))
    # Tray-only mode: closing the overlay (or any internal window)
    # must NOT exit the app — only the Quit menu item should.
    qt_app.setQuitOnLastWindowClosed(False)

    # First-launch / missing-keys flow: show modal until all 3 keys
    # are saved. Modal blocks the QApplication.exec() loop so this
    # is synchronous from main()'s perspective.
    from settings_dialog import SettingsDialog, required_keys_present
    if not required_keys_present():
        print("First-launch setup — showing API key dialog...")
        dlg = SettingsDialog()
        if dlg.exec() != dlg.DialogCode.Accepted:
            print("Setup cancelled by user. Exiting.")
            sys.exit(1)
        # Sanity check — Save was clicked AND all 3 keys are now resolvable.
        if not required_keys_present():
            print(
                "ERROR: Setup completed but at least one API key still "
                "missing. Aborting."
            )
            sys.exit(1)

    # Resolve keys AFTER the modal has run — module-level constants
    # were captured at import time and may not reflect newly-saved
    # values. config.resolve_api_key() always reads fresh.
    api_anthropic = resolve_api_key("ANTHROPIC_API_KEY")
    api_assemblyai = resolve_api_key("ASSEMBLYAI_API_KEY")

    # v0.2.0: resolve effective LLM model + api key based on LLM_PROVIDER
    # setting (Settings dialog dropdown). Reads keyring fresh so any change
    # the user just made in the modal is honored. See _resolve_llm_credentials
    # docstring for the Anthropic vs Ollama dispatch logic.
    _llm_model_id, _llm_api_key = _resolve_llm_credentials()

    # Sprint 4: dispatch TTS subclass based on TTS_PROVIDER setting.
    # Cartesia (default) and ElevenLabs (opt-in) are both supported;
    # user picks via Settings dialog dropdown which writes to keyring
    # under "TTS_PROVIDER" + the provider's key under e.g. "ELEVENLABS_API_KEY".
    tts_provider, tts_api_key = _resolve_tts_credentials()
    if not tts_api_key:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Clicky needs an API key for {tts_provider.title()} TTS.\n\n"
            "Right-click the tray icon → Settings... to set it.",
            f"{tts_provider.title()} key missing",
            0x40,
        )
        sys.exit(1)
    from tts import create_tts_client
    try:
        tts_instance = create_tts_client(provider=tts_provider, api_key=tts_api_key)
    except ValueError as exc:
        # Stale provider_id in keyring (e.g. user downgraded after a future
        # version added a new provider that no longer exists). Show a
        # friendly MessageBox instead of dumping a traceback into the
        # bundled-EXE void. Per Sprint 4 review T2-1.
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Clicky's TTS configuration is invalid: {exc}\n\n"
            "Right-click the tray icon → Settings... to choose a "
            "supported provider.",
            "TTS provider not supported",
            0x40,
        )
        sys.exit(1)

    clicky = ClickyApp(
        # v0.2.0: route LLM_PROVIDER to the right model/client. When user
        # selected "Ollama (local)" in Settings, _llm_model_id is
        # 'ollama/<vision-model>' and _llm_api_key is empty — create_ai_client
        # dispatches to OllamaClient and Anthropic key is ignored.
        ai_client=create_ai_client(
            model_id=_llm_model_id,
            api_key=_llm_api_key,
            ollama_host=OLLAMA_HOST,
        ),
        stt_client=AssemblyAIStreamingSTT(api_key=api_assemblyai),
        tts_client=tts_instance,
    )

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

    # System tray icon — the ONLY clean exit path now that the overlay
    # has WS_EX_TOOLWINDOW (no taskbar entry) and there's no console
    # for Ctrl+C. Right-click tray → Quit triggers a clean shutdown.
    from tray import ClickyTray

    def _quit_via_tray() -> None:
        _log("Quit requested via tray menu — shutting down...")
        clicky.stop()
        qt_app.quit()

    def _show_settings() -> None:
        dlg = SettingsDialog()
        if dlg.exec() == dlg.DialogCode.Accepted:
            _log(
                "Settings saved. Restart Clicky for new keys to take effect."
            )

    # Tray construction can raise RuntimeError if the user's Windows
    # has no system tray available (rare — kiosk mode, custom shells,
    # certain VMs). Show a QMessageBox + exit cleanly rather than
    # leaving an invisible app running with no quit path.
    try:
        tray = ClickyTray(
            on_quit=_quit_via_tray,
            on_settings=_show_settings,
        )
    except RuntimeError as exc:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "Clicky Windows -- Tray Error", str(exc)
        )
        clicky.stop()
        sys.exit(1)

    def _shutdown(*_args):
        _log("Shutting down...")
        clicky.stop()
        qt_app.quit()

    signal.signal(signal.SIGINT, _shutdown)

    _log(f"Model: {MODEL_ID}")
    _log("Listening for Ctrl+Alt+Space... (Ctrl+C to quit)")

    sys.exit(qt_app.exec())
