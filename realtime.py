"""GPT-Realtime speech-to-speech session for Clicky (v0.3.0).

This is a SEPARATE pipeline from the STT -> AIClient -> TTS chain. GPT-Realtime
collapses all three into one WebSocket: audio streams in, the model reasons
(GPT-5-class) on the audio plus a screenshot, and audio streams back, all in
near-real-time. There is no intermediate text-with-[POINT] tag, so this can't
be an AIClient subclass — it's its own thing, selected via
LLM_PROVIDER='openai-realtime'. Every other provider is untouched.

Interaction model (push-to-talk, to avoid barge-in/VAD complexity):
  press  -> start_turn(): open mic, append PCM16 to the input audio buffer
  release -> respond(screenshot_b64, query): commit the buffer, attach the
            screenshot as an image input, request a response. The model
            streams spoken audio back (played live) and emits a point_at
            function call with the target. The rough coordinate is then
            refined by the grid-locator (same as GPT-4o).

Audio format: 24 kHz PCM16 mono both directions (Realtime API native).
Input is appended as base64. Output deltas arrive as base64 PCM16, decoded to
float32 for sounddevice (same conversion as the ElevenLabs TTS path).

Threading: the event-consume loop runs on a background thread (audio output
deltas stream in continuously). Coordinate + audio-start are delivered via
callbacks. The caller (app.py) marshals to the Qt main thread via pyqtSignal.

Testability: the WebSocket connection, the mic input stream, and the speaker
output stream are all injectable (factory args), so unit tests run with mocks
and no real audio device or network — mirrors stt.py's DI pattern.
"""
from __future__ import annotations

import base64
import json
import threading
from typing import Callable, Optional

import numpy as np

from config import OPENAI_REALTIME_MODEL


# 24 kHz PCM16 mono is the Realtime API's native audio format (both directions).
REALTIME_SAMPLE_RATE = 24_000
REALTIME_CHUNK_FRAMES = 1024


# The point_at tool: the model calls this with its best guess of where to point.
# Rough coords are fine — the grid-locator refines them downstream.
_POINT_AT_TOOL = {
    "type": "function",
    "name": "point_at",
    "description": (
        "Point the on-screen cursor at a UI element the user asked about "
        "(a button, menu item, link, field, icon). Call this whenever the "
        "user asks where something is or how to do something that involves "
        "clicking. Give your best pixel guess in the screenshot's coordinate "
        "space; precision is refined automatically, so approximate is fine."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "x pixel in the screenshot"},
            "y": {"type": "integer", "description": "y pixel in the screenshot"},
            "label": {"type": "string", "description": "short name of the element"},
        },
        "required": ["x", "y", "label"],
    },
}

# Voice for spoken replies. "alloy" is broadly available; newer realtime
# voices (marin, cedar) also work if the account has them.
REALTIME_VOICE = "alloy"

_REALTIME_INSTRUCTIONS = (
    "You are Clicky, a friendly screen-aware buddy that helps people use "
    "software by talking to them and pointing at where to click. ALWAYS speak "
    "a short spoken reply out loud, one or two sentences — speak first, every "
    "time. When the user asks where something is or how to do something on "
    "screen, ALSO look at the screenshot and call the point_at function with "
    "the pixel location of the element. You never click for the user; you "
    "point and explain. Do not read coordinates aloud."
)


def _pcm16_bytes_to_float32(chunk: bytes) -> np.ndarray:
    """Decode raw PCM16 little-endian bytes to float32 in [-1, 1] for sounddevice.

    Same conversion the ElevenLabs TTS path uses (tts.py). int16 max is 32768.
    """
    return np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0


