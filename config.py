"""Clicky Windows configuration.

Loads environment variables from .env AND from the OS keyring (Windows
Credential Manager via the keyring package, DPAPI per-user encryption).
On launch with .env present, the keys are auto-migrated to the keyring
as a backup; the user can then delete .env without losing the keys.
"""

from __future__ import annotations

import os
from pathlib import Path

import keyring
from dotenv import load_dotenv

load_dotenv()


# ── Secrets resolution (env → keyring with one-shot migration) ──────────────

KEYRING_SERVICE: str = "clicky-windows"
"""Service name for keyring entries. Both Windows Credential Manager
and macOS Keychain treat this as the namespace key. All Clicky API
keys live under this single service name; the ``name`` parameter is
the env-var name (ANTHROPIC_API_KEY, etc.)."""


def resolve_api_key(name: str) -> str | None:
    """Resolve an API key by name, preferring env var then keyring.

    On env-var-present, ALSO write the value to keyring as a backup.
    This is the one-shot migration path from ``.env`` to keyring
    storage: later launches with no .env pick up the value from keyring
    transparently.

    Failures in keyring (locked vault, no backend, transient errors)
    are swallowed; the env-var path always works as a fallback. We
    never want a credential-store glitch to block app startup when the
    user has perfectly valid keys in their .env.

    Returns None if neither source has a value (caller shows the
    first-launch settings dialog).
    """
    env_value = os.getenv(name)
    if env_value:
        try:
            keyring.set_password(KEYRING_SERVICE, name, env_value)
        except Exception:
            # Keyring backend unreachable; env value is still good.
            pass
        return env_value
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None


def resolve_setting(name: str, default: str) -> str:
    """Resolve a non-secret setting by name with env→keyring→default fallback.

    Sibling to ``resolve_api_key`` for config knobs (TTS_PROVIDER,
    LLM_PROVIDER, STT_PROVIDER, etc.) that need keyring persistence so
    bundled-EXE startup doesn't silently fall back to defaults when the
    user's `.env` doesn't load (the EXE's cwd is the install dir, not the
    repo root).

    Differs from resolve_api_key in that it always returns a string:
    callers pass the right default for the setting (e.g. "cartesia" for
    TTS_PROVIDER) rather than handling None.

    Failures in keyring (locked vault, no backend) are swallowed in both
    directions: env path always returns successfully even if keyring write
    fails; keyring read errors fall through to the default.
    """
    env_value = os.getenv(name)
    if env_value:
        try:
            keyring.set_password(KEYRING_SERVICE, name, env_value)
        except Exception:
            pass
        return env_value
    try:
        stored = keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        stored = None
    return stored if stored else default


# Defaults from older releases. Settings used to save the model it showed,
# so a stored copy of one of these means it was never picked.
_RETIRED_MODEL_DEFAULTS = frozenset({
    "claude-sonnet-4-6",
    "gpt-5.4",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3.5-flash",
})


def resolve_model(name: str, default: str) -> str:
    """resolve_setting for a model id. A stored retired default gives way to
    ``default``; an env var always wins."""
    value = resolve_setting(name, default)
    if os.getenv(name) or value not in _RETIRED_MODEL_DEFAULTS:
        return value
    return default


# ── API keys ─────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY: str | None = resolve_api_key("ANTHROPIC_API_KEY")
"""Anthropic key (sk-ant-) or an OpenRouter key (sk-or-)."""

ASSEMBLYAI_API_KEY: str | None = resolve_api_key("ASSEMBLYAI_API_KEY")
"""Streaming STT via AssemblyAI u3-rt-pro WebSocket +
ForceEndpoint for ~150ms P50 PTT finalization. $50 free credit from
https://www.assemblyai.com/dashboard/signup, no credit card required."""

CARTESIA_API_KEY: str | None = resolve_api_key("CARTESIA_API_KEY")
"""Cartesia key: the Sonic-3 voice (streamed over HTTP, first audio in about
160ms) and, by default, ink-2 listening. 20k free credits/month from
https://play.cartesia.ai/sign-in, no credit card required."""

