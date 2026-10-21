"""Unit tests for stt.py.

All tests are mock-based. Zero real mic, zero real WebSocket. Green in <2s.
Mirrors the class-based structure of ``tests/test_ai.py`` and the DI mock
pattern of ``tests/test_overlay.py`` (``_MockScreen``-style factories).
"""
import threading
import time
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
        assert params.format_turns is False

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

        audio_stream.stop/close and client.disconnect run in a
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

        # Synthesize a partial (turn_is_formatted=False, end_of_turn=False).
        # Explicit end_of_turn=False matches real AssemblyAI TurnEvent shape
        # and guards against MagicMock's truthy auto-attribute.
        partial = MagicMock()
        partial.transcript = "how do i"
        partial.turn_is_formatted = False
        partial.end_of_turn = False
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

    def test_on_audio_chunk_computes_rms_and_calls_level_callback(self):
        """Each audio chunk must compute RMS and emit via the
        registered on_audio_level callback. Drives the waveform widget."""
        import struct
        stt_obj, _, _, _, _ = self._make_stt()
        stt_obj.connect()
        stt_obj.start_recording()

        received: list = []
        stt_obj.on_audio_level(received.append)

        # 1024-frame int16 PCM buffer with known amplitude (0.5 of int16 max)
        samples = [int(0.5 * 32767)] * 1024
        pcm_bytes = struct.pack("<" + "h" * 1024, *samples)

        stt_obj._on_audio_chunk(pcm_bytes, 1024, None, None)

        assert len(received) == 1, (
            f"Expected 1 level emission per chunk, got {len(received)}"
        )
        assert 0.0 < received[0] <= 1.0, (
            f"Level must be clamped to (0, 1], got {received[0]}"
        )

    def test_audio_level_decay_filter_prevents_sudden_drops(self):
        """Level must never drop faster than AUDIO_POWER_DECAY between chunks
        (smoother waveform, no jitter during natural speech pauses)."""
        import struct
        from config import AUDIO_POWER_DECAY
        stt_obj, _, _, _, _ = self._make_stt()
        stt_obj.connect()
        stt_obj.start_recording()

        received: list = []
        stt_obj.on_audio_level(received.append)

        loud = struct.pack("<" + "h" * 1024, *([int(0.8 * 32767)] * 1024))
        silent = b"\x00" * 2048

        stt_obj._on_audio_chunk(loud, 1024, None, None)
        stt_obj._on_audio_chunk(silent, 1024, None, None)

        assert received[1] >= received[0] * AUDIO_POWER_DECAY * 0.95, (
            f"Level dropped too fast: {received[0]} → {received[1]}, "
            f"expected floor of {received[0] * AUDIO_POWER_DECAY:.3f}"
        )

    def test_stop_recording_waits_for_delayed_end_of_turn(self):
        """Regression for the "How do I add..." cutoff.

        AssemblyAI emits end_of_turn=True ~300-700ms AFTER force_endpoint()
        is called. stop_recording must keep waiting until the real deadline
        (2s) OR until a final event arrives, not exit after the FIRST 300ms
        with no event and return the stale _latest_partial.

        This test simulates the timing by delaying the end_of_turn=True
        event 500ms (between the "too fast" and "too slow" thresholds)
        and asserting the final transcript is returned, not the partial.
        """
        import threading as _threading
        import time as _time

        stt_obj, fake_client, fake_audio_stream, _, _ = self._make_stt()

        turn_handler_holder: dict = {}

        def record_on(event, handler):
            if getattr(event, "name", str(event)) == "Turn":
                turn_handler_holder["handler"] = handler

        fake_client.on.side_effect = record_on

        # Fire the interim partial immediately (no end_of_turn) so
        # _latest_partial is populated (matches real behavior).
        def on_force_endpoint():
            handler = turn_handler_holder["handler"]
            partial = MagicMock()
            partial.transcript = "How do I add—"
            partial.turn_is_formatted = True
            partial.end_of_turn = False
            handler(fake_client, partial)

            # 500ms later: real final end_of_turn=True. This arrives AFTER
            # the first 300ms wait of stop_recording's loop, so the loop must
            # still be waiting.
            def delayed_final():
                _time.sleep(0.5)
                final = MagicMock()
                final.transcript = "How do I add an MCP server?"
                final.turn_is_formatted = True
                final.end_of_turn = True
                handler(fake_client, final)

            _threading.Thread(target=delayed_final, daemon=True).start()

        fake_client.force_endpoint.side_effect = on_force_endpoint

        stt_obj.start()
        result = stt_obj.stop()

        assert result == "How do I add an MCP server?", (
            f"Expected final transcript 'How do I add an MCP server?', got {result!r}: "
            "stop_recording is returning the stale partial instead of waiting "
            "for the real end_of_turn=True event."
        )

    def test_stop_recording_grace_window_is_100ms(self):
        """After the first end_of_turn=True event fires, stop_recording waits
        only 100ms for a trailing multi-utterance event. With Conservative VAD
        the multi-utterance case is rare enough that 100ms is sufficient, and
        a 300ms grace would cost ~200ms median STT finalize.

        Tests by firing end_of_turn=True synchronously from force_endpoint's
        side_effect, then measuring how long stop_recording takes to return.
        Expected ~100ms grace + tiny overhead. Upper bound 200ms, so a
        300ms grace fails.
        """
        import time as _time

        stt_obj, fake_client, _, _, _ = self._make_stt()

        turn_handler_holder: dict = {}

        def record_on(event, handler):
            if getattr(event, "name", str(event)) == "Turn":
                turn_handler_holder["handler"] = handler

        fake_client.on.side_effect = record_on

        def on_force_endpoint():
            handler = turn_handler_holder["handler"]
            final = MagicMock()
            final.transcript = "hello world"
            final.turn_is_formatted = False
            final.end_of_turn = True
            handler(fake_client, final)

        fake_client.force_endpoint.side_effect = on_force_endpoint

        stt_obj.connect()
        stt_obj.start_recording()

        t0 = _time.perf_counter()
        result = stt_obj.stop_recording()
        elapsed = _time.perf_counter() - t0

        assert result == "hello world"
        assert elapsed < 0.2, (
            f"stop_recording took {elapsed * 1000:.0f}ms after first "
            f"end_of_turn; the grace window must be ~100ms, not "
            f"{elapsed * 1000:.0f}ms. Was it reverted to the "
            f"old 300ms grace?"
        )

    def test_on_turn_ignores_formatted_revision_without_end_of_turn(self):
        """Regression for the doubled "That's kind of" stutter.

        AssemblyAI may emit a formatted-revision event (end_of_turn=False,
        turn_is_formatted=True) after an end_of_turn=True event. If our
        handler fired on is_formatted as a fallback, that extra event would
        append its text to _final_transcript and produce a cut-off "That's
        kind of" followed by "That's kind of weird." from a single clean
        utterance.

        Per AssemblyAI docs: "The only reliable way to detect turn completion
        is end_of_turn: true." Handler must ignore is_formatted events that
        don't have end_of_turn=True.
        """
        stt_obj, fake_client, _, _, _ = self._make_stt()
        stt_obj.connect()
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)  # events only come from a published session

        # First event: real end_of_turn finalization.
        final_event = MagicMock()
        final_event.transcript = "That's kind of weird."
        final_event.turn_is_formatted = False
        final_event.end_of_turn = True
        stt_obj._on_turn(fake_client, final_event)

        # Second event: a formatted revision arriving afterward. Must NOT
        # append, otherwise we get the stutter artifact.
        revision_event = MagicMock()
        revision_event.transcript = "That's kind of—"
        revision_event.turn_is_formatted = True
        revision_event.end_of_turn = False
        stt_obj._on_turn(fake_client, revision_event)

        assert stt_obj._final_transcript == "That's kind of weird.", (
            f"Expected revision to be ignored, got {stt_obj._final_transcript!r}"
        )

    def test_set_tts_grace_until_blocks_mic_chunks(self):
        """set_tts_grace_until(t) must drop mic chunks until time.time() >= t.

        Prevents TTS speaker decay after tts.stop() from being transcribed as
        the next PTT's audio (acoustic feedback loop: debug logs show
        transcripts containing phantom phrases from the previous TTS).
        """
        import time as _t
        stt_obj, fake_client, _, _, _ = self._make_stt()
        stt_obj.connect()
        stt_obj.start_recording()

        # Before grace: chunks forwarded normally.
        stt_obj._on_audio_chunk(b"\x00" * 2048, 1024, None, None)
        assert stt_obj._chunk_count == 1

        # Set grace window 200ms into the future.
        stt_obj.set_tts_grace_until(_t.time() + 0.200)

        # Chunk during grace: must be dropped.
        stt_obj._on_audio_chunk(b"\x00" * 2048, 1024, None, None)
        assert stt_obj._chunk_count == 1, (
            "Expected chunk dropped during TTS grace, but it was forwarded"
        )

        # End grace window explicitly (avoid real-time sleep in tests).
        stt_obj._tts_grace_until = 0.0
        stt_obj._on_audio_chunk(b"\x00" * 2048, 1024, None, None)
        assert stt_obj._chunk_count == 2

    def test_on_turn_sets_final_event_on_end_of_turn_flag(self):
        """Regression: end_of_turn=True must set _final_event regardless of turn_is_formatted.

        We run with format_turns=False, so a formatted event never fires.
        Gating _final_event on turn_is_formatted cuts the transcript off
        after a few characters ('How do I'). The only reliable completion
        signal on u3-rt-pro is end_of_turn.
        """
        stt_obj, fake_client, _, _, _ = self._make_stt()
        stt_obj.connect()
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)  # events only come from a published session

        # Simulate an unformatted Turn with end_of_turn=True (format_turns=False mode).
        event = MagicMock()
        event.transcript = "How do I make my repo public?"
        event.turn_is_formatted = False
        event.end_of_turn = True

        stt_obj._on_turn(fake_client, event)

        assert stt_obj._final_event.is_set(), (
            "Expected _final_event to be set on end_of_turn=True, "
            "but it was only triggering on turn_is_formatted=True."
        )
        assert stt_obj._final_transcript == "How do I make my repo public?"


