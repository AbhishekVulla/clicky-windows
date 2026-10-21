"""Unit tests for tts.py.

All tests are mock-based. Zero real network, zero real audio. Green in <2s.
Covers: TTS abstract, CartesiaSonicTTS speak/stop/cancel Event pattern.
"""
import threading
import time

import pytest


def test_tts_module_importable():
    import tts  # noqa: F401



def _streamed(response):
    """What tts.with_streaming_response.generate() returns: a context manager around ``response``."""
    from unittest.mock import MagicMock
    cm = MagicMock()
    cm.__enter__.return_value = response
    return cm

class TestTTSAbstract:

    def test_tts_abstract_raises(self):
        from tts import TTS
        with pytest.raises(TypeError):
            TTS()  # type: ignore[abstract]


class TestCartesiaSonicTTSSpeak:

    def _make_tts(self, chunks=None):
        """Helper: build a CartesiaSonicTTS with mock factories.

        Returns (tts_instance, fake_client, fake_play_callable).
        player_factory returns (play_fn, None): None for the stream since
        tests don't need real sounddevice cleanup.
        """
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        fake_client = MagicMock(name="fake_cartesia_client")
        fake_client.tts.with_streaming_response.generate.return_value.__enter__.return_value.iter_bytes.return_value = iter(
            chunks if chunks is not None else [b"\x00" * 16, b"\x00" * 16]
        )
        fake_play = MagicMock(name="fake_play")

        def client_factory(*, api_key):
            return fake_client

        def player_factory(*, sample_rate):
            return fake_play, None

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )
        return tts_obj, fake_client, fake_play

    def test_speak_dispatches_to_background_thread_non_blocking(self):
        tts_obj, fake_client, fake_play = self._make_tts()

        t0 = time.perf_counter()
        tts_obj.speak("hello")
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert elapsed_ms < 50

        if tts_obj._current_thread:
            tts_obj._current_thread.join(timeout=5)

        fake_client.tts.with_streaming_response.generate.assert_called_once()
        call_kwargs = fake_client.tts.with_streaming_response.generate.call_args.kwargs
        assert call_kwargs["transcript"] == "hello"
        assert call_kwargs["model_id"] == "sonic-3"
        assert fake_play.call_count >= 1

    def test_speak_empty_string_skips_thread(self):
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        client_factory = MagicMock(name="client_factory")
        player_factory = MagicMock(name="player_factory")
        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak("")
        tts_obj.speak("   \t\n")

        assert tts_obj._current_thread is None
        client_factory.assert_not_called()
        player_factory.assert_not_called()

    def test_do_speak_error_raises_runtime_error(self):
        from tts import CartesiaSonicTTS

        def bad_client_factory(*, api_key):
            raise ConnectionError("boom: DNS lookup failed")

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=bad_client_factory,
            player_factory=lambda *, sample_rate: (lambda samples: None, None),
        )

        cancel = threading.Event()
        with pytest.raises(RuntimeError) as exc_info:
            tts_obj._do_speak("hello", cancel)

        msg = str(exc_info.value)
        assert "Cartesia" in msg
        assert "boom: DNS lookup failed" in msg

    def test_stop_cancels_via_event(self):
        """After stop(), a new _do_speak with the same cancel Event exits immediately."""
        tts_obj, fake_client, fake_play = self._make_tts()

        cancel = tts_obj._cancel_event
        tts_obj.stop()
        assert cancel.is_set()

        tts_obj._do_speak("should not be spoken", cancel)
        fake_client.tts.with_streaming_response.generate.assert_not_called()
        fake_play.assert_not_called()

    def test_stop_during_streaming_exits_between_chunks(self):
        """If cancel fires mid-stream, the loop exits before the next chunk."""
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        cancel = threading.Event()

        def gen_chunks():
            yield b"\x00" * 16
            cancel.set()
            yield b"\x00" * 16

        fake_client = MagicMock(name="fake_cartesia_client")
        fake_client.tts.with_streaming_response.generate.return_value.__enter__.return_value.iter_bytes.return_value = gen_chunks()
        fake_play = MagicMock(name="fake_play")

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=lambda *, api_key: fake_client,
            player_factory=lambda *, sample_rate: (fake_play, None),
        )

        tts_obj._do_speak("two chunks one stop", cancel)

        assert fake_play.call_count == 1

    def test_speak_cancels_previous_thread(self):
        """Calling speak() twice: first call's cancel Event should be set."""
        tts_obj, fake_client, fake_play = self._make_tts()

        tts_obj.speak("first")
        first_cancel = tts_obj._cancel_event
        if tts_obj._current_thread:
            tts_obj._current_thread.join(timeout=5)

        fake_client.tts.with_streaming_response.generate.return_value.__enter__.return_value.iter_bytes.return_value = iter(
            [b"\x00" * 16]
        )
        tts_obj.speak("second")
        if tts_obj._current_thread:
            tts_obj._current_thread.join(timeout=5)

        assert first_cancel.is_set()

    def test_cancelled_do_speak_suppresses_error(self):
        """If cancel is set and an exception occurs, it should NOT raise."""
        from tts import CartesiaSonicTTS

        def bad_client_factory(*, api_key):
            raise ConnectionError("network down")

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=bad_client_factory,
            player_factory=lambda *, sample_rate: (lambda s: None, None),
        )

        cancel = threading.Event()
        cancel.set()
        # Should NOT raise because cancel is set
        tts_obj._do_speak("should be silent", cancel)

    def test_speak_sentence_queues_and_plays_sequentially(self):
        """Multiple speak_sentence calls play sequentially via a queue worker,
        NOT cancelling each other. Sentence-level TTS streaming in the app.py
        pipeline depends on this.
        """
        import time as _t
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        played_count = [0]

        def fake_play(samples):
            played_count[0] += 1

        def client_factory(*, api_key):
            client = MagicMock(name="multi-sentence-client")

            def gen_response(**kwargs):
                # Each generate() call must return a response with a FRESH iter_bytes
                resp = MagicMock()
                resp.iter_bytes.return_value = iter([b"\x00" * 16])
                return resp

            client.tts.with_streaming_response.generate.side_effect = lambda **kw: _streamed(gen_response(**kw))
            return client

        def player_factory(*, sample_rate):
            return fake_play, None

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak_sentence("first sentence.")
        tts_obj.speak_sentence("second sentence.")
        tts_obj.speak_sentence("third sentence.")

        # Wait (up to 2s) for worker to drain the queue.
        for _ in range(100):
            if played_count[0] >= 3:
                break
            _t.sleep(0.02)

        assert played_count[0] >= 3, (
            f"Expected >=3 sentences played, got {played_count[0]}: "
            "worker may not be consuming queue sequentially"
        )

    def test_stop_drains_pending_sentences(self):
        """stop() must clear queued sentences so they don't play after abort."""
        import time as _t
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        def client_factory(*, api_key):
            client = MagicMock()

            def slow_gen(**kwargs):
                resp = MagicMock()
                # Slow iter so the worker is blocked inside _do_speak when we call stop()
                def slow_iter():
                    for _ in range(10):
                        _t.sleep(0.05)
                        yield b"\x00" * 16

                resp.iter_bytes.return_value = slow_iter()
                return resp

            client.tts.with_streaming_response.generate.side_effect = lambda **kw: _streamed(slow_gen(**kw))
            return client

        def player_factory(*, sample_rate):
            return MagicMock(), None

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak_sentence("pending-1.")
        tts_obj.speak_sentence("pending-2.")
        tts_obj.speak_sentence("pending-3.")

        # Give worker a moment to pick up first item (but not finish; it's slow)
        _t.sleep(0.02)

        tts_obj.stop()
        assert tts_obj._sentence_queue.empty(), (
            "stop() must drain queued sentences; none should remain pending"
        )

    def test_prefetch_fires_before_previous_playback_completes(self):
        """HTTP request for sentence N+1 must start while N is still
        playing. Uses an Event handshake (not time.sleep timing math) so the
        test is deterministic under thread-scheduling jitter: we block the
        first sentence's playback inside fake_play, check that generate was
        called for the second sentence DURING that block, then release.

        Fails on single-worker impl: worker is stuck inside fake_play for the
        first sentence, so generate('second') cannot have been called yet.
        Passes on double-buffer impl: prefetch thread is independent of
        playback thread, so it fires generate('second') while playback sits
        in fake_play.
        """
        import time as _t
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        gen_log: list[str] = []
        gen_log_lock = threading.Lock()
        first_play_started = threading.Event()
        release_first_play = threading.Event()

        def client_factory(*, api_key):
            client = MagicMock()

            def gen(**kwargs):
                with gen_log_lock:
                    gen_log.append(kwargs["transcript"])
                resp = MagicMock()
                resp.iter_bytes.return_value = iter([b"\x00" * 16])
                return resp

            client.tts.with_streaming_response.generate.side_effect = lambda **kw: _streamed(gen(**kw))
            return client

        def player_factory(*, sample_rate):
            def fake_play(samples):
                first_play_started.set()
                # Block until the test explicitly releases. This simulates
                # "first sentence is still playing" in a deterministic way.
                if not release_first_play.wait(timeout=2.0):
                    raise AssertionError("release_first_play was never set")

            return fake_play, None

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak_sentence("first.")
        tts_obj.speak_sentence("second.")

        # Deterministic signal: first sentence has started playing.
        assert first_play_started.wait(timeout=2.0), (
            "First sentence's fake_play was never entered; something is "
            "wrong with the queue worker startup."
        )

        # Prefetch is decoupled from playback, so give the prefetch worker
        # up to 500ms to call generate() for the second sentence while
        # the first is still "playing" (blocked in fake_play).
        for _ in range(25):
            with gen_log_lock:
                if "second." in gen_log:
                    break
            _t.sleep(0.02)

        # Snapshot gen_log BEFORE releasing playback, so we know whether
        # 'second.' was generated WHILE first was playing (not after).
        with gen_log_lock:
            generated_during_first_playback = list(gen_log)

        # Release playback so the test can exit cleanly.
        release_first_play.set()

        assert "second." in generated_during_first_playback, (
            f"Prefetch did not fire during first sentence's playback. "
            f"gen_log while first was blocked in fake_play: "
            f"{generated_during_first_playback!r}. Expected 'second.' "
            f"to appear there (prefetch overlapping playback)."
        )

    def test_prefetch_error_does_not_deadlock_playback(self):
        """If generate() raises for one sentence, the prefetch worker must
        catch the exception and put (epoch, sentence, None) so playback
        skips without hanging. Subsequent good sentences must still play.
        """
        import time as _t
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        played_count = [0]
        lock = threading.Lock()

        def client_factory(*, api_key):
            client = MagicMock()

            def gen(**kwargs):
                transcript = kwargs["transcript"]
                if "bad" in transcript:
                    raise ConnectionError(f"simulated HTTP error for {transcript!r}")
                resp = MagicMock()
                resp.iter_bytes.return_value = iter([b"\x00" * 16])
                return resp

            client.tts.with_streaming_response.generate.side_effect = lambda **kw: _streamed(gen(**kw))
            return client

        def player_factory(*, sample_rate):
            def play(samples):
                with lock:
                    played_count[0] += 1

            return play, None

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak_sentence("good one.")
        tts_obj.speak_sentence("bad one.")
        tts_obj.speak_sentence("good two.")

        # Wait up to 2s for 2 good sentences to play (bad one skipped).
        for _ in range(100):
            with lock:
                count = played_count[0]
            if count >= 2:
                break
            _t.sleep(0.02)

        with lock:
            final = played_count[0]
        assert final >= 2, (
            f"Playback deadlocked: expected 2 good sentences to play around "
            f"the failing one, got {final}"
        )


