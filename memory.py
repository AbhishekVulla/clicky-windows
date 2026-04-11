"""Clicky Windows persistent memory — Karpathy-style markdown + SQLite index.

This module is the **Phase 1 differentiator**. Every competitor (Clicky/Clippi/
tekram/danpeg/GhostDesk) is a stateless Claude wrapper — upstream Issue #30
on farzaa/clicky says so explicitly: *"stateless Claude wrapper: no memory
between sessions, no tools, no persistent context."* This module is the whole
reason Clicky Windows is more than a port — it gives Claude a human-readable
long-term memory per Windows app.

Design philosophy (Karpathy LLM-KB tweet, cited in DECISIONS.md 2026-04-11
"Karpathy-style markdown memory + SQLite index hybrid"):

    "I thought I had to reach for fancy RAG, but the LLM has been pretty
     good about auto-maintaining index files and brief summaries of all the
     documents and it reads all the important related data fairly easily
     at this small scale."

So: no vector DB, no embeddings, no RAG. Just plain markdown files
(`~/.clicky-windows/memory/<app>.md`) that the LLM writes and reads directly,
plus a tiny SQLite index (`~/.clicky-windows/index.db`) for fast
"how many times has this user used this app" lookups without re-reading every
markdown file. The markdown files are the source of truth; SQLite is a
denormalized cache. Humans can `cat <app>.md` in a terminal and understand
exactly what Clicky remembers about them — transparency is the UX contract.

Responsibility boundary:
- THIS MODULE owns the read/write API over the markdown files + SQLite index.
  It does NOT decide when to record, what to inject into Claude's prompt, or
  which app is currently active.
- app.py (Step 7) calls `recall()` before sending a Claude request, and
  `record()` after receiving the response. app.py is also the single-threaded
  writer (Qt main thread) — memory.py has no Qt deps and no internal locking
  beyond SQLite's built-in WAL mode.
- tools/lint_memory.py (Step 7.5) uses `list_known_apps()` to iterate over all
  known apps for the weekly insights pass.

Top-to-bottom order (so `py -3.13 -m memory` works — `__main__` MUST be LAST
per feedback_main_block_ordering.md):
    1. Module docstring
    2. Imports
    3. Constants (Windows reserved chars, header template)
    4. Pure helper functions (_sanitize_app_name, _escape_markdown_fences,
       _escape_single_line)
    5. MemoryStore class
    6. __main__ manual-verification block
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from config import INDEX_DB_PATH, MEMORY_DIR, MEMORY_RECALL_MAX_CHARS


# --- Constants ---------------------------------------------------------------

_WINDOWS_RESERVED_CHARS = set('<>:"/\\|?*')
"""Characters Windows forbids in file names. Sanitization replaces each with
an underscore. Forward slash / backslash are the two most common in
GetForegroundWindow() process names with weird edge cases."""

_HEADER_TRANSPARENCY_LINE = (
    "_(This file is human-readable. Delete it to reset memory for this app.\n"
    "  Clicky reads the tail of this file to remember past interactions.)_"
)
"""The italic transparency line in every app's memory file header — the
Karpathy "human-readable, user can delete to reset" promise that makes
persistent memory feel benign instead of creepy. Stored as a constant so the
display header can be assembled via f-string (avoids .format() injection
risk if an app name contains literal ``{`` or ``}`` characters)."""


# --- Pure helpers ------------------------------------------------------------

def _sanitize_app_name(name: str) -> str:
    """Normalize an app name for use as a filesystem + SQLite primary key.

    Lowercases the whole string, strips leading/trailing whitespace, and
    replaces every Windows-reserved character (``<>:"/\\|?*``) with an
    underscore. The sanitized name is stable across case changes, so
    ``"EXCEL.EXE"`` and ``"excel.exe"`` always map to the same memory file.

    The ORIGINAL (pre-sanitization) name should still be passed into
    ``record()`` so the markdown header can show the human-friendly form;
    this helper is only for the filename + DB key.

    Raises:
        ValueError: if `name` is empty or becomes empty after sanitization.

    Examples:
        >>> _sanitize_app_name("EXCEL.EXE")
        'excel.exe'
        >>> _sanitize_app_name('My App: v2.EXE')
        'my app_ v2.exe'
        >>> _sanitize_app_name('a/b\\\\c')
        'a_b_c'
    """
    if not name:
        raise ValueError("app_name cannot be empty")
    lowered = name.lower().strip()
    sanitized = "".join(
        "_" if ch in _WINDOWS_RESERVED_CHARS else ch
        for ch in lowered
    )
    if not sanitized.strip():
        raise ValueError(f"app_name is empty after sanitization: {name!r}")
    return sanitized


def _escape_markdown_fences(text: str) -> str:
    """Replace triple backticks with triple single-quotes.

    Claude's responses can contain ```python code fences; writing those
    verbatim into our markdown blocks would break the block shape when
    tools/lint_memory.py parses them in Step 7.5. Swap to ``'''`` — plain
    ASCII, human-readable, impossible to confuse with a real fence.
    Zero-width-space insertion was rejected as too clever.
    """
    return text.replace("```", "'''")


def _escape_single_line(text: str) -> str:
    """Escape a string that must fit on a single markdown line.

    User questions, window titles, and the display name in the header are
    all rendered on one line each. Embedded newlines would break the
    "## heading\\nfield\\nfield\\nfield" block shape that lint_memory.py
    expects. Replace ``\\n`` with the visible unicode ``↵`` marker (so the
    user can still see the original newline boundary when they cat the
    file) and strip ``\\r`` entirely. Also applies fence escaping.
    """
    return _escape_markdown_fences(text).replace("\n", " ↵ ").replace("\r", "")


# --- MemoryStore -------------------------------------------------------------

class MemoryStore:
    """Per-app persistent memory: one markdown file per Windows executable
    plus a SQLite index for fast counters.

    Construct once at app startup (via ``app.py`` Step 7). The constructor
    ensures the SQLite schema exists via ``CREATE TABLE IF NOT EXISTS`` +
    ``CREATE INDEX IF NOT EXISTS`` so subsequent constructions are
    idempotent. The memory directory itself is created lazily on the first
    ``record()`` call — if the user never triggers an interaction, we
    don't pollute their home directory with empty folders.

    **Thread safety:** each method opens its own short-lived SQLite
    connection and closes it before returning. SQLite's WAL journal mode
    handles concurrent reads cleanly; Phase 1 has ONE writer (the Qt main
    thread via app.py) so we don't need explicit in-process locking.
    Phase 2 proactive mode may add a background writer; that ceremony
    belongs in the Step 7 app.py plan, not here.

    **Error handling:** this module trusts its inputs — callers pass
    validated app names and non-null strings. The only ``ValueError`` we
    raise is from ``_sanitize_app_name`` on an empty/whitespace-only name,
    which indicates a real bug in the caller (app.py) and should surface
    loudly. Filesystem or SQLite errors propagate to the caller.
    """

    def __init__(
        self,
        memory_dir: Path | str = MEMORY_DIR,
        index_db_path: Path | str = INDEX_DB_PATH,
    ) -> None:
        """Ensure the SQLite parent directory exists and the schema is live.

        Does NOT create ``memory_dir`` — that's lazy, on first ``record()``.
        Does NOT open a persistent SQLite connection — each method opens
        its own so Phase 2 proactive mode (multi-thread reads) doesn't
        need lock coordination with the Qt main thread.

        Args:
            memory_dir: directory where per-app markdown files live.
                Defaults to ``config.MEMORY_DIR`` = ``~/.clicky-windows/memory``.
            index_db_path: path to the SQLite index file. Defaults to
                ``config.INDEX_DB_PATH`` = ``~/.clicky-windows/index.db``.
        """
        self.memory_dir = Path(memory_dir)
        self.index_db_path = Path(index_db_path)
        # Only create the SQLite parent dir (needed for the first connect()).
        # The memory_dir for markdown files stays lazy until record().
        self.index_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh SQLite connection in autocommit mode with Row factory.

        ``isolation_level=None`` = autocommit, so individual execute() calls
        commit immediately without manual BEGIN/COMMIT. ``row_factory =
        Row`` gives dict-like access in list_known_apps().
        """
        conn = sqlite3.connect(str(self.index_db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Create the ``apps`` table + index + enable WAL mode. Idempotent.

        ``CREATE TABLE IF NOT EXISTS`` means this is safe to call every
        session; no migration logic needed for Phase 1. Phase 2 schema
        changes will use ``ALTER TABLE ... ADD COLUMN`` which is also
        idempotent against modern SQLite.
        """
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS apps (
                    app_name          TEXT PRIMARY KEY,
                    first_seen        TEXT NOT NULL,
                    last_seen         TEXT NOT NULL,
                    interaction_count INTEGER NOT NULL DEFAULT 0,
                    md_path           TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_apps_last_seen "
                "ON apps(last_seen DESC)"
            )
        finally:
            conn.close()

    def _md_path_for(self, sanitized_name: str) -> Path:
        """Path to the markdown file for a pre-sanitized app name."""
        return self.memory_dir / f"{sanitized_name}.md"

    # -- Public API -----------------------------------------------------------

    def recall(
        self,
        app_name: str,
        max_chars: int = MEMORY_RECALL_MAX_CHARS,
    ) -> str:
        """Return the tail of the markdown file for ``app_name``.

        Reads the whole file and returns the last ``max_chars`` characters
        (or the whole file if smaller). Returns ``''`` if the file does not
        exist. Python's ``str`` slicing is codepoint-indexed (not byte-
        indexed), so ``text[-max_chars:]`` can never split a UTF-8
        character mid-sequence — no manual safety needed.

        This is Option 1 of the recall-shape tradeoff: a dumb tail read,
        no filtering, no relevance scoring. The LLM sees raw history and
        decides what matters. Phase 2 may add a smarter recall if 5+ real
        sessions reveal that raw-tail is insufficient; that's a Karpathy
        "wait for the data" decision.

        Args:
            app_name: display form, will be sanitized internally. Safe to
                pass the exact string from ``GetForegroundWindow()``.
            max_chars: hard cap on returned string length. Defaults to
                ``config.MEMORY_RECALL_MAX_CHARS`` (3000, ~750 tokens).

        Returns:
            The markdown content (up to ``max_chars`` characters, from the
            end), or ``''`` if no memory exists for this app yet.
        """
        # Defensive: max_chars <= 0 is nonsense input. Python's text[-0:]
        # returns the whole string (not empty), so a direct tail-read would
        # silently return the entire file. Fail closed instead.
        if max_chars <= 0:
            return ""
        sanitized = _sanitize_app_name(app_name)
        md_path = self._md_path_for(sanitized)
        if not md_path.exists():
            return ""
        text = md_path.read_text(encoding="utf-8")
        if len(text) > max_chars:
            return text[-max_chars:]
        return text

    def record(
        self,
        app_name: str,
        window_title: str,
        user_question: str,
        claude_response: str,
        pointer_targets: list[tuple[int, int]],
    ) -> None:
        """Append an interaction to the app's markdown and update SQLite.

        Called by ``app.py`` Step 7 immediately after a Claude response
        lands + the overlay animation completes. Creates the markdown file
        + memory directory lazily on the first call for each app. The
        markdown header is rewritten on every call so ``Interactions:`` is
        always current.

        All timestamps within a single ``record()`` call share the same
        ``datetime.now()`` value — this prevents a micro-skew between the
        markdown header and the SQLite ``last_seen`` column that would
        otherwise make sort-by-last_seen non-deterministic in rapid-fire
        test scenarios.

        Args:
            app_name: display form (e.g. ``"EXCEL.EXE"``). Preserved in
                the markdown header for human readability; sanitized for
                the filename + SQLite key.
            window_title: the active window title at interaction time.
                Rendered on one line in the block.
            user_question: transcript of the user's voice question.
                Rendered in the block heading inside quotes.
            claude_response: the natural-language text that was sent to TTS.
                Rendered in the ``Response:`` paragraph.
            pointer_targets: list of (x, y) tuples in physical pixels for
                the overlay pointer animation. Empty list means Claude
                returned text-only (``"no specific element"``) — rendered
                as ``(none — text-only response)`` for block-shape stability.
        """
        sanitized = _sanitize_app_name(app_name)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        md_path = self._md_path_for(sanitized)
        md_path_abs = str(md_path.absolute())

        # --- SQLite upsert (single connection, short-lived) ----------------
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT first_seen, interaction_count FROM apps WHERE app_name = ?",
                (sanitized,),
            ).fetchone()

            if row is None:
                first_seen = timestamp
                new_count = 1
                conn.execute(
                    """
                    INSERT INTO apps
                      (app_name, first_seen, last_seen, interaction_count, md_path)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (sanitized, first_seen, timestamp, new_count, md_path_abs),
                )
            else:
                first_seen = row["first_seen"]
                new_count = row["interaction_count"] + 1
                conn.execute(
                    """
                    UPDATE apps
                       SET last_seen = ?, interaction_count = ?, md_path = ?
                     WHERE app_name = ?
                    """,
                    (timestamp, new_count, md_path_abs, sanitized),
                )
        finally:
            conn.close()

        # --- Markdown write (creates memory_dir on first call) --------------
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Escape all user-facing strings before embedding in markdown.
        escaped_question = _escape_single_line(user_question)
        escaped_window = _escape_single_line(window_title)
        escaped_response = _escape_markdown_fences(claude_response)

        if pointer_targets:
            pointer_str = ", ".join(f"({x}, {y})" for (x, y) in pointer_targets)
        else:
            pointer_str = "(none — text-only response)"

        new_block = (
            f'## {timestamp} — "{escaped_question}"\n'
            f"Window: {escaped_window}\n"
            f"Response: {escaped_response}\n"
            f"Pointed at: {pointer_str}\n"
            f"\n"
        )

        # Header reflects the ORIGINAL app name (escaped for single-line safety)
        # plus the newly-computed count. Built via f-string (not .format) so
        # literal ``{`` or ``}`` in the display name can never trigger a
        # KeyError or format-spec crash.
        display_name = _escape_single_line(app_name)
        new_header = (
            f"# {display_name} — Clicky Memory\n"
            f"\n"
            f"First seen: {first_seen}\n"
            f"Interactions: {new_count}\n"
            f"{_HEADER_TRANSPARENCY_LINE}\n"
            f"\n"
        )

        # Preserve existing interaction blocks: everything from the first
        # "## " heading to EOF. If the file doesn't exist yet or has no
        # interaction blocks (malformed / manually edited), body is empty
        # and we get a clean-slate rewrite.
        if md_path.exists():
            existing = md_path.read_text(encoding="utf-8")
            idx = existing.find("## ")
            body = existing[idx:] if idx != -1 else ""
        else:
            body = ""

        md_path.write_text(new_header + body + new_block, encoding="utf-8")

    def list_known_apps(self) -> list[dict]:
        """Return every app with a SQLite row, sorted most-recent-first.

        Used by tools/lint_memory.py (Step 7.5) and future Phase 2
        proactive mode. Each dict has keys ``{app_name, first_seen,
        last_seen, interaction_count, md_path}``. Returns ``[]`` if no
        apps have been recorded yet.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT app_name, first_seen, last_seen, interaction_count, md_path
                  FROM apps
                 ORDER BY last_seen DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

# --- Manual verification entry point -----------------------------------------

if __name__ == "__main__":
    # Run: py -3.13 -m memory
    #
    # Writes to the REAL ~/.clicky-windows/ dir (not a temp dir) so the user
    # can inspect the files via File Explorer and confirm the "human-readable"
    # claim. Uses a synthetic app name CLICKY_GATE_TEST.EXE so repeated gate
    # runs don't pollute real Excel memory built up during actual usage.
    import sys as _sys

    # Windows consoles default to cp1252 which cannot encode common Unicode
    # characters that land in Claude responses (→, —, em-quotes, etc.).
    # Reconfigure stdout to UTF-8 with replacement fallback so the manual
    # gate prints cleanly even when the .md content contains arrows.
    # app.py (Step 7) will need the same reconfigure at its own entry point.
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass  # older Python or unusual stdout — best-effort

    _TEST_APP = "CLICKY_GATE_TEST.EXE"

    print("=" * 70)
    print("Clicky Windows -- memory.py manual verification")
    print("=" * 70)

    store = MemoryStore()
    print(f"\nWriting to: {store.memory_dir.parent}")

    # Clean slate for this gate's synthetic app so interaction count starts
    # at 3 each run. Real app memory (EXCEL.EXE etc.) is untouched.
    _sanitized_test = _sanitize_app_name(_TEST_APP)
    _test_md = store.memory_dir / f"{_sanitized_test}.md"
    if _test_md.exists():
        _test_md.unlink()
    _cleanup_conn = store._connect()
    try:
        _cleanup_conn.execute("DELETE FROM apps WHERE app_name = ?", (_sanitized_test,))
    finally:
        _cleanup_conn.close()

    print(f"\nSeeding 3 fake interactions for {_TEST_APP}...")
    store.record(
        app_name=_TEST_APP,
        window_title="Sales Q1 - Excel",
        user_question="how do I freeze panes",
        claude_response="Click View tab, then Freeze Panes → Freeze Top Row.",
        pointer_targets=[(1245, 82)],
    )
    print('  [1] "how do I freeze panes"        -> (1245, 82)')

    store.record(
        app_name=_TEST_APP,
        window_title="Sales Q1 - Excel",
        user_question="sort column alphabetically",
        claude_response="Select the column, click Data tab → Sort A to Z.",
        pointer_targets=[(892, 110)],
    )
    print('  [2] "sort column alphabetically"   -> (892, 110)')

    store.record(
        app_name=_TEST_APP,
        window_title="Budget 2026 - Excel",
        user_question="what's a pivot table",
        claude_response=(
            "A pivot table summarizes large datasets by grouping and "
            "aggregating rows — great for turning 10,000 rows into a 20-"
            "row summary."
        ),
        pointer_targets=[(1456, 280)],
    )
    print('  [3] "what\'s a pivot table"         -> (1456, 280)')

    recalled = store.recall(_TEST_APP)
    print(f"\nrecall({_TEST_APP!r}) returned {len(recalled)} chars:")
    print("-" * 16 + " RECALLED MEMORY (last 3000 chars) " + "-" * 19)
    print(recalled)
    print("-" * 70)

    print("\nlist_known_apps():")
    for i, app in enumerate(store.list_known_apps(), 1):
        print(
            f"  [{i}] {app['app_name']:<30} "
            f"first={app['first_seen']}   "
            f"interactions={app['interaction_count']}   "
            f"last={app['last_seen']}"
        )

    print(f"\nSQLite:   {store.index_db_path}")
    print(f"Markdown: {_test_md}")

    print("\nManual verification checklist (confirm each):")
    print("  1. Recalled markdown is human-readable (cat the file)")
    print("  2. Interaction count in the file header matches seeded number (3)")
    print("  3. list_known_apps() shows the test app with correct counts")
    print("  4. Delete the .md file + re-run this script -> recall() returns '',")
    print("     record() re-creates file cleanly")
    print("=" * 70)
