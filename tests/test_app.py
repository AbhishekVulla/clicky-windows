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

    def test_handle_press_sets_tts_grace_on_stt(self, mocker):
        """_handle_press calls tts.stop() then sets a ~200ms STT grace window
        so speaker decay doesn't leak into the transcription.
        """
        import time as _t
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))

        t_before = _t.time()
        app._handle_press()

        app._stt.set_tts_grace_until.assert_called_once()
        grace_ts = app._stt.set_tts_grace_until.call_args.args[0]
        # Should be ~200ms in the future (give ±50ms slack for test timing)
        assert grace_ts >= t_before + 0.150, (
            f"Grace ts {grace_ts} should be ~200ms after t_before {t_before}"
        )
        assert grace_ts <= t_before + 0.300, (
            f"Grace ts {grace_ts} should not be more than 300ms in future"
        )

    def test_handle_release_sets_tts_grace_when_cancelling_worker(self, mocker):
        """On a re-press mid-response, _handle_release kills in-flight TTS + sets
        grace so the new PTT doesn't pick up the aborted TTS's decay.
        """
        import threading
        import time as _t
        app = self._make_app(mocker)
        app._stt.stop_recording.return_value = ""

        # Fake an in-flight worker thread so the cancel branch runs.
        fake_worker = mocker.MagicMock()
        fake_worker.is_alive.return_value = True
        app._worker_thread = fake_worker

        t_before = _t.time()
        app._handle_release()

        app._tts.stop.assert_called()
        app._stt.set_tts_grace_until.assert_called()
        grace_ts = app._stt.set_tts_grace_until.call_args.args[0]
        assert grace_ts >= t_before + 0.150

        # Let the spawned worker thread exit cleanly so pytest teardown is clean.
        if app._worker_thread is not None and app._worker_thread is not fake_worker:
            if hasattr(app._worker_thread, "join"):
                try:
                    app._worker_thread.join(timeout=2)
                except Exception:
                    pass

    def test_stop_sets_cancel_event(self, mocker):
        app = self._make_app(mocker)
        app.stop()
        assert app._cancel_event.is_set()
        app._hotkey.stop.assert_called_once()
        app._tts.stop.assert_called_once()

    def test_press_handler_shows_waveform_at_cursor(self, mocker):
        """Path A Task 10: _handle_press emits sig_show_waveform with the cursor
        position + the containing monitor → OverlayController routes to the
        right screen + hides cursor polygon + shows the 5-bar waveform."""
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        mocker.patch("app.get_cursor_position", return_value=(500, 600))
        mocker.patch("app.capture_all_screens", return_value=[mocker.MagicMock()])
        # Ensure list_monitors + monitor_containing return a usable mon dict.
        mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        mocker.patch("app.list_monitors", return_value=[mon])
        mocker.patch("app.monitor_containing", return_value=mon)

        app._handle_press()
        if app._capture_thread is not None:
            app._capture_thread.join(timeout=2.0)

        app._overlay.show_waveform.assert_called_once()
        call_args = app._overlay.show_waveform.call_args
        assert call_args.args[0] == 500, "x coordinate should be cursor x"
        assert call_args.args[1] == 600, "y coordinate should be cursor y"
        assert call_args.args[2] == mon, "monitor dict should be the containing monitor"

    def test_release_handler_hides_waveform(self, mocker):
        """_handle_release must fire hide_waveform so the bars disappear once
        the user lets go of the hotkey."""
        app = self._make_app(mocker)
        app._stt.stop_recording.return_value = ""  # empty transcript → fast exit
        app._handle_release()
        app._overlay.hide_waveform.assert_called()

    def test_audio_level_slot_forwards_to_overlay(self, mocker):
        """RMS level from stt → pyqtSignal → Qt main thread slot →
        overlay.set_audio_level. Test the slot directly since pytest has no
        Qt event loop to marshal the signal.emit() → slot_handler hop."""
        app = self._make_app(mocker)
        app._on_audio_level(0.42)
        app._overlay.set_audio_level.assert_called_once_with(0.42)

    def test_release_emits_show_spinner(self, mocker):
        """On RELEASE, the THINKING spinner must appear at the cursor position."""
        app = self._make_app(mocker)
        app._stt.stop_recording.return_value = ""  # fast-exit pipeline
        mocker.patch("app.get_cursor_position", return_value=(500, 600))
        mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        mocker.patch("app.list_monitors", return_value=[mon])
        mocker.patch("app.monitor_containing", return_value=mon)

        app._handle_release()

        app._overlay.show_spinner.assert_called_once()
        args = app._overlay.show_spinner.call_args.args
        assert args[0] == 500 and args[1] == 600

    def test_press_emits_hide_spinner_to_clear_stale(self, mocker):
        """PRESS must clear any stale spinner from a prior interaction."""
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        mocker.patch("app.get_cursor_position", return_value=(100, 100))
        mocker.patch("app.capture_all_screens", return_value=[mocker.MagicMock()])

        app._handle_press()
        if app._capture_thread is not None:
            app._capture_thread.join(timeout=2.0)

        # Any hide_spinner call proves the defensive clear fired.
        app._overlay.hide_spinner.assert_called()

    def test_hide_spinner_slot_forwards_to_overlay(self, mocker):
        """sig_hide_spinner slot must delegate to overlay.hide_spinner."""
        app = self._make_app(mocker)
        app._on_hide_spinner()
        app._overlay.hide_spinner.assert_called_once()

    def test_press_handler_plays_listening_chime(self, mocker):
        """Path A Task 11: _handle_press plays a short chime the moment the
        hotkey goes down so the user has immediate feedback 'mic is hot, keep
        talking'. Must be non-blocking (0ms pipeline latency)."""
        import app as app_module
        play_spy = mocker.patch.object(app_module, "_play_chime_async")
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        mocker.patch("app.get_cursor_position", return_value=(100, 100))
        mocker.patch("app.capture_all_screens", return_value=[mocker.MagicMock()])

        app = self._make_app(mocker)
        app._handle_press()
        if app._capture_thread is not None:
            app._capture_thread.join(timeout=2.0)

        play_spy.assert_called_once()

    def test_press_handler_kicks_off_capture_in_background(self, mocker):
        """_handle_press starts capture_all_screens + memory.recall on a
        background thread so the work overlaps with the user speaking.

        Saves ~250ms post-release wall-clock (the full capture stage — hide
        overlay + 50ms wait + mss.grab + PIL resize + show overlay).
        """
        app = self._make_app(mocker)
        mocker.patch("app.get_foreground_app", return_value=("EXCEL.EXE", "Sheet1"))
        mocker.patch("app.get_cursor_position", return_value=(100, 200))

        fake_capture = mocker.MagicMock()
        mocker.patch("app.capture_all_screens", return_value=[fake_capture])
        app._memory.recall.return_value = "prior interaction"

        app._handle_press()

        # Background thread should have been spawned.
        assert app._capture_thread is not None, (
            "_handle_press should spawn a background capture thread"
        )
        app._capture_thread.join(timeout=2.0)

        assert app._press_captures == [fake_capture]
        assert app._press_memory == "prior interaction"
        assert app._press_cursor_pos == (100, 200)
        app._memory.recall.assert_called_once_with("EXCEL.EXE")

    def test_pipeline_worker_reuses_press_time_captures_when_cursor_still(self, mocker):
        """If cursor moved <=50px between press and release, reuse the
        press-time captures (no re-grab on release path)."""
        import threading
        app = self._make_app(mocker)

        fake_capture = mocker.MagicMock()
        fake_capture.image = mocker.MagicMock()
        fake_capture.label = "screen 1 of 1"
        fake_capture.scale_x = 1.0
        fake_capture.scale_y = 1.0
        fake_capture.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        fake_capture.target_width = 1280
        fake_capture.target_height = 800
        fake_capture.is_cursor_screen = True

        app._press_captures = [fake_capture]
        app._press_memory = "prior memory"
        app._press_cursor_pos = (100, 100)

        # Cursor moved only 20px (well within 50px threshold)
        mocker.patch("app.get_cursor_position", return_value=(115, 105))
        capture_fn = mocker.patch("app.capture_all_screens")

        app._stt.stop_recording.return_value = "test transcript"
        # Short-circuit the Claude call by making ask_stream a no-op
        fake_stream = mocker.MagicMock()
        fake_stream.text_deltas.return_value = iter([])
        fake_stream.final_result.return_value = mocker.MagicMock(
            spoken_text="ok", coordinate=None, element_label=None, screen_number=None,
        )
        app._ai.ask_stream.return_value.__enter__ = mocker.MagicMock(return_value=fake_stream)
        app._ai.ask_stream.return_value.__exit__ = mocker.MagicMock(return_value=False)

        cancel = threading.Event()
        app._pipeline_worker("EXCEL.EXE", "Sheet1", cancel)

        assert not capture_fn.called, (
            "Expected pipeline to reuse press-time captures when cursor is still"
        )

    def test_pipeline_streams_sentences_during_claude_generation(self, mocker):
        """Pipeline must call tts.speak_sentence for each .!? boundary in the
        Claude stream (not batch tts.speak() at the end). The tag-start character
        '[' must freeze flushing so the POINT tag is never spoken aloud.

        Biggest latency win in Path A: first audible word happens when sentence-1
        is ready (~1200ms after Claude TTFT) instead of when sentence-N is done
        (~3700ms).
        """
        import threading
        from PIL import Image
        app = self._make_app(mocker)

        # Prime press-time captures so the pipeline takes the fast path.
        fake_cap = mocker.MagicMock()
        fake_cap.image = Image.new("RGB", (1280, 800))
        fake_cap.label = "screen 1 of 1"
        fake_cap.scale_x = 1.0; fake_cap.scale_y = 1.0
        fake_cap.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        fake_cap.target_width = 1280; fake_cap.target_height = 800
        fake_cap.is_cursor_screen = True

        app._press_captures = [fake_cap]
        app._press_memory = ""
        app._press_cursor_pos = (100, 100)
        mocker.patch("app.get_cursor_position", return_value=(100, 100))
        app._stt.stop_recording.return_value = "how do I make my repo public"

        # Stream that yields sentences one delta at a time + a [POINT:...] tag at the end.
        def fake_deltas():
            yield "you "; yield "want "; yield "the settings tab. "
            yield "scroll "; yield "down to the bottom. "
            yield "click 'change visibility'. "
            yield "[POINT:721,215:settings tab]"

        fake_stream = mocker.MagicMock()
        fake_stream.text_deltas.return_value = iter(fake_deltas())
        fake_stream.final_result.return_value = mocker.MagicMock(
            spoken_text=(
                "you want the settings tab. "
                "scroll down to the bottom. "
                "click 'change visibility'."
            ),
            coordinate=(721, 215),
            element_label="settings tab",
            screen_number=None,
        )
        app._ai.ask_stream.return_value.__enter__ = mocker.MagicMock(return_value=fake_stream)
        app._ai.ask_stream.return_value.__exit__ = mocker.MagicMock(return_value=False)

        cancel = threading.Event()
        app._pipeline_worker("TEST.EXE", "TestWindow", cancel)

        sentence_calls = [c.args[0] for c in app._tts.speak_sentence.call_args_list]

        # Sentence-level streaming during the Claude generation.
        assert any("settings tab." in s for s in sentence_calls), (
            "First sentence should have been flushed during streaming, "
            f"got speak_sentence calls: {sentence_calls}"
        )
        assert any("to the bottom." in s for s in sentence_calls), (
            "Second sentence should have been flushed during streaming"
        )

        # POINT tag must NEVER appear in anything sent to TTS.
        for s in sentence_calls:
            assert "[POINT" not in s, (
                f"POINT tag must not be spoken aloud, but got: {s!r}"
            )

        # The batch speak() path must be gone (replaced by sentence-level).
        assert not app._tts.speak.called, (
            "Batch tts.speak() should be replaced with sentence-level streaming"
        )

    def test_pipeline_worker_recaptures_on_large_cursor_move(self, mocker):
        """If cursor moved >50px, pipeline re-captures on release (safeguard
        against stale screenshots when user actively repositioned mid-utterance)."""
        import threading
        from PIL import Image
        app = self._make_app(mocker)

        stale_capture = mocker.MagicMock()
        stale_capture.image = Image.new("RGB", (1280, 800))
        stale_capture.label = "stale"
        stale_capture.scale_x = 1.0; stale_capture.scale_y = 1.0
        stale_capture.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        stale_capture.target_width = 1280; stale_capture.target_height = 800
        stale_capture.is_cursor_screen = True

        fresh_capture = mocker.MagicMock()
        fresh_capture.image = Image.new("RGB", (1280, 800))
        fresh_capture.label = "fresh"
        fresh_capture.scale_x = 1.0; fresh_capture.scale_y = 1.0
        fresh_capture.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        fresh_capture.target_width = 1280; fresh_capture.target_height = 800
        fresh_capture.is_cursor_screen = True

        app._press_captures = [stale_capture]
        app._press_memory = "prior"
        app._press_cursor_pos = (100, 100)

        # Cursor moved 141px (sqrt(100²+100²)) — well past 50px threshold
        mocker.patch("app.get_cursor_position", return_value=(200, 200))
        capture_fn = mocker.patch("app.capture_all_screens", return_value=[fresh_capture])

        app._stt.stop_recording.return_value = "test transcript"
        fake_stream = mocker.MagicMock()
        fake_stream.text_deltas.return_value = iter([])
        fake_stream.final_result.return_value = mocker.MagicMock(
            spoken_text="ok", coordinate=None, element_label=None, screen_number=None,
        )
        app._ai.ask_stream.return_value.__enter__ = mocker.MagicMock(return_value=fake_stream)
        app._ai.ask_stream.return_value.__exit__ = mocker.MagicMock(return_value=False)

        cancel = threading.Event()
        app._pipeline_worker("EXCEL.EXE", "Sheet1", cancel)

        assert capture_fn.called, (
            "Expected re-capture when cursor moved >50px between press and release"
        )

    def test_default_ai_client_comes_from_factory(self, mocker):
        """When no ai_client passed, ClickyApp calls create_ai_client(MODEL_ID, ...)."""
        mock_factory = mocker.patch("app.create_ai_client")
        mock_factory.return_value = mocker.MagicMock(name="ai_client_returned")
        from app import ClickyApp
        clicky = ClickyApp(
            stt_client=mocker.MagicMock(),
            tts_client=mocker.MagicMock(),
            memory_store=mocker.MagicMock(),
            overlay_controller=mocker.MagicMock(),
            hotkey_instance=mocker.MagicMock(),
        )
        mock_factory.assert_called_once()
        kwargs = mock_factory.call_args.kwargs
        assert "model_id" in kwargs
        assert "api_key" in kwargs
        assert clicky._ai is mock_factory.return_value
