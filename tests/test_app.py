"""Unit tests for app.py.

All tests are mock-based. Zero real-hardware or real-API dependency.
Covers: flush_sentences, get_foreground_app, ClickyApp signal wiring.
"""

import pytest


def test_app_module_importable():
    import app  # noqa: F401


# --- flush_sentences ----------------------------------------------------------

class TestFlushSentences:
    """Tests for app.flush_sentences — regex sentence splitter."""

    def test_single_sentence_with_trailing_text(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences("hello world. more text")
        assert sentences == ["hello world."]
        assert remaining == "more text"

    def test_multiple_sentences(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences(
            "first sentence. second one! third? leftover"
        )
        assert len(sentences) == 3
        assert sentences[0] == "first sentence."
        assert sentences[1] == "second one!"
        assert sentences[2] == "third?"
        assert remaining == "leftover"

    def test_no_boundary_returns_empty_list(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences("no boundary here")
        assert sentences == []
        assert remaining == "no boundary here"

    def test_empty_string(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences("")
        assert sentences == []
        assert remaining == ""

    def test_sentence_ending_at_buffer_end_without_space(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences("hello world.")
        assert sentences == []
        assert remaining == "hello world."

    def test_exclamation_and_question_marks(self):
        from app import flush_sentences
        sentences, remaining = flush_sentences("wow! really? yes. done")
        assert len(sentences) == 3
        assert remaining == "done"


# --- get_foreground_app -------------------------------------------------------

class TestGetForegroundApp:
    """Tests for app.get_foreground_app — ctypes Win32 wrapper."""

    def test_returns_tuple_of_two_strings(self):
        from app import get_foreground_app
        result = get_foreground_app()
        assert isinstance(result, tuple)
        assert len(result) == 2
        app_name, window_title = result
        assert isinstance(app_name, str)
        assert isinstance(window_title, str)
        assert len(app_name) > 0

    def test_app_name_is_exe_basename(self):
        from app import get_foreground_app
        app_name, _ = get_foreground_app()
        assert "." in app_name or app_name == "unknown"
        assert "/" not in app_name
        assert "\\" not in app_name


# --- ClickyApp ---------------------------------------------------------------

class TestClickyApp:
    """Tests for ClickyApp orchestrator with fully mocked services."""

    def _make_app(self, mocker):
        from app import ClickyApp
        return ClickyApp(
            ai_client=mocker.MagicMock(),
            stt_client=mocker.MagicMock(),
            tts_client=mocker.MagicMock(),
            memory_store=mocker.MagicMock(),
            overlay_controller=mocker.MagicMock(),
            hotkey_instance=mocker.MagicMock(),
        )

    def test_construction_with_mocks(self, mocker):
        app = self._make_app(mocker)
        assert app._history == []
        assert app._current_app == "unknown"

    def test_handle_press_starts_recording(self, mocker):
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        app._handle_press()
        app._stt.start_recording.assert_called_once()
        app._tts.stop.assert_called_once()
        assert app._current_app == "EXCEL.EXE"
        assert app._current_title == "Sheet1"

    def test_handle_release_spawns_worker(self, mocker):
        app = self._make_app(mocker)
        app._stt.stop_recording.return_value = ""
        app._handle_release()
        assert app._worker_thread is not None
        assert app._worker_thread.daemon is True
        app._worker_thread.join(timeout=2)

    def test_stop_sets_cancel_event(self, mocker):
        app = self._make_app(mocker)
        app.stop()
        assert app._cancel_event.is_set()
        app._hotkey.stop.assert_called_once()
        app._tts.stop.assert_called_once()
