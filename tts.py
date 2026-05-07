"""Clicky Windows text-to-speech layer.

TTS abstract base + CartesiaSonicTTS concrete implementation using Cartesia's
`sonic-3` model for ~150-250ms TTFB streaming with an expressive "buddy" voice.

This module is the voice output half of the push-to-talk loop. Latency is the
#1 UX priority (see DECISIONS.md "Priority inversion: latency > local-first"
for why Cartesia Sonic-3 was picked over ElevenLabs / Deepgram / pyttsx3).

Responsibility boundary:
- THIS MODULE owns streaming TTS I/O and background playback threads only.
- app.py (Step 7) owns sentence-boundary chunking and will call speak_sentence()
  on each completed sentence while Claude is still generating subsequent tokens.
- No cross-sentence queueing in Phase 1 -- each call is independent.

Threading model:
- speak() / speak_sentence() are non-blocking: they spawn a daemon thread
  that opens the Cartesia stream, iterates chunks as they arrive, and plays
  them via sounddevice. Return within ~10ms.
- stop() sets a flag that in-progress threads check on each chunk.
- Full cancellation (WebSocket close) is Phase 2; Phase 1 is flag-based only.

Top-to-bottom order (so `py -3.13 -m tts` works):
    1. Module docstring
    2. Imports
    3. TTS abstract base class
    4. CartesiaSonicTTS concrete class
    5. __main__ block for manual live-API verification
"""
from __future__ import annotations

import queue
import threading
from abc import ABC, abstractmethod
from typing import Callable

import numpy as np

from config import (
    CARTESIA_MODEL_ID,
    CARTESIA_OUTPUT_SAMPLE_RATE,
    CARTESIA_VOICE_ID,
    ELEVENLABS_MODEL_ID,
    ELEVENLABS_OUTPUT_SAMPLE_RATE,
    ELEVENLABS_VOICE_ID,
)


# Sentinel put into the queues on shutdown to unblock blocking get() calls.
# Using a unique object() beats None because a None sentence is valid no-op input.
_SHUTDOWN_SENTINEL = object()


# --- TTS abstract base -------------------------------------------------------

class TTS(ABC):
    """Abstract base for text-to-speech providers.

    Phase 1: CartesiaSonicTTS (cloud streaming, ~150-250ms TTFB, expressive
    buddy voice). Phase 2 candidates: Pyttsx3TTS (local offline fallback),
    EdgeTTS (free Microsoft Neural), ElevenLabsFlashTTS, DeepgramAura2TTS.
    """

    @abstractmethod
    def speak(self, text: str) -> None:
        """Speak a full response non-blocking.

        Spawns a daemon thread that opens a Cartesia streaming TTS session,
        iterates audio chunks as they arrive, and plays them via sounddevice.
        Returns immediately (~10ms). Empty or whitespace text is a no-op -- no
        thread is spawned.
        """
        ...

    @abstractmethod
    def speak_sentence(self, sentence: str) -> None:
        """Speak a single sentence. Used by app.py for sentence-level chunking.

        As Claude generates response tokens, app.py buffers until a sentence
        boundary (./!/?), then calls speak_sentence() on that chunk while
        Claude continues generating. Each call is independent -- there is no
        cross-sentence queueing in Phase 1, so overlapping calls will produce
        overlapping audio. app.py is responsible for serializing calls.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Interrupt current speech.

        Phase 1 wires the API but only partially implements cancellation
        (Phase 2 Issue #36 feature). Sets a stop flag that newly-spawned
        speak() threads check at startup and on each chunk -- in-progress
        chunks already in the sounddevice buffer still play out.
        """
        ...

    def arm_first_chunk_callback(self, cb: Callable[[], None]) -> None:
        """Arm a one-shot callback that fires when the next audible chunk
        starts playing.

        Used by ``app.py:_pipeline_worker`` to log the first-audible-word
        timestamp per interaction — closes the measurement gap between
        ``CLAUDE: streaming started`` and the moment the user actually
        hears something. Subclasses override only if they need custom
        slot semantics; the default stores the callback on
        ``self._first_chunk_callback`` and the playback loop consumes +
        clears it.
        """
        self._first_chunk_callback = cb