# --- ElevenLabs TTS ------------------------------------------------------------


class TestElevenLabsTTSSpeak:
    """Mirrors TestCartesiaSonicTTSSpeak: same speak/stop/cancel
    semantics. Differences: stream() returns Iterator[bytes] directly
    (no .iter_bytes()); chunks are int16 PCM, converted to float32 in
    the playback loop."""

    def _make_tts(self, chunks=None):
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        fake_client = MagicMock(name="fake_elevenlabs_client")
        # ElevenLabs streaming method: client.text_to_speech.stream(...)
        # returns an Iterator[bytes] directly (true streaming, no body fetch)
        fake_client.text_to_speech.stream.return_value = iter(
            chunks if chunks is not None
            else [b"\x00\x00" * 8, b"\x00\x00" * 8]  # int16 zeros
        )
        fake_play = MagicMock(name="fake_play")

        def client_factory(*, api_key):
            return fake_client

        def player_factory(*, sample_rate):
            return fake_play, None

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )
        return tts_obj, fake_client, fake_play

    def test_speak_dispatches_to_background_thread_non_blocking(self):
        tts_obj, fake_client, fake_play = self._make_tts()

        t0 = time.perf_counter()
        tts_obj.speak("hello")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 50

        if tts_obj._current_thread:
            tts_obj._current_thread.join(timeout=5)

        fake_client.text_to_speech.stream.assert_called_once()
        call_kwargs = fake_client.text_to_speech.stream.call_args.kwargs
        assert call_kwargs["text"] == "hello"
        assert call_kwargs["model_id"] == "eleven_flash_v2_5"
        assert call_kwargs["voice_id"] == "21m00Tcm4TlvDq8ikWAM"
        assert call_kwargs["output_format"] == "pcm_22050"
        assert fake_play.call_count >= 1

    def test_speak_empty_string_skips_thread(self):
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        client_factory = MagicMock(name="client_factory")
        player_factory = MagicMock(name="player_factory")
        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak("")
        tts_obj.speak("   \t\n")

        assert tts_obj._current_thread is None
        client_factory.assert_not_called()
        player_factory.assert_not_called()

    def test_play_response_converts_int16_to_float32(self):
        """ElevenLabs PCM chunks are int16 little-endian. The playback
        loop must convert each chunk to float32 in [-1, 1] range before
        passing to sounddevice (which expects float32 per OutputStream
        config). This is the key difference from Cartesia, which emits
        float32 directly."""
        import struct
        import threading
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS
        import numpy as np

        # Build a chunk of 4 int16 samples at +0.5 amplitude.
        max_int16 = 32767
        amplitude = int(0.5 * max_int16)
        chunk_bytes = struct.pack("<hhhh", amplitude, -amplitude, amplitude, 0)

        fake_client = MagicMock()
        fake_client.text_to_speech.stream.return_value = iter([chunk_bytes])
        captured_samples = []

        def fake_play(samples):
            captured_samples.append(samples.copy())

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=lambda *, api_key: fake_client,
            player_factory=lambda *, sample_rate: (fake_play, None),
        )

        cancel = threading.Event()
        response = tts_obj._generate_response("hi")
        tts_obj._play_response("hi", response, cancel)

        assert len(captured_samples) == 1
        arr = captured_samples[0]
        assert arr.dtype == np.float32
        # 0.5 amplitude after divide by 32768 ≈ 0.4999..., so assert close
        assert abs(float(arr[0]) - 0.5) < 0.001
        assert abs(float(arr[1]) + 0.5) < 0.001
        assert abs(float(arr[3])) < 0.001


