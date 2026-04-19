"""Clicky Windows speech-to-text layer.

Phase 1: AssemblyAIStreamingSTT -- cloud streaming via the AssemblyAI
Universal-3 realtime-pro (``u3-rt-pro``) WebSocket using ``StreamingClient``
from ``assemblyai.streaming.v3`` and ``force_endpoint()`` on hotkey release.
The streaming WebSocket + ``force_endpoint`` control message gives ~150ms
P50 finalization latency, which is the dominant term in the Phase 1 end-to-end
budget (see ``DECISIONS.md`` entry "Priority inversion: latency > local-first
(2026-04-11 session 3)").

Phase 2 candidates (subclass STT, do not rewrite the protocol):
- ``FasterWhisperSTT``: offline CT2 Whisper-base for privacy / offline users.
- ``GroqWhisperSTT``: batch cloud (whisper-large-v3) -- simpler, slower.

Responsibility boundary:
- THIS MODULE owns microphone capture + WebSocket lifecycle + transcript
  accumulation. It exposes ``start()`` / ``stop()`` / ``on_partial_transcript``.
- Phase 2 ``app.py`` will call ``start()`` on hotkey press and ``stop()`` on
  release, marshalling partial-transcript callbacks onto the Qt main thread
  via ``pyqtSignal`` because event handlers fire on the AssemblyAI WebSocket
  client thread (never call Qt APIs from those handlers directly).

Top-to-bottom order (so ``python -m stt`` works -- see MEMORY.md feedback note
"feedback_main_block_ordering"):
    1. Module docstring
    2. Imports
    3. Constants
    4. STT abstract base class
    5. AssemblyAIStreamingSTT concrete class
    6. __main__ block for manual live-API verification
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Callable, Optional

import assemblyai as aai
from assemblyai.streaming.v3 import (
    Encoding,
    StreamingClient,
    StreamingClientOptions,
    StreamingEvents,
    StreamingParameters,
    TurnEvent,
)

from config import (
    ASSEMBLYAI_SPEECH_MODEL,
    ASSEMBLYAI_STREAMING_URL,
    AUDIO_CHUNK_FRAMES,
    AUDIO_SAMPLE_RATE,
)


# --- Constants ---------------------------------------------------------------

_FINAL_TRANSCRIPT_TIMEOUT_S = 2.0
"""Max time to wait for the post-force_endpoint formatted Turn event before
giving up and returning whatever we've accumulated. 500ms is the target on a
fast network; 2s is the hard ceiling so a flaky connection never hangs the UI
after the user has released the hotkey."""


# --- STT abstract base -------------------------------------------------------

class STT(ABC):
    """Abstract base for speech-to-text providers.

    Phase 1: :class:`AssemblyAIStreamingSTT` (cloud streaming).
    Phase 2 will add ``FasterWhisperSTT`` (local offline) and
    ``GroqWhisperSTT`` (batch cloud simpler) as subclasses. Do not break
    this shape -- the Phase 2 provider swap is supposed to be a subclass,
    not a refactor (see ``CLAUDE.md`` "Provider abstraction" rule).
    """

    @abstractmethod
    def start(self) -> None:
        """Open the STT session: WebSocket connection + audio input stream.

        Idempotent: calling twice is a no-op on the second call so that
        ``app.py`` can call ``start()`` on every hotkey press without having
        to track per-session state.
        """
        ...

    @abstractmethod
    def stop(self) -> str:
        """Signal end of utterance and return the final transcript string.

        Sends the AssemblyAI ``force_endpoint`` control message (or the
        subclass equivalent), waits for the final formatted-Turn event,
        closes the audio stream + WebSocket, and returns the accumulated
        transcript. Empty string if no speech was detected.

        Must return within ~500ms on a fast network. The hard timeout is
        :data:`_FINAL_TRANSCRIPT_TIMEOUT_S` -- callers never block forever.
        """
        ...

    @abstractmethod
    def on_partial_transcript(self, callback: Callable[[str], None]) -> None:
        """Register a callback fired for each partial transcript update.

        Phase 1 uses this for optional debug printing; Phase 2 wires it to
        a live caption overlay. Thread safety: the callback runs on the
        AssemblyAI WebSocket client thread, **not** the Qt main thread.
        Callers that touch Qt must marshal via ``pyqtSignal``.
        """
        ...


# --- Concrete AssemblyAI streaming implementation ---------------------------

class AssemblyAIStreamingSTT(STT):
    """Phase 1 STT using AssemblyAI Universal-3 realtime-pro streaming.

    Mirrors Clicky's
    ``leanring-buddy/AssemblyAIStreamingTranscriptionProvider.swift:447-451``:
    ``speech_model=u3-rt-pro``, ``sample_rate=16000``, ``encoding=pcm_s16le``,
    ``format_turns=true``. The ``force_endpoint`` control message on hotkey
    release is what gets us ~150ms P50 finalization -- it tells the server
    "user stopped talking, flush the turn now" instead of waiting for the
    natural VAD end-of-turn detector.

    The SDK ``StreamingClient`` runs its WebSocket on a background thread and
    dispatches events (``Begin``, ``Turn``, ``Termination``, ``Error``) via
    the ``on(event, handler)`` API. We accumulate formatted-Turn transcripts
    into ``self._final_transcript`` and signal ``self._final_event`` when a
    Turn arrives with ``turn_is_formatted=True`` after ``force_endpoint``.

    Threading model:
    - ``start()`` / ``stop()`` are called on the Qt main thread (or any
      thread -- ``app.py`` will call from a worker in Phase 2).
    - The AssemblyAI WebSocket worker thread invokes ``_on_turn`` and
      ``_on_error`` callbacks. Those callbacks touch ``self._final_transcript``
      and ``self._final_event`` only -- never Qt, never sounddevice APIs.
    - ``_final_event`` bridges the two threads without a lock: ``stop()``
      waits on the event, the WS thread sets it.
    """

    def __init__(
        self,
        api_key: str,
        speech_model: str = ASSEMBLYAI_SPEECH_MODEL,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        chunk_frames: int = AUDIO_CHUNK_FRAMES,
        client_factory: Optional[Callable[..., StreamingClient]] = None,
        audio_stream_factory: Optional[Callable[..., object]] = None,
    ) -> None:
        """Construct the STT session descriptor (does **not** open the mic).

        Args:
            api_key: AssemblyAI API key (from ``config.ASSEMBLYAI_API_KEY``).
            speech_model: ``u3-rt-pro`` by default -- matches Clicky exactly.
            sample_rate: 16000 Hz PCM16 mono (AssemblyAI u3-rt-pro requirement).
            chunk_frames: 1024 frames per audio callback block -- matches
                Clicky's ``AVAudioEngine.installTap(bufferSize:1024)``.
            client_factory: DI hook. Defaults to building an SDK
                :class:`StreamingClient`. Tests inject a ``MagicMock``.
            audio_stream_factory: DI hook. Defaults to
                :func:`sounddevice.RawInputStream`. Tests inject a ``MagicMock``
                so no real audio device is required.
        """
        self._api_key = api_key
        self._speech_model = speech_model
        self._sample_rate = sample_rate
        self._chunk_frames = chunk_frames
        self._client_factory = client_factory or self._default_client_factory
        self._audio_stream_factory = (
            audio_stream_factory or self._default_audio_stream_factory
        )

        self._client: Optional[StreamingClient] = None
        self._audio_stream = None
        self._connected = False
        self._recording = False
        self._started = False  # backwards compat for old start()/stop()
        self._partial_cb: Optional[Callable[[str], None]] = None

        # Populated by WebSocket-thread event handlers.
        self._final_transcript = ""
        self._final_event = threading.Event()
        self._latest_partial = ""
        self._stream_error: Exception | None = None
        self._chunk_count = 0

        # TTS-to-mic feedback-loop suppression (Path A Task 2).
        # After tts.stop(), app.py sets a 200ms grace window; mic chunks
        # received before self._tts_grace_until are dropped so speaker
        # decay doesn't leak into the next PTT's transcript.
        self._tts_grace_until: float = 0.0

    # -- DI factory defaults --------------------------------------------------

    @staticmethod
    def _default_client_factory(api_key: str) -> StreamingClient:
        """Default :class:`StreamingClient` constructor.

        Kept as a staticmethod so tests can override via the ``client_factory``
        constructor argument without monkey-patching module globals.
        """
        return StreamingClient(StreamingClientOptions(api_key=api_key))

    def _default_audio_stream_factory(self, callback):
        """Default ``sounddevice.RawInputStream`` constructor.

        Imported lazily inside the method so the module can be imported on
        systems without portaudio (CI, headless test runners) without
        importing sounddevice. Tests never exercise this code path because
        they inject ``audio_stream_factory``.
        """
        import sounddevice as sd

        return sd.RawInputStream(
            samplerate=self._sample_rate,
            blocksize=self._chunk_frames,
            dtype="int16",
            channels=1,
            callback=callback,
        )

    # -- Public API -----------------------------------------------------------

    # -- New lifecycle: connect once at startup, record per hotkey press ------

    def connect(self) -> None:
        """Open mic + WebSocket once at startup. Call before any recording.

        After this returns, the mic is hot (capturing audio to /dev/null)
        and the WebSocket is connected. start_recording() just flips a flag.
        """
        if self._connected:
            return
        if not self._api_key:
            raise RuntimeError(
                "ASSEMBLYAI_API_KEY missing or empty -- add it to .env. "
                "Get a key at https://www.assemblyai.com/dashboard/signup "
                "($50 free credits, no credit card)."
            )

        import time as _t
        _t0 = _t.time()

        # 1. Open mic (~400-1000ms on Windows via portaudio/WASAPI).
        try:
            self._audio_stream = self._audio_stream_factory(self._on_audio_chunk)
            self._audio_stream.start()
            print(f"[stt] mic opened in {(_t.time()-_t0)*1000:.0f}ms", flush=True)
        except Exception as exc:
            raise RuntimeError(
                "Microphone input stream failed to open "
                f"(sample_rate={self._sample_rate}, blocksize={self._chunk_frames}).\n"
                f"Original error: {type(exc).__name__}: {exc}\n"
                "Troubleshooting: check that a microphone is connected, "
                "check Windows Settings -> Privacy -> Microphone is enabled "
                "for Python, and check no other app has exclusive mic access."
            ) from exc

        # 2. WebSocket (~800-1200ms on Windows).
        _t1 = _t.time()
        try:
            self._client = self._client_factory(self._api_key)
            self._client.on(StreamingEvents.Turn, self._on_turn)
            self._client.on(StreamingEvents.Error, self._on_error)
            self._client.connect(
                StreamingParameters(
                    sample_rate=self._sample_rate,
                    speech_model=self._speech_model,
                    encoding=Encoding.pcm_s16le,
                    format_turns=False,  # True adds ~1-2s latency for formatting we don't need
                )
            )
            print(f"[stt] WebSocket connected in {(_t.time()-_t1)*1000:.0f}ms", flush=True)
        except Exception as exc:
            try:
                self._audio_stream.stop()
                self._audio_stream.close()
            except Exception:
                pass
            self._audio_stream = None
            raise RuntimeError(
                "AssemblyAI streaming WebSocket connection failed "
                f"(url={ASSEMBLYAI_STREAMING_URL}, model={self._speech_model}).\n"
                f"Original error: {type(exc).__name__}: {exc}\n"
                "Troubleshooting: check ASSEMBLYAI_API_KEY in .env, "
                "check your internet connection, and check your AssemblyAI "
                "account credits at https://www.assemblyai.com/dashboard"
            ) from exc

        print(f"[stt] total connect() time: {(_t.time()-_t0)*1000:.0f}ms", flush=True)
        self._connected = True

    def start_recording(self) -> None:
        """Begin forwarding mic audio to AssemblyAI. Called on hotkey press.

        Takes <1ms because mic + WebSocket are already open from connect().
        Just flips _recording = True so _on_audio_chunk starts forwarding.
        """
        if not self._connected:
            print("[stt] WARNING: not connected, calling connect() now", flush=True)
            self.connect()
        client_alive = self._client is not None
        audio_alive = self._audio_stream is not None
        print(f"[stt] start_recording: client={client_alive}, audio_stream={audio_alive}", flush=True)
        self._final_transcript = ""
        self._latest_partial = ""
        self._final_event.clear()
        self._stream_error = None
        self._chunk_count = 0
        self._recording = True

    def stop_recording(self) -> str:
        """Stop recording, send ForceEndpoint, return final transcript.

        Called on hotkey release. Blocks for up to _FINAL_TRANSCRIPT_TIMEOUT_S
        waiting for AssemblyAI's final formatted Turn. Does NOT close the mic
        or WebSocket — they stay alive for the next press.
        """
        if not self._recording:
            return ""
        self._recording = False
        print(f"[stt] stop_recording: {self._chunk_count} chunks forwarded, latest_partial={self._latest_partial!r}", flush=True)

        try:
            if self._client is not None:
                self._client.force_endpoint()
        except Exception as exc:
            if self._stream_error is None:
                self._stream_error = RuntimeError(f"force_endpoint failed: {exc}")
            self._final_event.set()

        # Wait for Turn events. AssemblyAI often sends a short partial first,
        # then the full transcript 1-2s later. We wait for the LONGEST
        # transcript within the timeout window, not just the first one.
        import time as _t
        deadline = _t.time() + _FINAL_TRANSCRIPT_TIMEOUT_S
        while _t.time() < deadline:
            self._final_event.wait(timeout=0.3)
            if self._final_event.is_set():
                # Got a Turn — but there might be a longer one coming.
                # Reset the event and wait 300ms more for a better result.
                self._final_event.clear()
                remaining = deadline - _t.time()
                if remaining > 0.3:
                    self._final_event.wait(timeout=0.3)
                    if not self._final_event.is_set():
                        break  # 300ms of silence — no more Turns coming
                else:
                    break  # near deadline, take what we have
            else:
                break  # 300ms with no Turn at all

        result = (self._final_transcript or self._latest_partial or "").strip()
        stream_error = self._stream_error
        self._stream_error = None

        if stream_error is not None:
            raise stream_error
        return result

    def disconnect(self) -> None:
        """Close mic + WebSocket. Called on app shutdown."""
        self._recording = False
        self._connected = False

        if self._audio_stream is not None:
            try:
                self._audio_stream.stop()
                self._audio_stream.close()
            except Exception:
                pass
            self._audio_stream = None

        if self._client is not None:
            def _teardown(client):
                try:
                    client.disconnect(terminate=True)
                except Exception:
                    pass
            threading.Thread(
                target=_teardown, args=(self._client,),
                daemon=True, name="stt-teardown",
            ).start()
            self._client = None

    # -- Backwards-compatible start()/stop() for __main__ gate ----------------

    def start(self) -> None:
        """Legacy: connect + start_recording in one call."""
        self.connect()
        self.start_recording()
        self._started = True

    def stop(self) -> str:
        """Legacy: stop_recording + disconnect in one call."""
        if not self._started:
            return ""
        self._started = False
        result = self.stop_recording()
        self.disconnect()
        return result

    def on_partial_transcript(self, callback: Callable[[str], None]) -> None:
        """Store the partial-transcript callback. See base class docstring
        for the thread-safety contract."""
        self._partial_cb = callback

    def set_tts_grace_until(self, epoch_ts: float) -> None:
        """Mic chunks before ``epoch_ts`` are discarded — used after ``tts.stop()``.

        Prevents TTS speaker decay from being transcribed as the next PTT's
        audio. ``app.py`` calls this with ``time.time() + 0.200`` immediately
        after every ``tts.stop()`` call (press and release handlers). A simple
        float assignment — safe to call from any thread.
        """
        self._tts_grace_until = epoch_ts

    # -- Internal callbacks (run on WebSocket client thread) -----------------

    def _on_audio_chunk(self, indata, frames, time_info, status) -> None:
        """``sounddevice`` callback: forward raw PCM bytes to the WebSocket.

        Runs on the portaudio callback thread. Must be fast and must not
        raise. Only forwards audio when _recording is True (hotkey held).
        When _recording is False (idle), chunks are discarded — mic stays hot.

        TTS-to-mic feedback protection (Path A Task 2): chunks received
        before self._tts_grace_until are dropped so speaker decay after
        tts.stop() doesn't leak into the next PTT's transcript.
        """
        if not self._recording:
            return
        # TTS-to-mic grace: app.py sets a 200ms window after every tts.stop().
        import time as _t
        if _t.time() < self._tts_grace_until:
            return
        if self._client is None:
            print("[stt] WARNING: _recording=True but _client is None — audio dropped", flush=True)
            return
        self._chunk_count += 1
        try:
            self._client.stream(bytes(indata))
        except Exception as exc:
            print(f"[stt] client.stream() FAILED: {exc}", flush=True)

    def _on_turn(self, _client, event: TurnEvent) -> None:
        """Handle an incoming :class:`TurnEvent` from the WebSocket."""
        text = getattr(event, "transcript", "") or ""
        is_formatted = bool(getattr(event, "turn_is_formatted", False))
        print(f"[stt] Turn event: formatted={is_formatted}, recording={self._recording}, text={text[:80]!r}", flush=True)

        if getattr(event, "end_of_turn", False) is True or is_formatted:
            # End-of-turn is the only reliable completion signal with
            # format_turns=False (AssemblyAI docs). Keep the is_formatted
            # branch as a backup for any backend that emits both.
            # `is True` guards against MagicMock auto-attributes in tests
            # (MagicMock is truthy but `is True` is False) AND matches the
            # real SDK contract where end_of_turn is a bool.
            if text:
                if self._final_transcript:
                    self._final_transcript = f"{self._final_transcript} {text}".strip()
                else:
                    self._final_transcript = text
            self._final_event.set()
        else:
            self._latest_partial = text
            if self._partial_cb is not None and text:
                try:
                    self._partial_cb(text)
                except Exception:
                    # User callback errors must never crash the WS thread.
                    pass

    def _on_error(self, _client, error) -> None:
        """Handle a :class:`StreamingError`.

        B2 fix: captures the error into ``self._stream_error`` so ``stop()``
        can raise a clear ``RuntimeError`` instead of silently returning an
        empty transcript when e.g. AssemblyAI sends ``{"error": "invalid_api_key"}``.
        Still unblocks ``_final_event`` so ``stop()`` never hangs waiting for
        a transcript that will never arrive. Runs on the WS client thread.
        """
        import sys

        print(f"[stt] AssemblyAI streaming error: {error}", file=sys.stderr)
        # Capture for stop() to surface as a clear RuntimeError instead of
        # silent empty transcript.
        self._stream_error = RuntimeError(
            f"AssemblyAI streaming error: {error}. "
            "Check ASSEMBLYAI_API_KEY validity, account credits, and network connectivity."
        )
        # Unblock stop() if it's waiting on the final event.
        self._final_event.set()


# --- Manual live-API verification entry point -------------------------------

if __name__ == "__main__":
    # Manual live-API acceptance gate. Run: py -3.13 -m stt
    # Requires ASSEMBLYAI_API_KEY in .env and an audio input device.
    import time

    from config import ASSEMBLYAI_API_KEY

    if not ASSEMBLYAI_API_KEY:
        raise SystemExit(
            "ASSEMBLYAI_API_KEY missing from .env. Get a key at "
            "https://www.assemblyai.com/dashboard/signup"
        )

    print("=" * 70)
    print("Clicky Windows -- stt.py manual verification")
    print("=" * 70)
    print(
        "\nOpen your mic, then press Enter. Speak for 5 seconds. "
        "Release (type anything + Enter) to stop."
    )
    input("Press Enter when ready to start recording...")

    stt = AssemblyAIStreamingSTT(api_key=ASSEMBLYAI_API_KEY)

    def _print_partial(text: str) -> None:
        print(f"  [partial] {text}")

    stt.on_partial_transcript(_print_partial)

    t_start = time.time()
    stt.start()
    print("  Recording... (speak your question)")
    input("  Press Enter to stop recording...")
    t_stop_signal = time.time()

    final = stt.stop()
    t_final = time.time()

    print(f"\nFinal transcript: {final!r}")
    print(f"Recording duration: {t_stop_signal - t_start:.2f}s")
    print(
        f"Finalization latency: {(t_final - t_stop_signal) * 1000:.0f}ms "
        "(target <500ms)"
    )

    print("\nManual verification checklist:")
    print("  1. Partials printed during speech (at least 1)")
    print("  2. Final transcript matches what you said")
    print("  3. Finalization latency is under 500ms (target <200ms)")
