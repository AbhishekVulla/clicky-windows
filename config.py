"""Clicky Windows configuration.

Loads environment variables from .env (Phase 1 BYOK pattern) AND from
the OS keyring (Sprint 3 — Windows Credential Manager via the keyring
package, DPAPI per-user encryption). On launch with .env present, the
keys are auto-migrated to the keyring as a backup; user can then delete
.env without losing the keys.

See DECISIONS.md for the rationale behind each default.
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

    On env-var-present, ALSO write the value to keyring as a backup —
    this is the one-shot migration path from Phase 1's ``.env`` workflow
    to Phase 2's keyring storage. Subsequent launches with no .env will
    pick up the value from keyring transparently.

    Failures in keyring (locked vault, no backend, transient errors)
    are swallowed — the env-var path always works as a fallback. We
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
    user's `.env` doesn't load (cwd is install dir, not repo root — see
    DECISIONS.md 2026-05-05 Sprint 3.6).

    Differs from resolve_api_key in that it always returns a string —
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


# ── API keys ─────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY: str | None = resolve_api_key("ANTHROPIC_API_KEY")
"""Required. Plain vision streaming via messages.stream(). Sonnet 4.6 default."""

ASSEMBLYAI_API_KEY: str | None = resolve_api_key("ASSEMBLYAI_API_KEY")
"""Required for Phase 1. Streaming STT via AssemblyAI u3-rt-pro WebSocket +
ForceEndpoint for ~150ms P50 PTT finalization. $50 free credit from
https://www.assemblyai.com/dashboard/signup, no credit card required."""

CARTESIA_API_KEY: str | None = resolve_api_key("CARTESIA_API_KEY")
"""Required for Phase 1. Streaming TTS via Cartesia Sonic-3 WebSocket with
~150-250ms TTFB + expressive "buddy" voice. 20k free credits/month from
https://play.cartesia.ai/sign-in, no credit card required."""


# ── OpenRouter dual-SDK routing (BYOK, model-agnostic) ──────────────────────

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
"""OpenRouter's OpenAI-compatible endpoint for Gemini / Grok / Llama / etc.

The existing ANTHROPIC_BASE_URL env var (read natively by the Anthropic SDK)
points at 'https://openrouter.ai/api' for Claude models. This constant is the
sibling endpoint for the OpenAI SDK used by GeminiClient (ai.py). Same API
key (ANTHROPIC_API_KEY from .env — which is actually the OpenRouter
sk-or-v1-... key when ANTHROPIC_BASE_URL is set to OpenRouter).

See DECISIONS.md 2026-04-19 'Gemini 3 Flash via OpenRouter' for the full
dual-SDK routing rationale."""


# ── LLM model ID (routed by prefix via ai.create_ai_client) ─────────────────

MODEL_ID: str = os.getenv("MODEL_ID", "anthropic/claude-sonnet-4-6")
"""OpenRouter-style model ID. Prefix routes to the right SDK via
ai.create_ai_client():
    'anthropic/...'  → AnthropicClient (via anthropic SDK, OpenRouter
                        Anthropic-compat endpoint)
    'google/...'     → GeminiClient (via openai SDK, OpenRouter OpenAI-compat
                        endpoint)

Phase 1.5 default is 'google/gemini-3-flash-preview' for ~50% latency
reduction on vision tasks. Set in .env to override.

See DECISIONS.md 2026-04-19 for the model swap rationale + latency
benchmarks."""


# ── Screen capture ───────────────────────────────────────────────────────────

CANDIDATE_RESOLUTIONS: list[tuple[int, int]] = [
    (1024, 768),   # 4:3   = 1.333 (legacy displays)
    (1280, 800),   # 16:10 = 1.600 (most laptops)
    (1366, 768),   # ~16:9 = 1.779 (external monitors, ultrawide fallback)
]
"""Anthropic-recommended screenshot resolutions. capture.py picks the
closest-aspect-ratio pair to the actual monitor to avoid distortion. Mirrors
Clicky's CompanionScreenCaptureUtility.swift (max dimension 1280)."""


# ── Hotkey ───────────────────────────────────────────────────────────────────

