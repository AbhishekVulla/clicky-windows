"""Clicky Windows speech-to-text layer.

AssemblyAIStreamingSTT streams to the AssemblyAI Universal-3 realtime-pro
(``u3-rt-pro``) WebSocket using ``StreamingClient`` from
``assemblyai.streaming.v3`` and sends ``force_endpoint()`` on hotkey release.
The streaming WebSocket + ``force_endpoint`` control message gives ~150ms
P50 finalization latency, which is the dominant term in the end-to-end
latency budget.

New providers subclass STT; do not rewrite the protocol.

Responsibility boundary:
- THIS MODULE owns microphone capture + WebSocket lifecycle + transcript
  accumulation. It exposes ``start_recording()`` / ``stop_recording()`` /
  ``on_partial_transcript``.
- ``app.py`` calls ``start_recording()`` on hotkey press and
  ``stop_recording()`` on release. Partial-transcript callbacks must be
  marshalled onto the Qt main thread via ``pyqtSignal`` because event
  handlers fire on the provider's client thread (never call Qt APIs from
  those handlers directly).

Top-to-bottom order (so ``python -m stt`` works):
    1. Module docstring
    2. Imports
    3. Constants
    4. STT abstract base class
    5. AssemblyAIStreamingSTT concrete class
    6. __main__ block for manual live-API verification
"""
from __future__ import annotations

import queue
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

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
    AUDIO_POWER_BOOST,
    AUDIO_SAMPLE_RATE,
    FASTER_WHISPER_COMPUTE,
    FASTER_WHISPER_DEVICE,
    FASTER_WHISPER_MODEL,
)


# --- Constants ---------------------------------------------------------------

_FINAL_TRANSCRIPT_TIMEOUT_S = 2.0

_IDLE_CLOSE_S = 300.0
"""AssemblyAI bills every second a session is open, idle included. Close it
five minutes after the last question: questions come in bursts, and a
follow-up in a warm session skips the 1-3 s open."""

_CONNECT_WAIT_S = 5.0
"""How long a release waits for a session that is still opening."""

_MAX_PENDING_CHUNKS = 500
"""Audio buffered while the session opens (~30 s at 1024 frames / 16 kHz)."""
"""Max time to wait for the post-force_endpoint formatted Turn event before
giving up and returning whatever we've accumulated. 500ms is the target on a
fast network; 2s is the hard ceiling so a flaky connection never hangs the UI
after the user has released the hotkey."""


# --- STT abstract base -------------------------------------------------------

class STT(ABC):
    """Abstract base for speech-to-text providers.

    Do not break this shape: adding a provider should only mean adding a
    subclass, with no changes to app.py.
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

        app.py uses this for optional debug printing. Thread safety: the
        callback runs on the AssemblyAI WebSocket client thread, **not** the
        Qt main thread. Callers that touch Qt must marshal via ``pyqtSignal``.
        """
        ...


# --- Concrete AssemblyAI streaming implementation ---------------------------

