"""Ollama health + compatibility helpers (v0.2.1 — Issue #1 fix D).

When the user picks Ollama as the LLM provider, certain vision models
require a minimum Ollama version. ``llama3.2-vision`` (mllama
architecture) needs Ollama >=0.4.x. Older versions return HTTP 500
with ``error loading model: unknown model architecture: 'mllama'``,
which surfaces to the user as a silent failure 5-10 seconds into
their first PTT.

This module pings ``/api/version`` and compares against a known
model->min-version table. The Settings dialog wires the result into
a non-blocking warning QMessageBox on Save, and app.py logs the
result to the per-interaction debug log on startup.

What this module does NOT do:
- Enable architectures Ollama itself doesn't support. The user still
  needs to upgrade Ollama OR pick a compatible model.
- Block save. Warning is informational; user can override.
- Fail noisily when Ollama is unreachable. ``detect_ollama_version``
  returns None on network error, and ``check_model_compatibility``
  treats None as "can't check" (returns no warning rather than a
  misleading one).
"""
from __future__ import annotations

from typing import Optional

import httpx


# Known vision-model minimum-Ollama-version requirements.
#
# llama3.2-vision uses the mllama architecture. Ollama added mllama
# support in the 0.4.x line (commit 6f25f73, 2024-10). Versions older
# than that return HTTP 500 at model-load time.
#
# llava, llava-llama3, qwen2.5-vl, and bakllava all use widely-supported
# architectures (llava-1.5 family + Qwen) that work on every Ollama
# version with any vision support (>=0.1.30 ish). No minimum recorded.
MODEL_MIN_OLLAMA: dict[str, str] = {
    "llama3.2-vision": "0.4.0",
}


def detect_ollama_version(host: str, timeout: float = 2.0) -> Optional[str]:
    """Ping ``{host}/api/version`` and return the version string.

    Returns None on any failure (network error, non-200, parse error,
    timeout). Caller should treat None as "couldn't check" — distinct
    from "checked, found incompatible."

    Short timeout (2s) so Clicky startup isn't blocked if Ollama is
    down. /api/version is cheap; healthy Ollama responds in ~10ms.
    """
    host = host.rstrip("/")
    url = f"{host}/api/version"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
        if response.status_code != 200:
            return None
        data = response.json()
        version = data.get("version")
        if not isinstance(version, str) or not version.strip():
            return None
        return version.strip()
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def _parse_version(version_str: str) -> Optional[tuple[int, ...]]:
    """Parse a dotted-decimal version string into a tuple of ints.

    Returns None for anything that doesn't parse cleanly (e.g. dev
    builds with hashes appended). Conservative — when we can't parse,
    we skip the compatibility check rather than guess wrong.
    """
    parts = version_str.split(".")
    try:
        # Only take leading numeric components; stop at first non-numeric.
        # Handles e.g. "0.4.0-rc1" -> (0, 4, 0).
        nums = []
        for p in parts:
            # Strip any trailing non-numeric suffix
            num_str = ""
            for ch in p:
                if ch.isdigit():
                    num_str += ch
                else:
                    break
            if not num_str:
                break
            nums.append(int(num_str))
        if not nums:
            return None
        return tuple(nums)
    except (ValueError, AttributeError):
        return None


def check_model_compatibility(
    model: str,
    ollama_version: Optional[str],
) -> Optional[str]:
    """Return a user-facing warning string if the model needs a newer
    Ollama version than the user has, else None.

    Returns None when:
    - Model has no recorded minimum (works on all versions)
    - Ollama version couldn't be detected (don't guess — let the
      real call fail with the actual error if it does)
    - Either version string can't be parsed

    The warning is one line, names both the model and the version
    mismatch, and suggests a concrete action.
    """
    if ollama_version is None:
        return None

    # Strip optional "ollama/" prefix the user might have on the
    # model name (matches OllamaClient.__init__ behavior).
    model_clean = model
    if model_clean.lower().startswith("ollama/"):
        model_clean = model_clean[len("ollama/"):]

    min_required = MODEL_MIN_OLLAMA.get(model_clean)
    if min_required is None:
        return None  # no known minimum, assume compatible

    have = _parse_version(ollama_version)
    need = _parse_version(min_required)
    if have is None or need is None:
        return None  # couldn't parse, don't warn on guess

    if have >= need:
        return None  # compatible

    return (
        f"{model_clean} needs Ollama >={min_required} — yours is "
        f"{ollama_version}. Switch to llava:7b or upgrade Ollama."
    )