# --- CartesiaSonicTTS concrete implementation --------------------------------

class CartesiaSonicTTS(TTS):
    """Phase 1 TTS using Cartesia Sonic-3 streaming.

    Uses the Cartesia Python SDK's `tts.bytes()` sync iterator over raw PCM
    float32 chunks at 44.1kHz, played via sounddevice. First-audible-word
    target is <400ms from speak() call (measured by the __main__ gate).

    Threading: speak() spawns a daemon thread per call. The thread opens the
    HTTP stream, iterates chunks, writes each chunk to an OutputStream. On
    error, it raises RuntimeError with diagnostic instructions -- the error
    propagates via threading.excepthook since Python has no built-in way to
    rethrow background exceptions.

    No fallback: if Cartesia is unreachable, the app is voiceless. Phase 2
    Pyttsx3TTS subclass is ~1 hour of work if that ever happens.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str = CARTESIA_VOICE_ID,
        model_id: str = CARTESIA_MODEL_ID,
        sample_rate: int = CARTESIA_OUTPUT_SAMPLE_RATE,
        client_factory: Callable | None = None,
        player_factory: Callable | None = None,
    ) -> None:
        """Construct a Cartesia Sonic-3 TTS client.

        Args:
            api_key: Cartesia API key (from .env via config.CARTESIA_API_KEY).
            voice_id: Cartesia voice ID. Defaults to config.CARTESIA_VOICE_ID.
            model_id: Cartesia model ID. Defaults to "sonic-3".
            sample_rate: Output sample rate in Hz. Defaults to 44100.
            client_factory: Optional DI hook returning the Cartesia client.
                Defaults to `cartesia.Cartesia`. Tests inject a MagicMock.
            player_factory: Optional DI hook returning a callable that plays
                a single float32 numpy chunk. Defaults to a sounddevice-based
                player that opens an OutputStream on first use. Tests inject
                a MagicMock.

        No network I/O happens here -- client + player are lazy on first speak.
        """
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.sample_rate = sample_rate
        self._client_factory = client_factory
        self._player_factory = player_factory
        self._cancel_event = threading.Event()
        self._current_thread: threading.Thread | None = None
        self._active_response = None  # Cartesia HTTP response, closed by stop()
        self._active_audio_stream = None  # sounddevice stream, aborted by stop()
        self._first_chunk_callback: Callable[[], None] | None = None  # one-shot, armed by app.py

        # Option B: HTTP double-buffer. _sentence_queue feeds the prefetch
        # worker which calls generate() (blocks for full audio body download,
        # ~200-400ms) and hands (epoch, sentence, response) to the playback
        # worker via _prefetch_queue. Size=1 keeps exactly one sentence warm
        # ahead of the playing one. _epoch is bumped by stop() so stale
        # responses that slipped past the drain are rejected at playback time.
        self._sentence_queue: queue.Queue = queue.Queue()
        self._prefetch_queue: queue.Queue = queue.Queue(maxsize=1)
        self._epoch: int = 0
        self._prefetch_thread = threading.Thread(
            target=self._prefetch_worker,
            name="CartesiaSonicTTS-prefetch",
            daemon=True,
        )
        self._playback_thread = threading.Thread(
            target=self._playback_worker,
            name="CartesiaSonicTTS-playback",
            daemon=True,
        )
        self._prefetch_thread.start()
        self._playback_thread.start()

    def speak(self, text: str) -> None:
        """Stream TTS for the full response text non-blocking. See base class.

        Cancels any in-progress playback before starting new audio.
        Uses a per-invocation threading.Event so old threads stay cancelled
        even if they outlive the join timeout.
        """
        if not text or not text.strip():
            return
        self._cancel_event.set()
        old = self._current_thread
        if old and old.is_alive():
            old.join(timeout=0.5)
        self._cancel_event = threading.Event()
        cancel = self._cancel_event
        self._current_thread = threading.Thread(
            target=self._do_speak,
            args=(text, cancel),
            name=f"CartesiaSonicTTS-speak-{id(text)}",
            daemon=True,
        )
        self._current_thread.start()

    def speak_sentence(self, sentence: str) -> None:
        """Queue a sentence for sequential TTS playback.

        Unlike ``speak()``, this does NOT cancel previous playback. Sentences
        play back-to-back via the internal queue worker. Used by app.py to
        stream Claude's response sentence-by-sentence while later sentences
        are still being generated.

        Empty/whitespace text is a no-op. Thread-safe (``queue.Queue`` is MT-safe).
        """
        if not sentence or not sentence.strip():
            return
        self._sentence_queue.put(sentence)

    def _prefetch_worker(self) -> None:
        """Pops sentences from _sentence_queue, calls generate() (blocks for
        full audio body download), hands (epoch, sentence, response) to
        _playback_worker via _prefetch_queue.

        Blocks on _prefetch_queue.put() when size=1 is full — backpressure
        guarantees we don't prefetch more than one sentence ahead. Epoch
        is captured BEFORE generate() so a concurrent stop() (which bumps
        the epoch) is detectable at playback time, preventing orphaned
        responses from playing after a user-triggered abort.
        """
        while True:
            sentence = self._sentence_queue.get()
            if sentence is _SHUTDOWN_SENTINEL:
                break
            my_epoch = self._epoch
            try:
                response = self._generate_response(sentence)
            except Exception as exc:
                print(f"[tts] prefetch error for {sentence!r}: {exc}", flush=True)
                response = None
            try:
                self._prefetch_queue.put((my_epoch, sentence, response))
            finally:
                self._sentence_queue.task_done()

    def _playback_worker(self) -> None:
        """Pops (epoch, sentence, response) tuples from _prefetch_queue.

        Rejects items with stale epoch (stop() bumped _epoch since prefetch
        queued this) by closing the response without playing. Rejects items
        where response is None (prefetch generate() failed). Otherwise
        assigns a fresh cancel Event and delegates to _play_response.
        """
        while True:
            item = self._prefetch_queue.get()
            if item is _SHUTDOWN_SENTINEL:
                break
            my_epoch, sentence, response = item
            if my_epoch != self._epoch or response is None:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
                continue
            try:
                cancel = threading.Event()
                self._cancel_event = cancel
                self._play_response(sentence, response, cancel)
            except Exception as exc:
                print(f"[tts] playback error for {sentence!r}: {exc}", flush=True)

    def stop(self) -> None:
        """Kill audio playback INSTANTLY + drain both queues.

        Six-pronged kill (Option B extends Path A's drain with epoch guard):
        1. Bump _epoch              — any response prefetched under the old epoch
                                      will be rejected by _playback_worker
        2. Drain _sentence_queue    — pending sentences never start
        3. Drain _prefetch_queue    — prefetched responses closed, not played
        4. Set cancel event         — currently-playing sentence exits its loop
        5. Abort sounddevice stream — stops audio output mid-sample
        6. Close HTTP response      — interrupts active iter_bytes() read

        Race hardening: if prefetch is mid-generate() when stop() fires, its
        eventual put() will carry the OLD epoch. _playback_worker compares
        epochs on each pop and closes-without-playing stale items. This is
        cheaper than joining the prefetch thread + guarantees no audio
        plays after stop() returns.
        """
        # 1. Bump epoch FIRST so any in-flight prefetch becomes stale.
        self._epoch += 1

        # 2. Drain sentence queue so prefetch worker doesn't pull a new one.
        while not self._sentence_queue.empty():
            try:
                self._sentence_queue.get_nowait()
                self._sentence_queue.task_done()
            except queue.Empty:
                break

        # 3. Drain prefetch queue. Any response sitting here was paid for
        #    but never played — close it to release the HTTP connection.
        while not self._prefetch_queue.empty():
            try:
                _, _, pending_response = self._prefetch_queue.get_nowait()
                if pending_response is not None:
                    try:
                        pending_response.close()
                    except Exception:
                        pass
            except queue.Empty:
                break

        # 4-6. Existing abort path for the currently-playing sentence.
        self._cancel_event.set()
        stream = self._active_audio_stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
        resp = self._active_response
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def _generate_response(self, text: str):
        """Call Cartesia TTS HTTP endpoint. Returns a BinaryAPIResponse whose
        body has been fully downloaded into memory (verified: the SDK's
        non-streaming path eagerly reads the full body before returning).

        Blocks ~200-400ms per typical sentence. No playback, no player
        construction — that's _play_response's job. Lets the prefetch worker
        run this call for sentence N+1 while the playback worker is draining
        N's already-buffered audio.
        """
        client = self._build_client()
        return client.tts.generate(
            model_id=self.model_id,
            transcript=text,
            voice={"id": self.voice_id, "mode": "id"},
            output_format={
                "container": "raw",
                "encoding": "pcm_f32le",
                "sample_rate": self.sample_rate,
            },
        )

    def _play_response(self, text: str, response, cancel: threading.Event) -> None:
        """Iterate an already-generated Cartesia response and play chunks.

        Separated from _generate_response so the prefetch worker can hand off
        a warm response to the playback worker. Sets _active_response /
        _active_audio_stream so stop() can abort mid-stream.
        """
        if cancel.is_set():
            return

        import time as _t
        _tts_start = _t.time()
        print(f"[tts] _play_response START: {len(text)} chars", flush=True)
        audio_stream = None
        try:
            self._active_response = response
            chunk_iter = response.iter_bytes()
            play, audio_stream = self._build_player()
            self._active_audio_stream = audio_stream
            for chunk in chunk_iter:
                if cancel.is_set():
                    return
                if not chunk:
                    continue
                samples = np.frombuffer(chunk, dtype=np.float32)
                if samples.size == 0:
                    continue
                play(samples)
                # Fire one-shot first-chunk callback after the first
                # successful play() — this is the moment the user actually
                # hears something. Used by app.py to log first-audible-word
                # latency per interaction. Subsequent sentences in the same
                # interaction don't re-fire (callback slot cleared).
                cb = self._first_chunk_callback
                if cb is not None:
                    self._first_chunk_callback = None
                    try:
                        cb()
                    except Exception:
                        pass  # never let a logging error break audio
        except Exception as exc:
            if cancel.is_set():
                return
            raise RuntimeError(
                "Cartesia Sonic-3 playback failed. Diagnostic checklist:\n"
                "  1. Is CARTESIA_API_KEY set in .env?\n"
                "  2. Is your internet connection up?\n"
                "  3. Is Cartesia up? (https://status.cartesia.ai)\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            self._active_response = None
            self._active_audio_stream = None
            duration_ms = (_t.time() - _tts_start) * 1000
            cancelled = cancel.is_set()
            print(f"[tts] _play_response END: {duration_ms:.0f}ms, cancelled={cancelled}", flush=True)
            if audio_stream is not None:
                try:
                    audio_stream.abort()
                    audio_stream.close()
                except Exception:
                    pass

    def _do_speak(self, text: str, cancel: threading.Event) -> None:
        """One-shot speak path (used by speak(), not speak_sentence()).

        Calls _generate_response then _play_response back-to-back on the
        same thread. Keeps the same RuntimeError diagnostic on generate
        failure that callers rely on (see test_do_speak_error_raises_runtime_error).
        """
        if cancel.is_set():
            return
        try:
            response = self._generate_response(text)
        except Exception as exc:
            if cancel.is_set():
                return
            raise RuntimeError(
                "Cartesia Sonic-3 TTS failed. Diagnostic checklist:\n"
                "  1. Is CARTESIA_API_KEY set in .env? (check https://play.cartesia.ai/)\n"
                "  2. Is your internet connection up?\n"
                "  3. Is Cartesia up? (status page: https://status.cartesia.ai)\n"
                "  4. Reactive fix: subclass TTS as Pyttsx3TTS for an offline\n"
                "     fallback -- ~1 hour of work, see Phase 2 notes.\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc
        self._play_response(text, response, cancel)

    def _build_client(self):
        """Lazily construct the Cartesia client on first use."""
        if self._client_factory is not None:
            return self._client_factory(api_key=self.api_key)
        # Default: real Cartesia SDK. Imported lazily so tests that inject
        # a mock client_factory don't need the real SDK import to succeed.
        from cartesia import Cartesia
        return Cartesia(api_key=self.api_key)

    def _build_player(self):
        """Lazily construct a callable that plays one float32 numpy chunk.

        Returns (play_fn, stream) so the caller can close the stream in a
        finally block. Tests inject player_factory returning (MagicMock, None).
        """
        if self._player_factory is not None:
            return self._player_factory(sample_rate=self.sample_rate)
        import sounddevice as sd

        stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
        )
        stream.start()

        def _play(samples: np.ndarray) -> None:
            stream.write(samples)

        return _play, stream


# --- ElevenLabsTTS (Sprint 4 — opt-in alternative to Cartesia) ---------------


class ElevenLabsTTS(TTS):
    """ElevenLabs Flash v2.5 streaming TTS as an opt-in alternative to
    Cartesia. Mirrors CartesiaSonicTTS Option B prefetch+playback
    architecture with three deliberate divergences:

    1. ``_generate_response`` calls ``client.text_to_speech.stream(...)``
       which returns an ``Iterator[bytes]`` DIRECTLY (true streaming, no
       body pre-fetch). Cartesia's ``generate(...)`` blocks for the full
       body before returning a response with ``.iter_bytes()``.
    2. ``_play_response`` converts each int16 PCM chunk to float32 inline:
       ``samples = np.frombuffer(chunk, np.int16).astype(np.float32) / 32768.0``.
       Cartesia emits float32 directly so no conversion needed.
    3. ``stop()`` is 5-pronged (not 6): no ``response.close()`` — the
       elevenlabs SDK doesn't expose one. Cancellation = break the for
       loop via cancel event. Python GC closes the underlying httpx
       connection. Functionally equivalent kill latency to Cartesia's
       6-pronged stop because the cancel event check fires once per chunk
       and chunks arrive at <50ms intervals.

    Default sample rate is 22050 (NOT 44.1k) because ElevenLabs free tier
    doesn't include 44.1kHz PCM (Pro tier feature). Each TTS subclass owns
    its own sample_rate — sounddevice OutputStream is constructed
    per-instance via ``_build_player``.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str = ELEVENLABS_VOICE_ID,
        model_id: str = ELEVENLABS_MODEL_ID,
        sample_rate: int = ELEVENLABS_OUTPUT_SAMPLE_RATE,
        client_factory: Callable | None = None,
        player_factory: Callable | None = None,
    ) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.sample_rate = sample_rate
        self._client_factory = client_factory
        self._player_factory = player_factory

        self._cancel_event = threading.Event()
        self._current_thread: threading.Thread | None = None
        self._active_audio_stream = None  # sounddevice stream, aborted by stop()
        self._first_chunk_callback: Callable[[], None] | None = None  # one-shot, armed by app.py

        # Option B: prefetch+playback two-thread architecture, mirrors
        # CartesiaSonicTTS verbatim except no _active_response (elevenlabs
        # has no response.close()).
        self._sentence_queue: queue.Queue = queue.Queue()
        self._prefetch_queue: queue.Queue = queue.Queue(maxsize=1)
        self._epoch: int = 0
        self._prefetch_thread = threading.Thread(
            target=self._prefetch_worker,
            name="ElevenLabsTTS-prefetch",
            daemon=True,
        )
        self._playback_thread = threading.Thread(
            target=self._playback_worker,
            name="ElevenLabsTTS-playback",
            daemon=True,
        )
        self._prefetch_thread.start()
        self._playback_thread.start()

    def speak(self, text: str) -> None:
        """One-shot speak path. Cancels any in-progress playback."""
        if not text or not text.strip():
            return
        self._cancel_event.set()
        old = self._current_thread
        if old and old.is_alive():
            old.join(timeout=0.5)
        self._cancel_event = threading.Event()
        cancel = self._cancel_event
        self._current_thread = threading.Thread(
            target=self._do_speak,
            args=(text, cancel),
            name=f"ElevenLabsTTS-speak-{id(text)}",
            daemon=True,
        )
        self._current_thread.start()

    def speak_sentence(self, sentence: str) -> None:
        if not sentence or not sentence.strip():
            return
        self._sentence_queue.put(sentence)

    def _prefetch_worker(self) -> None:
        while True:
            sentence = self._sentence_queue.get()
            if sentence is _SHUTDOWN_SENTINEL:
                break
            my_epoch = self._epoch
            try:
                response = self._generate_response(sentence)
            except Exception as exc:
                print(f"[tts] elevenlabs prefetch error for {sentence!r}: {exc}", flush=True)
                response = None
            try:
                self._prefetch_queue.put((my_epoch, sentence, response))
            finally:
                self._sentence_queue.task_done()

    def _playback_worker(self) -> None:
        while True:
            item = self._prefetch_queue.get()
            if item is _SHUTDOWN_SENTINEL:
                break
            my_epoch, sentence, response = item
            if my_epoch != self._epoch or response is None:
                # Stale or failed — skip without playing. No response.close()
                # to call (elevenlabs SDK iterator has no explicit close).
                continue
            try:
                cancel = threading.Event()
                self._cancel_event = cancel
                self._play_response(sentence, response, cancel)
            except Exception as exc:
                print(f"[tts] elevenlabs playback error for {sentence!r}: {exc}", flush=True)

    def stop(self) -> None:
        """5-pronged kill (no response.close vs Cartesia's 6-pronged):
        1. Bump _epoch — any in-flight prefetch becomes stale at playback time
        2. Drain _sentence_queue — pending sentences never start
        3. Drain _prefetch_queue — prefetched iterators dropped
        4. Set cancel event — currently-playing sentence's loop exits
        5. Abort sounddevice stream — stops audio output mid-sample
        """
        self._epoch += 1

        while not self._sentence_queue.empty():
            try:
                self._sentence_queue.get_nowait()
                self._sentence_queue.task_done()
            except queue.Empty:
                break

        while not self._prefetch_queue.empty():
            try:
                self._prefetch_queue.get_nowait()
                # No response.close — elevenlabs iterator has no explicit close.
                # Python GC will close the underlying httpx connection.
            except queue.Empty:
                break

        self._cancel_event.set()
        stream = self._active_audio_stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass

    def _generate_response(self, text: str):
        """Call ElevenLabs streaming endpoint. Returns Iterator[bytes]
        directly — TRUE streaming, no body pre-fetch.
        """
        client = self._build_client()
        return client.text_to_speech.stream(
            text=text,
            voice_id=self.voice_id,
            model_id=self.model_id,
            output_format=f"pcm_{self.sample_rate}",
        )

    def _play_response(self, text: str, response, cancel: threading.Event) -> None:
        """Iterate the int16 PCM chunk stream, convert to float32 inline,
        play via sounddevice. Sets _active_audio_stream so stop() can abort.
        """
        if cancel.is_set():
            return

        import time as _t
        _tts_start = _t.time()
        print(f"[tts] elevenlabs _play_response START: {len(text)} chars", flush=True)
        audio_stream = None
        try:
            play, audio_stream = self._build_player()
            self._active_audio_stream = audio_stream
            for chunk in response:
                if cancel.is_set():
                    return
                if not chunk:
                    continue
                # int16 → float32 in [-1, 1]
                samples = (
                    np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
                if samples.size == 0:
                    continue
                play(samples)
                # Fire one-shot first-chunk callback (mirrors Cartesia path).
                cb = self._first_chunk_callback
                if cb is not None:
                    self._first_chunk_callback = None
                    try:
                        cb()
                    except Exception:
                        pass  # never let a logging error break audio
        except Exception as exc:
            if cancel.is_set():
                return
            raise RuntimeError(
                "ElevenLabs TTS playback failed. Diagnostic checklist:\n"
                "  1. Is ELEVENLABS_API_KEY set + valid?\n"
                "  2. Is your free-tier quota exhausted? (10k chars/month)\n"
                "  3. Is your internet connection up?\n"
                "  4. Is ElevenLabs up? (https://status.elevenlabs.io)\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            self._active_audio_stream = None
            duration_ms = (_t.time() - _tts_start) * 1000
            cancelled = cancel.is_set()
            print(f"[tts] elevenlabs _play_response END: {duration_ms:.0f}ms, cancelled={cancelled}", flush=True)
            if audio_stream is not None:
                try:
                    audio_stream.abort()
                    audio_stream.close()
                except Exception:
                    pass

    def _do_speak(self, text: str, cancel: threading.Event) -> None:
        """One-shot speak path: get the iterator + play. Used by speak().
        speak_sentence uses the prefetch+playback workers instead.
        """
        if cancel.is_set():
            return
        try:
            response = self._generate_response(text)
        except Exception as exc:
            if cancel.is_set():
                return
            raise RuntimeError(
                "ElevenLabs TTS request failed. Diagnostic checklist:\n"
                "  1. Is ELEVENLABS_API_KEY set in keyring or .env?\n"
                "  2. Is your free-tier quota exhausted?\n"
                "  3. Is your internet connection up?\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc
        self._play_response(text, response, cancel)

    def _build_client(self):
        if self._client_factory is not None:
            return self._client_factory(api_key=self.api_key)
        from elevenlabs import ElevenLabs
        return ElevenLabs(api_key=self.api_key)

    def _build_player(self):
        if self._player_factory is not None:
            return self._player_factory(sample_rate=self.sample_rate)
        import sounddevice as sd

        stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
        )
        stream.start()

        def _play(samples: np.ndarray) -> None:
            stream.write(samples)

        return _play, stream


# --- Factory: route provider string to the right TTS subclass ----------------


def create_tts_client(provider: str, api_key: str) -> TTS:
    """Construct the right TTS subclass based on a provider string.

    Mirrors ai.create_ai_client's factory pattern. Used by app.py main
    block to dispatch on the TTS_PROVIDER constant (resolved from env or
    keyring via config.resolve_setting).

    Args:
        provider: "cartesia" or "elevenlabs". Case-insensitive.
        api_key: provider-specific API key (CARTESIA_API_KEY or
            ELEVENLABS_API_KEY).

    Returns:
        A concrete TTS subclass ready for speak_sentence() / speak() calls.

    Raises:
        ValueError: if provider is not recognized.
    """
    p = provider.lower()
    if p == "cartesia":
        return CartesiaSonicTTS(api_key=api_key)
    if p == "elevenlabs":
        return ElevenLabsTTS(api_key=api_key)
    raise ValueError(
        f"Unsupported TTS provider: {provider!r}. "
        f"Supported: 'cartesia', 'elevenlabs'. To add a new provider, "
        f"subclass TTS in tts.py and extend create_tts_client() with a new branch."
    )


# --- Manual live-API verification entry point --------------------------------

if __name__ == "__main__":
    # Run: py -3.13 -m tts
    # Requires CARTESIA_API_KEY in .env and working speakers.
    import time

    from config import CARTESIA_API_KEY

    if not CARTESIA_API_KEY:
        raise SystemExit(
            "CARTESIA_API_KEY missing from .env. Get one at "
            "https://play.cartesia.ai/sign-in (20k free credits, no credit card)."
        )

    print("=" * 70)
    print("Clicky Windows -- tts.py manual verification (Cartesia Sonic-3)")
    print("=" * 70)

    tts = CartesiaSonicTTS(api_key=CARTESIA_API_KEY)

    test_text = (
        "Hello, I am Clicky Windows. I am your voice AI buddy built on "
        "Cartesia Sonic three."
    )
    print(f"\nSpeaking: {test_text!r}")
    print(f"Voice ID: {tts.voice_id}")
    print(f"Model:    {tts.model_id}")
    print(f"Rate:     {tts.sample_rate} Hz")

    t0 = time.time()
    tts.speak(test_text)
    t_return = time.time()
    print(
        f"\nspeak() returned in {(t_return - t0) * 1000:.0f}ms "
        "(should be <50ms, non-blocking)"
    )

    # Give it time to actually play.
    print("Waiting 10s for playback...")
    time.sleep(10)

    print("\n" + "=" * 70)
    print("Manual verification checklist:")
    print("  1. speak() returned in <50ms (non-blocking)")
    print("  2. Voice is audible and NATURAL-sounding (not robotic)")
    print("  3. First audible word within ~400ms of speak() call")
    print("  4. Full sentence completes without cutouts")
    print("=" * 70)