class TestElevenLabsTTSSentenceQueue:
    """Mirrors TestCartesiaSonicTTSSpeak::test_speak_sentence_queues_and_plays_sequentially.
    Multiple speak_sentence calls play sequentially via the prefetch+playback
    two-thread architecture, NOT cancelling each other."""

    def test_speak_sentence_queues_and_plays_sequentially(self):
        import time as _t
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        played_count = [0]

        def fake_play(samples):
            played_count[0] += 1

        def client_factory(*, api_key):
            client = MagicMock(name="multi-sentence-elevenlabs-client")

            def gen_iterator(**kwargs):
                # Each stream() call must return a fresh iterator
                return iter([b"\x00\x00" * 8])

            client.text_to_speech.stream.side_effect = gen_iterator
            return client

        def player_factory(*, sample_rate):
            return fake_play, None

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak_sentence("first sentence.")
        tts_obj.speak_sentence("second sentence.")
        tts_obj.speak_sentence("third sentence.")

        for _ in range(100):
            if played_count[0] >= 3:
                break
            _t.sleep(0.02)

        assert played_count[0] >= 3, (
            f"Expected >=3 sentences played, got {played_count[0]}: "
            "queue worker may not be consuming sequentially"
        )


class TestElevenLabsTTSStop:
    """5-pronged kill (NOT 6: no response.close, since elevenlabs SDK
    doesn't expose one). Order: epoch++ → drain sentence queue → drain
    prefetch queue → cancel event → sounddevice abort."""

    def test_stop_drains_pending_sentences(self):
        import time as _t
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        def client_factory(*, api_key):
            client = MagicMock()

            def slow_iter(**kwargs):
                def _gen():
                    for _ in range(10):
                        _t.sleep(0.05)
                        yield b"\x00\x00" * 8

                return _gen()

            client.text_to_speech.stream.side_effect = slow_iter
            return client

        def player_factory(*, sample_rate):
            return MagicMock(), None

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak_sentence("pending-1.")
        tts_obj.speak_sentence("pending-2.")
        tts_obj.speak_sentence("pending-3.")

        _t.sleep(0.02)

        tts_obj.stop()
        assert tts_obj._sentence_queue.empty(), (
            "stop() must drain queued sentences"
        )

    def test_stop_sets_cancel_event_and_bumps_epoch(self):
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=lambda *, api_key: MagicMock(),
            player_factory=lambda *, sample_rate: (MagicMock(), None),
        )
        old_epoch = tts_obj._epoch
        old_cancel = tts_obj._cancel_event
        tts_obj.stop()
        assert tts_obj._epoch == old_epoch + 1
        assert old_cancel.is_set()