OPENAI_API_KEY: str | None = resolve_api_key("OPENAI_API_KEY")
"""Optional. OpenAI native API key (sk-...) for the OpenAI LLM provider:
vision in the normal pipeline, and GPT-Realtime speech-to-speech as a
separate path. Selected via LLM_PROVIDER='openai' (vision) or
'openai-realtime' (speech-to-speech). Get one at
https://platform.openai.com/api-keys."""


# ── OpenRouter dual-SDK routing (BYOK, model-agnostic) ──────────────────────

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
"""OpenRouter's OpenAI-compatible endpoint, used by the OpenAI-SDK clients
(GeminiClient, OpenAIVisionClient) when the key is an OpenRouter sk-or- key.
Claude models use the Anthropic SDK against https://openrouter.ai/api."""


# ── LLM model ID (routed by prefix via ai.create_ai_client) ─────────────────

MODEL_ID: str = os.getenv("MODEL_ID", "anthropic/claude-haiku-5-5")
"""OpenRouter-style model ID. Prefix routes to the right SDK via
ai.create_ai_client():
    'anthropic/...'  → AnthropicClient (via anthropic SDK, OpenRouter
                        Anthropic-compat endpoint)
    'google/...'     → GeminiClient (via openai SDK, OpenRouter OpenAI-compat
                        endpoint)

Set in .env to override."""


# ── Screen capture ───────────────────────────────────────────────────────────

CANDIDATE_RESOLUTIONS: list[tuple[int, int]] = [
    (1024, 768),   # 4:3   = 1.333 (legacy displays)
    (1280, 800),   # 16:10 = 1.600 (most laptops)
    (1366, 768),   # ~16:9 = 1.779 (external monitors, ultrawide fallback)
]
"""Anthropic-recommended screenshot resolutions. capture.py picks the
closest-aspect-ratio pair to the actual monitor to avoid distortion. Mirrors
Clicky's CompanionScreenCaptureUtility.swift (max dimension 1280)."""


# ── STT (AssemblyAI u3-rt-pro streaming) ─────────────────────────────────────

ASSEMBLYAI_SPEECH_MODEL: str = "u3-rt-pro"
"""AssemblyAI Universal-3 realtime-pro. Matches Clicky's Swift source
(AssemblyAIStreamingTranscriptionProvider.swift:447-451).
~150ms P50 finalization after ForceEndpoint message on hotkey release."""

ASSEMBLYAI_STREAMING_URL: str = "wss://streaming.assemblyai.com/v3/ws"
"""AssemblyAI streaming WebSocket endpoint. Query params are set via SDK."""

AUDIO_SAMPLE_RATE: int = 16_000
"""PCM16 mono at 16kHz. Matches AssemblyAI u3-rt-pro's required sample rate +
Clicky's audio pipeline + the canonical input shape for every major
streaming STT provider."""

AUDIO_CHUNK_FRAMES: int = 1024
"""sounddevice RawInputStream blocksize. Matches Clicky's
AVAudioEngine.installTap(onBus:0, bufferSize:1024) exactly so the streaming
WebSocket payload shape is identical across STT providers."""

# ── Audio level (RMS) filter for the waveform widget ────────────────────────

AUDIO_POWER_BOOST: float = 10.2
"""Multiplier applied to per-chunk RMS before clamping to [0, 1]. Tuned to
make normal speech register ~0.4-0.8 on the waveform. Matches the
dictation level meter in Farza's Clicky verbatim."""

AUDIO_POWER_DECAY: float = 0.72
"""Exponential decay floor between chunks: smoothed = max(raw, old * 0.72).
Prevents the UI waveform from jumping DOWN sharply at natural speech pauses,
so the meter feels responsive to loud sounds but stable at quiet ones.
Matches Farza's implementation."""


# ── TTS (Cartesia Sonic-3, streamed over HTTP) ──────────────────────────────

CARTESIA_MODEL_ID: str = "sonic-3"
"""Cartesia's state-space-model-based TTS. ~90ms model-internal TTFB,
150-250ms real-world through the WebSocket stream + sounddevice playback.
Picked for the most expressive conversational voice among cloud TTS providers."""

