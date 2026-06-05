"""Unit tests for locator.py — two-stage grid-locator for pixel-pointing.

Adapted from Bitshank-2338/clicky-windows ai/universal_locator.py (MIT) to
Clicky's sync code style + coord conventions + AIClient context-manager
interface (returns _StreamingResponse, not async iterator).

Tests cover:
    TestDrawGrid          - grid annotation visual correctness
    TestParseCellNumber   - JSON / integer-scan reply parsing edge cases
    TestAskGridPick       - LLM round-trip with mocked AIClient
    TestLocateViaGrid     - end-to-end two-stage pipeline + coord transform
"""

from __future__ import annotations

import base64
import io
import pytest
from PIL import Image


# --- TestDrawGrid -------------------------------------------------------------

class TestDrawGrid:
    def test_returns_rgb_image_of_same_size(self):
        from locator import _draw_grid

        src = Image.new("RGB", (640, 480), color=(128, 128, 128))
        out = _draw_grid(src, cols=12, rows=8)
        assert out.size == (640, 480)
        assert out.mode == "RGB"

    def test_grid_overlay_changes_pixels(self):
        """Quick sanity: gridded image should have meaningfully more non-grey
        pixels than source (lines + labels visibly draw on top)."""
        from locator import _draw_grid
        import numpy as np

        src = Image.new("RGB", (240, 160), color=(128, 128, 128))
        out = _draw_grid(src, cols=12, rows=8)

        arr_src = np.array(src)
        arr_out = np.array(out)
        diff_pixels = (arr_src != arr_out).any(axis=2).sum()

        # Expect at least ~500 pixels different (grid lines + ~96 label boxes,
        # tiny image so labels overlap).
        assert diff_pixels > 500, f"only {diff_pixels} pixels changed — grid not drawn?"

    def test_grid_includes_red_pixels(self):
        """The grid colour is red (255, 0, 0). After alpha-compositing on a
        grey background, lines should produce pixels with R > G + B."""
        from locator import _draw_grid
        import numpy as np

        src = Image.new("RGB", (240, 160), color=(128, 128, 128))
        out = _draw_grid(src, cols=12, rows=8)
        arr = np.array(out)

        # Pixels where R is dominant (red lines + label backgrounds)
        red_dominant = (arr[:, :, 0] > arr[:, :, 1] + 30) & (arr[:, :, 0] > arr[:, :, 2] + 30)
        assert red_dominant.sum() > 100, "no red pixels in grid output"


# --- TestParseCellNumber ------------------------------------------------------

class TestParseCellNumber:
    def test_extracts_json_cell_answer(self):
        from locator import _parse_cell_number
        assert _parse_cell_number('{"cell": 42}', max_n=96) == 42

    def test_extracts_first_integer_when_no_json(self):
        from locator import _parse_cell_number
        assert _parse_cell_number(
            "I think cell 23 contains the save button.", max_n=96
        ) == 23

    def test_returns_zero_for_conceptual_question_marker(self):
        """Cell 0 = LLM signal that question is conceptual, no UI element to point at."""
        from locator import _parse_cell_number
        assert _parse_cell_number('{"cell": 0}', max_n=96) == 0
        assert _parse_cell_number("0", max_n=96) == 0

    def test_returns_none_when_no_valid_number(self):
        from locator import _parse_cell_number
        assert _parse_cell_number("I don't know", max_n=96) is None

    def test_skips_out_of_range_numbers(self):
        """If LLM rambles with '99999' (out of range), pick the next valid number."""
        from locator import _parse_cell_number
        assert _parse_cell_number("Maybe 99999 but actually 5", max_n=96) == 5

    def test_accepts_alternate_json_keys(self):
        from locator import _parse_cell_number
        assert _parse_cell_number('{"number": 7}', max_n=96) == 7
        assert _parse_cell_number('{"answer": 12}', max_n=96) == 12

    def test_handles_json_with_extra_whitespace_and_text(self):
        from locator import _parse_cell_number
        assert _parse_cell_number(
            '  Sure! Here is my answer:  \n\n{"cell": 33}\n\nDone.',
            max_n=96,
        ) == 33

    def test_handles_malformed_json_gracefully(self):
        """Malformed JSON falls through to integer-scan, picks first valid int."""
        from locator import _parse_cell_number
        assert _parse_cell_number('{"cell": twentythree}', max_n=96) is None
        assert _parse_cell_number('{cell: 11}', max_n=96) == 11  # falls through to int scan


