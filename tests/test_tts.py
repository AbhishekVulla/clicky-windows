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
