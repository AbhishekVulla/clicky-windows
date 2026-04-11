"""Unit tests for tts.py.

All tests are mock-based. Zero real network, zero real audio. Green in <2s.
Mirrors tests/test_ai.py class-based structure + DI-mock pattern.
"""
import threading
import time

import pytest


def test_tts_module_importable():
    import tts  # noqa: F401


class TestTTSAbstract:
    """Tests for tts.TTS abstract base class."""

    def test_tts_abstract_raises(self):
        """TTS() must raise TypeError because all methods are abstract."""
        from tts import TTS
        with pytest.raises(TypeError):
            TTS()  # type: ignore[abstract]


class TestCartesiaSonicTTSSpeak:
    """Tests for CartesiaSonicTTS.speak using injected mock factories."""

    def _make_tts(self, chunks=None):
        """Helper: build a CartesiaSonicTTS with mock factories.

        Returns (tts_instance, fake_client, fake_play_callable).
        """
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        fake_client = MagicMock(name="fake_cartesia_client")
        # tts.generate() returns a BinaryAPIResponse; iter_bytes() is the chunk iterator.
        # Mock the full chain so code can call `client.tts.generate(...).iter_bytes()`.
        fake_client.tts.generate.return_value.iter_bytes.return_value = iter(
            chunks if chunks is not None else [b"\x00" * 16, b"\x00" * 16]
        )
        fake_play = MagicMock(name="fake_play")

        def client_factory(*, api_key):
            return fake_client

        def player_factory(*, sample_rate):
            return fake_play

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )
        return tts_obj, fake_client, fake_play

    def test_speak_dispatches_to_background_thread_non_blocking(self):
        """speak('hello') returns <50ms and calls client.tts.generate once."""
        tts_obj, fake_client, fake_play = self._make_tts()

        # Count threads before to locate the daemon after.
        pre_threads = set(threading.enumerate())

        t0 = time.perf_counter()
        tts_obj.speak("hello")
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert elapsed_ms < 50, (
            f"speak() blocked for {elapsed_ms:.1f}ms (should be non-blocking)"
        )

        # Find the spawned thread and join it (mock is fast, <5s).
        post_threads = set(threading.enumerate())
        new_threads = post_threads - pre_threads
        for t in new_threads:
            t.join(timeout=5)
            assert not t.is_alive(), "speak() daemon thread did not finish"

        # Cartesia client called once with the correct transcript + voice shape.
        fake_client.tts.generate.assert_called_once()
        call_kwargs = fake_client.tts.generate.call_args.kwargs
        assert call_kwargs["transcript"] == "hello"
        assert call_kwargs["model_id"] == "sonic-3"
        assert call_kwargs["voice"]["mode"] == "id"
        assert isinstance(call_kwargs["voice"]["id"], str)
        assert call_kwargs["output_format"]["container"] == "raw"
        assert call_kwargs["output_format"]["encoding"] == "pcm_f32le"
        assert call_kwargs["output_format"]["sample_rate"] == 44100

        # Player called at least once (we gave it 2 non-empty chunks).
        assert fake_play.call_count >= 1

    def test_speak_empty_string_skips_thread(self):
        """Empty and whitespace-only text must not touch factories."""
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        client_factory = MagicMock(name="client_factory")
        player_factory = MagicMock(name="player_factory")
        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        pre_threads = set(threading.enumerate())
        tts_obj.speak("")
        tts_obj.speak("   \t\n")
        post_threads = set(threading.enumerate())

        assert post_threads == pre_threads, "empty speak() spawned a thread"
        client_factory.assert_not_called()
        player_factory.assert_not_called()

    def test_do_speak_error_raises_runtime_error(self):
        """_do_speak wraps underlying errors as RuntimeError with diagnostics.

        We call _do_speak() directly (synchronous) to avoid the fragility of
        cross-thread exception capture, per the tts.py docstring contract that
        _do_speak is exposed as a standalone method for exactly this test.
        """
        from tts import CartesiaSonicTTS

        def bad_client_factory(*, api_key):
            raise ConnectionError("boom: DNS lookup failed")

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=bad_client_factory,
            player_factory=lambda *, sample_rate: (lambda samples: None),
        )

        with pytest.raises(RuntimeError) as exc_info:
            tts_obj._do_speak("hello")

        msg = str(exc_info.value)
        assert "Cartesia" in msg
        assert "check" in msg.lower()
        assert "boom: DNS lookup failed" in msg

    def test_stop_sets_flag_checked_by_next_speak(self):
        """After stop(), a subsequent _do_speak() exits before any player call.

        Phase 1 cancellation is best-effort: stop() sets _stopped, and
        _do_speak() checks it at startup. We call _do_speak synchronously to
        make the timing deterministic; the flag semantics are what we assert.
        """
        tts_obj, fake_client, fake_play = self._make_tts()

        tts_obj.stop()
        # Directly call _do_speak without going through speak() (which would
        # reset _stopped to False). This mirrors the check a pre-stopped
        # thread would make at its own startup.
        tts_obj._do_speak("should not be spoken")

        fake_client.tts.generate.assert_not_called()
        fake_play.assert_not_called()

    def test_stop_during_streaming_exits_between_chunks(self):
        """If _stopped flips mid-stream, the loop exits before the next chunk.

        Inject a chunk iterator that flips the flag after yielding one chunk.
        The player should be called exactly once (for chunk 1), not twice.
        """
        from unittest.mock import MagicMock
        from tts import CartesiaSonicTTS

        tts_obj_holder = {}

        def gen_chunks():
            yield b"\x00" * 16  # chunk 1 -- plays
            tts_obj_holder["tts"].stop()  # flip flag
            yield b"\x00" * 16  # chunk 2 -- skipped

        fake_client = MagicMock(name="fake_cartesia_client")
        fake_client.tts.generate.return_value.iter_bytes.return_value = gen_chunks()
        fake_play = MagicMock(name="fake_play")

        tts_obj = CartesiaSonicTTS(
            api_key="test-key",
            client_factory=lambda *, api_key: fake_client,
            player_factory=lambda *, sample_rate: fake_play,
        )
        tts_obj_holder["tts"] = tts_obj

        tts_obj._do_speak("two chunks one stop")

        assert fake_play.call_count == 1, (
            f"expected 1 play call (chunk 2 skipped after stop), got {fake_play.call_count}"
        )
