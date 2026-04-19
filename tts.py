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
)


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

        # Sentence-level sequential queue (Path A Task 5). Unblocks sentence-
        # streaming TTS in app.py: each .!? boundary in Claude's stream calls
        # speak_sentence() which puts to this queue; the worker plays sentences
        # back-to-back without cancelling each other.
        self._sentence_queue: queue.Queue = queue.Queue()
        self._queue_worker_thread = threading.Thread(
            target=self._queue_worker,
            name="CartesiaSonicTTS-queue-worker",
            daemon=True,
        )
        self._queue_worker_thread.start()

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

    def _queue_worker(self) -> None:
        """Daemon thread that pulls sentences from ``_sentence_queue`` and plays
        each one to completion via ``_do_speak``.

        Runs for the lifetime of the process (daemon=True, no explicit
        shutdown sentinel needed). Each sentence gets a fresh
        ``threading.Event`` assigned to ``self._cancel_event`` so ``stop()``
        can abort only the currently-playing sentence.
        """
        while True:
            sentence = self._sentence_queue.get()
            try:
                cancel = threading.Event()
                self._cancel_event = cancel
                self._do_speak(sentence, cancel)
            except Exception as exc:
                # Swallow — queue worker must not die on a single bad sentence.
                print(f"[tts] queue worker: sentence failed — {exc}", flush=True)
            finally:
                self._sentence_queue.task_done()

    def stop(self) -> None:
        """Kill audio playback INSTANTLY + drain any pending queued sentences.

        Four-pronged kill (Path A Task 5 adds the queue drain on top of the
        existing three-pronged abort):
        1. Drain ``_sentence_queue`` — pending sentences never start
        2. Set cancel event — currently-playing sentence's loop checks + returns
        3. Abort sounddevice stream — stops audio output mid-sample
        4. Close HTTP response — interrupts iter_bytes() network read
        """
        # Drain queue FIRST so the worker doesn't pull a new sentence right
        # after we set the cancel event.
        while not self._sentence_queue.empty():
            try:
                self._sentence_queue.get_nowait()
                self._sentence_queue.task_done()
            except queue.Empty:
                break

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

    def _do_speak(self, text: str, cancel: threading.Event) -> None:
        """Background-thread body: open stream, iterate chunks, play them.

        Each invocation gets its own cancel Event. Old threads that survive
        the join timeout remain cancelled because their Event stays set.
        The sounddevice OutputStream is explicitly closed in the finally block.
        """
        if cancel.is_set():
            return

        import time as _t
        _tts_start = _t.time()
        print(f"[tts] _do_speak START: {len(text)} chars", flush=True)
        audio_stream = None
        try:
            client = self._build_client()
            response = client.tts.generate(
                model_id=self.model_id,
                transcript=text,
                voice={"id": self.voice_id, "mode": "id"},
                output_format={
                    "container": "raw",
                    "encoding": "pcm_f32le",
                    "sample_rate": self.sample_rate,
                },
            )
            self._active_response = response
            chunk_iter = response.iter_bytes()
            play, audio_stream = self._build_player()
            self._active_audio_stream = audio_stream  # so stop() can abort() it
            for chunk in chunk_iter:
                if cancel.is_set():
                    return
                if not chunk:
                    continue
                samples = np.frombuffer(chunk, dtype=np.float32)
                if samples.size == 0:
                    continue
                play(samples)
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
        finally:
            self._active_response = None
            self._active_audio_stream = None
            duration_ms = (_t.time() - _tts_start) * 1000
            cancelled = cancel.is_set()
            print(f"[tts] _do_speak END: {duration_ms:.0f}ms, cancelled={cancelled}", flush=True)
            if audio_stream is not None:
                try:
                    audio_stream.abort()
                    audio_stream.close()
                except Exception:
                    pass

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
