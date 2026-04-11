"""Unit tests for stt.py.

All tests are mock-based. Zero real mic, zero real WebSocket. Green in <2s.
Mirrors the class-based structure of ``tests/test_ai.py`` and the DI mock
pattern of ``tests/test_overlay.py`` (``_MockScreen``-style factories).
"""
from unittest.mock import MagicMock

import pytest


def test_stt_module_importable():
    import stt  # noqa: F401


# --- STT abstract base -------------------------------------------------------

class TestSTT:
    """Tests for the abstract base class."""

    def test_stt_is_abstract(self):
        """``STT()`` must raise ``TypeError`` because start/stop/on_partial are abstract."""
        from stt import STT

        with pytest.raises(TypeError):
            STT()  # type: ignore[abstract]


# --- AssemblyAIStreamingSTT --------------------------------------------------

class TestAssemblyAIStreamingSTT:
    """Tests for ``stt.AssemblyAIStreamingSTT`` using DI-mocked factories.

    The ``client_factory`` argument lets us substitute a ``MagicMock`` for the
    real ``StreamingClient``, and ``audio_stream_factory`` substitutes for
    ``sounddevice.RawInputStream``. No test touches the real audio device,
    no test opens a real WebSocket.
    """

    def _make_stt(self, **overrides):
        """Build an ``AssemblyAIStreamingSTT`` with mock factories.

        Returns ``(stt, fake_client, client_factory, audio_stream_factory)``.
        The audio stream factory captures the ``callback=`` kwarg so tests
        can verify the sample_rate/blocksize/dtype/channels that would be
        passed to ``sounddevice.RawInputStream``.
        """
        from stt import AssemblyAIStreamingSTT

        fake_client = MagicMock(name="StreamingClient")
        client_factory = MagicMock(name="client_factory", return_value=fake_client)

        fake_audio_stream = MagicMock(name="RawInputStream")

        captured: dict = {}

        def audio_stream_factory(callback, **kwargs):
            captured["callback"] = callback
            captured["kwargs"] = kwargs
            return fake_audio_stream

        # Wrap in MagicMock so tests can still assert call_count etc.
        audio_stream_factory_mock = MagicMock(
            name="audio_stream_factory", side_effect=audio_stream_factory
        )

        stt_obj = AssemblyAIStreamingSTT(
            api_key="test-key",
            client_factory=client_factory,
            audio_stream_factory=audio_stream_factory_mock,
            **overrides,
        )
        return stt_obj, fake_client, fake_audio_stream, client_factory, audio_stream_factory_mock

    def test_start_constructs_client_and_audio_stream(self):
        """``start()`` calls both factories once and wires StreamingParameters
        with ``u3-rt-pro`` + ``pcm_s16le`` + ``format_turns=True``."""
        from assemblyai.streaming.v3 import Encoding, StreamingParameters

        stt_obj, fake_client, fake_audio_stream, client_factory, audio_factory = (
            self._make_stt()
        )

        stt_obj.start()

        # 1. Client factory called once with the API key.
        client_factory.assert_called_once_with("test-key")

        # 2. Audio factory called once. Verify it was given a callback
        #    (the internal _on_audio_chunk method).
        audio_factory.assert_called_once()
        assert callable(audio_factory.call_args.args[0] or audio_factory.call_args.kwargs.get("callback"))

        # 3. Event handlers wired on the fake client.
        assert fake_client.on.call_count >= 2  # Turn + Error subscriptions

        # 4. connect() called with a StreamingParameters carrying Clicky's
        #    exact shape (sample_rate=16000, speech_model=u3-rt-pro,
        #    encoding=pcm_s16le, format_turns=True).
        fake_client.connect.assert_called_once()
        params = fake_client.connect.call_args.args[0]
        assert isinstance(params, StreamingParameters)
        assert params.sample_rate == 16000
        assert params.speech_model == "u3-rt-pro"
        assert params.encoding == Encoding.pcm_s16le
        assert params.format_turns is True

        # 5. Audio stream was started.
        fake_audio_stream.start.assert_called_once()

    def test_start_idempotent(self):
        """Calling ``start()`` twice is a no-op on the second call:
        factories are still invoked exactly once total."""
        stt_obj, fake_client, _, client_factory, audio_factory = self._make_stt()

        stt_obj.start()
        stt_obj.start()

        assert client_factory.call_count == 1
        assert audio_factory.call_count == 1
        assert fake_client.connect.call_count == 1

    def test_stop_sends_force_endpoint_and_returns_final(self):
        """``stop()`` calls ``force_endpoint()`` on the client, waits for the
        formatted-Turn event, and returns the accumulated transcript.

        Scaffolding: after wiring is done, we grab the Turn handler that the
        STT registered on the client and synthesize a ``TurnEvent`` with
        ``turn_is_formatted=True``. We trigger it from inside
        ``force_endpoint`` (via side_effect) so the ``_final_event.wait()``
        call in ``stop()`` unblocks immediately.

        R1 note: audio_stream.stop/close and client.disconnect now run in a
        daemon thread ("stt-teardown") so stop() can hit the 500ms SLA even
        when the real SDK would block on thread joins. We join that thread
        before asserting because the mocks make it essentially instant, but
        we still need to wait for it to schedule and execute on slow CI.
        """
        import threading as _t
        import time as _time
        stt_obj, fake_client, fake_audio_stream, _, _ = self._make_stt()

        # Capture the Turn handler as StreamingClient.on(Turn, handler) is called.
        turn_handler_holder: dict = {}

        def record_on(event, handler):
            # StreamingEvents is an enum; compare by name to avoid tight coupling.
            if getattr(event, "name", str(event)) == "Turn":
                turn_handler_holder["handler"] = handler

        fake_client.on.side_effect = record_on

        # When force_endpoint() is called, synthesize a final Turn event.
        def synth_final():
            handler = turn_handler_holder.get("handler")
            assert handler is not None, "Turn handler should be registered in start()"
            fake_turn = MagicMock()
            fake_turn.transcript = "how do I save this file"
            fake_turn.turn_is_formatted = True
            handler(fake_client, fake_turn)

        fake_client.force_endpoint.side_effect = synth_final

        stt_obj.start()
        result = stt_obj.stop()

        # Wait for the daemon "stt-teardown" thread to finish releasing
        # resources before asserting on audio_stream / client mocks.
        deadline = _time.time() + 2.0
        while _time.time() < deadline:
            if any(
                th.name == "stt-teardown"
                for th in _t.enumerate()
            ):
                _time.sleep(0.01)
                continue
            break

        fake_client.force_endpoint.assert_called_once()
        fake_audio_stream.stop.assert_called_once()
        fake_audio_stream.close.assert_called_once()
        fake_client.disconnect.assert_called_once()
        assert result == "how do I save this file"

    def test_on_partial_transcript_callback_fired(self):
        """Registering a partial callback then simulating a non-formatted
        Turn fires the callback with the expected text."""
        stt_obj, fake_client, _, _, _ = self._make_stt()

        # Capture the Turn handler registered during start().
        turn_handler_holder: dict = {}

        def record_on(event, handler):
            if getattr(event, "name", str(event)) == "Turn":
                turn_handler_holder["handler"] = handler

        fake_client.on.side_effect = record_on

        received: list = []
        stt_obj.on_partial_transcript(received.append)
        stt_obj.start()

        # Synthesize a partial (turn_is_formatted=False).
        partial = MagicMock()
        partial.transcript = "how do i"
        partial.turn_is_formatted = False
        turn_handler_holder["handler"](fake_client, partial)

        assert received == ["how do i"]

    def test_connection_error_raises_runtime_error_with_diagnostic(self):
        """If ``client_factory`` raises, ``start()`` re-raises ``RuntimeError``
        with a diagnostic message mentioning AssemblyAI and troubleshooting."""
        from stt import AssemblyAIStreamingSTT

        def failing_factory(api_key):
            raise ConnectionError("DNS lookup failed")

        stt_obj = AssemblyAIStreamingSTT(
            api_key="test-key",
            client_factory=failing_factory,
            audio_stream_factory=MagicMock(),
        )

        with pytest.raises(RuntimeError) as exc_info:
            stt_obj.start()

        msg = str(exc_info.value)
        assert "AssemblyAI" in msg
        assert "check" in msg.lower()
        # Original error should be chained for debugging.
        assert isinstance(exc_info.value.__cause__, ConnectionError)
