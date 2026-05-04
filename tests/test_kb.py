"""Unit tests for kb.py.

Mock-free where possible: uses pytest's ``tmp_path`` fixture for real
filesystem round-trips. Mirrors test_memory.py style.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_kb_module_importable():
    import kb  # noqa: F401


# --- TestSanitizeAppName -----------------------------------------------------

class TestSanitizeAppName:
    """Pure function tests — no fixture, no filesystem."""

    def test_sanitize_lowercases_keeping_dot_exe(self):
        from kb import _sanitize_app_name
        assert _sanitize_app_name("EXCEL.EXE") == "excel.exe"
        assert _sanitize_app_name("EduPack.EXE") == "edupack.exe"

    def test_sanitize_replaces_filesystem_reserved_chars(self):
        from kb import _sanitize_app_name
        assert _sanitize_app_name("My App: v2.EXE") == "my app_ v2.exe"
        assert _sanitize_app_name("a/b\\c") == "a_b_c"


# --- TestRecall --------------------------------------------------------------

class TestRecall:
    """recall() reads <app>.md from KB_DIR (or override). Returns
    (content, sanitized_name) or ('', '') for the empty paths."""

    def test_recall_returns_empty_for_missing_file(self, tmp_path: Path):
        """No .md file for this app → ('', '') — Claude already knows."""
        from kb import recall
        content, name = recall("edupack.exe", kb_dir=tmp_path)
        assert content == ""
        assert name == ""

    def test_recall_returns_empty_when_kb_dir_does_not_exist(
        self, tmp_path: Path
    ):
        """Override pointing at a missing dir → ('', '') — no exception."""
        from kb import recall
        missing = tmp_path / "does_not_exist"
        content, name = recall("edupack.exe", kb_dir=missing)
        assert content == ""
        assert name == ""

    def test_recall_returns_full_content_when_file_under_budget(
        self, tmp_path: Path
    ):
        """Small file (< max_chars) → full content + sanitized name."""
        from kb import recall
        body = "# EduPack KB\n\n## Plotting\nUse Chart > Add..."
        (tmp_path / "edupack.exe.md").write_text(body, encoding="utf-8")
        content, name = recall("EDUPACK.EXE", kb_dir=tmp_path)
        assert content == body
        assert name == "edupack.exe"

    def test_recall_tail_truncates_when_over_budget(self, tmp_path: Path):
        """File > max_chars → exactly the last max_chars chars."""
        from kb import recall
        big_body = "x" * 80_000
        (tmp_path / "fusion360.exe.md").write_text(big_body, encoding="utf-8")
        content, _ = recall("Fusion360.exe", kb_dir=tmp_path, max_chars=60_000)
        assert len(content) == 60_000
        assert content == big_body[-60_000:]

    def test_recall_returns_empty_for_non_positive_max_chars(
        self, tmp_path: Path
    ):
        """max_chars <= 0 → ('', '') (defensive — Python's text[-0:]
        returns FULL string, so a misconfigured caller would get
        the whole file. Fail closed.)."""
        from kb import recall
        (tmp_path / "edupack.exe.md").write_text("hello", encoding="utf-8")
        assert recall("edupack.exe", kb_dir=tmp_path, max_chars=0) == ("", "")
        assert recall("edupack.exe", kb_dir=tmp_path, max_chars=-5) == ("", "")
        # Sanity: positive max_chars works on the same file.
        assert recall("edupack.exe", kb_dir=tmp_path)[0] != ""

    def test_recall_returns_empty_for_blank_app_name(self, tmp_path: Path):
        """Empty/whitespace app_name → ('', '') — no exception."""
        from kb import recall
        assert recall("", kb_dir=tmp_path) == ("", "")
        assert recall("   ", kb_dir=tmp_path) == ("", "")

    def test_recall_uses_default_kb_dir_when_override_is_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Without explicit kb_dir param, falls back to config.KB_DIR.

        Patches kb.KB_DIR module-level binding (the value imported at
        module load time) — this is how tests verify the env-override
        path without touching the real ~/Documents/Clicky Wiki/.
        """
        from kb import recall
        import kb as kb_module
        monkeypatch.setattr(kb_module, "KB_DIR", tmp_path)
        body = "# Blender KB\n"
        (tmp_path / "blender.exe.md").write_text(body, encoding="utf-8")
        content, name = recall("blender.exe")
        assert content == body
        assert name == "blender.exe"
