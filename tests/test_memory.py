"""Unit tests for memory.py.

Uses pytest's ``tmp_path`` fixture for real filesystem + real SQLite in an
isolated temp directory per test. No mocks — the whole value is the
round-trip behavior of writes + reads. Full suite completes in <1s.

See docs/superpowers/plans/2026-04-12-memory.md for the design spec and
test-list rationale.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest


# --- Helper ------------------------------------------------------------------

def _make_store(tmp_path: Path):
    """Construct a fully-isolated MemoryStore rooted at ``tmp_path``.

    Inlines the import so the test for "module importable" can run first
    and expose any import-time errors before the rest of the suite.
    """
    from memory import MemoryStore
    return MemoryStore(
        memory_dir=tmp_path / "memory",
        index_db_path=tmp_path / "index.db",
    )


def test_memory_module_importable():
    import memory  # noqa: F401


# --- TestMemoryStoreConstruction ---------------------------------------------

class TestMemoryStoreConstruction:
    """The constructor must be idempotent + lazy on the memory dir."""

    def test_init_creates_memory_dir_lazily_on_first_record(self, tmp_path):
        """Constructor does NOT create the memory directory — record() does.

        The SQLite parent dir IS created (needed for the first connect)
        but the markdown-file subdirectory is lazy. This avoids polluting
        the home directory with an empty folder if the user never actually
        triggers an interaction.
        """
        store = _make_store(tmp_path)
        memory_dir = tmp_path / "memory"
        # Before record(): memory subdir doesn't exist yet.
        assert not memory_dir.exists()
        # But the SQLite index file does exist (schema creation ran).
        assert (tmp_path / "index.db").exists()

        store.record(
            app_name="EXCEL.EXE",
            window_title="Sales Q1 - Excel",
            user_question="how do I freeze panes",
            claude_response="Click View tab, Freeze Panes.",
            pointer_targets=[(1245, 82)],
        )

        assert memory_dir.exists()
        assert (memory_dir / "excel.exe.md").exists()


# --- TestRecall --------------------------------------------------------------

class TestRecall:
    """recall() returns the tail of the per-app markdown file."""

    def test_recall_returns_empty_for_unknown_app(self, tmp_path):
        """No file on disk -> empty string, no exception."""
        store = _make_store(tmp_path)
        assert store.recall("excel.exe") == ""

    def test_recall_returns_full_markdown_when_smaller_than_max_chars(self, tmp_path):
        """Small file (<max_chars) -> return the whole thing unchanged."""
        store = _make_store(tmp_path)
        store.record(
            app_name="EXCEL.EXE",
            window_title="Sales Q1",
            user_question="freeze panes",
            claude_response="Click View -> Freeze Panes.",
            pointer_targets=[(1245, 82)],
        )
        recalled = store.recall("EXCEL.EXE")
        assert "# EXCEL.EXE — Clicky Memory" in recalled
        assert "freeze panes" in recalled
        assert "(1245, 82)" in recalled
        assert "Interactions: 1" in recalled

    def test_recall_returns_empty_for_non_positive_max_chars(self, tmp_path):
        """max_chars <= 0 -> empty string (defensive guard).

        Without the guard, Python's ``text[-0:]`` returns the whole string,
        so a caller that accidentally passes 0 (e.g. from a config override)
        would get the FULL file instead of an empty one. Fail closed.
        Also verifies negative values.
        """
        store = _make_store(tmp_path)
        store.record(
            app_name="EXCEL.EXE",
            window_title="W",
            user_question="q",
            claude_response="r",
            pointer_targets=[(1, 2)],
        )
        assert store.recall("EXCEL.EXE", max_chars=0) == ""
        assert store.recall("EXCEL.EXE", max_chars=-5) == ""
        # Sanity: the file DOES have content (default max_chars returns non-empty).
        assert store.recall("EXCEL.EXE") != ""

    def test_recall_returns_tail_when_larger_than_max_chars(self, tmp_path):
        """Large file (>max_chars) -> return exactly the last max_chars chars.

        Seeds a ~14 KB markdown file via direct filesystem write (bypassing
        the normal record() path) so we can reliably test the tail slice
        without caring about the header format.
        """
        store = _make_store(tmp_path)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir(parents=True)
        large_file = memory_dir / "bigapp.exe.md"
        big_content = "# BigApp — Clicky Memory\n\n" + ("# filler line\n" * 1000)
        large_file.write_text(big_content, encoding="utf-8")

        recalled = store.recall("BIGAPP.EXE", max_chars=500)
        assert len(recalled) == 500
        assert recalled == big_content[-500:]


# --- TestRecord --------------------------------------------------------------

class TestRecord:
    """record() writes the markdown block + updates the SQLite counters."""

    def test_record_creates_markdown_file_and_sqlite_row(self, tmp_path):
        """First-ever record() for an app creates .md + inserts SQLite row."""
        store = _make_store(tmp_path)
        store.record(
            app_name="EXCEL.EXE",
            window_title="Sales Q1 - Excel",
            user_question="freeze panes",
            claude_response="Click View -> Freeze Panes.",
            pointer_targets=[(1245, 82)],
        )

        md = (tmp_path / "memory" / "excel.exe.md").read_text(encoding="utf-8")
        assert "Interactions: 1" in md
        assert "freeze panes" in md

        apps = store.list_known_apps()
        assert len(apps) == 1
        assert apps[0]["app_name"] == "excel.exe"
        assert apps[0]["interaction_count"] == 1
        # On first record, first_seen == last_seen (same datetime.now() value)
        assert apps[0]["first_seen"] == apps[0]["last_seen"]

    def test_record_appends_second_interaction_and_updates_index(self, tmp_path, monkeypatch):
        """Second record() appends, updates count + last_seen, keeps first_seen."""
        # Patch datetime.now() to return a fixed sequence so we can assert on
        # the exact timestamps that land in the file header + SQLite.
        call_times = [
            datetime(2026, 4, 12, 14, 30),
            datetime(2026, 4, 12, 15, 45),
        ]
        call_idx = [0]

        class _FakeDatetime:
            @classmethod
            def now(cls):
                t = call_times[call_idx[0]]
                call_idx[0] += 1
                return t

        monkeypatch.setattr("memory.datetime", _FakeDatetime)

        store = _make_store(tmp_path)
        store.record("EXCEL.EXE", "A", "q1", "r1", [(1, 2)])
        store.record("EXCEL.EXE", "B", "q2", "r2", [(3, 4)])

        md = (tmp_path / "memory" / "excel.exe.md").read_text(encoding="utf-8")
        assert "Interactions: 2" in md
        assert "q1" in md
        assert "q2" in md
        # Both interaction blocks present in order (order within file: older first)
        assert md.count("## 2026-04-12") == 2

        apps = store.list_known_apps()
        assert apps[0]["interaction_count"] == 2
        assert apps[0]["first_seen"] == "2026-04-12 14:30"
        assert apps[0]["last_seen"] == "2026-04-12 15:45"

    def test_record_escapes_triple_backticks_in_user_question_and_response(self, tmp_path):
        """Triple-backticks in user content -> swapped to triple single-quotes.

        Prevents Claude's ```python code fences from corrupting the markdown
        block shape that tools/lint_memory.py (Step 7.5) will parse.
        """
        store = _make_store(tmp_path)
        store.record(
            app_name="vscode.exe",
            window_title="main.py - VS Code",
            user_question="what does ```python do",
            claude_response="The ```python fence starts a code block.",
            pointer_targets=[],
        )
        md = (tmp_path / "memory" / "vscode.exe.md").read_text(encoding="utf-8")
        assert "```" not in md
        assert "'''" in md

    def test_record_handles_empty_pointer_targets_as_text_only_marker(self, tmp_path):
        """Empty pointer list -> '(none — text-only response)' line.

        Keeps the block shape consistent: every interaction always has a
        'Pointed at:' line, even when Claude returned text-only.
        """
        store = _make_store(tmp_path)
        store.record(
            app_name="chrome.exe",
            window_title="Google - Chrome",
            user_question="what is this page about",
            claude_response="It's about search.",
            pointer_targets=[],
        )
        md = (tmp_path / "memory" / "chrome.exe.md").read_text(encoding="utf-8")
        assert "Pointed at: (none — text-only response)" in md

    def test_record_uses_same_timestamp_for_markdown_and_sqlite(self, tmp_path, monkeypatch):
        """Header 'First seen:' and SQLite first_seen/last_seen all share one now()."""

        class _FakeDatetime:
            @classmethod
            def now(cls):
                return datetime(2026, 4, 12, 15, 30)

        monkeypatch.setattr("memory.datetime", _FakeDatetime)

        store = _make_store(tmp_path)
        store.record("excel.exe", "A", "q", "r", [(1, 2)])

        md = (tmp_path / "memory" / "excel.exe.md").read_text(encoding="utf-8")
        assert "First seen: 2026-04-12 15:30" in md
        assert "## 2026-04-12 15:30" in md

        apps = store.list_known_apps()
        assert apps[0]["first_seen"] == "2026-04-12 15:30"
        assert apps[0]["last_seen"] == "2026-04-12 15:30"


# --- TestListKnownApps -------------------------------------------------------

class TestListKnownApps:
    """list_known_apps() returns a sorted list of dicts for lint_memory.py."""

    def test_list_known_apps_empty_store_returns_empty_list(self, tmp_path):
        """Fresh store -> []."""
        store = _make_store(tmp_path)
        assert store.list_known_apps() == []

    def test_list_known_apps_sorted_by_last_seen_desc_with_correct_dict_shape(
        self, tmp_path, monkeypatch
    ):
        """Multiple apps -> sorted most-recent-first, correct dict keys."""
        # Patch datetime.now() to return increasing timestamps.
        times = [
            datetime(2026, 4, 12, 10, 0),  # a.exe first
            datetime(2026, 4, 12, 11, 0),  # b.exe second
            datetime(2026, 4, 12, 12, 0),  # c.exe third
        ]
        idx = [0]

        class _FakeDatetime:
            @classmethod
            def now(cls):
                t = times[idx[0]]
                idx[0] += 1
                return t

        monkeypatch.setattr("memory.datetime", _FakeDatetime)

        store = _make_store(tmp_path)
        store.record("a.exe", "W", "q", "r", [])
        store.record("b.exe", "W", "q", "r", [])
        store.record("c.exe", "W", "q", "r", [])

        apps = store.list_known_apps()
        assert len(apps) == 3
        assert apps[0]["app_name"] == "c.exe"  # most recent first
        assert apps[1]["app_name"] == "b.exe"
        assert apps[2]["app_name"] == "a.exe"

        # Dict shape is stable — lint_memory.py (Step 7.5) will rely on these keys.
        for app in apps:
            assert set(app.keys()) == {
                "app_name",
                "first_seen",
                "last_seen",
                "interaction_count",
                "md_path",
            }


# --- TestSanitizeAppName -----------------------------------------------------

class TestSanitizeAppName:
    """Pure function tests — no fixture, no filesystem."""

    def test_sanitize_lowercases_and_replaces_reserved_chars(self):
        from memory import _sanitize_app_name
        assert _sanitize_app_name("EXCEL.EXE") == "excel.exe"
        assert _sanitize_app_name("My App: v2.EXE") == "my app_ v2.exe"
        assert _sanitize_app_name("a/b\\c") == "a_b_c"

    def test_sanitize_empty_raises(self):
        from memory import _sanitize_app_name
        with pytest.raises(ValueError):
            _sanitize_app_name("")
        with pytest.raises(ValueError):
            _sanitize_app_name("   ")