CARTESIA_VOICE_ID: str = os.getenv(
    "CARTESIA_VOICE_ID",
    "f786b574-daa5-4673-aa0c-cbe3e8534c02",  # "Katie - Friendly Fixer", Cartesia-recommended for voice agents
)
"""Cartesia voice ID for Sonic-3. Default is "Katie - Friendly Fixer"
("enunciating young adult female, conversational support" in Cartesia's
voice catalog).

Swap via .env CARTESIA_VOICE_ID=...
Other strong feminine candidates from the Cartesia catalog:
  - Cathy - Coworker (e8e5fffb-252c-436d-b842-8879b84445b6): "nice young adult female for casual conversations"
  - Skylar - Friendly Guide (db6b0ed5-d5d3-463d-ae85-518a07d3c2b4): "approachable American female"
  - Lauren - Lively Narrator (a33f7a4c-100f-41cf-a1fd-5822e8fc253f): "expressive female, narration, storytelling" (most dramatic/emotive)"""

CARTESIA_OUTPUT_SAMPLE_RATE: int = 44_100
"""Cartesia output stream sample rate. 44.1 kHz PCM float32 via sounddevice
OutputStream. Cartesia supports 22.05k / 44.1k / 48k; 44.1k is the most
natural for a conversational voice without oversampling cost."""


# ── Provider selection (which subclass app.py constructs at startup) ────────

def default_llm_provider() -> str:
    """AI provider when none has been chosen: the first one there is a key
    for, so an existing setup keeps working, else OpenAI (GPT-6.1 Sol, the
    fastest to its first word in testing). A MODEL_ID override keeps the path
    that honours it."""
    if os.getenv("MODEL_ID"):
        return "anthropic"
    for provider, key_name in (
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
    ):
        if resolve_api_key(key_name):
            return provider
    return "openai"

STT_PROVIDER: str = resolve_setting("STT_PROVIDER", default="cartesia")
"""Which STT subclass to construct."""

TTS_PROVIDER: str = resolve_setting("TTS_PROVIDER", default="cartesia")
"""Which TTS subclass to construct: "cartesia" (default), "elevenlabs" or
"kokoro". User switches via Settings dialog dropdown."""

LLM_PROVIDER: str = resolve_setting("LLM_PROVIDER", default=default_llm_provider())
"""Which LLM subclass to construct. Settings dialog dropdown writes this.
Values: 'openai' (default for a new install), 'anthropic', 'gemini' or 'ollama'
(local); 'openai-realtime' is env-only.

When 'ollama' is selected, app.py builds an OllamaClient pointed at
OLLAMA_HOST with OLLAMA_MODEL_VISION as the model, bypassing
ANTHROPIC_API_KEY (which may be empty).

To override via env var (advanced): set MODEL_ID=ollama/llama3.2-vision
directly. That takes precedence over LLM_PROVIDER because the create_ai_client
factory dispatches on MODEL_ID prefix first. LLM_PROVIDER is the
GUI-friendly way for users who don't edit .env."""

ANNOTATION_MODE: str = resolve_setting("ANNOTATION_MODE", default="off")
"""Draw-on-screen teaching mode. When 'on', the vision
model is given the annotation system prompt and emits
[ARROW]/[CIRCLE]/[UNDERLINE]/[LABEL] tags that the overlay renders as shapes
(in ADDITION to the [POINT] cursor). When 'off' (default) nothing is
overridden. Accuracy comes from the vision model. Resolved ONCE here at
import (env→keyring→default); app.py reads this cached constant per interaction
rather than calling resolve_setting on the hot path, so there is no
per-interaction keyring read/write latency. Set it in .env and restart to
toggle."""

DEBUG_SCREENSHOTS: bool = resolve_setting("DEBUG_SCREENSHOTS", default="off").lower() == "on"
"""Keep a copy of each screenshot in the debug folder. Off by default: the
text log is enough for a bug report, and a folder of screen captures is a
privacy risk. Set DEBUG_SCREENSHOTS=on to keep them while debugging pointing."""


# ── ElevenLabs TTS (opt-in alternative to Cartesia) ─────────────────────────