class TestCreateTTSClient:
    """Tests for tts.create_tts_client factory, which routes the provider
    string to the right TTS subclass. Constructing the subclass is safe without
    mocks: __init__ stores api_key + factories but doesn't touch the
    real SDK (lazy import inside _build_client only fires on first speak)."""

    def test_routes_cartesia_to_cartesia_sonic_tts(self):
        from tts import create_tts_client, CartesiaSonicTTS
        client = create_tts_client(provider="cartesia", api_key="test-key")
        assert isinstance(client, CartesiaSonicTTS)

    def test_routes_elevenlabs_to_elevenlabs_tts(self):
        from tts import create_tts_client, ElevenLabsTTS
        client = create_tts_client(provider="elevenlabs", api_key="test-key")
        assert isinstance(client, ElevenLabsTTS)

    def test_unknown_provider_raises_value_error(self):
        from tts import create_tts_client
        with pytest.raises(ValueError) as excinfo:
            create_tts_client(provider="googletts", api_key="x")
        msg = str(excinfo.value)
        assert "googletts" in msg
        assert "cartesia" in msg
        assert "elevenlabs" in msg

    def test_provider_string_is_case_insensitive(self):
        from tts import create_tts_client, CartesiaSonicTTS
        client = create_tts_client(provider="Cartesia", api_key="x")
        assert isinstance(client, CartesiaSonicTTS)


# --- First-audible-word callback --------------------------------------------


class TestFirstChunkCallback:
    """``arm_first_chunk_callback`` arms a one-shot callback that fires
    on the first successful sounddevice.play(samples) per interaction.
    Used by app.py to log first-audible-word latency. Slot must clear
    after firing so subsequent sentences in the same interaction do NOT
    re-fire; the next interaction re-arms a fresh callback."""

    def test_cartesia_first_chunk_callback_fires_once_then_clears(self):
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        fake_client = MagicMock(name="fake_cartesia_client")
        fake_client.tts.with_streaming_response.generate.return_value.__enter__.return_value.iter_bytes.return_value = iter(
            [b"\x00" * 16, b"\x00" * 16, b"\x00" * 16]  # 3 chunks
        )
        fake_play = MagicMock(name="fake_play")
        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=lambda *, api_key: fake_client,
            player_factory=lambda *, sample_rate: (fake_play, None),
        )
        calls = []
        tts_obj.arm_first_chunk_callback(lambda: calls.append("fired"))

        tts_obj.speak("hello")
        if tts_obj._current_thread:
            tts_obj._current_thread.join(timeout=5)

        assert calls == ["fired"], (
            f"Expected callback to fire exactly once across 3 chunks, got {calls}"
        )
        assert tts_obj._first_chunk_callback is None, (
            "Callback slot must clear after firing"
        )

    def test_elevenlabs_first_chunk_callback_fires_once_then_clears(self):
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        fake_client = MagicMock(name="fake_elevenlabs_client")
        # 3 int16 PCM chunks (each 8 samples = 16 bytes)
        fake_client.text_to_speech.stream.return_value = iter(
            [b"\x00\x00" * 8, b"\x00\x00" * 8, b"\x00\x00" * 8]
        )
        fake_play = MagicMock(name="fake_play")
        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=lambda *, api_key: fake_client,
            player_factory=lambda *, sample_rate: (fake_play, None),
        )
        calls = []
        tts_obj.arm_first_chunk_callback(lambda: calls.append("fired"))

        tts_obj.speak("hello")
        if tts_obj._current_thread:
            tts_obj._current_thread.join(timeout=5)

        assert calls == ["fired"], (
            f"Expected callback to fire exactly once across 3 chunks, got {calls}"
        )
        assert tts_obj._first_chunk_callback is None, (
            "Callback slot must clear after firing"
        )

    def test_cartesia_callback_exception_does_not_break_playback(self):
        """If the callback raises, the playback loop must continue
        (e.g. dbg.log failing must not silence the user)."""
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        fake_client = MagicMock()
        fake_client.tts.with_streaming_response.generate.return_value.__enter__.return_value.iter_bytes.return_value = iter(
            [b"\x00" * 16, b"\x00" * 16]
        )
        fake_play = MagicMock(name="fake_play")
        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=lambda *, api_key: fake_client,
            player_factory=lambda *, sample_rate: (fake_play, None),
        )

        def boom():
            raise RuntimeError("simulated dbg.log failure")

        tts_obj.arm_first_chunk_callback(boom)
        tts_obj.speak("hello")
        if tts_obj._current_thread:
            tts_obj._current_thread.join(timeout=5)

        # Both chunks should have played despite the callback raising.
        assert fake_play.call_count >= 2, (
            f"Expected >=2 play() calls; callback exception should not break "
            f"playback. Got {fake_play.call_count}"
        )