# --- TestAskGridPick ----------------------------------------------------------

class TestAskGridPick:
    """Tests _ask_grid_pick with a mock AIClient."""

    def _fake_client_yielding(self, mocker, reply_text: str):
        """Build a mock AIClient whose ask_stream context manager yields reply_text."""
        mock_stream = mocker.MagicMock()
        mock_stream.__enter__ = mocker.MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = mocker.MagicMock(return_value=None)
        mock_stream.text_deltas = mocker.MagicMock(return_value=iter([reply_text]))

        mock_client = mocker.MagicMock()
        mock_client.ask_stream = mocker.MagicMock(return_value=mock_stream)
        return mock_client

    def test_returns_parsed_cell_number(self, mocker):
        from locator import _ask_grid_pick, _img_to_jpeg_b64

        mock_client = self._fake_client_yielding(mocker, '{"cell": 42}')
        img = Image.new("RGB", (200, 100), color="white")
        img_b64 = _img_to_jpeg_b64(img)

        result = _ask_grid_pick(mock_client, img_b64, "where is save", max_n=96)
        assert result == 42

    def test_returns_zero_for_conceptual_reply(self, mocker):
        from locator import _ask_grid_pick, _img_to_jpeg_b64

        mock_client = self._fake_client_yielding(mocker, '{"cell": 0}')
        img = Image.new("RGB", (200, 100))
        img_b64 = _img_to_jpeg_b64(img)

        result = _ask_grid_pick(mock_client, img_b64, "what is html", max_n=96)
        assert result == 0

    def test_returns_none_on_unparseable_reply(self, mocker):
        from locator import _ask_grid_pick, _img_to_jpeg_b64

        mock_client = self._fake_client_yielding(mocker, "I don't know")
        img = Image.new("RGB", (200, 100))
        img_b64 = _img_to_jpeg_b64(img)

        result = _ask_grid_pick(mock_client, img_b64, "?", max_n=96)
        assert result is None

    def test_returns_none_on_llm_exception(self, mocker):
        """If the LLM call raises (network error, etc.), grid-pick returns None gracefully."""
        from locator import _ask_grid_pick, _img_to_jpeg_b64

        mock_client = mocker.MagicMock()
        mock_client.ask_stream = mocker.MagicMock(side_effect=RuntimeError("network down"))
        img = Image.new("RGB", (200, 100))
        img_b64 = _img_to_jpeg_b64(img)

        result = _ask_grid_pick(mock_client, img_b64, "?", max_n=96)
        assert result is None

    def test_logs_distinct_message_on_llm_transport_failure(self, mocker):
        """Codex MED fix: when LLM call raises (Ollama timeout, model not
        pulled, network error), _ask_grid_pick must emit a distinct
        debug-log message identifying the FAILURE TYPE — not just silently
        return None. Without distinguishing transport failures from
        'model said no UI element', operators can't tell whether to debug
        Ollama or rephrase the question."""
        from locator import _ask_grid_pick, _img_to_jpeg_b64

        mock_client = mocker.MagicMock()
        mock_client.ask_stream = mocker.MagicMock(
            side_effect=ConnectionError("Ollama unreachable")
        )
        img = Image.new("RGB", (200, 100))
        img_b64 = _img_to_jpeg_b64(img)

        captured_logs: list[str] = []
        result = _ask_grid_pick(
            mock_client, img_b64, "where is x", max_n=96,
            debug_log=captured_logs.append,
        )
        assert result is None
        # Log message must clearly identify this as a transport / LLM failure,
        # NOT a cell-0 / unparseable case.
        joined = " ".join(captured_logs).lower()
        assert "grid-locator" in joined
        assert "llm call failed" in joined or "transport" in joined
        assert "ConnectionError" in " ".join(captured_logs) or "ollama unreachable" in joined.lower()

    def test_logs_distinct_message_on_unparseable_reply(self, mocker):
        """Codex MED fix sibling: when LLM responds but reply is unparseable
        (no JSON, no integer 1-96), log a DIFFERENT distinct message — so
        operator can distinguish 'model rambled' from 'model uncertain
        (cell 0)' from 'transport failure'."""
        from locator import _ask_grid_pick, _img_to_jpeg_b64

        mock_stream = mocker.MagicMock()
        mock_stream.__enter__ = mocker.MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = mocker.MagicMock(return_value=None)
        mock_stream.text_deltas = mocker.MagicMock(
            return_value=iter(["sorry I cannot tell"])
        )
        mock_client = mocker.MagicMock()
        mock_client.ask_stream = mocker.MagicMock(return_value=mock_stream)

        img = Image.new("RGB", (200, 100))
        img_b64 = _img_to_jpeg_b64(img)

        captured_logs: list[str] = []
        result = _ask_grid_pick(
            mock_client, img_b64, "where is x", max_n=96,
            debug_log=captured_logs.append,
        )
        assert result is None
        joined = " ".join(captured_logs).lower()
        assert "grid-locator" in joined
        # Distinct from transport failure: mentions parse / unparseable
        assert "unparseable" in joined