ELEVENLABS_API_KEY: str | None = resolve_api_key("ELEVENLABS_API_KEY")
"""Optional. Required only when TTS_PROVIDER='elevenlabs'. 10k chars/month
free tier at https://elevenlabs.io/app/sign-up, no credit card."""

ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
"""ElevenLabs Flash v2.5: ~75ms model TTFB. ElevenLabs officially
recommends Flash over Turbo v2.5 for low-latency voice agents. In
ElevenLabs Python SDK 2.45.0, ``client.text_to_speech.stream`` accepts
``model_id="eleven_flash_v2_5"``.
"""

ELEVENLABS_VOICE_ID: str = os.getenv(
    "ELEVENLABS_VOICE_ID",
    "21m00Tcm4TlvDq8ikWAM",  # Rachel: American female, conversational
)
"""ElevenLabs voice ID for Clicky's voice. Default Rachel is a warm,
conversational adult female voice. Rachel's official voice ID in the
ElevenLabs voice catalog (https://elevenlabs.io/app/voice-library) is
``21m00Tcm4TlvDq8ikWAM``. If swapping to a different voice via env
override, copy the ID from the voice library page (NOT the URL slug)."""

ELEVENLABS_OUTPUT_SAMPLE_RATE: int = int(
    os.getenv("ELEVENLABS_OUTPUT_SAMPLE_RATE", "22050")
)
"""ElevenLabs PCM sample rate. Defaulted to 22050 because 44.1kHz PCM
requires Pro tier. ElevenLabs PCM is int16 (NOT float32 like Cartesia),
so playback path converts inline: np.frombuffer(chunk, np.int16).astype(
np.float32) / 32768.0."""


# ── Ollama (local LLM via Ollama server) ────────────────────────────────────

OLLAMA_HOST: str = os.getenv(
    "OLLAMA_HOST", resolve_setting("OLLAMA_HOST", "http://localhost:11434")
)
"""Local Ollama server URL. Default matches Ollama's out-of-the-box
``ollama serve`` binding. Set in .env or Settings dialog to point at a
different host (e.g. another machine on LAN). Only unauthenticated
Ollama is supported, so no API-key field is needed."""

OLLAMA_MODEL_VISION: str = os.getenv(
    "OLLAMA_MODEL_VISION",
    resolve_setting("OLLAMA_MODEL_VISION", "llava:7b"),
)
"""Ollama vision-capable model used when screenshots are present.
Default ``llava:7b`` works on every Ollama version with vision support
(~4.5 GB). ``llama3.2-vision`` is more accurate but needs Ollama
>=0.4.x (uses ``mllama`` arch). User can switch via Settings dialog;
``ollama_health.check_model_compatibility`` warns on mismatch."""


# ── OpenAI (vision + GPT-Realtime) ────────────────────────────────────────────

OPENAI_MODEL_VISION: str = resolve_model("OPENAI_MODEL_VISION", "gpt-6.1-sol")
"""OpenAI vision model for LLM_PROVIDER='openai', routed by the ``openai/``
prefix in create_ai_client. Returns a precise [POINT] tag, so the grid
locator never runs for it."""

OPENAI_REALTIME_MODEL: str = os.getenv(
    "OPENAI_REALTIME_MODEL",
    resolve_setting("OPENAI_REALTIME_MODEL", "gpt-realtime-2"),
)
"""OpenAI GPT-Realtime model for the speech-to-speech path
(LLM_PROVIDER='openai-realtime'). ``gpt-realtime-2`` is GPT-5-class
continuous-stream voice: near-zero latency, sees the screenshot, reasons,
and emits a pointing target via the point_at function call. This path
bypasses the STT→AIClient→TTS chain entirely (realtime.py owns the
WebSocket session + audio I/O). The pointing target comes from a separate
vision pass (see app.py)."""


# -- Local STT (faster-whisper, opt-in offline, no API key) ------------------

FASTER_WHISPER_MODEL: str = os.getenv(
    "FASTER_WHISPER_MODEL", resolve_setting("FASTER_WHISPER_MODEL", "base.en")
)
"""faster-whisper model size. 'base.en' is the low-latency English default
(~150MB, downloads to the HF cache on first use). 'small.en' is more accurate
but slower. Local offline STT needs no API key."""

