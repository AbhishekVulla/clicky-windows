"""Unit tests for realtime.py — GPT-Realtime speech-to-speech session (v0.3.0).

The WebSocket connection, mic input, and speaker output are all injected via
factory args, so these run with mocks: no real audio device, no network.
"""
from __future__ import annotations

import base64
import json

import numpy as np


class _FakeConnection:
    """Records sent events; yields a scripted list of server events on iter."""

    def __init__(self, scripted_events=None):
        self.sent = []
        self._scripted = list(scripted_events or [])
        self.closed = False

    def send(self, event):
        self.sent.append(event)

    def __iter__(self):
        return iter(self._scripted)

    def close(self):
        self.closed = True


class _FakeStream:
    """Stands in for a sounddevice input or output stream."""

    def __init__(self):
        self.started = False
        self.stopped = False
        self.aborted = False
        self.written = []

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        pass

    def abort(self):
        self.aborted = True

    def write(self, samples):
        self.written.append(samples)


def _make_session(mocker, scripted_events=None, **cbs):
    from realtime import RealtimeSession
    conn = _FakeConnection(scripted_events)
    speaker = _FakeStream()
    mic = _FakeStream()
    s = RealtimeSession(
        api_key="sk-proj-test",
        connection_factory=lambda: conn,
        speaker_factory=lambda: speaker,
        mic_stream_factory=lambda callback: mic,
        **cbs,
    )
    return s, conn, speaker, mic


class TestPcm16Conversion:
    def test_int16_to_float32_normalizes_to_unit_range(self):
        from realtime import _pcm16_bytes_to_float32
        # max int16 (32767) and min (-32768)
        raw = np.array([32767, -32768, 0], dtype=np.int16).tobytes()
        out = _pcm16_bytes_to_float32(raw)
        assert out.dtype == np.float32
        assert abs(out[0] - 0.999) < 0.01
        assert abs(out[1] + 1.0) < 0.01
        assert out[2] == 0.0


class TestConnect:
    def test_connect_configures_session_with_point_at_tool(self, mocker):
        s, conn, speaker, mic = _make_session(mocker)
        s.connect()
        # The first sent event is session.update
        update = conn.sent[0]
        assert update["type"] == "session.update"
        tools = update["session"]["tools"]
        assert any(t["name"] == "point_at" for t in tools)
        # speaker was wired up (the default factory starts it; the injected
        # fake just gets assigned — assert it's the one connect() will use)
        assert s._speaker is speaker
        s.close()

    def test_connect_sets_24khz_audio_format(self, mocker):
        s, conn, speaker, mic = _make_session(mocker)
        s.connect()
        audio = conn.sent[0]["session"]["audio"]
        assert audio["input"]["format"]["rate"] == 24000
        assert audio["output"]["format"]["rate"] == 24000
        s.close()


class TestTurnFlow:
    def test_start_turn_opens_mic(self, mocker):
        s, conn, speaker, mic = _make_session(mocker)
        s.connect()
        s.start_turn()
        assert mic.started is True
        s.close()

    def test_respond_commits_buffer_sends_screenshot_and_requests_response(self, mocker):
        s, conn, speaker, mic = _make_session(mocker)
        s.connect()
        s.start_turn()
        conn.sent.clear()  # ignore session.update + any append
        s.respond(screenshot_jpeg_b64="ZmFrZQ==", query="where is save")
        types = [e["type"] for e in conn.sent]
        assert "input_audio_buffer.commit" in types
        assert "conversation.item.create" in types
        assert "response.create" in types
        # screenshot is attached as an input_image data URL
        item = next(e for e in conn.sent if e["type"] == "conversation.item.create")
        content = item["item"]["content"][0]
        assert content["type"] == "input_image"
        assert "data:image/jpeg;base64,ZmFrZQ==" in content["image_url"]
        s.close()


class TestEventHandling:
    def test_function_call_done_fires_on_coordinate(self, mocker):
        coords = []
        s, conn, speaker, mic = _make_session(
            mocker, on_coordinate=lambda x, y, label: coords.append((x, y, label)),
        )
        s.connect()
        s._handle_event({
            "type": "response.function_call_arguments.done",
            "arguments": json.dumps({"x": 640, "y": 300, "label": "save button"}),
        })
        assert coords == [(640, 300, "save button")]
        s.close()

    def test_function_call_accumulates_deltas_then_fires(self, mocker):
        coords = []
        s, conn, speaker, mic = _make_session(
            mocker, on_coordinate=lambda x, y, label: coords.append((x, y, label)),
        )
        s.connect()
        # arguments arrive split across deltas, then done with no full args
        s._handle_event({"type": "response.function_call_arguments.delta", "delta": '{"x": 10,'})
        s._handle_event({"type": "response.function_call_arguments.delta", "delta": ' "y": 20, "label": "ok"}'})
        s._handle_event({"type": "response.function_call_arguments.done", "arguments": None})
        assert coords == [(10, 20, "ok")]
        s.close()

    def test_malformed_function_args_does_not_fire_or_crash(self, mocker):
        coords = []
        s, conn, speaker, mic = _make_session(
            mocker, on_coordinate=lambda x, y, label: coords.append((x, y, label)),
        )
        s.connect()
        s._handle_event({"type": "response.function_call_arguments.done", "arguments": "not json"})
        assert coords == []
        s.close()

    def test_audio_delta_plays_and_fires_audio_start_once(self, mocker):
        starts = []
        s, conn, speaker, mic = _make_session(
            mocker, on_audio_start=lambda: starts.append(1),
        )
        s.connect()
        pcm = np.array([1000, -1000], dtype=np.int16).tobytes()
        b64 = base64.b64encode(pcm).decode("ascii")
        s._handle_event({"type": "response.output_audio.delta", "delta": b64})
        s._handle_event({"type": "response.output_audio.delta", "delta": b64})
        # speaker got two writes, audio_start fired exactly once
        assert len(speaker.written) == 2
        assert starts == [1]
        s.close()


class TestStop:
    def test_stop_aborts_speaker_and_cancels_response(self, mocker):
        s, conn, speaker, mic = _make_session(mocker)
        s.connect()
        conn.sent.clear()
        s.stop()
        assert speaker.aborted is True
        assert any(e["type"] == "response.cancel" for e in conn.sent)
        s.close()