# --- TestLocateViaGrid --------------------------------------------------------

class TestLocateViaGrid:
    """End-to-end two-stage grid-locator with mocked AIClient."""

    def _build_jpeg_b64(self, width: int, height: int) -> str:
        img = Image.new("RGB", (width, height), color=(50, 50, 50))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def _mock_client_replies(self, mocker, replies: list[str]):
        """Mock AIClient that yields a different reply each time ask_stream is called.

        replies[0] → response to Stage 1 grid-pick
        replies[1] → response to Stage 2 grid-pick
        etc.
        """
        call_idx = {"i": 0}

        def make_stream(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            reply = replies[idx] if idx < len(replies) else replies[-1]

            mock_stream = mocker.MagicMock()
            mock_stream.__enter__ = mocker.MagicMock(return_value=mock_stream)
            mock_stream.__exit__ = mocker.MagicMock(return_value=None)
            mock_stream.text_deltas = mocker.MagicMock(return_value=iter([reply]))
            return mock_stream

        mock_client = mocker.MagicMock()
        mock_client.ask_stream = mocker.MagicMock(side_effect=make_stream)
        return mock_client

    def test_returns_coords_when_both_stages_succeed(self, mocker):
        """Stage 1 picks cell 42, Stage 2 picks sub-cell 18 → returns coords."""
        from locator import locate_via_grid

        mock_client = self._mock_client_replies(
            mocker, ['{"cell": 42}', '{"cell": 18}']
        )

        jpeg_b64 = self._build_jpeg_b64(1280, 800)

        result = locate_via_grid(
            llm_client=mock_client,
            screenshot_jpeg_b64=jpeg_b64,
            original_size=(1280, 800),
            physical_size=(1280, 800),
            physical_origin=(0, 0),
            dpi_scale=1.0,
            query="Where is the save button?",
        )

        assert result is not None
        x, y = result
        assert isinstance(x, int) and isinstance(y, int)
        # Cell 42 in a 12x8 grid is at row 3 (0-indexed), col 5.
        # Centre roughly (5.5/12 * 1280, 3.5/8 * 800) = (587, 350) ish
        # Sub-cell 18 in 6x6 zoomed crop refines from there.
        # Sanity check: result is inside screen bounds.
        assert 0 < x < 1280
        assert 0 < y < 800

    def test_returns_none_when_stage1_says_cell_zero(self, mocker):
        """If LLM responds {"cell": 0}, the question is conceptual — return None."""
        from locator import locate_via_grid

        mock_client = self._mock_client_replies(mocker, ['{"cell": 0}'])
        jpeg_b64 = self._build_jpeg_b64(640, 480)

        result = locate_via_grid(
            llm_client=mock_client,
            screenshot_jpeg_b64=jpeg_b64,
            original_size=(640, 480),
            physical_size=(640, 480),
            query="What is the meaning of life?",
        )
        assert result is None

    def test_returns_none_when_stage1_unparseable(self, mocker):
        """If LLM reply has no parseable number, return None."""
        from locator import locate_via_grid

        mock_client = self._mock_client_replies(mocker, ["I don't know"])
        jpeg_b64 = self._build_jpeg_b64(640, 480)

        result = locate_via_grid(
            llm_client=mock_client,
            screenshot_jpeg_b64=jpeg_b64,
            original_size=(640, 480),
            physical_size=(640, 480),
            query="anything",
        )
        assert result is None

    def test_falls_back_to_stage1_centre_when_stage2_fails(self, mocker):
        """If Stage 1 picks a cell but Stage 2 fails, use Stage-1 cell centre."""
        from locator import locate_via_grid

        # Stage 1 picks cell 42; Stage 2 returns garbage → fall back to S1 centre
        mock_client = self._mock_client_replies(
            mocker, ['{"cell": 42}', "no idea"]
        )
        jpeg_b64 = self._build_jpeg_b64(1280, 800)

        result = locate_via_grid(
            llm_client=mock_client,
            screenshot_jpeg_b64=jpeg_b64,
            original_size=(1280, 800),
            physical_size=(1280, 800),
            query="where",
        )
        # Should still return coords (Stage-1 centre fallback)
        assert result is not None
        x, y = result
        assert 0 < x < 1280
        assert 0 < y < 800

    def test_coord_transform_handles_dpi_scale(self, mocker):
        """At 200% DPI, logical Qt coords should be half of physical coords."""
        from locator import locate_via_grid

        mock_client = self._mock_client_replies(
            mocker, ['{"cell": 42}', '{"cell": 18}']
        )
        # 1280x800 JPEG; 2560x1600 physical monitor at 200% DPI → 1280x800 logical
        jpeg_b64 = self._build_jpeg_b64(1280, 800)

        result_1x = locate_via_grid(
            llm_client=self._mock_client_replies(mocker, ['{"cell": 42}', '{"cell": 18}']),
            screenshot_jpeg_b64=jpeg_b64,
            original_size=(1280, 800),
            physical_size=(1280, 800),
            physical_origin=(0, 0),
            dpi_scale=1.0,
            query="x",
        )
        result_2x = locate_via_grid(
            llm_client=self._mock_client_replies(mocker, ['{"cell": 42}', '{"cell": 18}']),
            screenshot_jpeg_b64=jpeg_b64,
            original_size=(1280, 800),
            physical_size=(2560, 1600),  # 2x physical
            physical_origin=(0, 0),
            dpi_scale=2.0,
            query="x",
        )
        # At 2x DPI, physical coords are 2x but logical = physical/dpi = same as 1x.
        # Within rounding.
        assert abs(result_2x[0] - result_1x[0]) <= 1
        assert abs(result_2x[1] - result_1x[1]) <= 1

    def test_coord_transform_handles_secondary_monitor_origin(self, mocker):
        """Secondary monitor at physical origin (1920, 0) — coords should
        include that offset (since QCursor.pos() is in virtual-desktop space)."""
        from locator import locate_via_grid

        mock_client = self._mock_client_replies(
            mocker, ['{"cell": 42}', '{"cell": 18}']
        )
        jpeg_b64 = self._build_jpeg_b64(1280, 800)

        result = locate_via_grid(
            llm_client=mock_client,
            screenshot_jpeg_b64=jpeg_b64,
            original_size=(1280, 800),
            physical_size=(1280, 800),
            physical_origin=(1920, 0),  # secondary monitor right of primary
            dpi_scale=1.0,
            query="x",
        )
        # Result x must be > 1920 (somewhere on the secondary monitor)
        assert result is not None
        x, y = result
        assert x > 1920
        assert 0 < y < 800