class TestFasterWhisperSTT:
    """Local offline STT. Batch transcription adapted to the
    connect/start_recording/stop_recording lifecycle app.py drives."""

    @staticmethod
    def _seg(text):
        s = type("Seg", (), {})()
        s.text = text
        return s

    def test_records_then_transcribes_buffer(self, mocker):
        from stt import FasterWhisperSTT
        fake_model = mocker.MagicMock()
        fake_model.transcribe.return_value = ([self._seg("hello world")], None)
        stt = FasterWhisperSTT(
            model_factory=lambda: fake_model,
            audio_stream_factory=lambda cb: mocker.MagicMock(),
        )
        stt.connect()
        stt.start_recording()
        stt._on_audio(b"\x01\x00" * 1024, 1024, None, None)  # feed one chunk
        text = stt.stop_recording()
        assert text == "hello world"
        fake_model.transcribe.assert_called_once()

    def test_empty_buffer_returns_empty_string(self, mocker):
        from stt import FasterWhisperSTT
        stt = FasterWhisperSTT(
            model_factory=lambda: mocker.MagicMock(),
            audio_stream_factory=lambda cb: mocker.MagicMock(),
        )
        stt.connect()
        stt.start_recording()
        assert stt.stop_recording() == ""

    def test_grace_window_drops_chunks(self, mocker):
        """Chunks arriving before _tts_grace_until are not buffered."""
        import time
        from stt import FasterWhisperSTT
        fake_model = mocker.MagicMock()
        fake_model.transcribe.return_value = ([self._seg("x")], None)
        stt = FasterWhisperSTT(
            model_factory=lambda: fake_model,
            audio_stream_factory=lambda cb: mocker.MagicMock(),
        )
        stt.connect()
        stt.start_recording()
        stt.set_tts_grace_until(time.time() + 100)  # far future → drop chunks
        stt._on_audio(b"\x01\x00" * 1024, 1024, None, None)
        assert stt.stop_recording() == ""  # nothing buffered

    def test_lazy_no_faster_whisper_import_when_using_di(self):
        import sys
        sys.modules.pop("faster_whisper", None)
        from stt import FasterWhisperSTT
        stt = FasterWhisperSTT(
            model_factory=lambda: __import__("unittest").mock.MagicMock(),
            audio_stream_factory=lambda cb: __import__("unittest").mock.MagicMock(),
        )
        stt.connect()
        assert "faster_whisper" not in sys.modules


