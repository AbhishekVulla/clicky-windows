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
        self._stopped = False
        self._lock = threading.Lock()

    def speak(self, text: str) -> None:
        """Stream TTS for the full response text non-blocking. See base class."""
        if not text or not text.strip():
            return
        # Reset the stop flag for a fresh utterance.
        with self._lock:
            self._stopped = False
        thread = threading.Thread(
            target=self._do_speak,
            args=(text,),
            name=f"CartesiaSonicTTS-speak-{id(text)}",
            daemon=True,
        )
        thread.start()

    def speak_sentence(self, sentence: str) -> None:
        """Phase 1 delegates to speak(). See base class.

        Phase 2 may add queueing / interruption semantics; for now the single-
        sentence path is identical to the full-text path because Cartesia
        Sonic-3 TTFB (~150-250ms) is already well below the sentence duration
        Claude produces between boundaries.
        """
        self.speak(sentence)

    def stop(self) -> None:
        """Set the stop flag; in-progress threads exit on the next chunk boundary."""
        with self._lock:
            self._stopped = True

    def _do_speak(self, text: str) -> None:
        """Background-thread body: open stream, iterate chunks, play them.

        Exposed as a standalone method (not a closure) so unit tests can call
        it synchronously and assert on RuntimeError shape without dealing with
        threading.excepthook plumbing.
        """
        # Check stop flag at startup -- the common interrupt case.
        if self._stopped:
            return

        try:
            client = self._build_client()
            # Use client.tts.generate(...).iter_bytes() instead of client.tts.bytes(...)
            # — bytes() is deprecated in cartesia 3.0.2 ("Use .generate() instead").
            # generate() returns a BinaryAPIResponse; iter_bytes() is the streaming
            # chunk iterator. Same underlying HTTP chunked streaming.
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
            chunk_iter = response.iter_bytes()
            play = self._build_player()
            for chunk in chunk_iter:
                if self._stopped:
                    return
                if not chunk:
                    continue
                samples = np.frombuffer(chunk, dtype=np.float32)
                if samples.size == 0:
                    continue
                play(samples)
        except Exception as exc:
            raise RuntimeError(
                "Cartesia Sonic-3 TTS failed. Diagnostic checklist:\n"
                "  1. Is CARTESIA_API_KEY set in .env? (check https://play.cartesia.ai/)\n"
                "  2. Is your internet connection up?\n"
                "  3. Is Cartesia up? (status page: https://status.cartesia.ai)\n"
                "  4. Reactive fix: subclass TTS as Pyttsx3TTS for an offline\n"
                "     fallback -- ~1 hour of work, see Phase 2 notes.\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc

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

        Default implementation opens a sounddevice.OutputStream at the
        configured sample rate and returns a closure that writes samples to
        it. Tests inject their own player_factory returning a MagicMock.
        """
        if self._player_factory is not None:
            return self._player_factory(sample_rate=self.sample_rate)
        # Default: sounddevice OutputStream. Imported lazily so tests that
        # inject a mock player_factory don't need portaudio installed.
        import sounddevice as sd

        stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
        )
        stream.start()

        def _play(samples: np.ndarray) -> None:
            stream.write(samples)

        return _play


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
