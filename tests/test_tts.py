"""Unit tests for tts.py.

All tests are mock-based. Zero real network, zero real audio. Green in <2s.
Covers: TTS abstract, CartesiaSonicTTS speak/stop/cancel Event pattern.
"""
import threading
import time

import pytest


def test_tts_module_importable():
    import tts  # noqa: F401


class TestTTSAbstract:

    def test_tts_abstract_raises(self):
        from tts import TTS
        with pytest.raises(TypeError):
            TTS()  # type: ignore[abstract]


class TestCartesiaSonicTTSSpeak:

    def _make_tts(self, chunks=None):
        """Helper: build a CartesiaSonicTTS with mock factories.

        Returns (tts_instance, fake_client, fake_play_callable).
        player_factory returns (play_fn, None) — None for the stream since
        tests don't need real sounddevice cleanup.
        """
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        fake_client = MagicMock(name="fake_cartesia_client")
        fake_client.tts.generate.return_value.iter_bytes.return_value = iter(
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

        fake_client.tts.generate.assert_called_once()
        call_kwargs = fake_client.tts.generate.call_args.kwargs
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
        fake_client.tts.generate.assert_not_called()
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
        fake_client.tts.generate.return_value.iter_bytes.return_value = gen_chunks()
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

        fake_client.tts.generate.return_value.iter_bytes.return_value = iter(
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
        """Path A Task 5: multiple speak_sentence calls play sequentially via
        a queue worker, NOT cancelling each other (as the old speak-delegation
        behavior did). Unblocks sentence-level TTS streaming in app.py pipeline.
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

            client.tts.generate.side_effect = gen_response
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
            f"Expected >=3 sentences played, got {played_count[0]} — "
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

            client.tts.generate.side_effect = slow_gen
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

        # Give worker a moment to pick up first item (but not finish — it's slow)
        _t.sleep(0.02)

        tts_obj.stop()
        assert tts_obj._sentence_queue.empty(), (
            "stop() must drain queued sentences — none should remain pending"
        )

    def test_prefetch_fires_before_previous_playback_completes(self):
        """Option B: HTTP request for sentence N+1 must start while N is still
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

            client.tts.generate.side_effect = gen
            return client

        def player_factory(*, sample_rate):
            def fake_play(samples):
                first_play_started.set()
                # Block until the test explicitly releases — this simulates
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
            "First sentence's fake_play was never entered — something is "
            "wrong with the queue worker startup."
        )

        # Prefetch is decoupled from playback — give the prefetch worker
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

            client.tts.generate.side_effect = gen
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


# --- ElevenLabs TTS (Sprint 4) -------------------------------------------------


class TestElevenLabsTTSSpeak:
    """Mirrors TestCartesiaSonicTTSSpeak — same speak/stop/cancel
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
        config). This is the load-bearing divergence from Cartesia which
        emits float32 directly."""
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
        # 0.5 amplitude after divide by 32768 ≈ 0.4999... — assert close
        assert abs(float(arr[0]) - 0.5) < 0.001
        assert abs(float(arr[1]) + 0.5) < 0.001
        assert abs(float(arr[3])) < 0.001


class TestElevenLabsTTSSentenceQueue:
    """Mirrors TestCartesiaSonicTTSSpeak::test_speak_sentence_queues_and_plays_sequentially.
    Multiple speak_sentence calls play sequentially via the prefetch+playback
    two-thread architecture (Option B), NOT cancelling each other."""

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
            f"Expected >=3 sentences played, got {played_count[0]} — "
            "queue worker may not be consuming sequentially"
        )


class TestElevenLabsTTSStop:
    """5-pronged kill (NOT 6 — no response.close, since elevenlabs SDK
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