FASTER_WHISPER_DEVICE: str = os.getenv(
    "FASTER_WHISPER_DEVICE", resolve_setting("FASTER_WHISPER_DEVICE", "cpu")
)
"""'cpu' (portable default) or 'cuda' if the user has an NVIDIA GPU."""

FASTER_WHISPER_COMPUTE: str = os.getenv(
    "FASTER_WHISPER_COMPUTE", resolve_setting("FASTER_WHISPER_COMPUTE", "int8")
)
"""CTranslate2 compute type. 'int8' is fast + low-memory on CPU."""


# -- Local TTS (Kokoro-82M via ONNX, opt-in offline, no API key) -------------

KOKORO_VOICE: str = os.getenv("KOKORO_VOICE", resolve_setting("KOKORO_VOICE", "af_heart"))
"""Kokoro voice id. 'af_heart' is a warm conversational female voice."""

KOKORO_OUTPUT_SAMPLE_RATE: int = 24_000
"""Kokoro-82M output sample rate (24kHz float32)."""

_DEFAULT_KOKORO_DIR = Path.home() / ".clicky-windows" / "kokoro"
KOKORO_CACHE_DIR: Path = Path(os.getenv("KOKORO_CACHE_DIR", str(_DEFAULT_KOKORO_DIR)))
"""Where the Kokoro onnx + voices files download on first use (~336MB total)."""


# -- Google Gemini (cloud vision) --------------------------------------------

GEMINI_API_KEY: str | None = resolve_api_key("GEMINI_API_KEY")
"""Gemini key: a Google AI Studio key (calls Google directly) or an OpenRouter
sk-or- key. Left blank, an OpenRouter key saved for another provider is used."""

GEMINI_MODEL_VISION: str = resolve_model("GEMINI_MODEL_VISION", "google/gemini-4-argon")
"""Gemini model, as an OpenRouter slug. GeminiClient strips ``google/`` for a
direct Google AI Studio key and converts Gemini's 0-1000 coordinates to pixels."""


# ── Memory ───────────────────────────────────────────────────────────────────

_DEFAULT_MEMORY_DIR = Path.home() / ".clicky-windows"

MEMORY_DIR: Path = Path(os.getenv("MEMORY_DIR", str(_DEFAULT_MEMORY_DIR / "memory")))
"""Where per-app markdown files live. One .md per Windows app executable."""

INDEX_DB_PATH: Path = Path(os.getenv("INDEX_DB_PATH", str(_DEFAULT_MEMORY_DIR / "index.db")))
"""SQLite index at ~/.clicky-windows/index.db. Fast lookup for apps + interaction counts."""

MEMORY_RECALL_MAX_CHARS: int = 1500
"""Max characters of recalled memory to inject into the user message per request.
~1500 chars = last 5-6 interactions. Clicky macOS sends zero persistent memory;
ours is the differentiator, but too much context slows Claude down."""


# ── Knowledge base (user-uploadable per-app curated docs) ────────────────────

KB_DIR: Path = Path(
    os.getenv("KB_DIR", str(Path.home() / "Documents" / "Clicky Wiki"))
)
"""User drops a single .md file here per app, named to match the .exe
basename (e.g. ``edupack.exe.md`` for EduPack, ``fusion360.exe.md`` for
Fusion 360). Clicky reads it on every PTT and injects as authoritative
reference in Claude's system prompt.

Default location is visible in File Explorer (NOT a hidden ``.``-prefixed
folder) so users can find + edit + delete the files without terminal
gymnastics. Mirrors memory.py's transparency contract: human-readable,
hand-editable, no vector DB.
"""

KB_RECALL_MAX_CHARS: int = 60_000
"""Max characters of curated KB content to inject per request. ~15K
tokens, ~⅓ of Claude's context budget. Over-budget files tail-truncate
(same behavior as memory.recall). Anthropic supports up to 4
``cache_control`` breakpoints per request; injecting KB adds a 2nd
system block alongside the persona block, leaving 2 slots for the
user-message memory prefix + the implicit automatic-cache slot."""