HOTKEY: str = os.getenv("HOTKEY", "ctrl+alt+space")
"""Default push-to-talk hotkey. Ctrl+Alt+Space because:

  1. Alt+Space alone conflicts with the Windows window menu + Copilot
     (Microsoft reassigned it in Windows 11, late 2024). Making it work
     cleanly needs Win32 RegisterHotKey + GetAsyncKeyState polling for
     release detection -- 8-12h of fragile ctypes code, deferred to
     Phase 1.5 as a drop-in subclass.
  2. Ctrl+Shift+Space was our earlier pivot target but conflicts with
     Microsoft Excel + Google Sheets "Select entire worksheet" binding.
     Because our pynput listener uses suppress=False (observe-only),
     the spreadsheet underneath ALSO receives the keypress and wipes
     the user's selection every time they invoke Clicky -- a showstopper
     for the Excel-learning demo narrative.
  3. Fn+Space is firmware-level (handled by the keyboard EC below the
     OS) and invisible to WH_KEYBOARD_LL + pynput. Non-portable even
     where it happens to work. AutoHotkey docs: "the Fn key does not
     (as a general rule) generate any scan code that can be used."
  4. Ctrl+Alt+Space has no known code-level conflicts (Excel, Sheets,
     Windows menu, Copilot, VS Code all clear). Three-finger but all on
     the left side of the keyboard for one-handed ergonomics. suppress=
     False observe-only model carries over unchanged.

  KNOWN SETUP REQUIREMENT: if you have Claude Desktop for Windows
  installed, disable its Ctrl+Alt+Space binding in Claude Desktop
  Settings > Keyboard Shortcuts (same pattern Raycast / Flow Launcher
  users follow for Alt+Space). Clicky's listener is observe-only so
  both apps receive the keypress otherwise, and Claude Desktop's
  quick-access prompt will pop every time you invoke Clicky. See
  DECISIONS.md 2026-04-12 (evening) entry for the full rationale +
  the Phase 1.5 Win32 RegisterHotKey solution that eliminates the
  conflict at the OS level.

NEVER ctrl+space (VS Code IntelliSense conflict -- still rejected).

See DECISIONS.md 2026-04-12 (evening) entry "Ctrl+Alt+Space replaces
Ctrl+Shift+Space" for the full pivot story + research-backed Fn+Space
rejection. Phase 1.5 Win32 RegisterHotKey subclass will restore Alt+Space
ergonomics via the abstract PushToTalkHotkey interface."""


# ── STT (AssemblyAI u3-rt-pro streaming) ─────────────────────────────────────

ASSEMBLYAI_SPEECH_MODEL: str = "u3-rt-pro"
"""AssemblyAI Universal-3 realtime-pro. Matches Clicky's Swift source
(leanring-buddy/AssemblyAIStreamingTranscriptionProvider.swift:447-451).
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
WebSocket payload shape is identical for Phase 2 provider swaps."""

# ── Audio level (RMS) filter — drives the waveform widget ──────────────────

AUDIO_POWER_BOOST: float = 10.2
"""Multiplier applied to per-chunk RMS before clamping to [0, 1]. Tuned to
make normal speech register ~0.4-0.8 on the waveform. Matches Farza's
leanring-buddy/BuddyDictationManager.swift:687-721 verbatim."""

AUDIO_POWER_DECAY: float = 0.72
"""Exponential decay floor between chunks: smoothed = max(raw, old * 0.72).
Prevents the UI waveform from jumping DOWN sharply at natural speech pauses —
makes the meter feel responsive to loud sounds but stable at quiet ones.
Matches Farza's implementation."""


# ── TTS (Cartesia Sonic-3 WebSocket streaming) ──────────────────────────────

CARTESIA_MODEL_ID: str = "sonic-3"
"""Cartesia's state-space-model-based TTS. ~90ms model-internal TTFB,
150-250ms real-world through the WebSocket stream + sounddevice playback.
Most expressive 'buddy' voice quality in the cloud TTS field as of April 2026.
See DECISIONS.md 'Priority inversion' for the research."""

CARTESIA_VOICE_ID: str = os.getenv(
    "CARTESIA_VOICE_ID",
    "f786b574-daa5-4673-aa0c-cbe3e8534c02",  # "Katie - Friendly Fixer" — Cartesia-recommended for voice agents
)
"""Cartesia voice ID for Sonic-3. Default is "Brooke - Big Sister" — a confident
adult female voice described as "for conversational use cases" in Cartesia's
voice catalog. The "big sister" framing matches our "buddy next to you" UX.

Swap via .env CARTESIA_VOICE_ID=... if Brooke doesn't land for the demo.
Other strong feminine candidates from the Cartesia catalog:
  - Cathy - Coworker (e8e5fffb-252c-436d-b842-8879b84445b6) — "nice young adult female for casual conversations"
  - Skylar - Friendly Guide (db6b0ed5-d5d3-463d-ae85-518a07d3c2b4) — "approachable American female"
  - Lauren - Lively Narrator (a33f7a4c-100f-41cf-a1fd-5822e8fc253f) — "expressive female, narration, storytelling" (most dramatic/emotive)
  - Katie - Friendly Fixer (f786b574-daa5-4673-aa0c-cbe3e8534c02) — "enunciating young adult female, conversational support"
The previous default (a0e99841...) was a hallucinated UUID I made up without
verifying against Cartesia's catalog — sorry. User reported it as "kinda robotic"
which is probably because Cartesia fell back to a default voice."""

