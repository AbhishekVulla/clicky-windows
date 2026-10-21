"""Clicky Windows text-to-speech layer.

TTS abstract base + CartesiaSonicTTS concrete implementation using Cartesia's
`sonic-3` model for ~150-250ms TTFB streaming with an expressive conversational
voice.

This module is the voice output half of the push-to-talk loop. Latency is the
#1 UX priority, which is why Cartesia Sonic-3 is the default over ElevenLabs /
Deepgram / pyttsx3.

Responsibility boundary:
- THIS MODULE owns streaming TTS I/O and background playback threads only.
- app.py owns sentence-boundary chunking and calls speak_sentence() on each
  completed sentence while Claude is still generating subsequent tokens.

Threading model:
- speak() / speak_sentence() are non-blocking and return within ~10ms. Audio
  streams from the provider and plays via sounddevice on background threads.
- stop() sets a flag that in-progress threads check on each chunk.

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
    KOKORO_OUTPUT_SAMPLE_RATE,
    KOKORO_VOICE,
)


# Sentinel put into the queues on shutdown to unblock blocking get() calls.
# Using a unique object() beats None because a None sentence is valid no-op input.
_SHUTDOWN_SENTINEL = object()


# --- TTS abstract base -------------------------------------------------------

class TTS(ABC):
    """Abstract base for text-to-speech providers.

    CartesiaSonicTTS is the default (cloud streaming, ~150-250ms TTFB,
    expressive conversational voice).
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
        Claude continues generating.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Interrupt current speech.

        Sets a stop flag that speak() threads check at startup and on each
        chunk.
        """
        ...

    def warm(self) -> None:
        """Open the provider connection ahead of the first sentence. Never raises."""

    _error_callback: Callable[[BaseException], None] | None = None

    def on_error(self, callback: Callable[[BaseException], None]) -> None:
        """Register a callback for failures inside the background workers."""
        self._error_callback = callback

    def _report_error(self, exc: BaseException) -> None:
        cb = self._error_callback
        if cb is not None:
            try:
                cb(exc)
            except Exception:
                pass

    def arm_first_chunk_callback(self, cb: Callable[[], None]) -> None:
        """Arm a one-shot callback that fires when the next audible chunk
        starts playing.

        Used by ``app.py:_pipeline_worker`` to log the first-audible-word
        timestamp per interaction. That closes the measurement gap between
        ``LLM: streaming started`` and the moment the user actually
        hears something. Subclasses override only if they need custom
        slot semantics; the default stores the callback on
        ``self._first_chunk_callback`` and the playback loop consumes +
        clears it.
        """
        self._first_chunk_callback = cb

    def warmup(self) -> None:
        """Optionally pre-load a heavy local model so the first speak() isn't
        slow. Default no-op (cloud providers have nothing to pre-load). Local
        providers like KokoroTTS override this."""
        return None


# --- One audio stream per voice --------------------------------------------------

class _OutputStream:
    """One audio output stream per voice, kept open between sentences.

    Opening a Windows audio device takes 0.4-0.9 s; restarting a stream that
    is already open takes about 75 ms. stop() aborts the stream, dropping any
    buffered audio at once, and the next press restarts it.

    Writes, restarts and closes take turns under _io_lock. A write checks its
    sentence's cancel flag and restarts the stream inside _state_lock, the
    lock abort() takes after stop() sets that flag, so a cancelled sentence
    can never restart a stream stop() just silenced. abort() never waits on a
    write in progress.
    """

    _ABORT_WAIT_S = 1.0  # longer means a restart or close is stuck in the driver

    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._stream = None
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()

    def acquire(self):
        """The stream, running: opened on first use, restarted after an abort."""
        with self._io_lock:
            return self._ready(None)

    def write(self, samples: np.ndarray, cancel: threading.Event) -> None:
        """Play samples unless their sentence was cancelled."""
        with self._io_lock:
            stream = self._ready(cancel)
            if stream is not None:
                try:
                    stream.write(samples)
                finally:
                    self._cut_if_interrupted(cancel)

    def finish(self, cancel: threading.Event) -> None:
        """End a sentence with a little silence. MME plays a device buffer only
        once it is full, so without it the last few milliseconds of every
        answer would never be heard."""
        with self._io_lock:
            if self._stream is None:
                return
            stream = self._ready(cancel)
            if stream is not None:
                pad = max(int(stream.latency * self._sample_rate) + 1, 2048)
                try:
                    stream.write(np.zeros(pad, dtype=np.float32))
                finally:
                    self._cut_if_interrupted(cancel)

    def abort(self) -> None:
        """Silence the stream now, dropping buffered audio. Never raises."""
        if not self._state_lock.acquire(timeout=self._ABORT_WAIT_S):
            return  # a restart or close is stuck; neither leaves audio playing
        try:
            stream = self._stream
            if stream is not None and not stream.stopped:
                stream.abort()
        except Exception:
            pass
        finally:
            self._state_lock.release()

    def drop(self) -> None:
        """Close the stream after a device error; the next sentence opens it again."""
        with self._io_lock:
            self._close()

    def _ready(self, cancel: threading.Event | None):
        """The running stream, or None if `cancel` is set. Hold _io_lock."""
        opened = None
        if self._stream is None:
            import sounddevice as sd

            opened = sd.OutputStream(
                samplerate=self._sample_rate, channels=1, dtype="float32",
            )
            try:
                opened.start()
            except Exception:
                opened.close()
                raise
        with self._state_lock:
            if opened is not None:
                self._stream = opened
            if cancel is not None and cancel.is_set():
                return None
            if self._stream.stopped:
                self._stream.start()
                if cancel is not None and cancel.is_set():
                    return None  # stop() gave up waiting while the driver restarted
            return self._stream

    def _cut_if_interrupted(self, cancel: threading.Event) -> None:
        """Close the device if stop() landed during the write just finished:
        after an abort, MME keeps queuing the rest of that chunk and only a
        close drops it. The next press reopens the stream while the user talks.
        Hold _io_lock."""
        if cancel.is_set():
            self._close()

    def _close(self) -> None:
        """Hold _io_lock."""
        with self._state_lock:
            stream, self._stream = self._stream, None
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass


# --- CartesiaSonicTTS concrete implementation --------------------------------

class CartesiaSonicTTS(TTS):
    """Cartesia Sonic TTS.

    Each sentence is one streaming HTTP request on a reused client; raw
    float32 PCM plays through sounddevice chunk by chunk as it arrives.
    speak_sentence() feeds a prefetch worker and a playback worker so the
    next sentence is already streaming while the current one plays. speak()
    is the one-shot path on its own daemon thread; its errors surface through
    threading.excepthook.
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
                a single float32 numpy chunk. Defaults to one sounddevice
                stream kept open across sentences. Tests inject a MagicMock.

        No network I/O happens here -- client + player are lazy on first speak.
        """
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.sample_rate = sample_rate
        self._client_factory = client_factory
        self._player_factory = player_factory
        self._output = _OutputStream(sample_rate)
        self._client = None  # built once; every sentence reuses its connection
        self._http = None  # the client's httpx pool, warmed on hotkey press
        self._cancel_event = threading.Event()
        self._current_thread: threading.Thread | None = None
        self._active_response = None  # Cartesia HTTP response, closed by stop()
        self._active_audio_stream = None  # sounddevice stream, aborted by stop()
        self._first_chunk_callback: Callable[[], None] | None = None  # one-shot, armed by app.py

        # Double buffer: the prefetch worker opens sentence N+1's audio stream
        # while N plays, and hands (epoch, sentence, response) to the playback
        # worker through _prefetch_queue. Size 1 keeps exactly one sentence
        # warm. stop() bumps _epoch so a response that slipped past the drain
        # is rejected at playback time.
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
        self._sentence_queue.put((self._epoch, sentence))

    def _prefetch_worker(self) -> None:
        """Pops sentences from _sentence_queue, calls _generate_response()
        (returns once headers arrive), hands (epoch, sentence, response) to
        _playback_worker via _prefetch_queue.

        Blocks on _prefetch_queue.put() when size=1 is full; backpressure
        guarantees we don't prefetch more than one sentence ahead. Each
        sentence carries the epoch it was queued under, so a stop() (which
        bumps the epoch) rejects it at playback time even if it had already
        left the queue.
        """
        while True:
            item = self._sentence_queue.get()
            if item is _SHUTDOWN_SENTINEL:
                break
            my_epoch, sentence = item
            try:
                response = self._generate_response(sentence)
            except Exception as exc:
                print(f"[tts] prefetch error for {sentence!r}: {exc}", flush=True)
                self._report_error(exc)
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
                if my_epoch != self._epoch:
                    cancel.set()  # stop() landed while this sentence was handed over
                self._play_response(sentence, response, cancel)
            except Exception as exc:
                print(f"[tts] playback error for {sentence!r}: {exc}", flush=True)
                self._report_error(exc)

    def stop(self) -> None:
        """Kill audio playback INSTANTLY + drain both queues.

        Six-pronged kill:
        1. Bump _epoch: any response prefetched under the old epoch will be
           rejected by _playback_worker
        2. Drain _sentence_queue: pending sentences never start
        3. Drain _prefetch_queue: prefetched responses closed, not played
        4. Set cancel event: currently-playing sentence exits its loop
        5. Abort sounddevice stream: stops audio output mid-sample
        6. Close HTTP response: interrupts active iter_bytes() read

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
        #    but never played; close it to release the HTTP connection.
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

        # 4-6. Abort the currently-playing sentence.
        self._cancel_event.set()
        stream = self._active_audio_stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
        self._output.abort()  # audio buffered after the last sentence stops too
        resp = self._active_response
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def _generate_response(self, text: str):
        """Open a streaming Cartesia request and return the live response.

        Returns once headers arrive; audio keeps arriving while the sentence
        is still being synthesized, and _play_response plays each chunk as it
        lands. The caller owns the response and must close() it.
        """
        client = self._build_client()
        return client.tts.with_streaming_response.generate(
            model_id=self.model_id,
            transcript=text,
            voice={"id": self.voice_id, "mode": "id"},
            output_format={
                "container": "raw",
                "encoding": "pcm_f32le",
                "sample_rate": self.sample_rate,
            },
        ).__enter__()

    def _play_response(self, text: str, response, cancel: threading.Event) -> None:
        """Iterate an already-generated Cartesia response and play chunks.

        Separated from _generate_response so the prefetch worker can hand off
        a warm response to the playback worker. Sets _active_response /
        _active_audio_stream so stop() can abort mid-stream.
        """
        import time as _t
        _tts_start = _t.time()
        audio_stream = None
        try:
            if cancel.is_set():
                return  # the finally still releases the connection
            print(f"[tts] _play_response START: {len(text)} chars", flush=True)
            self._active_response = response
            chunk_iter = response.iter_bytes()
            play, audio_stream = self._build_player(cancel)
            self._active_audio_stream = audio_stream
            # Network chunks split at arbitrary bytes; carry the tail of a
            # partial float32 sample into the next chunk.
            leftover = b""
            for chunk in chunk_iter:
                if cancel.is_set():
                    return
                if not chunk:
                    continue
                chunk = leftover + chunk
                usable = len(chunk) - len(chunk) % 4
                leftover = chunk[usable:]
                samples = np.frombuffer(chunk[:usable], dtype=np.float32)
                if samples.size == 0:
                    continue
                play(samples)
                if cancel.is_set():
                    return  # a refused write: the sentence was interrupted
                # Fire one-shot first-chunk callback after the first
                # successful play(): this is the moment the user actually
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
            if self._player_factory is None:
                self._output.finish(cancel)
        except Exception as exc:
            if cancel.is_set():
                return
            self._output.drop()
            raise RuntimeError(
                "Cartesia Sonic-3 playback failed. Diagnostic checklist:\n"
                "  1. Is CARTESIA_API_KEY set in .env?\n"
                "  2. Is your internet connection up?\n"
                "  3. Is Cartesia up? (https://status.cartesia.ai)\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            try:
                response.close()
            except Exception:
                pass
            self._active_response = None
            self._active_audio_stream = None
            duration_ms = (_t.time() - _tts_start) * 1000
            cancelled = cancel.is_set()
            print(f"[tts] _play_response END: {duration_ms:.0f}ms, cancelled={cancelled}", flush=True)

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
                "  1. Is the Cartesia key in Settings correct? (check https://play.cartesia.ai/)\n"
                "  2. Is your internet connection up?\n"
                "  3. Is Cartesia up? (status page: https://status.cartesia.ai)\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc
        self._play_response(text, response, cancel)

    def _build_client(self):
        """Build the Cartesia client on first use, then reuse it so every
        sentence rides the same keep-alive connection (no TLS per sentence)."""
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory(api_key=self.api_key)
            else:
                from cartesia import Cartesia, DefaultHttpxClient
                self._http = DefaultHttpxClient()
                self._client = Cartesia(api_key=self.api_key, http_client=self._http)
        return self._client

    def warm(self) -> None:
        """Open the Cartesia connection and restart the audio stream while the
        user is still talking, so the first sentence skips both. Never raises."""
        self._warm_output()
        try:
            client = self._build_client()
            if self._http is not None:
                self._http.head(str(client.base_url), timeout=5)
        except Exception:
            pass

    def warmup(self) -> None:
        """Open the audio stream at startup, so the first answer skips it."""
        self._warm_output()

    def _warm_output(self) -> None:
        if self._player_factory is None:
            try:
                self._output.acquire()
            except Exception:
                pass

    def _build_player(self, cancel: threading.Event):
        """Return (play_fn, stream). The voice's own stream is reached only
        through _output, so stop() never races a close, and a write for a
        cancelled sentence is refused. Tests inject player_factory returning
        (MagicMock, None)."""
        if self._player_factory is not None:
            return self._player_factory(sample_rate=self.sample_rate)
        return (lambda samples: self._output.write(samples, cancel)), None


# --- ElevenLabsTTS (opt-in alternative to Cartesia) --------------------------


class ElevenLabsTTS(TTS):
    """ElevenLabs Flash v2.5 streaming TTS as an opt-in alternative to
    Cartesia. Mirrors CartesiaSonicTTS's prefetch+playback architecture
    with three deliberate divergences:

    1. ``_generate_response`` calls ``client.text_to_speech.stream(...)``
       which returns an ``Iterator[bytes]`` DIRECTLY. Cartesia returns a
       response object read with ``.iter_bytes()``.
    2. ``_play_response`` converts each int16 PCM chunk to float32 inline:
       ``samples = np.frombuffer(chunk, np.int16).astype(np.float32) / 32768.0``.
       Cartesia emits float32 directly so no conversion needed.
    3. ``stop()`` is 5-pronged (not 6): there is no ``response.close()``
       because the elevenlabs SDK doesn't expose one. Cancellation = break
       the for loop via cancel event. Python GC closes the underlying httpx
       connection. Functionally equivalent kill latency to Cartesia's
       6-pronged stop because the cancel event check fires once per chunk
       and chunks arrive at <50ms intervals.

    Default sample rate is 22050 (NOT 44.1k) because ElevenLabs free tier
    doesn't include 44.1kHz PCM (Pro tier feature). Each TTS subclass owns
    its own sample_rate; the sounddevice OutputStream is constructed
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
        self._output = _OutputStream(sample_rate)

        self._cancel_event = threading.Event()
        self._current_thread: threading.Thread | None = None
        self._active_audio_stream = None  # sounddevice stream, aborted by stop()
        self._first_chunk_callback: Callable[[], None] | None = None  # one-shot, armed by app.py

        # Prefetch+playback two-thread architecture, mirrors
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
        self._sentence_queue.put((self._epoch, sentence))

    def _prefetch_worker(self) -> None:
        while True:
            item = self._sentence_queue.get()
            if item is _SHUTDOWN_SENTINEL:
                break
            my_epoch, sentence = item
            try:
                response = self._generate_response(sentence)
            except Exception as exc:
                print(f"[tts] elevenlabs prefetch error for {sentence!r}: {exc}", flush=True)
                self._report_error(exc)
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
                # Stale or failed: skip without playing. No response.close()
                # to call (elevenlabs SDK iterator has no explicit close).
                continue
            try:
                cancel = threading.Event()
                self._cancel_event = cancel
                if my_epoch != self._epoch:
                    cancel.set()  # stop() landed while this sentence was handed over
                self._play_response(sentence, response, cancel)
            except Exception as exc:
                print(f"[tts] elevenlabs playback error for {sentence!r}: {exc}", flush=True)
                self._report_error(exc)

    def stop(self) -> None:
        """5-pronged kill (no response.close vs Cartesia's 6-pronged):
        1. Bump _epoch: any in-flight prefetch becomes stale at playback time
        2. Drain _sentence_queue: pending sentences never start
        3. Drain _prefetch_queue: prefetched iterators dropped
        4. Set cancel event: currently-playing sentence's loop exits
        5. Abort sounddevice stream: stops audio output mid-sample
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
                # No response.close: the elevenlabs iterator has no explicit close.
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
        self._output.abort()  # audio buffered after the last sentence stops too

    def _generate_response(self, text: str):
        """Call ElevenLabs streaming endpoint. Returns Iterator[bytes]
        directly (true streaming, no body pre-fetch).
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
            play, audio_stream = self._build_player(cancel)
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
                if cancel.is_set():
                    return  # a refused write: the sentence was interrupted
                # Fire one-shot first-chunk callback (mirrors Cartesia path).
                cb = self._first_chunk_callback
                if cb is not None:
                    self._first_chunk_callback = None
                    try:
                        cb()
                    except Exception:
                        pass  # never let a logging error break audio
            if self._player_factory is None:
                self._output.finish(cancel)
        except Exception as exc:
            if cancel.is_set():
                return
            self._output.drop()
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

    def warm(self) -> None:
        """Restart the audio stream while the user is still talking."""
        self.warmup()

    def warmup(self) -> None:
        """Open the audio stream ahead of the first sentence. Never raises."""
        if self._player_factory is None:
            try:
                self._output.acquire()
            except Exception:
                pass

    def _build_player(self, cancel: threading.Event):
        if self._player_factory is not None:
            return self._player_factory(sample_rate=self.sample_rate)
        return (lambda samples: self._output.write(samples, cancel)), None


# --- KokoroTTS (local offline TTS, opt-in) -----------------------------------


def ensure_kokoro_models() -> tuple[str, str]:
    """Download the Kokoro ONNX model + voices file on first use.

    Returns (onnx_path, voices_path). No-op if already cached. ~336MB total on
    the first call only; cached under config.KOKORO_CACHE_DIR thereafter.
    """
    import urllib.request

    from config import KOKORO_CACHE_DIR

    KOKORO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
        "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
    }
    paths: dict[str, str] = {}
    for name, url in files.items():
        dest = KOKORO_CACHE_DIR / name
        if not dest.exists():
            print(f"[tts] downloading Kokoro model {name} (one-time)...", flush=True)
            urllib.request.urlretrieve(url, dest)
        paths[name] = str(dest)
    return paths["kokoro-v1.0.onnx"], paths["voices-v1.0.bin"]


class KokoroTTS(CartesiaSonicTTS):
    """Local offline TTS via Kokoro-82M (ONNX runtime, no torch, no API key).

    Reuses CartesiaSonicTTS's two-thread prefetch/playback machinery verbatim;
    only response generation + playback differ. kokoro_onnx + onnxruntime are
    LAZY-IMPORTED so cloud users never load them. Model files download on first
    use (see ensure_kokoro_models). Kokoro emits 24kHz float32 audio.
    """

    def __init__(
        self,
        api_key: str = "",
        sample_rate: int = KOKORO_OUTPUT_SAMPLE_RATE,
        voice: str = KOKORO_VOICE,
        kokoro_factory: Callable | None = None,
        player_factory: Callable | None = None,
    ) -> None:
        super().__init__(
            api_key="", sample_rate=sample_rate, player_factory=player_factory
        )
        self._voice = voice
        self._kokoro_factory = kokoro_factory
        self._kokoro = None

    def _ensure_kokoro(self):
        if self._kokoro is None:
            if self._kokoro_factory is not None:
                self._kokoro = self._kokoro_factory()
            else:
                from kokoro_onnx import Kokoro  # lazy import

                onnx_path, voices_path = ensure_kokoro_models()
                self._kokoro = Kokoro(onnx_path, voices_path)
        return self._kokoro

    def warmup(self) -> None:
        """Pre-load the Kokoro model and open the audio stream, so the first
        reply isn't blocked by either. app.py calls this in a background thread
        at startup."""
        self._warm_output()
        self._ensure_kokoro()

    def _generate_response(self, text: str):
        """Synthesize the whole sentence to a float32 numpy array. Stands in for
        Cartesia's HTTP response in the prefetch worker; _play_response plays it."""
        kokoro = self._ensure_kokoro()
        samples, _sr = kokoro.create(text, voice=self._voice, speed=1.0, lang="en-us")
        return np.asarray(samples, dtype=np.float32)

    def _play_response(self, text: str, response, cancel: threading.Event) -> None:
        """Play the synthesized samples in ~0.25s blocks (mirrors Cartesia's
        per-chunk loop so cancel + first-chunk callback behave identically).
        `response` is the numpy array from _generate_response."""
        if cancel.is_set():
            return
        samples = np.asarray(response, dtype=np.float32)
        audio_stream = None
        try:
            play, audio_stream = self._build_player(cancel)
            self._active_audio_stream = audio_stream
            block = max(1, int(self.sample_rate * 0.25))
            for i in range(0, samples.size, block):
                if cancel.is_set():
                    return
                play(samples[i : i + block])
                if cancel.is_set():
                    return
                cb = self._first_chunk_callback
                if cb is not None:
                    self._first_chunk_callback = None
                    try:
                        cb()
                    except Exception:
                        pass
            if self._player_factory is None:
                self._output.finish(cancel)
        except Exception:
            if not cancel.is_set():
                self._output.drop()
            raise
        finally:
            self._active_audio_stream = None


# --- Factory: route provider string to the right TTS subclass ----------------


def create_tts_client(provider: str, api_key: str) -> TTS:
    """Construct the right TTS subclass based on a provider string.

    Mirrors ai.create_ai_client's factory pattern. Used by app.py main
    block to dispatch on the TTS_PROVIDER constant (resolved from env or
    keyring via config.resolve_setting).

    Args:
        provider: "cartesia", "elevenlabs" or "kokoro". Case-insensitive.
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
    if p == "kokoro":
        return KokoroTTS()
    raise ValueError(
        f"Unsupported TTS provider: {provider!r}. "
        f"Supported: 'cartesia', 'elevenlabs', 'kokoro'. To add a new provider, "
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
        "Hello, I am Clicky Windows, your voice assistant, built on "
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