class TestKokoroTTS:
    """Local offline TTS via Kokoro-82M. Subclasses CartesiaSonicTTS, overriding
    only synth + playback. kokoro_onnx is lazy-imported (DI in tests)."""

    def test_generate_returns_samples_from_kokoro(self, mocker):
        import numpy as np
        from tts import KokoroTTS
        fake = mocker.MagicMock()
        fake.create.return_value = (np.zeros(2400, dtype=np.float32), 24000)
        t = KokoroTTS(kokoro_factory=lambda: fake)
        samples = t._generate_response("hello")
        assert samples.shape[0] == 2400
        fake.create.assert_called_once()

    def test_sample_rate_is_24k(self, mocker):
        from tts import KokoroTTS
        t = KokoroTTS(kokoro_factory=lambda: mocker.MagicMock())
        assert t.sample_rate == 24000

    def test_lazy_no_kokoro_import_when_using_di(self, mocker):
        import sys
        sys.modules.pop("kokoro_onnx", None)
        from tts import KokoroTTS
        t = KokoroTTS(kokoro_factory=lambda: mocker.MagicMock())
        t._ensure_kokoro()  # uses the injected factory, not the real import
        assert "kokoro_onnx" not in sys.modules


def test_create_tts_client_routes_kokoro():
    from tts import KokoroTTS, create_tts_client
    assert isinstance(create_tts_client("kokoro", ""), KokoroTTS)


def test_kokoro_warmup_preloads_model(mocker):
    """warmup() loads the Kokoro model up front (app.py calls it at startup)."""
    from tts import KokoroTTS
    fake = mocker.MagicMock()
    t = KokoroTTS(kokoro_factory=lambda: fake)
    t.warmup()
    assert t._kokoro is fake


def test_cartesia_warmup_is_noop(mocker):
    """Cloud TTS inherits the base no-op warmup (nothing to pre-load)."""
    from tts import CartesiaSonicTTS
    t = CartesiaSonicTTS(api_key="x", client_factory=lambda **k: mocker.MagicMock())
    t.warmup()  # must not raise


