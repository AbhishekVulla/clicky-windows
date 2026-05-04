"""Curated knowledge base — user-uploadable per-app docs.

Lean architecture (decided 2026-05-04 after retracting JaySmith's
folder+TOML+overview pattern as cargo-cult for our scale): one .md
file per app at ``KB_DIR/<app_name>.md``. Match by .exe basename
(sanitized identically to memory.py — same convention so users see
matching filenames in `~/.clicky-windows/memory/` and KB folder).
No metadata files, no folder hierarchy, no keyword ranking.

If the file is missing → ``recall()`` returns ``('', '')``. The
pipeline worker checks for empty + skips KB injection, so Claude
proceeds with vision + memory only. This is the "Claude already
knows that software" path.

If the file is over ``KB_RECALL_MAX_CHARS``, tail-truncate (matches
``memory.recall()`` overflow handling).

The user's mental model: drop ``edupack.exe.md`` (or whatever the
.exe basename is — visible in their existing memory folder) into
``KB_DIR``, Clicky picks it up next PTT.
"""
from __future__ import annotations

from pathlib import Path

from config import KB_DIR, KB_RECALL_MAX_CHARS


def _sanitize_app_name(app_name: str) -> str:
    """Lowercase + replace filesystem-reserved chars.

    Mirrors ``memory._sanitize_app_name`` exactly so the same
    foreground app yields the same filename in both folders. Users
    can `ls ~/.clicky-windows/memory/` to learn the canonical name
    for their app, then drop the matching file in KB_DIR.
    """
    sanitized = app_name.lower()
    for ch in ":\\/":
        sanitized = sanitized.replace(ch, "_")
    return sanitized


def recall(
    app_name: str,
    kb_dir: Path | None = None,
    max_chars: int = KB_RECALL_MAX_CHARS,
) -> tuple[str, str]:
    """Look up the curated KB for ``app_name``.

    Args:
        app_name: foreground .exe basename, e.g. ``"EDUPACK.EXE"``.
        kb_dir: override the KB folder (test hook). Defaults to
            ``config.KB_DIR``.
        max_chars: tail-truncate limit. Defaults to
            ``config.KB_RECALL_MAX_CHARS``.

    Returns:
        ``(content, sanitized_name)``. ``content`` is the markdown
        body, tail-truncated to ``max_chars`` if the file exceeds
        it. ``sanitized_name`` (e.g. ``"edupack.exe"``) is what
        ``ai.py`` will display in the system-prompt injection
        marker after stripping the .exe suffix.

        Both empty if no file matches, ``app_name`` is blank, or
        ``max_chars <= 0`` (defensive guard — Python's ``text[-0:]``
        otherwise returns the whole string, so a caller passing 0
        from a misconfigured override would get the FULL file).
    """
    if max_chars <= 0:
        return ("", "")
    if not app_name or not app_name.strip():
        return ("", "")

    sanitized = _sanitize_app_name(app_name)
    base_dir = Path(kb_dir) if kb_dir is not None else KB_DIR
    md_path = base_dir / f"{sanitized}.md"
    if not md_path.is_file():
        return ("", "")

    text = md_path.read_text(encoding="utf-8")
    if len(text) > max_chars:
        text = text[-max_chars:]
    return (text, sanitized)


if __name__ == "__main__":
    print("=" * 70)
    print("Clicky Windows -- kb.py manual verification")
    print(f"  KB_DIR: {KB_DIR}")
    print(f"  KB_RECALL_MAX_CHARS: {KB_RECALL_MAX_CHARS}")
    print("=" * 70)

    test_apps = [
        "EDUPACK.EXE",
        "edupack.exe",
        "Fusion360.exe",
        "blender.exe",
        "DOES_NOT_EXIST.EXE",
    ]
    for app in test_apps:
        content, name = recall(app)
        if content:
            print(f"\n{app:30s} -> matched {name!r}, {len(content)} chars")
            preview = content[:120].replace("\n", " ")
            print(f"  preview: {preview!r}...")
        else:
            print(
                f"\n{app:30s} -> no file ({_sanitize_app_name(app)}.md "
                f"not in {KB_DIR})"
            )

    print(f"\nDrop an .md file at:\n  {KB_DIR / 'edupack.exe.md'}")
    print("...then re-run to see it injected.")