class _StreamingSTT(STT):
    """Push-to-talk over a provider's streaming session.

    The microphone opens once at startup and stays open (opening it takes
    400-1000ms on Windows). The session opens on a background thread when the
    key goes down, or at startup with ``warm``; audio spoken while it opens is
    buffered and sent in order once it is up. Release asks the provider to
    finalize and waits for the transcript. An error or a server-side close
    drops the session, so the next press opens a fresh one, and an open
    attempt older than _CONNECT_WAIT_S counts as hung and is abandoned.

    Subclasses supply the provider calls: _connect_socket, _send_audio,
    _request_final, _wait_for_final and _close_session.

    Session rules:
    - Only the current session may change a question's state. Events from a
      session that is still opening or already retired are ignored; one that
      dies while opening is marked dead and never published.
    - A session serves one question at a time. If its final transcript does
      not arrive cleanly (a timeout, or a new press while it finalizes), the
      session is retired, so a late transcript can never land in the next
      question.
    - Reserving an open attempt, publishing a session, starting a question and
      requesting its final transcript all happen under _lock.

    Threading: the mic callback runs on the portaudio thread, provider events
    on the SDK's threads, press and release on the hotkey and worker threads.
    Nothing here touches Qt.
    """

    _provider_name = "speech-to-text"
    _key_name = "API key"
    # True when the provider always confirms a finalize, even for silence, so a
    # missing confirmation means the connection failed rather than "no speech".
    _final_always_confirmed = False

    def __init__(
        self,
        api_key: str,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        chunk_frames: int = AUDIO_CHUNK_FRAMES,
        client_factory: Optional[Callable[..., object]] = None,
        audio_stream_factory: Optional[Callable[..., object]] = None,
        idle_close_s: float = _IDLE_CLOSE_S,
    ) -> None:
        self._api_key = api_key
        self._sample_rate = sample_rate
        self._chunk_frames = chunk_frames
        self._client_factory = client_factory or self._default_client_factory
        self._audio_stream_factory = (
            audio_stream_factory or self._default_audio_stream_factory
        )

        self._client = None
        self._audio_stream = None
        self._connected = False  # the microphone is open
        self._idle_close_s = idle_close_s
        self._lock = threading.Lock()
        self._pending: list[bytes] = []  # audio spoken while the session opens
        self._socket_ready = threading.Event()  # the latest open attempt finished
        self._socket_thread: threading.Thread | None = None
        self._attempt = 0  # id of the newest open attempt; older ones are stale
        self._attempt_started = 0.0
        self._turn = 0  # id of the newest press; a release for an older one is stale
        self._retired: list = []  # sessions we closed or dropped; their events are ignored
        self._dead: list = []  # sessions that closed before they were published
        self._finalizing_on = None  # session owing the current release its final transcript
        self._final_clean = False  # the provider delivered that final transcript
        self._idle_timer: threading.Timer | None = None
        self._recording = False
        self._started = False  # for the legacy start()/stop() pair
        self._partial_cb: Optional[Callable[[str], None]] = None

        # Set by the provider's event thread.
        self._final_transcript = ""
        self._final_event = threading.Event()
        self._latest_partial = ""
        self._stream_error: Exception | None = None
        self._chunk_count = 0

        # Mic chunks before this time are dropped, so the tail of Clicky's own
        # voice after tts.stop() is not transcribed into the next question.
        self._tts_grace_until: float = 0.0

        # Level meter for the waveform widget; runs on every chunk.
        self._audio_level_cb: Callable[[float], None] | None = None
        self._last_audio_level: float = 0.0

    # -- provider hooks -------------------------------------------------------

    @staticmethod
    def _default_client_factory(api_key: str):
        raise NotImplementedError

    def _connect_socket(self):
        """Open a session and return it. Blocks. Raises RuntimeError."""
        raise NotImplementedError

    def _send_audio(self, client, chunk: bytes) -> None:
        """Queue a chunk for the provider. Runs under _lock: must not block."""
        raise NotImplementedError

    def _request_final(self, client) -> None:
        """Tell the provider the user has stopped talking. Called outside
        _lock (the provider may answer on the calling thread); may raise."""
        raise NotImplementedError

    def _wait_for_final(self) -> None:
        """Block until the final transcript is in, or give up."""
        raise NotImplementedError

    def _close_session(self, client) -> None:
        """Close a session. May block; always called on a daemon thread."""
        raise NotImplementedError

    def _reset_turn(self) -> None:
        """Clear per-question provider state at the start of a press. Runs under _lock."""

    def _on_live(self, client) -> None:
        """Called once a session is published as the current one."""

    # -- mic ------------------------------------------------------------------

    def _default_audio_stream_factory(self, callback):
        """``sounddevice.RawInputStream``, imported lazily so the module loads
        on machines without portaudio."""
        import sounddevice as sd

        return sd.RawInputStream(
            samplerate=self._sample_rate,
            blocksize=self._chunk_frames,
            dtype="int16",
            channels=1,
            callback=callback,
        )

    def connect(self, warm: bool = True) -> None:
        """Open the microphone and, with ``warm``, start a session in the
        background so the first question is fast. An unused session closes
        after the idle timeout."""
        if self._connected:
            return
        if not self._api_key:
            raise RuntimeError(f"{self._key_name} missing or empty. Add it in Settings.")

        import time as _t
        _t0 = _t.time()
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
        self._connected = True
        if warm:
            self._ensure_socket()

    # -- session lifecycle ----------------------------------------------------

    @staticmethod
    def _holds(items: list, obj) -> bool:
        return any(x is obj for x in items)

    def _retire_locked(self, client) -> None:
        self._retired.append(client)
        del self._retired[:-8]

    def _attach(self, client, attempt: int) -> bool:
        """Publish ``client`` as the current session if ``attempt`` is still the
        newest and the session is alive, sending the audio buffered while it
        opened, in order. A stale or dead session is closed instead."""
        with self._lock:
            stale = attempt != self._attempt
            dead = self._holds(self._dead, client)
            if not stale and dead:
                self._pending.clear()
                self._stream_error = RuntimeError(
                    f"{self._provider_name} closed the session as it opened. Check your internet connection."
                )
                self._socket_ready.set()
            if not stale and not dead:
                for chunk in self._pending:
                    try:
                        self._send_audio(client, chunk)
                    except Exception as exc:
                        print(f"[stt] sending audio FAILED: {exc}", flush=True)
                self._pending.clear()
                self._client = client
            else:
                self._retire_locked(client)
        if stale or dead:
            self._teardown(client)
            return False
        self._on_live(client)
        self._socket_ready.set()
        if not self._recording:
            self._schedule_idle_close()
        return True

    def _open_socket_in_background(self, attempt: int) -> None:
        try:
            client = self._connect_socket()
        except RuntimeError as exc:
            with self._lock:
                if attempt == self._attempt:
                    self._pending.clear()
                    self._stream_error = exc
                    self._socket_ready.set()
            return
        self._attach(client, attempt)

    def _teardown(self, client) -> None:
        def _close(c):
            try:
                self._close_session(c)
            except Exception:
                pass
        threading.Thread(target=_close, args=(client,), daemon=True, name="stt-teardown").start()

    def _session_lost(self, client, error: Exception | None = None) -> None:
        """A session errored or closed. The current one is dropped and an
        in-flight question fails; one still opening is marked dead so _attach
        rejects it; a retired one is ignored (we closed it ourselves)."""
        with self._lock:
            if self._holds(self._retired, client):
                return
            if client is not self._client:
                self._dead.append(client)
                del self._dead[:-8]
                return
            self._client = None
            self._retire_locked(client)
            if self._recording or self._finalizing_on is client:
                if self._stream_error is None:
                    self._stream_error = error or RuntimeError(
                        f"{self._provider_name} closed the session mid-question. Check your internet connection."
                    )
                self._final_event.set()
        print("[stt] session lost", flush=True)
        self._teardown(client)

    def _schedule_idle_close(self) -> None:
        self._cancel_idle_close()
        timer = threading.Timer(self._idle_close_s, self._close_if_idle)
        timer.daemon = True
        timer.start()
        self._idle_timer = timer

    def _cancel_idle_close(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _close_if_idle(self) -> None:
        # Under the lock, so a press can't pick this session up as it closes.
        with self._lock:
            if self._recording or self._finalizing_on is not None:
                return
            client, self._client = self._client, None
            if client is not None:
                self._retire_locked(client)
        if client is not None:
            print("[stt] closing idle session", flush=True)
            self._teardown(client)

    def _ensure_socket(self) -> None:
        """Start opening a session unless one is open or a recent attempt is
        still opening. An attempt older than _CONNECT_WAIT_S counts as hung."""
        import time as _t
        with self._lock:
            opening = (
                self._socket_thread is not None and self._socket_thread.is_alive()
                and _t.time() - self._attempt_started < _CONNECT_WAIT_S
            )
            if self._client is not None or opening:
                return
            self._attempt += 1
            self._attempt_started = _t.time()
            self._socket_ready.clear()
            self._socket_thread = threading.Thread(
                target=self._open_socket_in_background, args=(self._attempt,),
                daemon=True, name="stt-connect",
            )
            self._socket_thread.start()

    # -- press / release ------------------------------------------------------

    def start_recording(self) -> None:
        """Begin capturing the question. Called on hotkey press."""
        if not self._connected:
            self.connect()
        self._cancel_idle_close()
        unfinished = None
        with self._lock:
            self._turn += 1
            if self._finalizing_on is not None:
                # The last question is still finalizing on this session; its late
                # transcript must not land in this one, so this one gets a new session.
                unfinished, self._finalizing_on = self._finalizing_on, None
                if self._client is unfinished:
                    self._client = None
                self._retire_locked(unfinished)
            self._pending.clear()  # audio left over from a question that failed
            self._final_transcript = ""
            self._latest_partial = ""
            self._final_event.clear()
            self._final_clean = False
            self._stream_error = None
            self._chunk_count = 0
            self._reset_turn()
            self._recording = True
        if unfinished is not None:
            self._teardown(unfinished)
        self._ensure_socket()

    def stop_recording(self) -> str:
        """Finalize the question and return its transcript. Called on release.

        Waits up to _CONNECT_WAIT_S for a session that is still opening (it
        flushes the buffered audio itself), then for the provider's final
        transcript. Raises RuntimeError if the provider reported an error.
        """
        with self._lock:
            if not self._recording:
                return ""
            turn = self._turn
            self._recording = False
            # Claimed now, not after the wait below: a press in between must see
            # this session as unfinished and get a fresh one.
            self._finalizing_on = self._client
            chunks, partial = self._chunk_count, self._latest_partial
        print(f"[stt] stop_recording: {chunks} chunks captured, latest_partial={partial!r}", flush=True)

        ready = self._socket_ready.wait(_CONNECT_WAIT_S)
        with self._lock:
            if self._turn != turn:
                return ""  # a newer press took over while this release waited
            if not ready and self._stream_error is None:
                self._stream_error = RuntimeError(
                    f"{self._provider_name} streaming session did not open in time. Check your internet connection."
                )
                self._pending.clear()
            client = self._client
            if client is None and self._stream_error is None:
                self._stream_error = RuntimeError(
                    f"{self._provider_name} session closed before the question finished. Check your internet connection."
                )
            if self._stream_error is not None:
                self._final_event.set()
                client = None
            else:
                # A final from a pause earlier in the question stays in the
                # transcript but does not answer this release.
                self._final_event.clear()
                self._final_clean = False
                self._finalizing_on = client
        if client is not None:
            try:
                self._request_final(client)
            except Exception as exc:
                with self._lock:
                    if self._turn == turn:
                        if self._stream_error is None:
                            self._stream_error = RuntimeError(f"{self._provider_name} finalize failed: {exc}")
                        self._final_event.set()
                    if self._finalizing_on is client:
                        self._finalizing_on = None
                    if self._client is client:
                        self._client = None
                    already = self._holds(self._retired, client)
                    self._retire_locked(client)
                if not already:
                    self._teardown(client)

        self._wait_for_final()

        stale = None
        with self._lock:
            if self._turn != turn:
                return ""
            finalizing, self._finalizing_on = self._finalizing_on, None
            if finalizing is not None and not self._final_clean and self._client is finalizing:
                # Its final transcript is still owed; never reuse it for another question.
                stale = finalizing
                self._client = None
                self._retire_locked(stale)
            result = (self._final_transcript or self._latest_partial or "").strip()
            stream_error, self._stream_error = self._stream_error, None
            if stale is not None and stream_error is None and self._final_always_confirmed:
                stream_error = RuntimeError(
                    f"{self._provider_name} didn't return the transcript in time. Check your internet connection."
                )
        if stale is not None:
            self._teardown(stale)
        self._schedule_idle_close()

        if stream_error is not None:
            raise stream_error
        return result

    def disconnect(self) -> None:
        """Close mic + session. Called on app shutdown."""
        self._cancel_idle_close()

        if self._audio_stream is not None:
            try:
                self._audio_stream.stop()
                self._audio_stream.close()
            except Exception:
                pass
            self._audio_stream = None

        with self._lock:
            self._recording = False
            self._connected = False
            self._attempt += 1  # an open still in flight must not attach afterwards
            self._finalizing_on = None
            client, self._client = self._client, None
            self._pending.clear()
            if client is not None:
                self._retire_locked(client)
        if client is not None:
            self._teardown(client)

    # -- legacy start()/stop() for the __main__ gate --------------------------

    def start(self) -> None:
        """Legacy: open mic + session synchronously, then start recording."""
        self.connect(warm=False)
        if self._client is None:
            with self._lock:
                self._attempt += 1
                attempt = self._attempt
            self._attach(self._connect_socket(), attempt)
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

    # -- callbacks ------------------------------------------------------------

    def on_partial_transcript(self, callback: Callable[[str], None]) -> None:
        """Store the partial-transcript callback (runs on the provider's thread)."""
        self._partial_cb = callback

    def on_audio_level(self, callback: Callable[[float], None]) -> None:
        """Register a callback fired once per audio chunk with a level in [0, 1].

        Level = RMS x AUDIO_POWER_BOOST, clamped, then decayed (max(raw, last x
        AUDIO_POWER_DECAY)) so the meter doesn't drop sharply at pauses. Runs on
        the portaudio thread: callers that touch Qt must go through a pyqtSignal.
        """
        self._audio_level_cb = callback

    def set_tts_grace_until(self, epoch_ts: float) -> None:
        """Drop mic chunks before ``epoch_ts``. app.py sets it right after
        every tts.stop() so Clicky's own voice isn't transcribed."""
        self._tts_grace_until = epoch_ts

    def _owns_question(self, client) -> bool:
        """True if ``client`` is the current session and a question is live on
        it (recording, or finalizing on it). Call with _lock held."""
        return client is self._client and (self._recording or self._finalizing_on is client)

    def _emit_partial(self, text: str) -> None:
        """Run the partial-transcript callback. _latest_partial is set by the
        caller under _lock, in the same step as its ownership check."""
        if self._partial_cb is not None and text:
            try:
                self._partial_cb(text)
            except Exception:
                pass  # a callback error must never kill the provider's thread

    def _on_audio_chunk(self, indata, frames, time_info, status) -> None:
        """``sounddevice`` callback (portaudio thread): meter every chunk, and
        while the key is held, send the chunk or buffer it until the session is
        up. Must be fast and must not raise."""
        if self._audio_level_cb is not None and indata is not None:
            try:
                import numpy as _np
                from config import AUDIO_POWER_BOOST, AUDIO_POWER_DECAY
                samples = _np.frombuffer(bytes(indata), dtype=_np.int16).astype(_np.float32) / 32768.0
                if samples.size > 0:
                    rms = float(_np.sqrt(_np.mean(samples * samples)))
                    raw_level = min(max(rms * AUDIO_POWER_BOOST, 0.0), 1.0)
                    smoothed = max(raw_level, self._last_audio_level * AUDIO_POWER_DECAY)
                    self._last_audio_level = smoothed
                    try:
                        self._audio_level_cb(smoothed)
                    except Exception:
                        pass
            except Exception:
                pass

        if not self._recording:
            return
        import time as _t
        if _t.time() < self._tts_grace_until:
            return
        chunk = bytes(indata)
        with self._lock:
            if not self._recording:
                return
            client = self._client
            if client is None:
                if len(self._pending) < _MAX_PENDING_CHUNKS:
                    self._pending.append(chunk)
                    self._chunk_count += 1
                return
            self._chunk_count += 1
            # Under the lock, so audio always queues ahead of the finalize
            # request. Both providers' sends only enqueue; they never block.
            try:
                self._send_audio(client, chunk)
            except Exception as exc:
                print(f"[stt] sending audio FAILED: {exc}", flush=True)


class AssemblyAIStreamingSTT(_StreamingSTT):
    """AssemblyAI streaming (``u3-rt-pro``; the server runs its newest
    Universal model). ``force_endpoint`` on release ends the turn without
    waiting for the server's own end-of-turn detection.

    AssemblyAI bills every second a session is open, idle included, which is
    why the session closes after _IDLE_CLOSE_S of quiet.
    """

    _provider_name = "AssemblyAI"
    _key_name = "ASSEMBLYAI_API_KEY"

    def __init__(
        self,
        api_key: str,
        speech_model: str = ASSEMBLYAI_SPEECH_MODEL,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        chunk_frames: int = AUDIO_CHUNK_FRAMES,
        client_factory: Optional[Callable[..., StreamingClient]] = None,
        audio_stream_factory: Optional[Callable[..., object]] = None,
        idle_close_s: float = _IDLE_CLOSE_S,
    ) -> None:
        super().__init__(
            api_key, sample_rate, chunk_frames, client_factory, audio_stream_factory, idle_close_s,
        )
        self._speech_model = speech_model

    @staticmethod
    def _default_client_factory(api_key: str) -> StreamingClient:
        return StreamingClient(StreamingClientOptions(api_key=api_key))

    def _connect_socket(self) -> StreamingClient:
        """Open a streaming session. Blocks ~1-3 s from Asia. Raises RuntimeError."""
        import time as _t
        _t0 = _t.time()
        try:
            client = self._client_factory(self._api_key)
            client.on(StreamingEvents.Turn, self._on_turn)
            client.on(StreamingEvents.Error, self._on_error)
            client.on(StreamingEvents.Termination, self._on_termination)
            # Conservative turn detection: for push-to-talk only force_endpoint
            # on release should end a turn, not a pause mid-sentence.
            client.connect(
                StreamingParameters(
                    sample_rate=self._sample_rate,
                    speech_model=self._speech_model,
                    encoding=Encoding.pcm_s16le,
                    format_turns=False,  # formatting adds 1-2s we don't need
                    end_of_turn_confidence_threshold=0.7,
                    min_turn_silence=800,
                    max_turn_silence=3600,
                )
            )
        except Exception as exc:
            raise RuntimeError(
                "AssemblyAI streaming WebSocket connection failed "
                f"(url={ASSEMBLYAI_STREAMING_URL}, model={self._speech_model}).\n"
                f"Original error: {type(exc).__name__}: {exc}\n"
                "Troubleshooting: check the AssemblyAI key in Settings, "
                "check your internet connection, and check your AssemblyAI "
                "account credits at https://www.assemblyai.com/dashboard"
            ) from exc
        print(f"[stt] session opened in {(_t.time()-_t0)*1000:.0f}ms", flush=True)
        return client

    def _send_audio(self, client, chunk: bytes) -> None:
        client.stream(chunk)

    def _request_final(self, client) -> None:
        client.force_endpoint()

    def _close_session(self, client) -> None:
        # terminate=True: on SDK 0.59 a session that never began otherwise
        # leaves disconnect() blocked on its writer thread forever.
        client.disconnect(terminate=True)

    def _wait_for_final(self) -> None:
        """Wait for the end_of_turn Turn that force_endpoint triggers, then
        100 ms more for a trailing one (a pause mid-hold can split a question
        into two turns). Up to _FINAL_TRANSCRIPT_TIMEOUT_S in total."""
        import time as _t
        deadline = _t.time() + _FINAL_TRANSCRIPT_TIMEOUT_S
        first_event_seen = False
        while _t.time() < deadline:
            self._final_event.wait(timeout=0.3)
            if self._final_event.is_set():
                self._final_event.clear()
                first_event_seen = True
                remaining = deadline - _t.time()
                if remaining > 0.1:
                    self._final_event.wait(timeout=0.1)
                    if not self._final_event.is_set():
                        break
                else:
                    break
            elif first_event_seen:
                break

    def _on_turn(self, client, event: TurnEvent) -> None:
        """Handle a TurnEvent on the SDK's thread."""
        text = getattr(event, "transcript", "") or ""
        end_of_turn = getattr(event, "end_of_turn", False) is True
        partial = None
        with self._lock:
            if not self._owns_question(client):
                return
            if end_of_turn:
                # end_of_turn is the completion signal. A formatted revision of
                # the same turn arrives separately with end_of_turn=false;
                # counting it too would repeat the text.
                if text:
                    self._final_transcript = f"{self._final_transcript} {text}".strip()
                self._final_clean = True
                self._final_event.set()
            else:
                self._latest_partial = text
                partial = text
        print(f"[stt] Turn event: end_of_turn={end_of_turn}, text={text[:80]!r}", flush=True)
        if partial is not None:
            self._emit_partial(partial)

    def _on_error(self, client, error) -> None:
        """Fail the question in flight and drop the session, so the next press
        opens a new one."""
        import sys

        print(f"[stt] AssemblyAI streaming error: {error}", file=sys.stderr)
        self._session_lost(client, RuntimeError(
            f"AssemblyAI streaming error: {error}. "
            "Check the AssemblyAI key, account credits, and network connectivity."
        ))

    def _on_termination(self, client, _event) -> None:
        """The server closed the session (its own timeout, or ours)."""
        self._session_lost(client)


class CartesiaInkSTT(_StreamingSTT):
    """Cartesia ink-2 push-to-talk, on the same key as Clicky's voice.

    About 160 ms from release to transcript and a 0.3 s session open from
    Singapore, against roughly 400 ms and 1-3 s for AssemblyAI, whose servers
    are in the US and EU. Release sends "finalize"; "flush_done" says the
    transcript is complete, so there is no grace wait. Cartesia bills seconds
    of audio sent, so a warm session costs nothing between questions.
    """

    _provider_name = "Cartesia"
    _key_name = "CARTESIA_API_KEY"
    _final_always_confirmed = True  # flush_done answers every finalize, even an empty one

    def __init__(
        self,
        api_key: str,
        model: str = "ink-2",
        sample_rate: int = AUDIO_SAMPLE_RATE,
        chunk_frames: int = AUDIO_CHUNK_FRAMES,
        client_factory: Optional[Callable[..., object]] = None,
        audio_stream_factory: Optional[Callable[..., object]] = None,
        idle_close_s: float = _IDLE_CLOSE_S,
    ) -> None:
        super().__init__(
            api_key, sample_rate, chunk_frames, client_factory, audio_stream_factory, idle_close_s,
        )
        self._model = model
        self._finals: list[str] = []

    @staticmethod
    def _default_client_factory(api_key: str):
        from cartesia import Cartesia
        return Cartesia(api_key=api_key)

    def _connect_socket(self):
        import time as _t
        _t0 = _t.time()
        try:
            client = self._client_factory(self._api_key)
            # max_retries=0: the SDK's own reconnect re-queues binary audio as
            # text; a dropped session is reopened by the next press instead.
            conn = client.stt.manual_finalize.websocket(
                encoding="pcm_s16le", model=self._model, sample_rate=self._sample_rate,
                language="en", max_retries=0,
            ).enter()
        except Exception as exc:
            raise RuntimeError(
                f"Cartesia speech-to-text connection failed (model={self._model}).\n"
                f"Original error: {type(exc).__name__}: {exc}\n"
                "Troubleshooting: check your Cartesia key in Settings and your internet connection."
            ) from exc
        print(f"[stt] session opened in {(_t.time()-_t0)*1000:.0f}ms", flush=True)
        return _CartesiaSession(conn, on_failure=self._on_write_failed)

    def _on_live(self, session) -> None:
        # The reader starts only once the session is published, so a session
        # that dies straight away is dropped instead of kept.
        threading.Thread(
            target=self._read_events, args=(session,), daemon=True, name="stt-cartesia-events",
        ).start()

    def _on_write_failed(self, session) -> None:
        self._session_lost(session, RuntimeError(
            "Cartesia speech-to-text connection failed while sending audio. Check your internet connection."
        ))

    def _read_events(self, session) -> None:
        """Turn Cartesia's events into transcript state; runs until the session
        ends. Only events for the question live on this session count."""
        import json
        while True:
            try:
                raw = session.conn.recv_bytes()
            except Exception:
                if not session.closing:
                    self._session_lost(session, RuntimeError(
                        "Cartesia speech-to-text connection dropped. Check your internet connection."
                    ))
                return
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "transcript":
                text = event.get("text") or ""
                partial = None
                with self._lock:
                    if not self._owns_question(session):
                        continue
                    if event.get("is_final"):
                        self._finals.append(text)
                    else:
                        self._latest_partial = text
                        partial = text
                if partial is not None:
                    self._emit_partial(partial)
            elif kind == "flush_done":
                with self._lock:
                    if session is self._client and self._finalizing_on is session:
                        self._final_transcript = "".join(self._finals).strip()
                        self._final_clean = True
                        self._final_event.set()
            elif kind == "error":
                message = event.get("message") or event.get("error") or str(event)
                self._session_lost(session, RuntimeError(f"Cartesia speech-to-text error: {message}"))
                return
            elif kind == "done":
                if not session.closing:
                    self._session_lost(session)
                return

    def _reset_turn(self) -> None:
        self._finals = []

    def _send_audio(self, session, chunk: bytes) -> None:
        session.send(chunk)

    def _request_final(self, session) -> None:
        if not session.send("finalize"):
            raise RuntimeError("the connection stalled")

    def _wait_for_final(self) -> None:
        self._final_event.wait(_FINAL_TRANSCRIPT_TIMEOUT_S)

    def _close_session(self, session) -> None:
        session.close()


class _CartesiaSession:
    """One Cartesia socket plus the thread that writes to it. The SDK's send
    blocks on the network, so callers only ever enqueue: a stalled connection
    drops audio instead of freezing capture, and a failed write is reported
    through ``on_failure``."""

    def __init__(self, conn, on_failure: Callable[["_CartesiaSession"], None]) -> None:
        self.conn = conn
        self.closing = False
        self.failed = False
        self._on_failure = on_failure
        self._outbox: queue.Queue = queue.Queue(maxsize=_MAX_PENDING_CHUNKS)
        self._writer = threading.Thread(target=self._write, daemon=True, name="stt-cartesia-writer")
        self._writer.start()

    def _write(self) -> None:
        while True:
            item = self._outbox.get()
            if item is None:
                return
            try:
                if isinstance(item, bytes):
                    self.conn.send_raw(item)
                else:
                    self.conn.send(item)
            except Exception:
                self.failed = True
                if not self.closing:
                    self._on_failure(self)
                return

    def send(self, item) -> bool:
        """Queue audio bytes or a control message. False if the session failed,
        is closing, or its queue is full."""
        if self.failed or self.closing:
            return False
        try:
            self._outbox.put_nowait(item)
            return True
        except queue.Full:
            return False

    def close(self) -> None:
        self.closing = True
        # Unsent audio is worthless once closing; dropping it guarantees room
        # for the stop signal however full the queue was.
        while True:
            try:
                self._outbox.get_nowait()
            except queue.Empty:
                break
        for item in ("close", None):
            try:
                self._outbox.put_nowait(item)
            except queue.Full:
                pass
        self._writer.join(2.0)
        try:
            self.conn.close()  # also unblocks a write stuck on the network
        except Exception:
            pass


# --- FasterWhisperSTT (local offline provider) ------------------------------

class FasterWhisperSTT(STT):
    """Local offline STT via faster-whisper (CTranslate2 Whisper).

    Batch transcription adapted to the connect / start_recording /
    stop_recording lifecycle app.py drives (the same public surface
    AssemblyAIStreamingSTT exposes). The mic stays hot after connect();
    start_recording() flips a buffering flag; stop_recording() runs the model
    on the buffered audio and returns the transcript. No network, no API key.

    Heavy deps (faster_whisper / ctranslate2) and sounddevice are
    LAZY-IMPORTED inside connect() / stop_recording() so cloud users who never
    select this provider never load them. Model weights download to the
    Hugging Face cache on first transcription.
    """

    def __init__(
        self,
        model_size: str = FASTER_WHISPER_MODEL,
        device: str = FASTER_WHISPER_DEVICE,
        compute_type: str = FASTER_WHISPER_COMPUTE,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        chunk_frames: int = AUDIO_CHUNK_FRAMES,
        model_factory: Optional[Callable[..., object]] = None,
        audio_stream_factory: Optional[Callable[..., object]] = None,
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._sample_rate = sample_rate
        self._chunk_frames = chunk_frames
        self._model_factory = model_factory
        self._audio_stream_factory = audio_stream_factory
        self._model = None
        self._audio_stream = None
        self._connected = False
        self._recording = False
        self._buffer = bytearray()
        self._partial_cb: Optional[Callable[[str], None]] = None
        self._audio_level_cb: Optional[Callable[[float], None]] = None
        self._tts_grace_until: float = 0.0
        self._chunk_count = 0
        self._latest_partial = ""

    def _default_model_factory(self):
        from faster_whisper import WhisperModel  # lazy import
        return WhisperModel(
            self._model_size, device=self._device, compute_type=self._compute_type
        )

    def _default_audio_stream_factory(self, callback):
        import sounddevice as sd  # lazy import
        return sd.RawInputStream(
            samplerate=self._sample_rate,
            blocksize=self._chunk_frames,
            dtype="int16",
            channels=1,
            callback=callback,
        )

    def connect(self) -> None:
        """Load the model + open the mic once. Idempotent."""
        if self._connected:
            return
        self._model = (self._model_factory or self._default_model_factory)()
        factory = self._audio_stream_factory or self._default_audio_stream_factory
        self._audio_stream = factory(self._on_audio)
        if hasattr(self._audio_stream, "start"):
            self._audio_stream.start()
        self._connected = True

    def _on_audio(self, indata, frames, time_info, status) -> None:
        """Mic callback: emit RMS level always; buffer PCM only while recording
        and past the post-TTS grace window."""
        import numpy as np
        buf = bytes(indata)
        arr = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
        if arr.size and self._audio_level_cb is not None:
            rms = float(np.sqrt(np.mean(arr * arr)))
            try:
                self._audio_level_cb(min(1.0, rms * AUDIO_POWER_BOOST))
            except Exception:
                pass
        if self._recording and time.time() >= self._tts_grace_until:
            self._buffer.extend(buf)
            self._chunk_count += 1

    def start_recording(self) -> None:
        self._buffer = bytearray()
        self._chunk_count = 0
        self._recording = True

    def stop_recording(self) -> str:
        """Stop buffering, transcribe the buffer, return the text."""
        self._recording = False
        if not self._buffer:
            return ""
        import numpy as np
        audio = (
            np.frombuffer(bytes(self._buffer), dtype=np.int16).astype(np.float32)
            / 32768.0
        )
        segments, _info = self._model.transcribe(audio, language="en", beam_size=1)
        text = " ".join(seg.text for seg in segments).strip()
        self._latest_partial = text
        if self._partial_cb is not None:
            try:
                self._partial_cb(text)
            except Exception:
                pass
        return text

    def disconnect(self) -> None:
        if self._audio_stream is not None:
            try:
                self._audio_stream.stop()
                self._audio_stream.close()
            except Exception:
                pass
        self._audio_stream = None
        self._connected = False

    def on_partial_transcript(self, callback: Callable[[str], None]) -> None:
        self._partial_cb = callback

    def on_audio_level(self, callback: Callable[[float], None]) -> None:
        self._audio_level_cb = callback

    def set_tts_grace_until(self, epoch_ts: float) -> None:
        self._tts_grace_until = epoch_ts

    def start(self) -> None:
        self.connect()
        self.start_recording()

    def stop(self) -> str:
        return self.stop_recording()


# --- Factory: route STT_PROVIDER to the right STT subclass -------------------

def create_stt_client(provider: str, api_key: str) -> STT:
    """Construct the right STT subclass based on a provider string.

    Mirrors tts.create_tts_client. app.py dispatches on STT_PROVIDER
    (resolved via config.resolve_setting). faster-whisper is local (no key).
    """
    p = provider.lower()
    if p == "cartesia":
        return CartesiaInkSTT(api_key=api_key)
    if p == "assemblyai":
        return AssemblyAIStreamingSTT(api_key=api_key)
    if p in ("faster-whisper", "local"):
        return FasterWhisperSTT()
    raise ValueError(
        f"Unsupported STT provider: {provider!r}. "
        f"Supported: 'cartesia', 'assemblyai', 'faster-whisper'. To add a new provider, "
        f"subclass STT in stt.py and extend create_stt_client() with a branch."
    )


# --- Manual live-API verification entry point -------------------------------

if __name__ == "__main__":
    # Manual live-API acceptance gate. Run: py -3.13 -m stt
    # Requires ASSEMBLYAI_API_KEY in .env and an audio input device.
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