CARTESIA_OUTPUT_SAMPLE_RATE: int = 44_100
"""Cartesia output stream sample rate. 44.1 kHz PCM float32 via sounddevice
OutputStream. Cartesia supports 22.05k / 44.1k / 48k — 44.1k is the most
natural for buddy voice without oversampling cost."""


# ── Provider selection (which subclass app.py constructs at startup) ────────

LLM_PROVIDER: str = resolve_setting("LLM_PROVIDER", default="anthropic")
"""Which AIClient subclass to construct. Sprint 4 ships only "anthropic"
in the dropdown; GeminiClient infrastructure stays in ai.py for opt-in
via env override (MODEL_ID=google/...) but is not user-selectable in the
settings dialog. See DECISIONS.md 2026-04-19 (late-evening) for the
empirical A/B that rejected Gemini on coordinate accuracy."""

STT_PROVIDER: str = resolve_setting("STT_PROVIDER", default="assemblyai")
"""Which STT subclass to construct. Sprint 4 ships only "assemblyai".
Deepgram is parked for post-launch."""

TTS_PROVIDER: str = resolve_setting("TTS_PROVIDER", default="cartesia")
"""Which TTS subclass to construct. Sprint 4 ships "cartesia" (default)
and "elevenlabs" (opt-in). User switches via Settings dialog dropdown."""

LLM_PROVIDER: str = resolve_setting("LLM_PROVIDER", default="anthropic")
"""Which LLM subclass to construct (v0.2.0). Settings dialog dropdown
writes this. Values: 'anthropic' (default, cloud) or 'ollama' (local).

When 'ollama' is selected, app.py builds an OllamaClient pointed at
OLLAMA_HOST with OLLAMA_MODEL_VISION as the model — completely bypassing
ANTHROPIC_API_KEY (which may be empty). Without this branch, the
Settings dropdown was cosmetic in v0.2.0 (caught by codex adversarial
review 2026-06-05): app would silently fall back to AnthropicClient
with whatever MODEL_ID env var said, ignoring the user's choice.

To override via env var (advanced): set MODEL_ID=ollama/llama3.2-vision
directly — that takes precedence over LLM_PROVIDER because the create_ai_client
factory dispatches on MODEL_ID prefix first. LLM_PROVIDER is the
GUI-friendly way for users who don't edit .env."""


# ── ElevenLabs TTS (opt-in alternative to Cartesia) ─────────────────────────

ELEVENLABS_API_KEY: str | None = resolve_api_key("ELEVENLABS_API_KEY")
"""Optional. Required only when TTS_PROVIDER='elevenlabs'. 10k chars/month
free tier at https://elevenlabs.io/app/sign-up — no credit card."""

ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
"""ElevenLabs Flash v2.5 — ~75ms model TTFB. ElevenLabs officially
recommends Flash over Turbo v2.5 for low-latency voice agents.
Verified 2026-05-06 against ElevenLabs Python SDK 2.45.0 (
``client.text_to_speech.stream`` accepts ``model_id="eleven_flash_v2_5"``).
"""

ELEVENLABS_VOICE_ID: str = os.getenv(
    "ELEVENLABS_VOICE_ID",
    "21m00Tcm4TlvDq8ikWAM",  # Rachel — American female, conversational
)
"""ElevenLabs voice ID for the buddy persona. Default Rachel matches
Cartesia "Brooke - Big Sister" warmth (conversational adult female).
Verified 2026-05-06 against ElevenLabs voice catalog
(https://elevenlabs.io/app/voice-library) — Rachel's official voice ID
is ``21m00Tcm4TlvDq8ikWAM``. If swapping to a different voice via env
override, copy the ID from the voice library page (NOT the URL slug)."""