class RealtimeSession:
    """One GPT-Realtime push-to-talk session.

    Lifecycle:
        s = RealtimeSession(api_key, on_coordinate=..., on_audio_start=...)
        s.connect()                 # open WS + configure session
        s.start_turn()              # press: begin streaming mic
        s.respond(screenshot_b64, query)  # release: commit + screenshot + respond
        s.stop()                    # abort audio + reset for next turn
        s.close()                   # tear down WS on app exit
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = OPENAI_REALTIME_MODEL,
        on_coordinate: Optional[Callable[[int, int, str], None]] = None,
        on_audio_start: Optional[Callable[[], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        connection_factory: Optional[Callable[..., object]] = None,
        mic_stream_factory: Optional[Callable[..., object]] = None,
        speaker_factory: Optional[Callable[..., object]] = None,
    ) -> None:
        """
        Args:
            api_key: OpenAI key (sk-...).
            model: realtime model id (gpt-realtime-2 default).
            on_coordinate: called (x, y, label) when the model emits a point_at
                function call. Coordinates are in the screenshot's pixel space
                (rough — caller refines via grid-locator).
            on_audio_start: called once when the first audio delta of a response
                begins playing (for the SPEAKING overlay state).
            on_transcript: called with the model's spoken-text transcript chunks.
            connection_factory: DI. Default opens a real OpenAI realtime WS.
                Tests inject a fake connection (send/recv/close).
            mic_stream_factory: DI. Default opens a 24kHz sounddevice input.
            speaker_factory: DI. Default opens a 24kHz sounddevice output.
        """
        self._api_key = api_key
        self._model = model
        self._on_coordinate = on_coordinate
        self._on_audio_start = on_audio_start
        self._on_transcript = on_transcript
        self._connection_factory = connection_factory or self._default_connection_factory
        self._mic_stream_factory = mic_stream_factory or self._default_mic_stream_factory
        self._speaker_factory = speaker_factory or self._default_speaker_factory

        self._conn = None
        self._conn_cm = None
        self._mic = None
        self._speaker = None
        self._recv_thread: Optional[threading.Thread] = None
        self._recording = False
        self._stop_flag = threading.Event()
        self._audio_started_this_turn = False
        self._fn_args_buffer = ""  # accumulates point_at function-call arg JSON

    # -- DI factory defaults --------------------------------------------------

    def _default_connection_factory(self):
        from openai import OpenAI
        client = OpenAI(api_key=self._api_key)
        cm = client.realtime.connect(model=self._model)
        conn = cm.__enter__()
        self._conn_cm = cm
        return conn

    def _default_mic_stream_factory(self, callback):
        import sounddevice as sd
        return sd.RawInputStream(
            samplerate=REALTIME_SAMPLE_RATE,
            blocksize=REALTIME_CHUNK_FRAMES,
            dtype="int16",
            channels=1,
            callback=callback,
        )

    def _default_speaker_factory(self):
        import sounddevice as sd
        stream = sd.OutputStream(
            samplerate=REALTIME_SAMPLE_RATE, channels=1, dtype="float32",
        )
        stream.start()
        return stream

    # -- Lifecycle ------------------------------------------------------------

    def connect(self) -> None:
        """Open the WebSocket, configure the session (audio + point_at tool),
        and start the background event-consume loop."""
        self._conn = self._connection_factory()
        self._conn.send({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": self._model,
                "instructions": _REALTIME_INSTRUCTIONS,
                "audio": {
                    "input": {"format": {"type": "audio/pcm", "rate": REALTIME_SAMPLE_RATE}},
                    # voice is REQUIRED to get spoken output — without it the
                    # model only returns the function call, no audio (verified
                    # live 2026-06-23). tool_choice=auto lets it speak AND point
                    # in one turn.
                    "output": {
                        "format": {"type": "audio/pcm", "rate": REALTIME_SAMPLE_RATE},
                        "voice": REALTIME_VOICE,
                    },
                },
                "tools": [_POINT_AT_TOOL],
                "tool_choice": "auto",
            },
        })
        self._speaker = self._speaker_factory()
        self._stop_flag.clear()
        self._recv_thread = threading.Thread(
            target=self._consume_events, daemon=True, name="realtime-recv",
        )
        self._recv_thread.start()

    def start_turn(self) -> None:
        """Hotkey press: open the mic and begin appending PCM16 to the input
        audio buffer. Idempotent within a turn."""
        if self._recording:
            return
        self._audio_started_this_turn = False
        self._fn_args_buffer = ""
        self._recording = True
        self._mic = self._mic_stream_factory(self._on_mic_chunk)
        self._mic.start()

    def respond(self, screenshot_jpeg_b64: str, query: str = "") -> None:
        """Hotkey release: stop the mic, commit the audio buffer, attach the
        screenshot as an image input, and request a spoken response."""
        self._recording = False
        if self._mic is not None:
            try:
                self._mic.stop()
                self._mic.close()
            except Exception:
                pass
            self._mic = None

        if self._conn is None:
            return

        # Commit the user's spoken audio.
        self._conn.send({"type": "input_audio_buffer.commit"})
        # Attach the screenshot as an image content item on a user message so
        # the model can see the screen for this turn.
        self._conn.send({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{screenshot_jpeg_b64}",
                    },
                ],
            },
        })
        # Request the response (audio + possible point_at call).
        self._conn.send({"type": "response.create"})

    def stop(self) -> None:
        """Abort the current response's audio playback instantly + reset turn
        state. Mirrors tts.stop() — used on a second hotkey press / cancel."""
        if self._conn is not None:
            try:
                self._conn.send({"type": "response.cancel"})
            except Exception:
                pass
        if self._speaker is not None:
            try:
                self._speaker.abort()
            except Exception:
                pass
        self._audio_started_this_turn = False
        self._fn_args_buffer = ""

    def close(self) -> None:
        """Tear down the WS + audio on app shutdown."""
        self._stop_flag.set()
        if self._mic is not None:
            try:
                self._mic.stop(); self._mic.close()
            except Exception:
                pass
            self._mic = None
        if self._speaker is not None:
            try:
                self._speaker.stop(); self._speaker.close()
            except Exception:
                pass
            self._speaker = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        if self._conn_cm is not None:
            try:
                self._conn_cm.__exit__(None, None, None)
            except Exception:
                pass

    # -- Internals ------------------------------------------------------------

    def _on_mic_chunk(self, indata, frames, time_info, status) -> None:
        """sounddevice callback: base64-append raw PCM16 to the input buffer.
        Runs on the portaudio thread; must be fast + must not raise."""
        if not self._recording or self._conn is None:
            return
        try:
            b64 = base64.b64encode(bytes(indata)).decode("ascii")
            self._conn.send({"type": "input_audio_buffer.append", "audio": b64})
        except Exception:
            pass

    def _consume_events(self) -> None:
        """Background loop: read server events, play audio, capture point_at."""
        try:
            for event in self._conn:
                if self._stop_flag.is_set():
                    break
                self._handle_event(event)
        except Exception:
            # Connection closed / errored — loop ends, app keeps running.
            pass

    def _handle_event(self, event) -> None:
        """Dispatch one server event. `event` may be an SDK object (with .type)
        or a plain dict (tests). Handles both."""
        etype = getattr(event, "type", None)
        if etype is None and isinstance(event, dict):
            etype = event.get("type")

        if etype == "response.output_audio.delta":
            self._play_audio_delta(self._event_field(event, "delta"))
        elif etype == "response.function_call_arguments.delta":
            self._fn_args_buffer += self._event_field(event, "delta") or ""
        elif etype == "response.function_call_arguments.done":
            self._finish_function_call(self._event_field(event, "arguments"))
        elif etype == "response.output_audio_transcript.delta":
            chunk = self._event_field(event, "delta")
            if chunk and self._on_transcript:
                try:
                    self._on_transcript(chunk)
                except Exception:
                    pass

    @staticmethod
    def _event_field(event, name: str):
        """Read a field from an SDK event object or a dict."""
        if isinstance(event, dict):
            return event.get(name)
        return getattr(event, name, None)

    def _play_audio_delta(self, b64_delta: Optional[str]) -> None:
        if not b64_delta or self._speaker is None:
            return
        try:
            samples = _pcm16_bytes_to_float32(base64.b64decode(b64_delta))
            if samples.size == 0:
                return
            if not self._audio_started_this_turn:
                self._audio_started_this_turn = True
                if self._on_audio_start:
                    try:
                        self._on_audio_start()
                    except Exception:
                        pass
            self._speaker.write(samples)
        except Exception:
            pass

    def _finish_function_call(self, full_args: Optional[str]) -> None:
        """Parse the completed point_at arguments and fire the coordinate callback."""
        raw = full_args if full_args is not None else self._fn_args_buffer
        self._fn_args_buffer = ""
        if not raw:
            return
        try:
            args = json.loads(raw)
            x, y = int(args["x"]), int(args["y"])
            label = str(args.get("label", ""))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return
        if self._on_coordinate:
            try:
                self._on_coordinate(x, y, label)
            except Exception:
                pass
