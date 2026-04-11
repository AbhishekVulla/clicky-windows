"""Clicky Windows configuration.

Loads environment variables from .env and exposes constants used across the app.
See DECISIONS.md for the rationale behind each default.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ── API keys ─────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
"""Required. Computer Use API beta is Anthropic-direct only. Sonnet 4.6 default."""

ASSEMBLYAI_API_KEY: str | None = os.getenv("ASSEMBLYAI_API_KEY")
"""Required for Phase 1. Streaming STT via AssemblyAI u3-rt-pro WebSocket +
ForceEndpoint for ~150ms P50 PTT finalization. $50 free credit from
https://www.assemblyai.com/dashboard/signup, no credit card required."""

CARTESIA_API_KEY: str | None = os.getenv("CARTESIA_API_KEY")
"""Required for Phase 1. Streaming TTS via Cartesia Sonic-3 WebSocket with
~150-250ms TTFB + expressive "buddy" voice. 20k free credits/month from
https://play.cartesia.ai/sign-in, no credit card required."""


# ── Claude model ─────────────────────────────────────────────────────────────

MODEL_ID: str = os.getenv("MODEL_ID", "claude-sonnet-4-6")
"""Claude model used for vision + Computer Use tool calls. Default Sonnet 4.6
because Haiku 4.5 doesn't support the computer-use-2025-11-24 beta header
(only the older computer-use-2025-01-24). Phase 2 may add benchmark-driven
switching. See DECISIONS.md 'Priority inversion: latency > local-first'."""

COMPUTER_USE_BETA: str = "computer-use-2025-11-24"
"""anthropic-beta header value that activates Computer Use API + pixel-counting training."""

COMPUTER_USE_TOOL_TYPE: str = "computer_20251124"
"""Tool type declared when registering the Computer Use tool with Claude."""


# ── Screen capture ───────────────────────────────────────────────────────────

CANDIDATE_RESOLUTIONS: list[tuple[int, int]] = [
    (1024, 768),   # 4:3   = 1.333 (legacy displays)
    (1280, 800),   # 16:10 = 1.600 (most laptops)
    (1366, 768),   # ~16:9 = 1.779 (external monitors, ultrawide fallback)
]
"""Anthropic-recommended resolutions for Computer Use. capture.py picks the
closest-aspect-ratio pair to the actual monitor to avoid distortion. Mirrors
Clicky's ElementLocationDetector.swift."""


# ── Hotkey ───────────────────────────────────────────────────────────────────

HOTKEY: str = os.getenv("HOTKEY", "ctrl+shift+space")
"""Default push-to-talk hotkey. Ctrl+Shift+Space because pynput's suppress
flag is all-or-nothing global — we cannot suppress just Alt+Space without
blocking all typing. Ctrl+Shift+Space has no default Windows OS behavior,
so we use suppress=False (observe but don't consume).

NEVER ctrl+space (VS Code IntelliSense conflict). Minor conflict: VS Code
triggers Parameter Hints on ctrl+shift+space — acceptable tradeoff.

See DECISIONS.md 2026-04-12 entry "Ctrl+Shift+Space over Alt+Space — pynput
suppress=True is globally destructive, not per-combo" for the pivot story.
Phase 1.5 may add a Win32 RegisterHotKey subclass of PushToTalkHotkey that
restores Alt+Space ergonomics (abstract interface makes it a drop-in swap)."""


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


# ── TTS (Cartesia Sonic-3 WebSocket streaming) ──────────────────────────────

CARTESIA_MODEL_ID: str = "sonic-3"
"""Cartesia's state-space-model-based TTS. ~90ms model-internal TTFB,
150-250ms real-world through the WebSocket stream + sounddevice playback.
Most expressive 'buddy' voice quality in the cloud TTS field as of April 2026.
See DECISIONS.md 'Priority inversion' for the research."""

CARTESIA_VOICE_ID: str = os.getenv(
    "CARTESIA_VOICE_ID",
    "e07c00bc-4134-4eae-9ea4-1a55fb45746b",  # "Brooke - Big Sister" — confident adult female, conversational
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


# ── Memory ───────────────────────────────────────────────────────────────────

_DEFAULT_MEMORY_DIR = Path.home() / ".clicky-windows"

MEMORY_DIR: Path = Path(os.getenv("MEMORY_DIR", str(_DEFAULT_MEMORY_DIR / "memory")))
"""Where per-app markdown files live. One .md per Windows app executable."""

INDEX_DB_PATH: Path = Path(os.getenv("INDEX_DB_PATH", str(_DEFAULT_MEMORY_DIR / "index.db")))
"""SQLite index at ~/.clicky-windows/index.db. Fast lookup for apps + interaction counts."""

INSIGHTS_PATH: Path = Path(os.getenv("INSIGHTS_PATH", str(_DEFAULT_MEMORY_DIR / "insights.md")))
"""Output of tools/lint_memory.py — Karpathy-style weekly health check."""

MEMORY_RECALL_MAX_CHARS: int = 3000
"""Max characters of recalled memory to inject into Claude's system prompt per request."""


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