ELEVENLABS_OUTPUT_SAMPLE_RATE: int = int(
    os.getenv("ELEVENLABS_OUTPUT_SAMPLE_RATE", "22050")
)
"""ElevenLabs PCM sample rate. Defaulted to 22050 because 44.1kHz PCM
requires Pro tier. ElevenLabs PCM is int16 (NOT float32 like Cartesia),
so playback path converts inline: np.frombuffer(chunk, np.int16).astype(
np.float32) / 32768.0."""


# ── Ollama (local LLM via Ollama server) — added v0.2.0 ─────────────────────

OLLAMA_HOST: str = os.getenv(
    "OLLAMA_HOST", resolve_setting("OLLAMA_HOST", "http://localhost:11434")
)
"""Local Ollama server URL. Default matches Ollama's out-of-the-box
``ollama serve`` binding. Set in .env or Settings dialog to point at a
different host (e.g. another machine on LAN). v0.2.0 only supports
unauthenticated local Ollama — no API-key field needed."""

OLLAMA_MODEL_VISION: str = os.getenv(
    "OLLAMA_MODEL_VISION",
    resolve_setting("OLLAMA_MODEL_VISION", "llava:7b"),
)
"""Ollama vision-capable model used when screenshots are present.
Default ``llava:7b`` works on every Ollama version with vision support
(~4.5 GB). ``llama3.2-vision`` is more accurate but needs Ollama
>=0.4.x (uses ``mllama`` arch). User can switch via Settings dialog;
``ollama_health.check_model_compatibility`` warns on mismatch."""

OLLAMA_MODEL_TEXT: str = os.getenv(
    "OLLAMA_MODEL_TEXT",
    resolve_setting("OLLAMA_MODEL_TEXT", "llama3.2"),
)
"""Ollama text-only model used when no screenshots are sent (rare in
Clicky's PTT flow but kept for parity with Bitshank's vision/text split).
Defaults to plain ``llama3.2`` (3B, ~2 GB)."""


# ── Memory ───────────────────────────────────────────────────────────────────

_DEFAULT_MEMORY_DIR = Path.home() / ".clicky-windows"

MEMORY_DIR: Path = Path(os.getenv("MEMORY_DIR", str(_DEFAULT_MEMORY_DIR / "memory")))
"""Where per-app markdown files live. One .md per Windows app executable."""

INDEX_DB_PATH: Path = Path(os.getenv("INDEX_DB_PATH", str(_DEFAULT_MEMORY_DIR / "index.db")))
"""SQLite index at ~/.clicky-windows/index.db. Fast lookup for apps + interaction counts."""

INSIGHTS_PATH: Path = Path(os.getenv("INSIGHTS_PATH", str(_DEFAULT_MEMORY_DIR / "insights.md")))
"""Output of tools/lint_memory.py — Karpathy-style weekly health check."""

MEMORY_RECALL_MAX_CHARS: int = 1500
"""Max characters of recalled memory to inject into the user message per request.
~1500 chars = last 5-6 interactions. Clicky macOS sends zero persistent memory —
our memory is the differentiator but too much context slows Claude down."""


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

Decided 2026-05-04 after retracting JaySmith502's folder+TOML+section
pattern as cargo-cult for our scale. See DECISIONS.md."""

KB_RECALL_MAX_CHARS: int = 60_000
"""Max characters of curated KB content to inject per request. ~15K
tokens, ~⅓ of Claude's context budget. Over-budget files tail-truncate
(same behavior as memory.recall). Anthropic supports up to 4
``cache_control`` breakpoints per request; injecting KB adds a 2nd
system block alongside the persona block, leaving 2 slots for the
user-message memory prefix + the implicit automatic-cache slot."""


# ── Overlay ──────────────────────────────────────────────────────────────────

POINTER_ANIMATION_MS: int = 400
"""QPropertyAnimation duration for pointer movement. 400ms feels responsive,
not jittery. Phase 2 may switch to bezier easing."""


# ── Latency targets ──────────────────────────────────────────────────────────

E2E_LATENCY_BUDGET_S: float = 1.5
"""Target perceived latency from hotkey release to first audible word.
Expected breakdown: ~150ms STT (AssemblyAI ForceEndpoint) + ~500-800ms
Claude Sonnet 4.6 TTFT + ~200ms Cartesia Sonic-3 TTFB - ~300ms sentence-
streaming overlap = ~800-1200ms. See DECISIONS.md 'Priority inversion:
latency > local-first' for the full budget derivation."""