class TestCartesiaStreaming:
    """First audio plays as soon as it arrives, on one reused connection."""

    def _tts(self, client, played=None):
        from tts import CartesiaSonicTTS
        play = played.append if played is not None else (lambda samples: None)
        return CartesiaSonicTTS(
            api_key="k",
            client_factory=lambda *, api_key: client,
            player_factory=lambda *, sample_rate: (play, None),
        )

    def test_one_client_serves_every_sentence(self):
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS
        factory = MagicMock(return_value=MagicMock())
        tts_obj = CartesiaSonicTTS(
            api_key="k", client_factory=factory,
            player_factory=lambda *, sample_rate: (lambda samples: None, None),
        )
        tts_obj._generate_response("one.")
        tts_obj._generate_response("two.")
        assert factory.call_count == 1

    def test_audio_is_streamed_not_downloaded_first(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        self._tts(client)._generate_response("hi.")
        client.tts.with_streaming_response.generate.assert_called_once()
        client.tts.generate.assert_not_called()

    def test_samples_split_across_network_chunks_play_intact(self):
        import numpy as np
        from unittest.mock import MagicMock
        samples = np.arange(6, dtype=np.float32)
        raw = samples.tobytes()
        response = MagicMock()
        response.iter_bytes.return_value = iter([raw[:5], raw[5:13], raw[13:]])
        played = []
        self._tts(MagicMock(), played)._play_response("hi.", response, threading.Event())
        assert np.concatenate(played).tolist() == samples.tolist()

    def test_a_sentence_cancelled_before_playback_still_releases_its_connection(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        cancel = threading.Event()
        cancel.set()  # stop() landed between the hand-off and playback
        self._tts(MagicMock())._play_response("hi.", response, cancel)
        response.close.assert_called_once()


class TestCartesiaWarmUp:
    def test_warm_opens_the_cartesia_connection(self, mocker):
        from tts import CartesiaSonicTTS
        http = mocker.MagicMock()
        mocker.patch("cartesia.DefaultHttpxClient", return_value=http)
        sdk = mocker.patch("cartesia.Cartesia")
        sdk.return_value.base_url = "https://api.cartesia.ai"
        tts_obj = CartesiaSonicTTS(api_key="k", player_factory=lambda *, sample_rate: (lambda s: None, None))
        tts_obj.warm()
        assert sdk.call_args.kwargs["http_client"] is http
        assert http.head.call_args.args[0] == "https://api.cartesia.ai"

    def test_warm_never_raises(self, mocker):
        from tts import CartesiaSonicTTS
        mocker.patch("cartesia.Cartesia", side_effect=OSError("offline"))
        CartesiaSonicTTS(api_key="k", player_factory=lambda *, sample_rate: (lambda s: None, None)).warm()


class TestTtsErrorsAreReported:
    def test_a_failed_sentence_reports_its_error(self):
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS
        client = MagicMock()
        boom = RuntimeError("HTTP 401 invalid key")
        client.tts.with_streaming_response.generate.side_effect = boom
        got = []
        tts_obj = CartesiaSonicTTS(
            api_key="k", client_factory=lambda *, api_key: client,
            player_factory=lambda *, sample_rate: (lambda s: None, None),
        )
        tts_obj.on_error(got.append)
        tts_obj.speak_sentence("hi.")
        for _ in range(100):
            if got:
                break
            time.sleep(0.02)
        assert got == [boom]


class TestOneOpenAudioStream:
    """Opening a Windows audio device takes 0.4-0.9 s; restarting an open one
    takes about 75 ms. Each voice keeps one stream open instead of opening a
    new one per sentence."""

    @staticmethod
    def _wait(cond, timeout=3.0):
        deadline = time.time() + timeout
        while not cond() and time.time() < deadline:
            time.sleep(0.01)
        return cond()

    @staticmethod
    def _fake_device(monkeypatch, fail_first=False):
        import sounddevice
        streams = []

        class FakeStream:
            def __init__(self, *a, **k):
                self.fail = fail_first and not streams
                self.started = self.aborted = self.closed = self.writes = 0
                self.stopped = True
                self.latency = 0.01
                streams.append(self)

            def start(self):
                self.started += 1
                self.stopped = False

            def write(self, data):
                if self.fail:
                    raise sounddevice.PortAudioError("Unanticipated host error")
                if data.any():  # the silence that ends each sentence is not a sentence
                    self.writes += 1

            def abort(self):
                self.aborted += 1
                self.stopped = True

            def close(self):
                self.closed += 1

        monkeypatch.setattr(sounddevice, "OutputStream", FakeStream)
        return streams

    @staticmethod
    def _cartesia():
        import numpy as np
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS
        client = MagicMock()

        def generate(**kw):
            resp = MagicMock()
            resp.iter_bytes.return_value = iter([np.ones(4, dtype=np.float32).tobytes()])
            return _streamed(resp)

        client.tts.with_streaming_response.generate.side_effect = generate
        return CartesiaSonicTTS(api_key="k", client_factory=lambda *, api_key: client)

    def test_sentences_share_one_open_stream(self, monkeypatch):
        streams = self._fake_device(monkeypatch)
        tts_obj = self._cartesia()
        tts_obj.speak_sentence("one.")
        assert self._wait(lambda: streams and streams[0].writes == 1)
        tts_obj.speak_sentence("two.")
        assert self._wait(lambda: streams[0].writes == 2)
        assert len(streams) == 1
        assert (streams[0].started, streams[0].aborted, streams[0].closed) == (1, 0, 0)

    def test_stop_silences_the_stream_and_the_next_answer_restarts_it(self, monkeypatch):
        streams = self._fake_device(monkeypatch)
        tts_obj = self._cartesia()
        tts_obj.speak_sentence("one.")
        assert self._wait(lambda: streams and streams[0].writes == 1 and tts_obj._active_audio_stream is None)
        tts_obj.stop()  # between sentences: audio still buffered must stop too
        assert streams[0].aborted == 1
        tts_obj.speak_sentence("two.")
        assert self._wait(lambda: streams[0].writes == 2)
        assert len(streams) == 1 and streams[0].started == 2

    def test_a_failed_device_is_reopened_for_the_next_sentence(self, monkeypatch):
        streams = self._fake_device(monkeypatch, fail_first=True)
        tts_obj = self._cartesia()
        tts_obj.speak_sentence("one.")
        assert self._wait(lambda: streams and streams[0].closed == 1)
        tts_obj.speak_sentence("two.")
        assert self._wait(lambda: len(streams) == 2 and streams[1].writes == 1)

    def test_warmup_opens_the_stream_before_the_first_sentence(self, monkeypatch):
        streams = self._fake_device(monkeypatch)
        tts_obj = self._cartesia()
        tts_obj.warmup()
        assert len(streams) == 1 and streams[0].started == 1
        tts_obj.speak_sentence("one.")
        assert self._wait(lambda: streams[0].writes == 1)
        assert len(streams) == 1

    def test_elevenlabs_sentences_share_one_open_stream(self, monkeypatch):
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS
        streams = self._fake_device(monkeypatch)
        client = MagicMock()
        client.text_to_speech.stream.side_effect = lambda **kw: iter([b"\x01\x00" * 8])
        tts_obj = ElevenLabsTTS(api_key="k", client_factory=lambda *, api_key: client)
        tts_obj.speak_sentence("one.")
        assert self._wait(lambda: streams and streams[0].writes == 1)
        tts_obj.speak_sentence("two.")
        assert self._wait(lambda: streams[0].writes == 2)
        assert len(streams) == 1 and streams[0].closed == 0

    def test_kokoro_sentences_share_one_open_stream(self, monkeypatch):
        import numpy as np
        from unittest.mock import MagicMock
        from tts import KokoroTTS
        streams = self._fake_device(monkeypatch)
        kokoro = MagicMock()
        kokoro.create.return_value = (np.ones(100, dtype=np.float32), 24000)
        tts_obj = KokoroTTS(kokoro_factory=lambda: kokoro)
        tts_obj.speak_sentence("one.")
        assert self._wait(lambda: streams and streams[0].writes >= 1)
        tts_obj.speak_sentence("two.")
        assert self._wait(lambda: streams[0].writes >= 2)
        assert len(streams) == 1 and streams[0].closed == 0


class TestOpenStreamSafety:
    """Interrupting an answer must silence it for good, a press must never
    fail on the voice's stream, and each answer must play to its last sample."""

    @staticmethod
    def _wait(cond, timeout=3.0):
        deadline = time.time() + timeout
        while not cond() and time.time() < deadline:
            time.sleep(0.01)
        return cond()

    @staticmethod
    def _device(monkeypatch, block_first_write=None, latency=0.05):
        import sounddevice
        log = {"streams": [], "writes": [], "active": 0, "peak": 0}
        lock = threading.Lock()

        class FakeStream:
            def __init__(self, *a, **k):
                self.starts = 0
                self.is_stopped = True
                self.broken = False
                self.closed = False
                self.latency = latency
                log["streams"].append(self)

            @property
            def stopped(self):
                if self.broken:  # the stream is being closed on another thread
                    raise sounddevice.PortAudioError("Error querying stream state")
                return self.is_stopped

            def start(self):
                self.starts += 1
                self.is_stopped = False

            def write(self, data):
                with lock:
                    log["active"] += 1
                    log["peak"] = max(log["peak"], log["active"])
                if block_first_write is not None and not log["writes"]:
                    block_first_write["entered"].set()
                    block_first_write["release"].wait(5)
                with lock:
                    log["active"] -= 1
                    # (restart count, samples, heard): a stopped or closed device plays nothing
                    heard = not self.is_stopped and not self.closed
                    log["writes"].append((self.starts, data.copy(), heard, self))

            def abort(self):
                self.is_stopped = True

            def close(self):
                self.closed = True

        monkeypatch.setattr(sounddevice, "OutputStream", FakeStream)
        return log

    @staticmethod
    def _cartesia(chunks):
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS
        client = MagicMock()

        def generate(**kw):
            resp = MagicMock()
            resp.iter_bytes.return_value = iter(list(chunks))
            return _streamed(resp)

        client.tts.with_streaming_response.generate.side_effect = generate
        return CartesiaSonicTTS(api_key="k", client_factory=lambda *, api_key: client)

    def test_an_interrupted_answer_never_plays_after_the_stream_restarts(self, monkeypatch):
        import numpy as np
        gate = {"entered": threading.Event(), "release": threading.Event()}
        log = self._device(monkeypatch, block_first_write=gate)
        chunk = np.ones(64, dtype=np.float32).tobytes()
        tts_obj = self._cartesia([chunk, chunk, chunk])
        tts_obj.speak_sentence("old answer.")
        assert gate["entered"].wait(3)  # mid-write when the next press lands
        tts_obj.stop()
        warm = threading.Thread(target=tts_obj.warm)
        warm.start()
        time.sleep(0.2)
        gate["release"].set()
        warm.join(3)
        time.sleep(0.2)
        assert log["streams"][-1].starts == 1  # the press got a running stream back
        old_audio_heard_later = [
            w for w in log["writes"] if w[1].any() and w[2] and w[3] is log["streams"][-1]
        ]
        assert len(log["streams"]) == 2 and not old_audio_heard_later

    def test_a_press_never_fails_on_a_stream_that_is_closing(self, monkeypatch):
        log = self._device(monkeypatch)
        tts_obj = self._cartesia([b"\x00" * 16])
        tts_obj.speak_sentence("one.")
        assert self._wait(lambda: log["writes"])
        log["streams"][0].broken = True
        tts_obj.stop()  # must not raise into the hotkey handler

    def test_each_sentence_plays_to_its_last_sample(self, monkeypatch):
        import numpy as np
        log = self._device(monkeypatch, latency=0.05)
        tts_obj = self._cartesia([np.ones(100, dtype=np.float32).tobytes()])
        tts_obj.speak_sentence("one.")
        assert self._wait(lambda: len(log["writes"]) >= 2)
        tail = log["writes"][-1][1]
        assert not tail.any() and tail.size >= int(0.05 * tts_obj.sample_rate)

    def test_two_speak_calls_never_write_at_once(self, monkeypatch):
        import numpy as np
        gate = {"entered": threading.Event(), "release": threading.Event()}
        log = self._device(monkeypatch, block_first_write=gate)
        chunk = np.ones(64, dtype=np.float32).tobytes()
        tts_obj = self._cartesia([chunk])
        tts_obj.speak("first")
        assert gate["entered"].wait(3)
        tts_obj.speak("second")  # gives up waiting on the first after 0.5 s
        time.sleep(0.8)
        gate["release"].set()
        assert self._wait(lambda: len(log["writes"]) >= 2)
        assert log["peak"] == 1

    def test_a_cancelled_sentence_never_restarts_a_silenced_stream(self, monkeypatch):
        import numpy as np
        from tts import _OutputStream
        log = self._device(monkeypatch)
        holder = _OutputStream(44100)
        holder.acquire()
        checked, go = threading.Event(), threading.Event()

        class LateCancel:
            """The writer reads 'not cancelled', then stop() runs before it acts."""
            def __init__(self):
                self.flag = False
                self.calls = 0

            def is_set(self):
                self.calls += 1
                if self.calls == 1:
                    checked.set()
                    go.wait(3)
                    return False
                return self.flag

        cancel = LateCancel()
        writer = threading.Thread(target=holder.write, args=(np.ones(64, dtype=np.float32), cancel))
        writer.start()
        assert checked.wait(3)
        cancel.flag = True  # stop(): the flag first, then the abort
        threading.Timer(0.1, go.set).start()
        holder.abort()
        writer.join(3)
        stream = log["streams"][0]
        assert stream.starts == 1  # the cancelled writer never restarted it
        assert stream.is_stopped or stream.closed  # and whatever it wrote was cut

    def test_an_interrupted_write_closes_the_device_and_the_next_press_reopens_it(self, monkeypatch):
        import numpy as np
        gate = {"entered": threading.Event(), "release": threading.Event()}
        log = self._device(monkeypatch, block_first_write=gate)
        chunk = np.ones(64, dtype=np.float32).tobytes()
        tts_obj = self._cartesia([chunk, chunk])
        tts_obj.speak_sentence("old answer.")
        assert gate["entered"].wait(3)
        tts_obj.stop()  # MME keeps queuing the rest of this chunk after an abort
        gate["release"].set()
        assert self._wait(lambda: log["streams"][0].closed)
        tts_obj.warm()
        assert len(log["streams"]) == 2 and log["streams"][1].starts == 1

    def test_a_sentence_handed_over_during_stop_never_plays(self, monkeypatch):
        import types
        import numpy as np
        import tts as tts_module
        log = self._device(monkeypatch)
        tts_obj = self._cartesia([np.ones(64, dtype=np.float32).tobytes()])
        fired = []

        class StopDuringHandover(threading.Event):
            def __init__(self):
                super().__init__()
                if not fired:  # stop() lands between the epoch check and the handover
                    fired.append(True)
                    tts_obj.stop()

        monkeypatch.setattr(tts_module, "threading", types.SimpleNamespace(
            Event=StopDuringHandover, Lock=threading.Lock, Thread=threading.Thread))
        tts_obj.speak_sentence("old answer.")
        time.sleep(0.5)
        assert fired and not [w for w in log["writes"] if w[1].any()]

    def test_a_restart_that_outlasts_the_press_never_plays_the_old_sentence(self, monkeypatch):
        import numpy as np
        import sounddevice
        from tts import _OutputStream
        log = self._device(monkeypatch)
        monkeypatch.setattr(_OutputStream, "_ABORT_WAIT_S", 0.2)
        holder = _OutputStream(44100)
        holder.acquire()
        holder.abort()  # a previous press silenced it
        stream = log["streams"][0]
        in_start, release = threading.Event(), threading.Event()
        real_start = type(stream).start

        def slow_start(self):
            in_start.set()
            release.wait(3)  # the driver is slow to restart
            real_start(self)

        monkeypatch.setattr(type(stream), "start", slow_start)
        cancel = threading.Event()
        writer = threading.Thread(target=holder.write, args=(np.ones(64, dtype=np.float32), cancel))
        writer.start()
        assert in_start.wait(3)
        cancel.set()
        holder.abort()  # gives up after _ABORT_WAIT_S
        release.set()
        writer.join(3)
        assert not [w for w in log["writes"] if w[1].any() and w[2]]

    def test_a_write_that_fails_after_a_press_still_closes_the_device(self, monkeypatch):
        import numpy as np
        import sounddevice
        log = self._device(monkeypatch)
        tts_obj = self._cartesia([np.ones(64, dtype=np.float32).tobytes()])
        tts_obj.warmup()
        entered, release = threading.Event(), threading.Event()

        def failing_write(self, data):
            entered.set()
            release.wait(3)  # the press lands mid-write, then the driver errors
            raise sounddevice.PortAudioError("Unanticipated host error")

        monkeypatch.setattr(type(log["streams"][0]), "write", failing_write)
        tts_obj.speak_sentence("old answer.")
        assert entered.wait(3)
        tts_obj.stop()
        release.set()
        assert self._wait(lambda: log["streams"][0].closed)

    def test_a_sentence_dequeued_just_before_a_press_never_plays(self, monkeypatch):
        import queue as queue_module
        import types
        import numpy as np
        import tts as tts_module
        log = self._device(monkeypatch)
        owner = {}

        class StopAfterDequeue(queue_module.Queue):
            def get(self, *a, **k):
                item = super().get(*a, **k)
                if self.maxsize == 0 and item is not tts_module._SHUTDOWN_SENTINEL and not owner.get("fired"):
                    owner["fired"] = True
                    owner["tts"].stop()  # the press lands after the sentence left the queue
                return item

        monkeypatch.setattr(tts_module, "queue", types.SimpleNamespace(
            Queue=StopAfterDequeue, Empty=queue_module.Empty))
        tts_obj = self._cartesia([np.ones(64, dtype=np.float32).tobytes()])
        owner["tts"] = tts_obj
        tts_obj.speak_sentence("old answer.")
        time.sleep(0.5)
        assert owner.get("fired") and not [w for w in log["writes"] if w[1].any()]