class TestCreateSTTClient:
    def test_routes_assemblyai(self):
        from stt import AssemblyAIStreamingSTT, create_stt_client
        assert isinstance(create_stt_client("assemblyai", "key"), AssemblyAIStreamingSTT)

    def test_routes_faster_whisper(self):
        from stt import FasterWhisperSTT, create_stt_client
        assert isinstance(create_stt_client("faster-whisper", ""), FasterWhisperSTT)

    def test_unknown_provider_raises(self):
        import pytest
        from stt import create_stt_client
        with pytest.raises(ValueError):
            create_stt_client("nope", "")


class TestAssemblyAISessionLifecycle:
    """The billed socket is open only around questions, and recovers after a drop."""

    def _stt(self, connect_gate=None, idle_close_s=None):
        from stt import AssemblyAIStreamingSTT
        clients: list = []

        def factory(api_key):
            client = MagicMock(name=f"client{len(clients)}")
            if connect_gate is not None:
                client.connect.side_effect = lambda params: connect_gate.wait(2)
            clients.append(client)
            return client

        audio = MagicMock(name="RawInputStream")
        holder: dict = {}

        def audio_factory(callback, **kwargs):
            holder["callback"] = callback
            return audio

        extra = {} if idle_close_s is None else {"idle_close_s": idle_close_s}
        stt_obj = AssemblyAIStreamingSTT(
            api_key="k", client_factory=factory, audio_stream_factory=audio_factory, **extra,
        )
        return stt_obj, clients, audio, holder

    @staticmethod
    def _speak(holder, data: bytes):
        holder["callback"](data, len(data) // 2, None, None)

    def test_startup_warms_a_session_that_closes_when_unused(self):
        stt_obj, clients, audio, _ = self._stt(idle_close_s=0.05)
        stt_obj.connect()
        audio.start.assert_called_once()
        assert stt_obj._socket_ready.wait(1)
        for _ in range(100):
            if clients[0].disconnect.called:
                break
            time.sleep(0.02)
        assert len(clients) == 1 and clients[0].disconnect.called

    def test_a_press_after_an_idle_close_opens_a_new_session(self):
        stt_obj, clients, _, _ = self._stt(idle_close_s=0.05)
        stt_obj.connect()
        assert stt_obj._socket_ready.wait(1)
        for _ in range(100):
            if stt_obj._client is None:
                break
            time.sleep(0.02)
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        assert len(clients) == 2 and clients[1].connect.called

    def test_speech_during_connect_reaches_the_socket_in_order(self):
        gate = threading.Event()
        stt_obj, clients, _, holder = self._stt(connect_gate=gate)
        stt_obj.connect()
        stt_obj.start_recording()
        self._speak(holder, b"\x01\x00" * 4)
        self._speak(holder, b"\x02\x00" * 4)
        gate.set()
        assert stt_obj._socket_ready.wait(1)
        self._speak(holder, b"\x03\x00" * 4)
        sent = [c.args[0] for c in clients[0].stream.call_args_list]
        assert sent == [b"\x01\x00" * 4, b"\x02\x00" * 4, b"\x03\x00" * 4]

    def test_socket_closes_when_idle(self):
        stt_obj, clients, _, _ = self._stt(idle_close_s=0.05)
        stt_obj.connect()
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        stt_obj._final_event.set()
        stt_obj.stop_recording()
        for _ in range(100):
            if clients[0].disconnect.called:
                break
            time.sleep(0.02)
        assert clients[0].disconnect.called

    def test_a_dropped_session_reconnects_on_the_next_press(self):
        stt_obj, clients, _, _ = self._stt()
        stt_obj.connect()
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        stt_obj._on_error(clients[0], "connection closed")
        with pytest.raises(RuntimeError):
            stt_obj.stop_recording()
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        assert len(clients) == 2

    def test_a_server_side_close_also_reconnects(self):
        stt_obj, clients, _, _ = self._stt()
        stt_obj.connect()
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        stt_obj._on_termination(clients[0], MagicMock())
        with pytest.raises(RuntimeError, match="closed the session"):
            stt_obj.stop_recording()
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        assert len(clients) == 2


class TestAssemblyAIHungConnect:
    """A session that hangs while opening must not block every later press."""

    def test_next_press_abandons_a_hung_open(self, monkeypatch):
        import stt as stt_module
        from stt import AssemblyAIStreamingSTT
        monkeypatch.setattr(stt_module, "_CONNECT_WAIT_S", 0.2)
        hung = threading.Event()
        clients: list = []

        def factory(api_key):
            client = MagicMock(name=f"client{len(clients)}")
            if not clients:  # the first open never returns until released
                client.connect.side_effect = lambda params: hung.wait(5)
            clients.append(client)
            return client

        stt_obj = AssemblyAIStreamingSTT(
            api_key="k", client_factory=factory,
            audio_stream_factory=lambda callback, **kw: MagicMock(),
        )
        stt_obj.connect()
        stt_obj.start_recording()
        with pytest.raises(RuntimeError, match="did not open in time"):
            stt_obj.stop_recording()

        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        assert len(clients) == 2 and stt_obj._client is clients[1]

        hung.set()  # the stale first open finally returns
        for _ in range(100):
            if clients[0].disconnect.called:
                break
            time.sleep(0.02)
        assert clients[0].disconnect.called
        assert stt_obj._client is clients[1]


class _FakeCartesiaSession:
    """Speaks Cartesia's manual-finalize event format: transcript / flush_done / done / error."""

    def __init__(self, on_finalize=()):
        import queue
        self.on_finalize = list(on_finalize)
        self.sent_audio: list = []
        self.sent: list = []
        self.closed = False
        self.inbox = queue.Queue()

    def push(self, event):
        import json
        self.inbox.put(json.dumps(event).encode())

    def send_raw(self, data):
        self.sent_audio.append(data)

    def send(self, event):
        self.sent.append(event)
        if event == "finalize":
            for e in self.on_finalize:
                self.push(e)
        elif event == "close":
            self.push({"type": "done"})

    def recv_bytes(self):
        item = self.inbox.get(timeout=5)
        if item is None:
            raise ConnectionError("closed")
        return item

    def close(self, **kwargs):
        self.closed = True
        self.inbox.put(None)


class TestCartesiaInkSTT:
    """Push-to-talk on Cartesia ink-2, the same key as Clicky's voice."""

    def _stt(self, sessions, gate=None):
        from stt import CartesiaInkSTT
        client = MagicMock()
        queue_of_sessions = list(sessions)

        def enter():
            if gate is not None:
                gate.wait(2)
            return queue_of_sessions.pop(0)

        client.stt.manual_finalize.websocket.return_value.enter.side_effect = enter
        holder: dict = {}

        def audio_factory(callback, **kwargs):
            holder["callback"] = callback
            return MagicMock()

        stt_obj = CartesiaInkSTT(
            api_key="k", client_factory=lambda api_key: client, audio_stream_factory=audio_factory,
        )
        return stt_obj, client, holder

    @staticmethod
    def _speak(holder, data: bytes):
        holder["callback"](data, len(data) // 2, None, None)

    def test_a_question_round_trip(self):
        session = _FakeCartesiaSession(on_finalize=[
            {"type": "transcript", "text": "Where is", "is_final": True},
            {"type": "transcript", "text": " the save button?", "is_final": True},
            {"type": "flush_done"},
        ])
        gate = threading.Event()
        stt_obj, client, holder = self._stt([session], gate)
        stt_obj.connect()
        stt_obj.start_recording()
        self._speak(holder, b"\x01\x00" * 4)
        self._speak(holder, b"\x02\x00" * 4)
        gate.set()
        assert stt_obj._socket_ready.wait(1)
        self._speak(holder, b"\x03\x00" * 4)
        assert stt_obj.stop_recording() == "Where is the save button?"
        assert session.sent_audio == [b"\x01\x00" * 4, b"\x02\x00" * 4, b"\x03\x00" * 4]
        assert session.sent[0] == "finalize"
        kwargs = client.stt.manual_finalize.websocket.call_args.kwargs
        assert kwargs["model"] == "ink-2" and kwargs["sample_rate"] == 16000
        assert kwargs["encoding"] == "pcm_s16le" and kwargs["max_retries"] == 0

    def test_partials_reach_the_callback(self):
        session = _FakeCartesiaSession(on_finalize=[{"type": "flush_done"}])
        stt_obj, _, _ = self._stt([session])
        heard: list = []
        stt_obj.on_partial_transcript(heard.append)
        stt_obj.connect()
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        session.push({"type": "transcript", "text": "where is", "is_final": False})
        for _ in range(100):
            if heard:
                break
            time.sleep(0.02)
        assert heard == ["where is"]
        stt_obj.stop_recording()

    def test_a_provider_error_is_raised_on_release(self):
        session = _FakeCartesiaSession(on_finalize=[{"type": "error", "message": "Invalid API key"}])
        stt_obj, _, _ = self._stt([session])
        stt_obj.connect()
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        with pytest.raises(RuntimeError, match="Invalid API key"):
            stt_obj.stop_recording()

    def test_a_dropped_session_reconnects_on_the_next_press(self):
        first = _FakeCartesiaSession(on_finalize=[{"type": "flush_done"}])
        second = _FakeCartesiaSession(on_finalize=[{"type": "flush_done"}])
        stt_obj, client, _ = self._stt([first, second])
        stt_obj.connect()
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        first.close()  # the server goes away
        for _ in range(100):
            if stt_obj._client is None:
                break
            time.sleep(0.02)
        stt_obj._final_event.set()
        try:
            stt_obj.stop_recording()
        except RuntimeError:
            pass
        stt_obj.start_recording()
        assert stt_obj._socket_ready.wait(1)
        assert stt_obj._client.conn is second

    def test_factory_routes_cartesia(self):
        from stt import CartesiaInkSTT, create_stt_client
        assert isinstance(create_stt_client("cartesia", "k"), CartesiaInkSTT)


class TestSessionRaces:
    """Questions must never bleed into each other."""

    def _aai(self, connect_gate=None):
        from stt import AssemblyAIStreamingSTT
        clients: list = []

        def factory(api_key):
            c = MagicMock(name=f"client{len(clients)}")
            if connect_gate is not None and not clients:
                c.connect.side_effect = lambda params: connect_gate.wait(5)
            clients.append(c)
            return c

        holder: dict = {}

        def audio_factory(callback, **kw):
            holder["callback"] = callback
            return MagicMock()

        s = AssemblyAIStreamingSTT(api_key="k", client_factory=factory, audio_stream_factory=audio_factory)
        return s, clients, holder

    @staticmethod
    def _speak(holder, data: bytes):
        holder["callback"](data, len(data) // 2, None, None)

    def test_audio_from_a_timed_out_question_is_not_sent_with_the_next(self, monkeypatch):
        import stt as stt_module
        monkeypatch.setattr(stt_module, "_CONNECT_WAIT_S", 0.2)
        gate = threading.Event()
        s, clients, holder = self._aai(connect_gate=gate)
        s.connect(warm=False)
        s.start_recording()
        self._speak(holder, b"\x0a\x00" * 4)  # question A, while the open hangs
        with pytest.raises(RuntimeError):
            s.stop_recording()
        s.start_recording()
        assert s._socket_ready.wait(1)
        self._speak(holder, b"\x0b\x00" * 4)  # question B
        sent = [c.args[0] for c in clients[1].stream.call_args_list]
        assert sent == [b"\x0b\x00" * 4]

    def test_a_superseded_release_does_not_finalize_the_new_question(self, monkeypatch):
        gate = threading.Event()
        s, clients, holder = self._aai(connect_gate=gate)
        s.connect(warm=False)
        s.start_recording()
        result: dict = {}
        release_a = threading.Thread(target=lambda: result.update(a=s.stop_recording()))
        release_a.start()          # A waits for the session to open
        time.sleep(0.1)
        s.start_recording()        # B presses before A's session is up
        gate.set()                 # the session opens
        release_a.join(2)
        assert result.get("a") == ""
        clients[0].force_endpoint.assert_not_called()

    def test_events_from_a_retired_session_do_not_touch_the_current_question(self):
        s, clients, _ = self._aai()
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        s._final_event.set()
        s.stop_recording()
        old = clients[0]
        s._on_termination(old, MagicMock())        # the server closes it while idle
        s.start_recording()
        assert s._socket_ready.wait(1)
        s._on_error(old, "late error from the old session")
        assert s._stream_error is None
        assert not s._final_event.is_set()


class TestCartesiaSessionFailures:
    def _stt(self, sessions):
        from stt import CartesiaInkSTT
        client = MagicMock()
        pool = list(sessions)
        client.stt.manual_finalize.websocket.return_value.enter.side_effect = lambda: pool.pop(0)
        holder: dict = {}

        def audio_factory(callback, **kw):
            holder["callback"] = callback
            return MagicMock()

        s = CartesiaInkSTT(api_key="k", client_factory=lambda api_key: client, audio_stream_factory=audio_factory)
        return s, holder

    def test_a_session_that_dies_while_opening_is_not_kept(self):
        dead = _FakeCartesiaSession()
        dead.inbox.put(None)  # closed before it was ever used
        healthy = _FakeCartesiaSession(on_finalize=[{"type": "flush_done"}])
        s, _ = self._stt([dead, healthy])
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        for _ in range(100):
            if s._client is None:
                break
            time.sleep(0.02)
        assert s._client is None

    def test_a_dropped_connection_is_an_error_not_a_half_sentence(self):
        session = _FakeCartesiaSession()
        s, _ = self._stt([session])
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        session.push({"type": "transcript", "text": "delete the", "is_final": False})
        time.sleep(0.1)
        session.inbox.put(None)  # the network drops
        with pytest.raises(RuntimeError):
            s.stop_recording()

    def test_a_stalled_network_does_not_block_the_microphone(self):
        stall = threading.Event()
        session = _FakeCartesiaSession(on_finalize=[{"type": "flush_done"}])
        session.send_raw = lambda data: stall.wait(5)  # the socket write hangs
        s, holder = self._stt([session])
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        t0 = time.perf_counter()
        for _ in range(5):
            holder["callback"](b"\x01\x00" * 4, 4, None, None)
        assert time.perf_counter() - t0 < 0.5
        stall.set()


class TestSessionOwnership:
    """Only the current session may touch a question, and a session whose
    question did not finish cleanly is never reused."""

    def _cartesia(self, sessions):
        from stt import CartesiaInkSTT
        client = MagicMock()
        pool = list(sessions)
        client.stt.manual_finalize.websocket.return_value.enter.side_effect = lambda: pool.pop(0)
        holder: dict = {}

        def audio_factory(callback, **kw):
            holder["callback"] = callback
            return MagicMock()

        s = CartesiaInkSTT(api_key="k", client_factory=lambda api_key: client, audio_stream_factory=audio_factory)
        return s, holder

    def _aai(self, on_connect=None, gate=None):
        from stt import AssemblyAIStreamingSTT
        clients: list = []
        box: dict = {}

        def factory(api_key):
            c = MagicMock(name=f"client{len(clients)}")
            first = not clients
            if first and gate is not None:
                c.connect.side_effect = lambda params: gate.wait(5)
            elif first and on_connect is not None:
                c.connect.side_effect = lambda params: on_connect(box["stt"], c)
            clients.append(c)
            return c

        s = AssemblyAIStreamingSTT(api_key="k", client_factory=factory, audio_stream_factory=lambda cb, **kw: MagicMock())
        box["stt"] = s
        return s, clients

    @staticmethod
    def _wait_for(predicate, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_a_late_transcript_from_an_unfinished_question_never_reaches_the_next(self, monkeypatch):
        import stt as stt_module
        monkeypatch.setattr(stt_module, "_FINAL_TRANSCRIPT_TIMEOUT_S", 0.2)
        slow = _FakeCartesiaSession(on_finalize=[])  # question A's final never comes in time
        fresh = _FakeCartesiaSession(on_finalize=[
            {"type": "transcript", "text": "new question.", "is_final": True},
            {"type": "flush_done"},
        ])
        s, _ = self._cartesia([slow, fresh])
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        with pytest.raises(RuntimeError):           # A's final never came
            s.stop_recording()
        s.start_recording()                         # B
        slow.push({"type": "transcript", "text": "old question. ", "is_final": True})
        slow.push({"type": "flush_done"})           # A's answer arrives late
        assert s._socket_ready.wait(1)
        time.sleep(0.1)
        assert s.stop_recording() == "new question."

    def test_a_failed_write_fails_the_question_and_is_not_reused(self, monkeypatch):
        import stt as stt_module
        monkeypatch.setattr(stt_module, "_FINAL_TRANSCRIPT_TIMEOUT_S", 0.3)
        broken = _FakeCartesiaSession(on_finalize=[{"type": "flush_done"}])

        def fail(data):
            raise ConnectionError("send failed")
        broken.send_raw = fail
        healthy = _FakeCartesiaSession(on_finalize=[{"type": "flush_done"}])
        s, holder = self._cartesia([broken, healthy])
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        holder["callback"](b"\x01\x00" * 4, 4, None, None)
        time.sleep(0.1)
        with pytest.raises(RuntimeError):
            s.stop_recording()
        s.start_recording()
        assert s._socket_ready.wait(1)
        assert s._client.conn is healthy

    def test_a_full_send_queue_at_release_fails_and_is_not_reused(self, monkeypatch):
        import stt as stt_module
        monkeypatch.setattr(stt_module, "_MAX_PENDING_CHUNKS", 2)
        stall = threading.Event()
        stuck = _FakeCartesiaSession(on_finalize=[{"type": "flush_done"}])
        stuck.send_raw = lambda data: stall.wait(5)
        healthy = _FakeCartesiaSession(on_finalize=[{"type": "flush_done"}])
        s, holder = self._cartesia([stuck, healthy])
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        for _ in range(6):
            holder["callback"](b"\x01\x00" * 4, 4, None, None)
        try:
            with pytest.raises(RuntimeError):
                s.stop_recording()
            s.start_recording()
            assert s._socket_ready.wait(1)
            assert s._client.conn is healthy
        finally:
            stall.set()

    def test_closing_a_session_with_a_full_queue_stops_its_writer(self, monkeypatch):
        import stt as stt_module
        monkeypatch.setattr(stt_module, "_MAX_PENDING_CHUNKS", 2)
        closed = threading.Event()
        conn = MagicMock()
        conn.send_raw.side_effect = lambda data: closed.wait(5)
        conn.close.side_effect = lambda: closed.set()
        session = stt_module._CartesiaSession(conn, on_failure=lambda s: None)
        for _ in range(4):
            session.send(b"\x01\x00")
        session.close()
        session._writer.join(3)
        assert not session._writer.is_alive()

    def test_assemblyai_closing_during_finalize_is_an_error(self):
        s, clients = self._aai()
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        outcome: dict = {}

        def release():
            try:
                outcome["text"] = s.stop_recording()
            except RuntimeError as exc:
                outcome["error"] = exc
        t = threading.Thread(target=release)
        t.start()
        assert self._wait_for(lambda: clients[0].force_endpoint.called)
        s._on_termination(clients[0], MagicMock())
        t.join(3)
        assert "error" in outcome, outcome

    def test_assemblyai_session_that_closes_while_opening_is_not_kept(self):
        def close_while_opening(stt_obj, client):
            stt_obj._on_termination(client, MagicMock())
        s, clients = self._aai(on_connect=close_while_opening)
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        assert s._client is None
        with pytest.raises(RuntimeError):
            s.stop_recording()

    def test_a_superseded_opening_session_cannot_touch_the_new_question(self, monkeypatch):
        import stt as stt_module
        monkeypatch.setattr(stt_module, "_CONNECT_WAIT_S", 0.2)
        gate = threading.Event()
        s, clients = self._aai(gate=gate)
        s.connect(warm=False)
        s.start_recording()
        with pytest.raises(RuntimeError):
            s.stop_recording()                  # A's open hung
        s.start_recording()                     # B opens a fresh session
        assert s._socket_ready.wait(1)
        ghost = MagicMock(transcript="ghost words", end_of_turn=True, turn_is_formatted=False)
        s._on_turn(clients[0], ghost)           # A's session, still opening, speaks up
        try:
            assert s._final_transcript == ""
            assert not s._final_event.is_set()
        finally:
            gate.set()


class TestReleaseAndPause:
    """The release claims its session at once, and a pause mid-question
    never ends it early."""

    def _aai(self):
        from stt import AssemblyAIStreamingSTT
        clients: list = []

        def factory(api_key):
            c = MagicMock(name=f"client{len(clients)}")
            clients.append(c)
            return c

        s = AssemblyAIStreamingSTT(api_key="k", client_factory=factory, audio_stream_factory=lambda cb, **kw: MagicMock())
        return s, clients

    def test_a_press_during_release_gets_a_fresh_session(self):
        s, clients = self._aai()
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        first = s._client
        real_wait = s._socket_ready.wait
        pressed = {}

        def wait_then_press(timeout=None):  # the next press lands mid-release
            if not pressed:
                pressed["done"] = True
                s.start_recording()
            return real_wait(timeout)
        s._socket_ready.wait = wait_then_press
        assert s.stop_recording() == ""
        s._socket_ready.wait = real_wait
        assert s._socket_ready.wait(1)
        assert s._client is not first
        first.force_endpoint.assert_not_called()

    def test_a_pause_mid_question_does_not_cut_off_the_rest(self):
        s, clients = self._aai()
        s.connect(warm=False)
        s.start_recording()
        assert s._socket_ready.wait(1)
        c = clients[0]
        s._on_turn(c, MagicMock(transcript="Where do I", end_of_turn=True, turn_is_formatted=False))

        def finalize():  # the rest of the sentence comes back 300 ms after release
            threading.Timer(0.3, lambda: s._on_turn(
                c, MagicMock(transcript="change the units?", end_of_turn=True, turn_is_formatted=False))).start()
        c.force_endpoint.side_effect = finalize
        assert s.stop_recording() == "Where do I change the units?"
