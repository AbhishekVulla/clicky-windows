"""Clicky Windows configuration.

Loads environment variables from .env and exposes constants used across the app.
See DECISIONS.md for the rationale behind each default.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ── API ──────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
"""Required for Phase 1. Computer Use API beta is Anthropic-direct only."""

MODEL_ID: str = os.getenv("MODEL_ID", "claude-sonnet-4-6")
"""Claude model used for vision + Computer Use tool calls."""

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

HOTKEY: str = os.getenv("HOTKEY", "alt+space")
"""Default push-to-talk hotkey. NEVER ctrl+space (conflicts with VS Code
IntelliSense). Fallback if alt+space suppression is flaky: ctrl+shift+space."""


# ── STT ──────────────────────────────────────────────────────────────────────

WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "base")
"""faster-whisper model size. Options: tiny, base, small, medium, large.
base = ~150 MB, ~real-time on modern CPU. Fall back to tiny if too slow."""

WHISPER_DEVICE: str = "cpu"
"""Whisper device. CPU for privacy-first local STT. GPU support is Phase 2."""

WHISPER_COMPUTE_TYPE: str = "int8"
"""Quantization for faster-whisper. int8 is 4x faster than float32 with negligible quality loss."""

AUDIO_SAMPLE_RATE: int = 16_000
"""Whisper expects 16 kHz mono audio."""


# ── TTS ──────────────────────────────────────────────────────────────────────

TTS_RATE_WPM: int = 180
"""pyttsx3 voice rate in words per minute. Default is 200 which is too fast."""


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

E2E_LATENCY_BUDGET_S: float = 7.0
"""Target end-to-end latency from hotkey release to voice response start.
Dominant costs: Whisper (~2s) + Anthropic API (~3-5s)."""
