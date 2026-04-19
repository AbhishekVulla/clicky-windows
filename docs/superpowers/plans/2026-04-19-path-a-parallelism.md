# Path A Parallelism — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut Clicky Windows end-to-end PTT latency from ~4.9s → ~1.6-1.9s by parallelizing press-time work, streaming TTS sentence-by-sentence, caching the Claude prompt prefix, and porting Clicky macOS's shipping visual state machine — while fixing two correctness bugs (STT cutoff, TTS-to-mic feedback loop) and adding a statistical measurement harness to prove the wins.

**Architecture:** One commit per task, each TDD (write failing test → implement → verify green → commit). 12 tasks grouped from the 8-item Path A scope in ROADMAP.md. Execution order sequences correctness fixes (low-risk, de-risk later items) → isolated module changes → cross-module state-machine wiring → verification harness. Every new public method has a test; every existing test must stay green (current: 138/138).

**Tech Stack:** Python 3.13, PyQt6, mss, pynput, sounddevice, numpy, assemblyai SDK, cartesia SDK, anthropic SDK, openai SDK, Pillow, pytest, pytest-mock, scipy (NEW — for Mann-Whitney U + bootstrap CI).

**Source of truth for the spec:** `ROADMAP.md` → "Step 2 (Path A parallelism)" section. This plan converts that spec into executable steps.

**Load-bearing constraints (never break):**
- 138/138 pytest tests stay green; new tests for new code
- Claude coordinate precision MUST NOT regress (Gemini rejected on 230px miss)
- `pyqtSignal` is the ONLY thread-crossing mechanism (PyQt6 is not thread-safe)
- `overlay.hide_for_capture()` fires BEFORE every `mss.grab()` (feedback-loop prevention)
- Per-monitor overlays, never virtual-desktop-spanning
- Commits use conventional-commit style (`feat:` / `fix:` / `test:` / `refactor:` / `docs:`) with NO `Co-Authored-By: Claude` trailer (user wants solo attribution)
- Use existing DI factory patterns (`client_factory` / `audio_stream_factory` / `player_factory`) for mockability

---

## File Structure Map

| File | Responsibility | Change type |
|---|---|---|
| `stt.py` | AssemblyAI streaming + mic + RMS audio-level signal | Modified (Tasks 1, 2a, 7) |
| `tts.py` | Cartesia Sonic-3 streaming + sentence queue | Modified (Task 5) |
| `ai.py` | Claude/Gemini factory + prompt caching | Modified (Task 3) |
| `app.py` | Orchestrator + press/release handlers + pipeline worker | Modified (Tasks 2b, 4, 6, 10, 11) |
| `overlay.py` | Bezier arc flight + waveform widget | Modified (Tasks 8, 9) |
| `config.py` | New constants (`AUDIO_POWER_DECAY`, `LISTENING_CHIME_PATH`, etc.) | Modified (various) |
| `requirements.txt` | Add `scipy` | Modified (Task 12) |
| `tests/test_stt.py` | New tests for end_of_turn, grace period, RMS signal | Modified |
| `tests/test_tts.py` | New tests for sentence queue + sequential playback | Modified |
| `tests/test_ai.py` | New tests for prompt caching request-body shape | Modified |
| `tests/test_app.py` | New tests for press-time capture + state transitions | Modified |
| `tests/test_overlay.py` | New tests for bezier math + waveform widget + state | Modified |
| `tests/test_bench.py` | New file — Mann-Whitney U + bootstrap wrappers | Created (Task 12) |
| `tools/bench_path_a.py` | Measurement harness CLI | Created (Task 12) |

---

## Test Fixture Convention (IMPORTANT — read before writing any test)

Verified against the actual test files 2026-04-19:

**There are NO `app_with_mocks` / `stt_with_mocks` pytest fixtures.** The shipping pattern is **helper methods inside the test class**, using pytest-mock's `mocker` fixture.

### Canonical `tests/test_app.py` pattern (class `TestClickyApp`)

```python
class TestClickyApp:
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

    def test_something(self, mocker):
        app = self._make_app(mocker)
        # ... assertions
```

### Canonical `tests/test_stt.py` pattern (class `TestAssemblyAIStreamingSTT`)

```python
class TestAssemblyAIStreamingSTT:
    def _make_stt(self, **overrides):
        from stt import AssemblyAIStreamingSTT
        fake_client = MagicMock(name="StreamingClient")
        client_factory = MagicMock(name="client_factory", return_value=fake_client)
        fake_audio_stream = MagicMock(name="RawInputStream")
        captured: dict = {}
        def audio_stream_factory(callback, **kwargs):
            captured["callback"] = callback
            captured["kwargs"] = kwargs
            return fake_audio_stream
        audio_stream_factory_mock = MagicMock(
            name="audio_stream_factory", side_effect=audio_stream_factory
        )
        stt_obj = AssemblyAIStreamingSTT(
            api_key="test-key",
            client_factory=client_factory,
            audio_stream_factory=audio_stream_factory_mock,
            **overrides,
        )
        return stt_obj, fake_client, fake_audio_stream, client_factory, audio_stream_factory_mock

    def test_something(self):
        stt_obj, fake_client, fake_audio, _, _ = self._make_stt()
        # ... assertions
```

**When adapting the tests in Tasks 1, 2, 4, 5, 6, 7, 10, 11:** wrap each test function inside the appropriate class, add a `self` first parameter, and replace `app_with_mocks` / `stt_with_mocks` fixture references with `self._make_app(mocker)` / `self._make_stt()` calls. The **shape of each test** (what it asserts, the MagicMock patterns) stays as written — only the fixture boilerplate changes.

For existing `test_ai.py` / `test_tts.py` / `test_overlay.py`, check their existing patterns and follow them. Never introduce new pytest fixtures — the codebase convention is class-local `_make_XXX` helpers.

---

## Execution Order Rationale

1. **Task 1 (STT end_of_turn)** — 1 LOC correctness fix, unblocks reliable measurement in Task 12
2. **Task 2 (TTS-to-mic grace)** — 10 LOC correctness fix, eliminates debug-log noise during Task 12 runs
3. **Task 3 (Prompt caching)** — 30 LOC, isolated ai.py change, independent of everything else
4. **Task 4 (Capture-at-press)** — 40 LOC, app.py restructure, prerequisite for Task 10's state transitions
5. **Task 5 (TTS sentence queue)** — 60 LOC, self-contained tts.py change
6. **Task 6 (TTS sentence wire-up in app.py)** — 40 LOC, uses Task 5's queue API
7. **Task 7 (RMS signal in stt.py)** — 30 LOC, prerequisite for waveform (Task 9)
8. **Task 8 (Bezier arc in overlay.py)** — 80 LOC, isolated overlay upgrade
9. **Task 9 (Waveform widget in overlay.py)** — 60 LOC, uses Task 7's RMS signal
10. **Task 10 (State transitions in app.py)** — 20 LOC, wires Tasks 8 + 9 together
11. **Task 11 (Listening chime)** — 10 LOC, trivial addition
12. **Task 12 (Measurement harness)** — 100 LOC, verification pass at end to prove wins with stats

**Total: ~481 LOC across 12 commits.**

---

## Task 1: STT `end_of_turn` Fix

**Files:**
- Modify: `stt.py:429`
- Test: `tests/test_stt.py` (add 1 test)

**Context:** `_on_turn` currently triggers `_final_event.set()` only when `turn_is_formatted=True`. But `format_turns=False` (line 265) means the server never emits that flag. Result: stop_recording() times out at 2s waiting for a Turn that will never come, returns stale partial like "How do I—". AssemblyAI docs: `end_of_turn=True` is the only reliable completion signal.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_stt.py`, inside class `TestAssemblyAIStreamingSTT` (the existing class with `_make_stt` helper — see Test Fixture Convention above):

```python
    def test_on_turn_sets_final_event_on_end_of_turn_flag(self):
        """Regression: end_of_turn=True must set _final_event regardless of turn_is_formatted."""
        from types import SimpleNamespace

        stt_obj, fake_client, _, _, _ = self._make_stt()
        stt_obj.connect()
        stt_obj.start_recording()

        # Simulate an unformatted Turn with end_of_turn=True (format_turns=False mode)
        event = SimpleNamespace(
            transcript="How do I make my repo public?",
            turn_is_formatted=False,
            end_of_turn=True,
        )
        stt_obj._on_turn(fake_client, event)

        assert stt_obj._final_event.is_set(), (
            "Expected _final_event to be set on end_of_turn=True, "
            "but it was only triggering on turn_is_formatted=True."
        )
        assert stt_obj._final_transcript == "How do I make my repo public?"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
py -3.13 -m pytest tests/test_stt.py::TestAssemblyAIStreamingSTTLifecycle::test_on_turn_sets_final_event_on_end_of_turn_flag -v
```

Expected: FAIL. The existing `_on_turn` branches only on `turn_is_formatted`, so `_final_event` stays unset and the assertion fails.

- [ ] **Step 3: Implement the minimal fix**

Edit `stt.py:429`:

```python
# BEFORE:
        if is_formatted:

# AFTER:
        if getattr(event, "end_of_turn", False) or is_formatted:
```

The `or is_formatted` preserves back-compat for any existing tests that pass formatted-only events.

- [ ] **Step 4: Run full STT suite**

```bash
py -3.13 -m pytest tests/test_stt.py -v
```

Expected: all tests pass, including the new one.

- [ ] **Step 5: Run full project suite**

```bash
py -3.13 -m pytest -q
```

Expected: 139 passed (was 138, +1 for new test).

- [ ] **Step 6: Commit**

```bash
git add stt.py tests/test_stt.py
git commit -m "fix(stt): trigger _final_event on end_of_turn, not just turn_is_formatted

format_turns=False means the server never emits turn_is_formatted=True,
so stop_recording() timed out at 2s and returned stale partial transcripts
like 'How do I—'. The only reliable completion signal is end_of_turn=True.

Verified fix against debug log 2026-04-13_03-24-32_chrome.exe/interaction.log
(9-char 'How do I—' cutoff)."
```

---

## Task 2: TTS-to-Mic Grace Period (200ms)

**Files:**
- Modify: `stt.py` (add grace-period logic to `_on_audio_chunk` + a new `set_tts_grace_until` method)
- Modify: `app.py` (call `stt.set_tts_grace_until()` after every `tts.stop()`)
- Test: `tests/test_stt.py`, `tests/test_app.py`

**Context:** After `tts.stop()`, speaker output decay for ~100-200ms. Laptop mic picks it up and streams it to AssemblyAI → next PTT transcript contains phantom TTS text. Fix: after `tts.stop()`, block mic chunks from being forwarded for 200ms.

- [ ] **Step 1: Write the failing test in test_stt.py**

```python
def test_audio_chunks_discarded_during_tts_grace(self, stt_with_mocks, monkeypatch):
    """After set_tts_grace_until(t), _on_audio_chunk drops chunks until time.time() >= t."""
    import time as _t
    stt = stt_with_mocks
    stt.connect()
    stt.start_recording()

    # Before grace: chunks forwarded
    stt._on_audio_chunk(b"\x00" * 2048, 1024, None, None)
    assert stt._chunk_count == 1

    # Set grace window 200ms into the future
    stt.set_tts_grace_until(_t.time() + 0.200)

    # Chunk during grace window must be dropped
    stt._on_audio_chunk(b"\x00" * 2048, 1024, None, None)
    assert stt._chunk_count == 1, "Expected chunk dropped during TTS grace, but it was forwarded"

    # Fast-forward past grace
    monkeypatch.setattr(_t, "time", lambda: _t.time.__wrapped__() + 1.0 if hasattr(_t.time, "__wrapped__") else _t.time() + 1.0)
    # Simpler: just explicitly end the grace period
    stt._tts_grace_until = 0.0
    stt._on_audio_chunk(b"\x00" * 2048, 1024, None, None)
    assert stt._chunk_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
py -3.13 -m pytest tests/test_stt.py -k "tts_grace" -v
```

Expected: FAIL with `AttributeError: 'AssemblyAIStreamingSTT' object has no attribute 'set_tts_grace_until'`.

- [ ] **Step 3: Implement in stt.py**

Add to `AssemblyAIStreamingSTT.__init__` (around existing state init):

```python
        self._tts_grace_until: float = 0.0  # Epoch ts until which mic chunks are dropped
```

Add new public method (place after `disconnect` around line 379):

```python
    def set_tts_grace_until(self, epoch_ts: float) -> None:
        """Mic chunks before ``epoch_ts`` are discarded — used after ``tts.stop()``.

        Prevents TTS speaker decay from being transcribed as the next PTT's
        audio. Called by ``app.py`` immediately after any ``tts.stop()`` call.
        Thread-safe: a single float assignment.
        """
        self._tts_grace_until = epoch_ts
```

Modify `_on_audio_chunk` (line 405):

```python
    def _on_audio_chunk(self, indata, frames, time_info, status) -> None:
        """``sounddevice`` callback: forward raw PCM bytes to the WebSocket."""
        if not self._recording:
            return
        # TTS-to-mic feedback loop protection
        import time as _t
        if _t.time() < self._tts_grace_until:
            return
        if self._client is None:
            print("[stt] WARNING: _recording=True but _client is None — audio dropped", flush=True)
            return
        self._chunk_count += 1
        try:
            self._client.stream(bytes(indata))
        except Exception as exc:
            print(f"[stt] client.stream() FAILED: {exc}", flush=True)
```

- [ ] **Step 4: Wire into app.py**

Modify `_handle_press` in `app.py` (line 187). The `tts.stop()` call at line 192 needs a matching grace-period set on STT:

```python
    def _handle_press(self) -> None:
        """Hotkey pressed: kill TTS + start recording + capture foreground app."""
        import time
        _log("PRESS handler START")
        t0 = time.time()
        self._tts.stop()
        # TTS-to-mic grace: discard mic chunks for 200ms so speaker decay
        # doesn't leak into the next PTT's transcript.
        self._stt.set_tts_grace_until(time.time() + 0.200)
        # ... rest unchanged
```

Also modify `_handle_release` line 213 where `self._tts.stop()` is called on re-press:

```python
        if self._worker_thread and self._worker_thread.is_alive():
            _log("  cancelling previous worker + stopping TTS")
            self._cancel_event.set()
            self._tts.stop()
            self._stt.set_tts_grace_until(time.time() + 0.200)
```

(Import `time` is already present at top of `_handle_release`.)

- [ ] **Step 5: Add app.py test**

Add to `tests/test_app.py`:

```python
def test_press_handler_sets_tts_grace_period(app_with_mocks):
    """_handle_press must set 200ms STT grace window after tts.stop()."""
    import time
    app = app_with_mocks
    t_before = time.time()
    app._handle_press()

    # Verify stt.set_tts_grace_until was called with ~200ms in the future
    assert app._stt.set_tts_grace_until.called
    call_arg = app._stt.set_tts_grace_until.call_args.args[0]
    assert call_arg >= t_before + 0.199, f"Grace ts {call_arg} should be ~200ms after {t_before}"
    assert call_arg <= t_before + 0.250, "Grace ts should not be more than 250ms in future"
```

- [ ] **Step 6: Run tests**

```bash
py -3.13 -m pytest tests/test_stt.py tests/test_app.py -v
```

Expected: all pass.

- [ ] **Step 7: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 141 passed.

- [ ] **Step 8: Commit**

```bash
git add stt.py app.py tests/test_stt.py tests/test_app.py
git commit -m "fix(stt,app): add 200ms TTS-to-mic grace period after tts.stop()

Speaker decay after tts.stop() was being picked up by the laptop mic and
transcribed as the next PTT's input (verified: transcripts contained
phantom TTS phrases like 'one thing to watch—' that no one said).

Fix: new stt.set_tts_grace_until(epoch_ts); _on_audio_chunk drops chunks
while time.time() < grace. app.py press + release handlers call it with
time.time() + 0.200 right after every tts.stop()."
```

---

## Task 3: OpenRouter Prompt Caching (System Prompt + Memory Block)

**Files:**
- Modify: `ai.py` (`AnthropicClient.ask_stream` — add `cache_control` breakpoints)
- Test: `tests/test_ai.py`

**Context:** OpenRouter passes through Anthropic-native `cache_control: {"type": "ephemeral"}` on `anthropic/*` routes. Caching system prompt + memory block (the ~800 static tokens) gives ~50-100ms TTFT reduction per turn after the first, breaks even on cost after one cache hit within 5 min.

**Cache only the system prompt + memory block** (the `[context from past sessions...]` prefix of the user message). NEVER cache the current transcript or per-turn screenshots — per the academic literature, full-context caching can paradoxically increase latency.

- [ ] **Step 1: Read the current ask_stream to locate the system + user message shape**

Run to confirm current shape:
```bash
grep -n "system=" ai.py
grep -n "cache_control" ai.py
```

Expected: `system=` kwarg on messages.stream(), no existing cache_control references.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_ai.py`:

```python
def test_ask_stream_adds_cache_control_to_system_prompt(self):
    """AnthropicClient must tag the system prompt with cache_control: ephemeral
    when the base_url points at OpenRouter (so cache writes are cheap after first hit)."""
    from unittest.mock import MagicMock, patch

    mock_client = MagicMock()
    mock_stream = MagicMock()
    mock_client.messages.stream.return_value.__enter__.return_value = mock_stream

    client = AnthropicClient(
        api_key="test",
        model_id="anthropic/claude-sonnet-4-6",
        base_url="https://openrouter.ai/api/v1",
        client_factory=lambda **kw: mock_client,
    )

    with client.ask_stream(
        images=[],
        transcript="test question",
        history=[],
    ) as stream:
        pass

    call_kwargs = mock_client.messages.stream.call_args.kwargs

    # System prompt must be a list (block form) with cache_control on last block
    assert isinstance(call_kwargs["system"], list), (
        "Expected system= to be a list of content blocks (required for cache_control), "
        f"got {type(call_kwargs['system'])}"
    )
    assert call_kwargs["system"][-1].get("cache_control") == {"type": "ephemeral"}, (
        "Last system block must have cache_control: ephemeral"
    )


def test_ask_stream_adds_cache_control_to_memory_block(self):
    """When transcript contains the memory-context prefix, tag that text block
    with cache_control too (caches after first hit for the session)."""
    from unittest.mock import MagicMock

    mock_client = MagicMock()
    mock_stream = MagicMock()
    mock_client.messages.stream.return_value.__enter__.return_value = mock_stream

    client = AnthropicClient(
        api_key="test",
        model_id="anthropic/claude-sonnet-4-6",
        base_url="https://openrouter.ai/api/v1",
        client_factory=lambda **kw: mock_client,
    )

    transcript_with_memory = (
        "[context from past sessions — use silently, don't summarize or reference it:]\n"
        "User asked about freeze panes in Excel yesterday.\n\n"
        "how do I hide gridlines"
    )

    with client.ask_stream(
        images=[],
        transcript=transcript_with_memory,
        history=[],
    ) as stream:
        pass

    call_kwargs = mock_client.messages.stream.call_args.kwargs
    user_content = call_kwargs["messages"][-1]["content"]

    # Find the memory-context text block
    memory_block = next(
        (b for b in user_content if b["type"] == "text" and "context from past sessions" in b.get("text", "")),
        None,
    )
    assert memory_block is not None, "Memory-context block not found in user message"
    assert memory_block.get("cache_control") == {"type": "ephemeral"}, (
        "Memory-context block must have cache_control: ephemeral"
    )
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
py -3.13 -m pytest tests/test_ai.py -k "cache_control" -v
```

Expected: FAIL — `system=` is currently a plain string, not a list with cache_control.

- [ ] **Step 4: Implement in ai.py**

Verified against ai.py actual structure (read 2026-04-19):
- `ask_stream` at line 222 receives `system_prompt: str = _CLICKY_SYSTEM_PROMPT` and `transcript: str`
- Line 264: `content_blocks.append({"type": "text", "text": transcript})` — this is where memory-merged transcript becomes a single text block
- Line 271: `system=system_prompt` passed as plain string to `self.client.messages.stream(...)`

Modify `AnthropicClient.ask_stream` (line 222 onwards). After the image-content loop but BEFORE `content_blocks.append({"type": "text", "text": transcript})` (line 264), replace the simple transcript append with:

```python
        # Split transcript into memory-prefix (cached) + current-transcript (uncached).
        # app.py sends transcript as:
        #   "[context from past sessions — use silently...]\n<memory>\n\n<actual transcript>"
        # We cache the prefix so multi-turn sessions get ~50-100ms TTFT savings after
        # the first cache hit (5-min TTL). Images go AFTER text so image changes
        # per-turn don't invalidate the text cache.
        _MEMORY_PREFIX_MARKER = "[context from past sessions"
        if _MEMORY_PREFIX_MARKER in transcript:
            parts = transcript.split("\n\n", 1)
            if len(parts) == 2:
                memory_text, actual_transcript = parts
                content_blocks.append({
                    "type": "text",
                    "text": memory_text + "\n\n",
                    "cache_control": {"type": "ephemeral"},
                })
                content_blocks.append({"type": "text", "text": actual_transcript})
            else:
                content_blocks.append({"type": "text", "text": transcript})
        else:
            content_blocks.append({"type": "text", "text": transcript})
```

(Remove the existing `content_blocks.append({"type": "text", "text": transcript})` at line 264.)

Then replace the `system=system_prompt` kwarg at line 271 with a block-form list carrying cache_control:

```python
        # Cache the system prompt (largest static text, ~1500 chars).
        system_blocks = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        sdk_stream_mgr = self.client.messages.stream(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system_blocks,  # was: system=system_prompt
            messages=[*history, new_user_turn],
        )
```

No changes to `GeminiClient` — OpenRouter Gemini caching is implicit, no pass-through needed.

- [ ] **Step 5: Run tests**

```bash
py -3.13 -m pytest tests/test_ai.py -v
```

Expected: all pass, including the 2 new cache_control tests and the existing 13.

- [ ] **Step 6: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 143 passed.

- [ ] **Step 7: Commit**

```bash
git add ai.py tests/test_ai.py
git commit -m "feat(ai): add OpenRouter prompt caching on system prompt + memory block

Saves ~50-100ms TTFT per turn after first cache hit. OpenRouter passes
Anthropic-native cache_control: ephemeral through for anthropic/* routes
(verified in openrouter.ai/docs/guides/best-practices/prompt-caching).

Cached blocks:
- System prompt (_CLICKY_SYSTEM_PROMPT, ~1500 chars)
- Memory-context prefix of user message (~800-1500 chars per turn)

NOT cached (avoids full-context latency paradox per arxiv 2601.06007):
- Current transcript
- Screenshots (images always after text blocks to preserve text cache)

Breaks even on cost after 1 cache hit within 5-min TTL."
```

---

## Task 4: Capture-at-Press + Memory-at-Press

**Files:**
- Modify: `app.py` (`_handle_press`, `_handle_release`, `_pipeline_worker`)
- Test: `tests/test_app.py`

**Context:** Current pipeline does `capture_all_screens()` + `memory.recall()` on release (+500-800ms wall clock post-release). Shift to press: press-handler kicks off capture + memory in a background thread, stores result on self. Pipeline worker uses cached result on release, re-capturing only if cursor has moved >50px (simple precision safeguard).

- [ ] **Step 1: Write failing test for press-time capture**

Add to `tests/test_app.py`:

```python
def test_press_handler_kicks_off_capture_in_background(app_with_mocks):
    """_handle_press starts capture + memory recall in a background thread
    so the work overlaps with the user speaking."""
    import time
    app = app_with_mocks

    # Patch capture_all_screens to record when it's called
    with patch("app.capture_all_screens", return_value=[MagicMock()]) as mock_capture:
        t_press = time.time()
        app._handle_press()

        # Give background thread 100ms to run
        if app._capture_thread:
            app._capture_thread.join(timeout=0.5)

        assert mock_capture.called, "capture_all_screens should be invoked on press"
        assert app._press_captures is not None, "Captures should be stored on self"
        assert app._press_memory is not None, "Memory should be recalled on press"


def test_pipeline_worker_reuses_press_time_captures(app_with_mocks):
    """If cursor hasn't moved >50px since press, pipeline uses press-time captures."""
    app = app_with_mocks
    app._press_captures = [MagicMock(image=MagicMock())]
    app._press_memory = "test memory"
    app._press_cursor_pos = (100, 100)

    with patch("app.get_cursor_position", return_value=(120, 105)):  # moved ~22px
        with patch("app.capture_all_screens") as mock_capture:
            # Trigger the release path (stub _ai/_stt/_tts as needed)
            app._handle_release()
            if app._worker_thread:
                app._worker_thread.join(timeout=2.0)

            # Should NOT re-capture since cursor moved <50px
            assert not mock_capture.called, "Expected press-time captures reused"


def test_pipeline_worker_recaptures_on_large_cursor_move(app_with_mocks):
    """If cursor moved >50px since press, pipeline re-captures on release."""
    app = app_with_mocks
    app._press_captures = [MagicMock(image=MagicMock())]
    app._press_memory = "test memory"
    app._press_cursor_pos = (100, 100)

    with patch("app.get_cursor_position", return_value=(200, 200)):  # moved ~141px
        with patch("app.capture_all_screens", return_value=[MagicMock(image=MagicMock())]) as mock_capture:
            app._handle_release()
            if app._worker_thread:
                app._worker_thread.join(timeout=2.0)

            assert mock_capture.called, "Expected re-capture when cursor moved >50px"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
py -3.13 -m pytest tests/test_app.py -k "press" -v
```

Expected: FAIL — `_press_captures`, `_press_memory`, `_capture_thread` attributes don't exist.

- [ ] **Step 3: Modify ClickyApp state + press handler in app.py**

Add to `ClickyApp.__init__` after existing state:

```python
        # Press-time captures (shifted from release-time for latency)
        self._press_captures: list | None = None
        self._press_memory: str = ""
        self._press_cursor_pos: tuple[int, int] | None = None
        self._capture_thread: threading.Thread | None = None
```

Import `get_cursor_position` at top of app.py:
```python
from capture import capture_all_screens, get_cursor_position, set_dpi_awareness, unscale_claude_coords
```

Modify `_handle_press` (append after STT start_recording):

```python
    def _handle_press(self) -> None:
        """Hotkey pressed: kill TTS + start recording + capture (press-time)."""
        import time
        _log("PRESS handler START")
        t0 = time.time()
        self._tts.stop()
        self._stt.set_tts_grace_until(time.time() + 0.200)
        self._current_app, self._current_title = get_foreground_app()
        _log(f"  app: {self._current_app}")
        try:
            self._stt.start_recording()
        except RuntimeError as exc:
            _log(f"ERROR: STT start failed — {exc}")
            return

        # Kick off capture + memory in background so they overlap with the
        # user speaking. Saves ~250ms post-release wall clock.
        self._press_captures = None
        self._press_memory = ""
        self._press_cursor_pos = get_cursor_position()
        self._capture_thread = threading.Thread(
            target=self._press_time_capture,
            args=(self._current_app,),
            daemon=True,
            name="clicky-press-capture",
        )
        self._capture_thread.start()
        _log(f"  press-handler done in {(time.time()-t0)*1000:.0f}ms")

    def _press_time_capture(self, app_name: str) -> None:
        """Runs on background thread during user's utterance. Hides overlay,
        captures all screens, recalls memory. Results stored on self.
        overlay.hide_for_capture() MUST fire before mss.grab (invariant #3)."""
        try:
            self.sig_hide_overlay.emit()
            threading.Event().wait(0.05)
            captures = capture_all_screens()
            self.sig_show_overlay.emit()
            self._press_captures = captures
            self._press_memory = self._memory.recall(app_name)
        except Exception as exc:
            _log(f"ERROR: press-time capture failed — {exc}")
            self._press_captures = None  # fall through to release-time re-capture
```

- [ ] **Step 4: Modify `_pipeline_worker` to use press-time results**

Replace the capture + memory block in `_pipeline_worker` (lines 257-273 in current code):

```python
            # Check if press-time capture is usable (cursor not moved far)
            cursor_now = get_cursor_position()
            cursor_moved_px = 9999
            if self._press_cursor_pos is not None:
                dx = cursor_now[0] - self._press_cursor_pos[0]
                dy = cursor_now[1] - self._press_cursor_pos[1]
                cursor_moved_px = int((dx * dx + dy * dy) ** 0.5)

            if self._press_captures is not None and cursor_moved_px <= 50:
                dbg.log(f"CAPTURE: reusing press-time captures (cursor moved {cursor_moved_px}px)")
                captures = self._press_captures
                memory_context = self._press_memory
            else:
                dbg.log(f"CAPTURE: re-capturing (cursor moved {cursor_moved_px}px or no press-time capture)")
                self.sig_hide_overlay.emit()
                threading.Event().wait(0.05)
                captures = capture_all_screens()
                self.sig_show_overlay.emit()
                memory_context = self._memory.recall(app_name)

            dbg.log(f"CAPTURE: {len(captures)} screen(s)")
            for i, c in enumerate(captures):
                dbg.log(f"  screen[{i}]: {c.target_width}x{c.target_height}, "
                        f"scale=({c.scale_x:.2f}, {c.scale_y:.2f}), "
                        f"monitor={c.monitor}, cursor={c.is_cursor_screen}")
                dbg.save_screenshot(c.image, f"screenshot_{i}.jpg")
            dbg.log(f"MEMORY: recalled {len(memory_context)} chars for {app_name}")
```

- [ ] **Step 5: Run full app.py tests**

```bash
py -3.13 -m pytest tests/test_app.py -v
```

Expected: all pass (including the 3 new press-time tests).

- [ ] **Step 6: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 146 passed.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat(app): shift capture + memory-recall to press-time background thread

Saves ~250ms post-release wall-clock. Previous flow: release triggered
overlay.hide_for_capture() + 50ms wait + mss.grab() + PIL LANCZOS +
overlay.show() = 238ms observed in debug log 2026-04-19_06-18-30.

New flow: press handler spawns background thread running the full capture
+ memory pipeline while user is still speaking. Release uses cached result.

Re-capture safeguard: if cursor moved >50px between press and release
(user intentionally repositioned), fall back to release-time capture.

overlay.hide_for_capture() still fires before every mss.grab() (invariant #3
preserved). Memory recall also shifts to press."
```

---

## Task 5: TTS Sentence Queue (tts.py)

**Files:**
- Modify: `tts.py` (`CartesiaSonicTTS` — add queue + worker thread)
- Test: `tests/test_tts.py`

**Context:** Current `speak_sentence` just calls `speak` which cancels the previous thread. So consecutive `speak_sentence(s1)` + `speak_sentence(s2)` cancels s1 mid-playback. Fix: add `queue.Queue` + dedicated worker thread that consumes sentences sequentially.

- [ ] **Step 1: Write failing test for sequential playback**

Add to `tests/test_tts.py`:

```python
def test_speak_sentence_queues_sequentially(self):
    """Multiple speak_sentence calls play one after another, not cancelling each other."""
    import queue
    played = queue.Queue()

    def mock_player_factory(sample_rate):
        def play(samples):
            played.put(("played", len(samples)))
        return play, MagicMock()

    def mock_client_factory(api_key):
        def _iter_bytes_once(text):
            yield b"\x00" * 16  # 4 float32 samples
        resp = MagicMock()
        resp.iter_bytes.side_effect = lambda: _iter_bytes_once("")
        client = MagicMock()
        client.tts.generate.return_value = resp
        return client

    tts = CartesiaSonicTTS(
        api_key="test",
        client_factory=mock_client_factory,
        player_factory=mock_player_factory,
    )

    tts.speak_sentence("first sentence.")
    tts.speak_sentence("second sentence.")
    tts.speak_sentence("third sentence.")

    # Wait for queue to drain
    import time as _t
    for _ in range(50):
        if played.qsize() >= 3:
            break
        _t.sleep(0.05)

    assert played.qsize() >= 3, f"Expected 3 sentences played, got {played.qsize()}"


def test_stop_drains_queue(self):
    """stop() must abort current playback AND drain pending queued sentences."""
    tts = CartesiaSonicTTS(
        api_key="test",
        client_factory=lambda api_key: MagicMock(),
        player_factory=lambda sample_rate: (MagicMock(), MagicMock()),
    )
    tts.speak_sentence("a.")
    tts.speak_sentence("b.")
    tts.speak_sentence("c.")
    tts.stop()

    # Queue should be empty after stop
    assert tts._sentence_queue.empty(), "Expected queue drained after stop()"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
py -3.13 -m pytest tests/test_tts.py -k "queue or drain" -v
```

Expected: FAIL — `_sentence_queue` doesn't exist; `speak_sentence` still delegates to `speak` which cancels.

- [ ] **Step 3: Implement queue + worker thread in tts.py**

Add imports at top of tts.py:
```python
import queue
```

Sentinel for graceful worker shutdown:
```python
_SHUTDOWN_SENTINEL = object()
```

Modify `CartesiaSonicTTS.__init__` to add queue + worker:

```python
    def __init__(
        self,
        api_key: str,
        voice_id: str = CARTESIA_VOICE_ID,
        model_id: str = CARTESIA_MODEL_ID,
        sample_rate: int = CARTESIA_OUTPUT_SAMPLE_RATE,
        client_factory: Callable | None = None,
        player_factory: Callable | None = None,
    ) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.sample_rate = sample_rate
        self._client_factory = client_factory
        self._player_factory = player_factory
        self._cancel_event = threading.Event()
        self._current_thread: threading.Thread | None = None
        self._active_response = None
        self._active_audio_stream = None

        # Sentence-level sequential playback queue
        self._sentence_queue: queue.Queue = queue.Queue()
        self._queue_worker_thread = threading.Thread(
            target=self._queue_worker,
            name="CartesiaSonicTTS-queue-worker",
            daemon=True,
        )
        self._queue_worker_thread.start()
```

Replace `speak_sentence`:

```python
    def speak_sentence(self, sentence: str) -> None:
        """Queue a sentence for sequential TTS playback.

        Unlike speak(), this does NOT cancel previous playback. Sentences play
        back-to-back. Use this for streaming Claude response sentence-by-sentence
        while the LLM is still generating later sentences.

        Empty/whitespace text is a no-op. Thread-safe (queue.Queue is MT-safe).
        """
        if not sentence or not sentence.strip():
            return
        self._sentence_queue.put(sentence)
```

Add `_queue_worker`:

```python
    def _queue_worker(self) -> None:
        """Daemon thread: pull sentences from queue, play each to completion.

        Runs for the lifetime of the process. Receives _SHUTDOWN_SENTINEL to exit
        (not currently sent — relies on daemon=True for process teardown).
        """
        while True:
            sentence = self._sentence_queue.get()
            if sentence is _SHUTDOWN_SENTINEL:
                return
            try:
                # Per-sentence cancel event so stop() can kill the currently-playing one
                cancel = threading.Event()
                self._cancel_event = cancel
                self._do_speak(sentence, cancel)
            except Exception as exc:
                print(f"[tts] queue worker: sentence failed — {exc}", flush=True)
            finally:
                self._sentence_queue.task_done()
```

Modify `stop` to drain the queue AND abort current:

```python
    def stop(self) -> None:
        """Kill audio playback INSTANTLY + drain pending queued sentences."""
        # Drain queue first so the worker doesn't pick up a new sentence after abort
        while not self._sentence_queue.empty():
            try:
                self._sentence_queue.get_nowait()
                self._sentence_queue.task_done()
            except queue.Empty:
                break
        # Abort current playback
        self._cancel_event.set()
        stream = self._active_audio_stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
        resp = self._active_response
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
```

Keep `speak()` unchanged for backward compat with old call sites (will be removed in Task 6's app.py wire-up, but retain for the __main__ gate in tts.py).

- [ ] **Step 4: Run tests**

```bash
py -3.13 -m pytest tests/test_tts.py -v
```

Expected: all pass including the 2 new queue tests.

- [ ] **Step 5: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 148 passed.

- [ ] **Step 6: Commit**

```bash
git add tts.py tests/test_tts.py
git commit -m "feat(tts): add sentence-level queue for sequential playback

CartesiaSonicTTS.speak_sentence() used to delegate to speak() which
cancels the previous thread — so consecutive sentence calls cancelled
each other mid-playback.

New behavior: speak_sentence() puts to queue.Queue; a daemon worker thread
pulls + plays each sentence to completion. Unblocks Task 6 (app.py wire-up
to flush_sentences on .!? boundaries during Claude streaming).

stop() now drains the queue + aborts the current playback so PTT re-press
kills everything cleanly."
```

---

## Task 6: Sentence Streaming in app.py Pipeline

**Files:**
- Modify: `app.py` (`_pipeline_worker` — replace batch `tts.speak(full_response)` with streaming sentence flush)
- Test: `tests/test_app.py`

**Context:** Current pipeline accumulates all Claude tokens, parses `[POINT]` from the final result, calls `tts.speak(full_response)` once. The refactor: during streaming, flush complete sentences to `tts.speak_sentence` as they form — BUT don't flush past `[` because that's the start of the POINT tag. On stream close, use `result.spoken_text` (tag-stripped) to flush the remaining tail.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_pipeline_streams_sentences_during_claude_generation(app_with_mocks):
    """Pipeline must call tts.speak_sentence as each .!? boundary is hit,
    not batch-call tts.speak() at the end."""
    app = app_with_mocks

    # Mock ai.ask_stream to yield deltas word-by-word building to 3 sentences
    def fake_deltas():
        yield "you "
        yield "want "
        yield "the "
        yield "settings tab. "
        yield "scroll down "
        yield "to the bottom. "
        yield "click 'change visibility'. "
        yield "[POINT:721,215:settings tab]"

    mock_stream = MagicMock()
    mock_stream.text_deltas.return_value = iter(fake_deltas())
    mock_stream.final_result.return_value = MagicMock(
        spoken_text="you want the settings tab. scroll down to the bottom. click 'change visibility'.",
        coordinate=(721, 215),
        element_label="settings tab",
        screen_number=None,
    )
    app._ai.ask_stream.return_value.__enter__.return_value = mock_stream

    # Run pipeline worker synchronously
    app._press_captures = [MagicMock(image=MagicMock(), label="test", scale_x=1.0, scale_y=1.0,
                                     monitor={"left": 0, "top": 0}, target_width=100, target_height=100,
                                     is_cursor_screen=True)]
    app._press_memory = ""
    app._press_cursor_pos = (100, 100)
    cancel = threading.Event()

    with patch("app.get_cursor_position", return_value=(100, 100)):
        app._pipeline_worker("TEST.EXE", "", cancel)

    # Verify tts.speak_sentence was called for each complete sentence before stream close
    speak_sentence_calls = [c.args[0] for c in app._tts.speak_sentence.call_args_list]
    assert any("settings tab." in s for s in speak_sentence_calls), (
        "First sentence should have been flushed during streaming"
    )
    assert any("to the bottom." in s for s in speak_sentence_calls), (
        "Second sentence should have been flushed during streaming"
    )

    # Verify tts.speak was NOT called (the old batch path must be gone)
    assert not app._tts.speak.called, "Batch tts.speak() must be replaced with speak_sentence"
```

- [ ] **Step 2: Run to verify failure**

```bash
py -3.13 -m pytest tests/test_app.py -k "streams_sentences" -v
```

Expected: FAIL — `tts.speak()` is still the only call.

- [ ] **Step 3: Implement in app.py**

Replace the Claude streaming + TTS block in `_pipeline_worker` (currently around lines 289-312):

```python
            dbg.log("CLAUDE: streaming started...")
            _log("Asking Claude...")

            # Sentence-level TTS streaming. Flush complete sentences as they form,
            # but don't flush past '[' (start of POINT tag). On stream close, use
            # result.spoken_text (tag-stripped) to flush the tail.
            sentence_buffer = ""
            tag_started = False
            already_flushed_chars = 0

            with self._ai.ask_stream(
                images=images,
                transcript=user_text,
                history=self._history,
            ) as stream:
                for delta in stream.text_deltas():
                    if cancel.is_set():
                        return
                    sentence_buffer += delta
                    if "[" in sentence_buffer:
                        tag_started = True

                    if not tag_started:
                        sentences, sentence_buffer = flush_sentences(sentence_buffer)
                        for s in sentences:
                            if cancel.is_set():
                                return
                            self._tts.speak_sentence(s)
                            already_flushed_chars += len(s) + 1  # +1 for the space after

                result = stream.final_result()

            if cancel.is_set():
                return

            dbg.log(f"CLAUDE: done ({len(result.spoken_text)} chars)")
            dbg.log(f"CLAUDE: spoken_text: {result.spoken_text!r}")
            dbg.log(f"CLAUDE: coordinate={result.coordinate}, label={result.element_label!r}, screen={result.screen_number}")

            # Flush the tail (everything in spoken_text that wasn't already flushed)
            remaining_spoken = result.spoken_text[already_flushed_chars:].strip()
            if remaining_spoken:
                dbg.log(f"TTS: flushing tail ({len(remaining_spoken)} chars)")
                self._tts.speak_sentence(remaining_spoken)

            if cancel.is_set():
                return
```

The rest of `_pipeline_worker` (coordinate routing, overlay, memory record, history append) stays as-is.

- [ ] **Step 4: Run app tests**

```bash
py -3.13 -m pytest tests/test_app.py -v
```

Expected: all pass. The old test expecting `tts.speak(full_response)` to be called needs updating — replace that assertion with the new `speak_sentence` pattern.

- [ ] **Step 5: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 149 passed.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat(app): stream sentences to TTS during Claude generation

Saves ~2000ms perceived latency. Previous pipeline collected Claude's full
response (~3.7s via OpenRouter) then called tts.speak(full_response) —
first audible word at ~3.7s + 250ms TTFB = ~3.95s.

New pipeline flushes complete sentences to tts.speak_sentence() as each
.!? boundary is hit. First sentence typically arrives ~1200ms into Claude
stream — first audible word now ~1450ms post-release.

POINT-tag safety: stop flushing when '[' appears in buffer. On stream
close, use PointParseResult.spoken_text (tag-stripped) to compute + flush
the remaining tail. Prevents speaking '[POINT:640,400:settings tab]' aloud."
```

---

## Task 7: RMS Audio-Level Signal (stt.py)

**Files:**
- Modify: `stt.py` (add RMS computation in `_on_audio_chunk` + new `pyqtSignal`-equivalent callback)
- Modify: `config.py` (add `AUDIO_POWER_DECAY = 0.72`, `AUDIO_POWER_BOOST = 10.2`)
- Test: `tests/test_stt.py`

**Context:** The waveform widget (Task 9) needs mic audio levels. Compute RMS per chunk in stt.py's audio callback, apply boost + clamp + decay filter (matching Farza's `BuddyDictationManager.swift:687-721`), and expose via a callback. STT module doesn't import Qt, so the interface is a `Callable[[float], None]` — app.py wraps it in a `pyqtSignal` emission.

- [ ] **Step 1: Add config constants**

Edit `config.py` to add:

```python
# --- Audio power (RMS) filter constants (for waveform widget) -----------------
AUDIO_POWER_BOOST = 10.2
"""Multiplier applied to RMS before clamping to [0, 1]. Matches
Farza's BuddyDictationManager.swift:687-721."""

AUDIO_POWER_DECAY = 0.72
"""Exponential decay factor applied between chunks: new_level = max(raw, old * 0.72).
Ensures the UI meter never jumps DOWN too fast — smoother animation."""
```

- [ ] **Step 2: Write failing test**

Add to `tests/test_stt.py`:

```python
def test_on_audio_chunk_computes_rms_and_calls_level_callback(self, stt_with_mocks):
    """_on_audio_chunk must compute RMS per chunk + call registered level callback."""
    import struct
    stt = stt_with_mocks
    stt.connect()
    stt.start_recording()

    received = []
    stt.on_audio_level(lambda lvl: received.append(lvl))

    # Build a 1024-frame int16 PCM buffer with known amplitude
    samples = [int(0.5 * 32767)] * 1024  # 0.5 amplitude float-equivalent
    pcm_bytes = struct.pack("<" + "h" * 1024, *samples)
    stt._on_audio_chunk(pcm_bytes, 1024, None, None)

    assert len(received) == 1, f"Expected 1 level emission, got {len(received)}"
    assert 0.0 < received[0] <= 1.0, f"Level must be in [0, 1], got {received[0]}"


def test_audio_level_decay_filter_prevents_sudden_drops(self, stt_with_mocks):
    """Level must never drop faster than AUDIO_POWER_DECAY between chunks."""
    import struct
    stt = stt_with_mocks
    stt.connect()
    stt.start_recording()

    received = []
    stt.on_audio_level(lambda lvl: received.append(lvl))

    # Loud chunk then silent chunk
    loud = struct.pack("<" + "h" * 1024, *([int(0.8 * 32767)] * 1024))
    silent = b"\x00" * 2048

    stt._on_audio_chunk(loud, 1024, None, None)
    stt._on_audio_chunk(silent, 1024, None, None)

    # Second level should be >= first * AUDIO_POWER_DECAY (not zero)
    from config import AUDIO_POWER_DECAY
    assert received[1] >= received[0] * AUDIO_POWER_DECAY * 0.95, (
        f"Level dropped too fast: {received[0]} → {received[1]}, "
        f"expected floor of {received[0] * AUDIO_POWER_DECAY}"
    )
```

- [ ] **Step 3: Run to verify failure**

```bash
py -3.13 -m pytest tests/test_stt.py -k "audio_level or decay" -v
```

Expected: FAIL — `on_audio_level` method doesn't exist.

- [ ] **Step 4: Implement in stt.py**

Add to `AssemblyAIStreamingSTT.__init__`:

```python
        self._audio_level_cb: Callable[[float], None] | None = None
        self._last_audio_level: float = 0.0
```

Add public method (next to `on_partial_transcript`):

```python
    def on_audio_level(self, callback: Callable[[float], None]) -> None:
        """Register callback fired per audio chunk with RMS-derived level in [0, 1].

        Level is computed as sqrt(sum(s²) / N) × AUDIO_POWER_BOOST, clamped to [0, 1],
        with a decay filter (max of new raw level vs last_level × AUDIO_POWER_DECAY)
        that ensures the UI meter never drops suddenly between chunks.

        Callback runs on the sounddevice portaudio callback thread — must be fast
        and MUST NOT touch Qt APIs directly. app.py wraps it in pyqtSignal emission.
        """
        self._audio_level_cb = callback
```

Modify `_on_audio_chunk` to compute and emit RMS. Place RMS computation BEFORE the grace-period check (audio level is UI feedback, should update even during grace):

```python
    def _on_audio_chunk(self, indata, frames, time_info, status) -> None:
        """``sounddevice`` callback: forward raw PCM bytes + update audio level."""
        # Compute RMS for waveform widget (even during grace period — UI needs feedback)
        if self._audio_level_cb is not None and indata is not None:
            try:
                import numpy as _np
                from config import AUDIO_POWER_BOOST, AUDIO_POWER_DECAY
                # indata is bytes from RawInputStream; interpret as int16 little-endian
                samples = _np.frombuffer(bytes(indata), dtype=_np.int16).astype(_np.float32) / 32768.0
                if samples.size > 0:
                    rms = float(_np.sqrt(_np.mean(samples * samples)))
                    raw_level = min(max(rms * AUDIO_POWER_BOOST, 0.0), 1.0)
                    # Decay filter
                    smoothed = max(raw_level, self._last_audio_level * AUDIO_POWER_DECAY)
                    self._last_audio_level = smoothed
                    try:
                        self._audio_level_cb(smoothed)
                    except Exception:
                        pass  # Never crash the audio callback thread
            except Exception:
                pass

        if not self._recording:
            return
        import time as _t
        if _t.time() < self._tts_grace_until:
            return
        if self._client is None:
            print("[stt] WARNING: _recording=True but _client is None — audio dropped", flush=True)
            return
        self._chunk_count += 1
        try:
            self._client.stream(bytes(indata))
        except Exception as exc:
            print(f"[stt] client.stream() FAILED: {exc}", flush=True)
```

- [ ] **Step 5: Run tests**

```bash
py -3.13 -m pytest tests/test_stt.py -v
```

Expected: all pass including new ones.

- [ ] **Step 6: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 151 passed.

- [ ] **Step 7: Commit**

```bash
git add stt.py config.py tests/test_stt.py
git commit -m "feat(stt): compute RMS audio level per chunk + expose via on_audio_level callback

Prerequisite for the waveform widget (Task 9). Matches Farza's
BuddyDictationManager.swift:687-721 exactly:
- RMS = sqrt(sum(samples²) / N)
- Boosted by AUDIO_POWER_BOOST (10.2) and clamped to [0, 1]
- Decay filter: smoothed = max(raw, last × AUDIO_POWER_DECAY (0.72))

Callback runs on the sounddevice thread; app.py wraps it in a pyqtSignal
for thread-safe delivery to the overlay widget."
```

---

## Task 8: Quadratic Bezier Flight Arc (overlay.py)

**Files:**
- Modify: `overlay.py` (`OverlayWindow.animate_pointer_to` — replace linear QPropertyAnimation with frame-driven bezier arc)
- Test: `tests/test_overlay.py`

**Context:** Port `farzaa/clicky leanring-buddy/OverlayWindow.swift:491-568` verbatim with ONE deviation: no tangent rotation (user locked in Q2). Keep: quadratic bezier, smoothstep easing, distance-scaled duration, scale pulse.

- [ ] **Step 1: Write failing tests for bezier math**

Add to `tests/test_overlay.py`:

```python
def test_bezier_position_at_endpoints():
    """Bezier B(0)=P0, B(1)=P2 for any control point."""
    from overlay import _bezier_position

    # P0=(100, 100), control=(200, 50), P2=(300, 100)
    assert _bezier_position(0.0, (100, 100), (200, 50), (300, 100)) == (100.0, 100.0)
    assert _bezier_position(1.0, (100, 100), (200, 50), (300, 100)) == (300.0, 100.0)


def test_bezier_position_at_midpoint():
    """B(0.5) = 0.25·P0 + 0.5·P1 + 0.25·P2."""
    from overlay import _bezier_position
    x, y = _bezier_position(0.5, (0, 0), (100, 50), (200, 0))
    assert abs(x - 100.0) < 0.001
    # y = 0.25*0 + 0.5*50 + 0.25*0 = 25
    assert abs(y - 25.0) < 0.001


def test_smoothstep_at_boundaries():
    """smoothstep(0)=0, smoothstep(1)=1, smoothstep(0.5)=0.5."""
    from overlay import _smoothstep
    assert _smoothstep(0.0) == 0.0
    assert _smoothstep(1.0) == 1.0
    assert abs(_smoothstep(0.5) - 0.5) < 0.001


def test_flight_duration_scales_with_distance():
    """Duration = clamp(distance / 800 seconds, 0.6s, 1.4s)."""
    from overlay import _flight_duration_s
    assert _flight_duration_s(0) == 0.6  # min clamp
    assert _flight_duration_s(400) == 0.6  # 400/800 = 0.5, clamped to 0.6
    assert _flight_duration_s(800) == 1.0
    assert _flight_duration_s(2000) == 1.4  # max clamp


def test_scale_pulse_peaks_at_midpoint():
    """Scale pulse: 1.0 at t=0, 1.3 at t=0.5, 1.0 at t=1."""
    from overlay import _scale_pulse
    assert abs(_scale_pulse(0.0) - 1.0) < 0.001
    assert abs(_scale_pulse(0.5) - 1.3) < 0.001
    assert abs(_scale_pulse(1.0) - 1.0) < 0.001
```

- [ ] **Step 2: Run tests to verify failure**

```bash
py -3.13 -m pytest tests/test_overlay.py -k "bezier or smoothstep or flight_duration or scale_pulse" -v
```

Expected: FAIL — the helper functions don't exist yet.

- [ ] **Step 3: Implement the math helpers in overlay.py**

Add near the top of overlay.py (after imports, before the classes):

```python
import math

def _bezier_position(t: float, p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float]) -> tuple[float, float]:
    """Quadratic Bezier interpolation: B(t) = (1-t)²·P0 + 2(1-t)t·P1 + t²·P2."""
    one_minus = 1.0 - t
    x = one_minus * one_minus * p0[0] + 2.0 * one_minus * t * p1[0] + t * t * p2[0]
    y = one_minus * one_minus * p0[1] + 2.0 * one_minus * t * p1[1] + t * t * p2[1]
    return (x, y)


def _smoothstep(t: float) -> float:
    """Hermite smoothstep: 3t² - 2t³. Eases in and out for natural motion."""
    return t * t * (3.0 - 2.0 * t)


def _flight_duration_s(distance_px: float) -> float:
    """Distance-scaled duration: clamp(distance/800 s, 0.6s, 1.4s).
    Ported from OverlayWindow.swift:509."""
    return max(0.6, min(distance_px / 800.0, 1.4))


def _scale_pulse(linear_t: float) -> float:
    """Sine scale pulse: 1.0 → 1.3 at midpoint → 1.0.
    Ported from OverlayWindow.swift:567."""
    return 1.0 + math.sin(linear_t * math.pi) * 0.3
```

- [ ] **Step 4: Rewrite OverlayWindow.animate_pointer_to using QVariantAnimation**

Verified against overlay.py actual structure (read 2026-04-19):
- Current `animate_pointer_to` at line 335-347 uses `QPropertyAnimation` on `pointerPos` property (QPoint), 300ms `OutCubic` — I had the wrong duration/easing earlier.
- `_animation` at line 306 is `QPropertyAnimation(self, b"pointerPos")` — reused across point-and-return flights.
- `_animation.finished` signal is connected to callbacks in `OverlayController` (lines 487, 510) — the NEW bezier must preserve this signal pattern.
- `paintEvent` at line 310 reads `self._pointer_pos.x()` / `.y()` into `px, py` then builds polygon from `_CURSOR_VERTICES`.

Approach: replace `QPropertyAnimation` with `QVariantAnimation` driven by valueChanged(float 0.0→1.0). Compute bezier(t) inside the handler and assign `pointerPos`. `finished` signal still fires, so `OverlayController._on_point_animation_finished` works unchanged.

In `overlay.py`, modify `OverlayWindow.__init__` — after the existing `_animation = QPropertyAnimation(...)` block (line 306-308), ADD:

```python
        # Bezier flight state (Task 8 — replaces linear QPropertyAnimation for flights)
        from PyQt6.QtCore import QVariantAnimation
        self._flight_anim = QVariantAnimation(self)
        self._flight_anim.setStartValue(0.0)
        self._flight_anim.setEndValue(1.0)
        self._flight_anim.valueChanged.connect(self._on_flight_value)
        self._flight_p0: tuple[float, float] = (0.0, 0.0)
        self._flight_p1: tuple[float, float] = (0.0, 0.0)
        self._flight_p2: tuple[float, float] = (0.0, 0.0)
        self._flight_scale: float = 1.0
```

Replace the existing `animate_pointer_to` (lines 335-347) with:

```python
    def animate_pointer_to(self, local_logical_x: int, local_logical_y: int) -> None:
        """Fly the pointer along a quadratic Bezier arc to (x, y).

        Ports farzaa/clicky leanring-buddy/OverlayWindow.swift:491-568 with
        ONE deliberate deviation: no tangent rotation (our tip-polygon keeps
        tip pointing at target — see Task-2 clarification Q2).

        Curve: P0=current pointer, P1=midpoint lifted up by min(dist*0.2, 80px),
        P2=target. Duration = clamp(distance/800 s, 0.6s, 1.4s). Smoothstep
        eases progress before bezier interpolation. Scale pulse 1.0→1.3→1.0.
        """
        start_x, start_y = float(self._pointer_pos.x()), float(self._pointer_pos.y())
        end_x, end_y = float(local_logical_x), float(local_logical_y)
        dx, dy = end_x - start_x, end_y - start_y
        distance = math.hypot(dx, dy)

        mid_x = (start_x + end_x) / 2.0
        mid_y = (start_y + end_y) / 2.0
        arc_height = min(distance * 0.2, 80.0)

        self._flight_p0 = (start_x, start_y)
        self._flight_p1 = (mid_x, mid_y - arc_height)
        self._flight_p2 = (end_x, end_y)

        duration_ms = int(_flight_duration_s(distance) * 1000.0)

        self._flight_anim.stop()
        self._flight_anim.setDuration(duration_ms)
        self._flight_anim.setStartValue(0.0)
        self._flight_anim.setEndValue(1.0)
        self._pointer_visible = True
        self._flight_anim.start()

    def _on_flight_value(self, linear_t: float) -> None:
        """QVariantAnimation.valueChanged callback: compute bezier(t) + scale pulse."""
        eased_t = _smoothstep(float(linear_t))
        x, y = _bezier_position(
            eased_t, self._flight_p0, self._flight_p1, self._flight_p2
        )
        self._pointer_pos = QPoint(int(x), int(y))
        self._flight_scale = _scale_pulse(float(linear_t))
        self.update()
```

Update `paintEvent` (lines 310-333) to apply the scale pulse. Wrap the polygon draw with translate/scale around the tip:

```python
    def paintEvent(self, _event) -> None:
        """Draw a blue arrow cursor polygon at the current pointer position."""
        if not self._pointer_visible:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        px, py = self._pointer_pos.x(), self._pointer_pos.y()

        # Glow: semi-transparent blue circle behind the cursor
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(30, 144, 255, 35))
        painter.drawEllipse(QPointF(px + 5, py + 10), 22, 22)

        # Cursor polygon with mid-flight scale pulse around the tip
        if self._flight_scale != 1.0:
            painter.save()
            painter.translate(float(px), float(py))
            painter.scale(self._flight_scale, self._flight_scale)
            painter.translate(-float(px), -float(py))

        painter.setBrush(QColor(30, 144, 255, 200))
        painter.setPen(QPen(QColor(40, 40, 40, 100), 1))
        poly = QPolygonF([
            QPointF(px + dx, py + dy) for dx, dy in _CURSOR_VERTICES
        ])
        painter.drawPolygon(poly)

        if self._flight_scale != 1.0:
            painter.restore()
```

Also update `OverlayController.point_at` (line 487) to connect to the new animation's `finished`:

```python
        target_overlay._animation.finished.connect(self._on_point_animation_finished)
```

...must become:

```python
        target_overlay._flight_anim.finished.connect(self._on_point_animation_finished)
```

Apply the same `_animation` → `_flight_anim` rename in:
- `OverlayController._on_point_animation_finished` (line 492): `self._pointing_overlay._animation.finished.disconnect` → `._flight_anim.finished.disconnect`
- `OverlayController._fly_back` (line 510): `self._pointing_overlay._animation.finished.connect(self._on_return_finished)` → `._flight_anim.finished.connect`
- `OverlayController._on_return_finished` (line 516): `self._pointing_overlay._animation.finished.disconnect(self._on_return_finished)` → `._flight_anim.finished.disconnect`
- `OverlayController.hide_for_capture` (line 538): `self._pointing_overlay._animation.state() == QPropertyAnimation.State.Running:` → `self._pointing_overlay._flight_anim.state() == QVariantAnimation.State.Running:` + `.stop()` / `.finished.disconnect()` accordingly

Keep the old `_animation` (QPropertyAnimation on pointerPos) for now — it's unused after this refactor but tests may still reference it. Can be deleted in a cleanup commit. Actually, since test_overlay.py tests pointer animation paths via controller, rename cleanly: delete `_animation` and update test_overlay.py accordingly.

Import `QVariantAnimation` at top of overlay.py. Also add `import math` at top (not yet present).

- [ ] **Step 5: Run overlay tests**

```bash
py -3.13 -m pytest tests/test_overlay.py -v
```

Expected: all pass, including the 5 new math tests and existing 14.

- [ ] **Step 6: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 156 passed.

- [ ] **Step 7: Commit**

```bash
git add overlay.py tests/test_overlay.py
git commit -m "feat(overlay): port Clicky's quadratic bezier flight arc (no rotation)

Upgrades cursor animation from linear 400ms to distance-scaled bezier arc
+ smoothstep easing + scale pulse (1.0 → 1.3 mid-flight → 1.0).

Ports farzaa/clicky leanring-buddy/OverlayWindow.swift:491-568 verbatim,
with ONE deliberate deviation: no tangent rotation. Our cursor is a
tip-anchored polygon (commit a775c55 replaced ball with cursor polygon) —
the tip IS the pointer, so it stays pointing at the target throughout
flight instead of rotating along the tangent like Clicky's triangle does.

Helper functions (_bezier_position, _smoothstep, _flight_duration_s,
_scale_pulse) are pure math, independently tested."
```

---

## Task 9: Waveform Widget (overlay.py)

**Files:**
- Modify: `overlay.py` (add `WaveformWidget` class + show/hide methods on `OverlayWindow`)
- Test: `tests/test_overlay.py`

**Context:** Port `farzaa/clicky leanring-buddy/OverlayWindow.swift:705-743`. 5 vertical rounded-rectangle bars, heights driven by the RMS level from Task 7 × profile `[0.4, 0.7, 1.0, 0.7, 0.4]` + sine idle-pulse at 0.57Hz. 36fps (27ms QTimer tick). Replaces the cursor polygon during LISTENING (cursor opacity = 0).

- [ ] **Step 1: Write failing tests**

Add to `tests/test_overlay.py`:

```python
def test_waveform_bar_height_scales_with_audio_level():
    """Central bar (index 2) should be TALLER than edge bars (0, 4) for same audio level."""
    from overlay import _waveform_bar_height

    # At the same (audio_level, phase), profile [0.4, 0.7, 1.0, 0.7, 0.4]
    # means bar 2 peaks highest
    h0 = _waveform_bar_height(bar_index=0, audio_level=0.5, phase_seconds=0.0)
    h2 = _waveform_bar_height(bar_index=2, audio_level=0.5, phase_seconds=0.0)
    h4 = _waveform_bar_height(bar_index=4, audio_level=0.5, phase_seconds=0.0)

    assert h2 > h0, f"Center bar (h={h2}) should be taller than edge (h={h0})"
    assert h2 > h4, f"Center bar (h={h2}) should be taller than right edge (h={h4})"


def test_waveform_bar_has_min_height_at_silence():
    """With audio_level=0, bars still show a small idle pulse (never fully flat)."""
    from overlay import _waveform_bar_height
    h = _waveform_bar_height(bar_index=2, audio_level=0.0, phase_seconds=0.0)
    assert h >= 3.0, f"Idle bar height should be >= 3px (base + min idle pulse), got {h}"
    assert h <= 6.0, f"Idle bar at silence should be <= ~6px, got {h}"


def test_waveform_widget_updates_on_audio_level_signal():
    """WaveformWidget.set_audio_level stores the level and triggers repaint."""
    from PyQt6.QtWidgets import QApplication
    from overlay import WaveformWidget
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    w = WaveformWidget()
    w.set_audio_level(0.5)
    assert w._audio_level == 0.5
```

- [ ] **Step 2: Run tests to verify failure**

```bash
py -3.13 -m pytest tests/test_overlay.py -k "waveform" -v
```

Expected: FAIL — `WaveformWidget` and `_waveform_bar_height` don't exist.

- [ ] **Step 3: Implement the waveform math helper**

Add to `overlay.py` (with the other helpers):

```python
_WAVEFORM_BAR_PROFILE = [0.4, 0.7, 1.0, 0.7, 0.4]
_WAVEFORM_MAX_REACTIVE_HEIGHT = 10.0  # px
_WAVEFORM_BASE_HEIGHT = 3.0
_WAVEFORM_IDLE_PULSE_HZ = 0.57  # Hz (matches Farza's 3.6 rad/s)


def _waveform_bar_height(bar_index: int, audio_level: float, phase_seconds: float) -> float:
    """Bar height in px. Port of OverlayWindow.swift:728-740.

    height = 3 + reactive_component + idle_pulse
      reactive = ((audio_level - 0.008) × 2.85)^0.76 × 10 × profile[bar_index]
      idle_pulse = (sin(t × 3.6 + bar_index × 0.35) + 1) / 2 × 1.5
    """
    # Reactive component
    normalized_level = max(audio_level - 0.008, 0.0)
    eased = pow(min(normalized_level * 2.85, 1.0), 0.76)
    reactive = eased * _WAVEFORM_MAX_REACTIVE_HEIGHT * _WAVEFORM_BAR_PROFILE[bar_index]

    # Idle pulse (per-bar phase offset)
    animation_phase = phase_seconds * 3.6 + bar_index * 0.35
    idle_pulse = (math.sin(animation_phase) + 1.0) / 2.0 * 1.5

    return _WAVEFORM_BASE_HEIGHT + reactive + idle_pulse
```

- [ ] **Step 4: Implement WaveformWidget class**

Add to `overlay.py`:

```python
from PyQt6.QtCore import QTimer, Qt, QRectF
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtWidgets import QWidget


class WaveformWidget(QWidget):
    """5-bar audio-level visualization, ported from Clicky's BlueCursorWaveformView.

    Render loop runs at 36 fps via internal QTimer (matches Farza's 1/36s cadence).
    Bar heights driven by set_audio_level() + an independent idle-pulse sine wave.
    """

    BAR_COUNT = 5
    BAR_WIDTH = 2
    BAR_SPACING = 2
    UPDATE_INTERVAL_MS = 28  # ~36 fps
    WIDGET_WIDTH = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_SPACING  # 18px
    WIDGET_HEIGHT = 14  # matches cursor-polygon height visually

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.WIDGET_WIDTH, self.WIDGET_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._audio_level: float = 0.0
        self._phase_start = 0.0
        self._phase_now = 0.0
        import time as _t
        self._phase_start = _t.time()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.UPDATE_INTERVAL_MS)

    def set_audio_level(self, level: float) -> None:
        """Update the live audio level. Called from app.py via pyqtSignal."""
        self._audio_level = max(0.0, min(level, 1.0))

    def _tick(self) -> None:
        import time as _t
        self._phase_now = _t.time() - self._phase_start
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(64, 156, 255)  # DS.Colors.overlayCursorBlue equivalent
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)

        for i in range(self.BAR_COUNT):
            bar_h = _waveform_bar_height(i, self._audio_level, self._phase_now)
            x = i * (self.BAR_WIDTH + self.BAR_SPACING)
            y = (self.WIDGET_HEIGHT - bar_h) / 2.0
            painter.drawRoundedRect(
                QRectF(x, y, self.BAR_WIDTH, bar_h),
                1.5, 1.5,
            )
```

- [ ] **Step 5: Add show/hide methods to OverlayWindow**

Add to `OverlayWindow` class:

```python
    def show_waveform(self, logical_x: float, logical_y: float) -> None:
        """Show waveform at logical position, hide the cursor polygon."""
        if not hasattr(self, "_waveform_widget") or self._waveform_widget is None:
            self._waveform_widget = WaveformWidget(self)
        self._waveform_widget.move(
            int(logical_x - WaveformWidget.WIDGET_WIDTH / 2),
            int(logical_y - WaveformWidget.WIDGET_HEIGHT / 2),
        )
        self._waveform_widget.show()
        self._cursor_hidden_for_waveform = True
        self.update()

    def hide_waveform(self) -> None:
        """Hide waveform, restore cursor polygon."""
        if hasattr(self, "_waveform_widget") and self._waveform_widget is not None:
            self._waveform_widget.hide()
        self._cursor_hidden_for_waveform = False
        self.update()

    def set_audio_level(self, level: float) -> None:
        """Forward audio level to the waveform widget (no-op if not shown)."""
        if hasattr(self, "_waveform_widget") and self._waveform_widget is not None:
            self._waveform_widget.set_audio_level(level)
```

Modify `paintEvent` to skip drawing the cursor polygon when `_cursor_hidden_for_waveform=True`. Initialize `_cursor_hidden_for_waveform = False` and `_waveform_widget = None` in `__init__`.

Also add corresponding delegation methods on `OverlayController`:

```python
    def show_waveform(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        window = screen_for_monitor(self._screens, monitor)
        if window is not None:
            lx, ly = physical_to_local_logical(physical_x, physical_y, window.screen())
            window.show_waveform(lx, ly)

    def hide_waveform(self) -> None:
        for window in self._windows:
            window.hide_waveform()

    def set_audio_level(self, level: float) -> None:
        for window in self._windows:
            window.set_audio_level(level)
```

- [ ] **Step 6: Run overlay tests**

```bash
py -3.13 -m pytest tests/test_overlay.py -v
```

Expected: all pass.

- [ ] **Step 7: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 159 passed.

- [ ] **Step 8: Commit**

```bash
git add overlay.py tests/test_overlay.py
git commit -m "feat(overlay): add WaveformWidget for LISTENING state (replaces cursor)

Ports farzaa/clicky leanring-buddy/OverlayWindow.swift:705-743 verbatim.

- 5 rounded-rect bars, 2px wide, 2px spacing (18px total width)
- Heights: base 3px + reactive (RMS × profile[0.4,0.7,1.0,0.7,0.4]) + idle pulse
- 36 fps repaint (28ms QTimer)
- Idle pulse: sine at 0.57Hz with per-bar phase offset, never fully flat
- Bar color: DS.Colors.overlayCursorBlue equivalent (QColor(64, 156, 255))

During PTT hold: OverlayWindow.show_waveform() hides the cursor polygon
and shows the widget at the cursor position. On release: hide_waveform()
restores the cursor. State transitions wired in Task 10.

Helper _waveform_bar_height is pure math, independently tested."
```

---

## Task 10: State Transitions in app.py

**Files:**
- Modify: `app.py` (add signals + wire `show_waveform` / `hide_waveform` / `set_audio_level` + connect STT's `on_audio_level` callback)
- Test: `tests/test_app.py`

**Context:** Tasks 7 + 9 gave us RMS signal and waveform widget. Now wire the state machine:
- PRESS → `sig_listening_started` → OverlayController.show_waveform
- RELEASE → `sig_listening_stopped` → OverlayController.hide_waveform
- STT audio level → pyqtSignal → OverlayController.set_audio_level

Use get_cursor_position to pick the monitor for the waveform.

- [ ] **Step 1: Write failing test**

Add to `tests/test_app.py`:

```python
def test_press_shows_waveform_on_correct_monitor(app_with_mocks):
    app = app_with_mocks
    app._handle_press()
    # Overlay should be told to show waveform
    assert app._overlay.show_waveform.called
    # Check it was called with the cursor position
    call = app._overlay.show_waveform.call_args
    assert call is not None


def test_release_hides_waveform(app_with_mocks):
    app = app_with_mocks
    app._handle_press()
    app._handle_release()
    assert app._overlay.hide_waveform.called


def test_audio_level_signal_forwards_to_overlay(app_with_mocks):
    """STT level callback → Qt signal → overlay.set_audio_level."""
    app = app_with_mocks
    # Directly invoke the wrapper that stt.on_audio_level registers
    app.sig_audio_level.emit(0.42)
    # Qt processes signals synchronously in test mode
    assert app._overlay.set_audio_level.called
    assert app._overlay.set_audio_level.call_args.args[0] == 0.42
```

- [ ] **Step 2: Run tests to verify failure**

```bash
py -3.13 -m pytest tests/test_app.py -k "waveform or audio_level_signal" -v
```

Expected: FAIL.

- [ ] **Step 3: Add signals + handlers in app.py**

Add signal declarations in `ClickyApp`:

```python
    sig_audio_level = pyqtSignal(float)
    sig_show_waveform = pyqtSignal(int, int, dict)
    sig_hide_waveform = pyqtSignal()
```

In `__init__` (connect signals after existing connects):

```python
        self.sig_audio_level.connect(self._on_audio_level)
        self.sig_show_waveform.connect(self._on_show_waveform)
        self.sig_hide_waveform.connect(self._on_hide_waveform)
```

Add the slot handlers:

```python
    def _on_audio_level(self, level: float) -> None:
        if self._overlay:
            self._overlay.set_audio_level(level)

    def _on_show_waveform(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        if self._overlay:
            self._overlay.show_waveform(physical_x, physical_y, monitor)

    def _on_hide_waveform(self) -> None:
        if self._overlay:
            self._overlay.hide_waveform()
```

In `start()` (after hotkey.start), register STT level callback:

```python
        # Wire RMS audio level → Qt signal → overlay waveform
        self._stt.on_audio_level(lambda lvl: self.sig_audio_level.emit(lvl))
```

In `_handle_press`, after cursor pos is read:

```python
        # Show waveform at cursor position on the current screen
        cursor_x, cursor_y = self._press_cursor_pos
        from capture import list_monitors, monitor_containing
        mon = monitor_containing(cursor_x, cursor_y, list_monitors())
        if mon is not None:
            self.sig_show_waveform.emit(cursor_x, cursor_y, mon)
```

In `_handle_release`, at the top:

```python
        self.sig_hide_waveform.emit()
```

- [ ] **Step 4: Run app tests**

```bash
py -3.13 -m pytest tests/test_app.py -v
```

Expected: all pass.

- [ ] **Step 5: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 162 passed.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat(app): wire LISTENING state — show/hide waveform + forward audio levels

PRESS → emit sig_show_waveform(cursor_x, cursor_y, monitor) → overlay shows
waveform widget at cursor position, hides cursor polygon.

STT _on_audio_chunk computes RMS → fires on_audio_level callback (sounddevice
thread) → wrapped in sig_audio_level.emit(lvl) → Qt main thread slot calls
overlay.set_audio_level(lvl) → waveform redraws at ~36fps.

RELEASE → sig_hide_waveform → overlay restores cursor polygon, hides widget.

All thread crossings use pyqtSignal (invariant #9 preserved)."
```

---

## Task 11: Listening Chime on Hotkey PRESS

**Files:**
- Create: `assets/listening_chime.wav` (a short 50-80ms tone, user-provided OR generated from Task 11 step 2 script)
- Modify: `app.py` (`_handle_press` — play chime via async sounddevice.play)
- Modify: `config.py` (add `LISTENING_CHIME_PATH` constant)
- Test: `tests/test_app.py`

- [ ] **Step 1: Generate the chime WAV**

Create `tools/gen_chime.py`:

```python
"""Generate a 60ms soft chime for listening-cue. Run once; output committed."""
import numpy as np
import soundfile as sf
from pathlib import Path

SAMPLE_RATE = 44100
DURATION_S = 0.060
FREQ_HZ = 880  # A5

t = np.linspace(0, DURATION_S, int(SAMPLE_RATE * DURATION_S), endpoint=False)
envelope = np.exp(-t * 40)  # Fast decay
samples = np.sin(2 * np.pi * FREQ_HZ * t) * envelope * 0.3

out_path = Path(__file__).parent.parent / "assets" / "listening_chime.wav"
out_path.parent.mkdir(exist_ok=True)
sf.write(out_path, samples.astype(np.float32), SAMPLE_RATE, subtype="FLOAT")
print(f"Wrote {out_path} ({len(samples)} samples, {DURATION_S*1000:.0f}ms)")
```

Run once:
```bash
py -3.13 -m pip install soundfile  # if not installed
py -3.13 -m tools.gen_chime
```

Verify `assets/listening_chime.wav` exists and is ~10-20 KB.

- [ ] **Step 2: Add config constant**

Edit `config.py`:

```python
from pathlib import Path

LISTENING_CHIME_PATH = str(Path(__file__).parent / "assets" / "listening_chime.wav")
"""60ms chime played on hotkey PRESS — confirms mic is hot.
Non-blocking via sounddevice.play(). Zero pipeline latency impact."""
```

- [ ] **Step 3: Write failing test**

Add to `tests/test_app.py`:

```python
def test_press_plays_listening_chime(app_with_mocks, monkeypatch):
    """_handle_press must play the chime via non-blocking sounddevice.play()."""
    play_calls = []
    def fake_play(samples, sample_rate):
        play_calls.append((samples.shape if hasattr(samples, "shape") else len(samples), sample_rate))

    import app as app_module
    monkeypatch.setattr(app_module, "_play_chime_async", lambda: play_calls.append(("called",)))

    app_with_mocks._handle_press()
    assert len(play_calls) == 1, "Chime should be played exactly once per press"
```

- [ ] **Step 4: Run to verify fail**

```bash
py -3.13 -m pytest tests/test_app.py -k "chime" -v
```

Expected: FAIL — `_play_chime_async` doesn't exist.

- [ ] **Step 5: Implement in app.py**

Add module-level helper (near `_log`):

```python
def _play_chime_async() -> None:
    """Play the listening chime non-blocking. Fire-and-forget — errors swallowed."""
    try:
        import sounddevice as sd
        import soundfile as sf
        from config import LISTENING_CHIME_PATH
        samples, sr = sf.read(LISTENING_CHIME_PATH, dtype="float32")
        sd.play(samples, sr)  # Non-blocking
    except Exception as exc:
        # Chime is UX-only; silent fail if audio device / file unavailable
        pass
```

Modify `_handle_press` — add at the top, before `_tts.stop()`:

```python
        _play_chime_async()
```

Add `soundfile` to `requirements.txt` if not already there.

- [ ] **Step 6: Run tests**

```bash
py -3.13 -m pytest tests/test_app.py -v
```

Expected: all pass.

- [ ] **Step 7: Manual verification**

```bash
py -3.13 -m app
```

Press Ctrl+Alt+Space — expected: a soft "ping" is audible immediately on press, before you start speaking.

- [ ] **Step 8: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 163 passed.

- [ ] **Step 9: Commit**

```bash
git add assets/listening_chime.wav tools/gen_chime.py app.py config.py tests/test_app.py requirements.txt
git commit -m "feat(app): add listening chime on hotkey PRESS (async, 0ms latency)

60ms 880Hz soft tone with exponential decay. Played via sounddevice.play()
which returns immediately (non-blocking via portaudio ring buffer).

Zero pipeline latency impact — chime plays in parallel with STT
start_recording + capture kickoff. Standard UX pattern (Alexa/Siri chime
on hotkey press). Confirms to user: mic is hot, keep talking.

Chime is pre-generated at asset/listening_chime.wav via tools/gen_chime.py
(run once, output committed to repo)."
```

---

## Task 12: Measurement Harness

**Files:**
- Create: `tools/bench_path_a.py` (CLI harness with Mann-Whitney U + bootstrap CI)
- Create: `tests/test_bench.py` (unit tests for the stats wrappers)
- Modify: `requirements.txt` (add `scipy>=1.11`)

**Context:** Prove Path A wins with statistical rigor. N=20 runs before vs after, four per-run metrics (release→partial, release→final-transcript, release→Claude-first-token, release→first-audible-word). Mann-Whitney U tests the "post is stochastically less than pre" hypothesis; bootstrap CI on median gives interpretable error bars.

- [ ] **Step 1: Add scipy dep**

Edit `requirements.txt`:
```
scipy>=1.11
soundfile>=0.12
```

Install:
```bash
py -3.13 -m pip install -r requirements.txt
```

- [ ] **Step 2: Write failing tests for stats wrappers**

Create `tests/test_bench.py`:

```python
"""Unit tests for tools.bench_path_a stats wrappers."""
import numpy as np
import pytest


def test_mann_whitney_u_detects_lower_after_distribution():
    """When 'after' samples are stochastically smaller, p-value should be < 0.05."""
    from tools.bench_path_a import mann_whitney_less
    rng = np.random.default_rng(42)
    before = rng.gamma(shape=2.0, scale=500, size=20) + 2000  # median ~3000ms
    after = rng.gamma(shape=2.0, scale=500, size=20) + 500   # median ~1500ms
    stat, p = mann_whitney_less(before, after)
    assert p < 0.05, f"Expected p < 0.05 for clearly-lower 'after', got p={p}"


def test_bootstrap_median_ci_contains_true_median():
    """95% CI should contain the true median of the sample with high probability."""
    from tools.bench_path_a import bootstrap_median_ci
    rng = np.random.default_rng(42)
    samples = rng.gamma(shape=2.0, scale=500, size=20) + 1000
    lo, hi = bootstrap_median_ci(samples, confidence=0.95, n_resamples=2000)
    true_median = float(np.median(samples))
    assert lo <= true_median <= hi, f"CI [{lo}, {hi}] should contain sample median {true_median}"


def test_bench_metric_row_schema():
    """A single bench metric row must have (name, before_p50, after_p50, delta_ms, p_value, ci_lo, ci_hi)."""
    from tools.bench_path_a import MetricRow
    row = MetricRow(name="STT", before=[100, 110, 105], after=[50, 55, 52])
    summary = row.summary()
    assert "name" in summary
    assert "before_p50" in summary
    assert "after_p50" in summary
    assert "delta_ms" in summary
    assert "p_value" in summary
    assert "ci_lo" in summary
    assert "ci_hi" in summary
```

- [ ] **Step 3: Run to verify fail**

```bash
py -3.13 -m pytest tests/test_bench.py -v
```

Expected: FAIL — `tools.bench_path_a` doesn't exist.

- [ ] **Step 4: Implement tools/bench_path_a.py**

Create `tools/bench_path_a.py`:

```python
"""Path A benchmark harness: run N PTT interactions, compute before/after stats.

Usage:
    # Record N runs of current build (call 'before')
    py -3.13 -m tools.bench_path_a record --label before --n 20

    # Apply Path A fixes, then:
    py -3.13 -m tools.bench_path_a record --label after --n 20

    # Compare:
    py -3.13 -m tools.bench_path_a compare before.json after.json

Output: table of per-metric median change + 95% CI + Mann-Whitney U p-value.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import stats

BENCH_DIR = Path.home() / ".clicky-windows" / "bench"


def mann_whitney_less(before: list[float], after: list[float]) -> tuple[float, float]:
    """One-sided Mann-Whitney U: tests 'after is stochastically < before'.

    Returns (statistic, p_value). Reject null (no difference) when p < 0.05.
    Suitable for right-skewed latency data (no normality assumption).
    """
    before_arr = np.asarray(before, dtype=float)
    after_arr = np.asarray(after, dtype=float)
    result = stats.mannwhitneyu(after_arr, before_arr, alternative="less")
    return float(result.statistic), float(result.pvalue)


def bootstrap_median_ci(
    samples: list[float],
    confidence: float = 0.95,
    n_resamples: int = 9999,
) -> tuple[float, float]:
    """Bootstrap confidence interval on the median.

    Returns (lower_bound, upper_bound). Uses percentile method — fine for
    latency medians since we don't assume Gaussian.
    """
    samples_arr = np.asarray(samples, dtype=float)
    result = stats.bootstrap(
        (samples_arr,),
        statistic=np.median,
        confidence_level=confidence,
        n_resamples=n_resamples,
        method="percentile",
        random_state=42,
    )
    return float(result.confidence_interval.low), float(result.confidence_interval.high)


@dataclass
class MetricRow:
    name: str
    before: list[float]
    after: list[float]

    def summary(self) -> dict:
        before_p50 = float(np.median(self.before))
        after_p50 = float(np.median(self.after))
        delta = after_p50 - before_p50
        _, p = mann_whitney_less(self.before, self.after)
        ci_lo, ci_hi = bootstrap_median_ci(self.after)
        return {
            "name": self.name,
            "before_p50": before_p50,
            "after_p50": after_p50,
            "delta_ms": delta,
            "p_value": p,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
        }


def record_cmd(label: str, n: int) -> None:
    """Prompt user to do N real PTT interactions; parse debug logs to extract metrics."""
    # Implementation stub: instructs user to run `py -3.13 -m app`, interact N times,
    # then this function scrapes ~/.clicky-windows/debug/ for N most-recent folders
    # and extracts 4 metrics per folder from interaction.log.
    print(f"Recording {n} PTT interactions labeled '{label}'.")
    print("Launch `py -3.13 -m app` in another terminal, do N PTT presses, then return.")
    input(f"Press Enter after completing {n} interactions...")

    debug_root = Path.home() / ".clicky-windows" / "debug"
    folders = sorted(debug_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:n]

    metrics = {
        "stt_finalize_ms": [],
        "capture_ms": [],
        "claude_first_token_ms": [],
        "first_audible_word_ms": [],
    }
    for folder in folders:
        log_path = folder / "interaction.log"
        if not log_path.exists():
            continue
        # Parse the [+Xms] lines and extract 4 milestones
        stt_end = _extract_timing(log_path, "STT: final transcript")
        capture_end = _extract_timing(log_path, "CAPTURE:", last=True) or stt_end
        claude_start = _extract_timing(log_path, "CLAUDE: streaming started")
        tts_start = _extract_timing(log_path, "TTS: calling speak")

        if all(v is not None for v in [stt_end, capture_end, claude_start, tts_start]):
            metrics["stt_finalize_ms"].append(stt_end)
            metrics["capture_ms"].append(capture_end - stt_end)
            metrics["claude_first_token_ms"].append(claude_start - stt_end)
            metrics["first_audible_word_ms"].append(tts_start)  # proxy for first-word

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out = BENCH_DIR / f"{label}.json"
    out.write_text(json.dumps(metrics, indent=2))
    print(f"Wrote {out} ({len(metrics['stt_finalize_ms'])} samples)")


def _extract_timing(log_path: Path, marker: str, last: bool = False) -> float | None:
    """Extract the [+Xms] timing from the first (or last) line containing marker."""
    found_ms = None
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if marker in line:
            # Line format: "[+1234ms] rest..."
            if line.startswith("[+") and "ms]" in line:
                try:
                    ms = float(line[2:line.index("ms]")])
                    if not last:
                        return ms
                    found_ms = ms
                except ValueError:
                    pass
    return found_ms


def compare_cmd(before_path: str, after_path: str) -> None:
    before = json.loads(Path(before_path).read_text())
    after = json.loads(Path(after_path).read_text())
    print(f"{'Metric':<30} {'Before P50':<12} {'After P50':<12} {'Δ':<10} {'p':<10} {'95% CI (after)':<20}")
    print("-" * 100)
    for key in before:
        if key not in after:
            continue
        row = MetricRow(name=key, before=before[key], after=after[key])
        s = row.summary()
        print(
            f"{s['name']:<30} {s['before_p50']:<12.0f} {s['after_p50']:<12.0f} "
            f"{s['delta_ms']:+<10.0f} {s['p_value']:<10.4f} [{s['ci_lo']:.0f}, {s['ci_hi']:.0f}]"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_record = sub.add_parser("record", help="Record N interactions into a labeled JSON")
    p_record.add_argument("--label", required=True)
    p_record.add_argument("--n", type=int, default=20)

    p_compare = sub.add_parser("compare", help="Compare two labeled JSONs")
    p_compare.add_argument("before")
    p_compare.add_argument("after")

    args = parser.parse_args()
    if args.cmd == "record":
        record_cmd(args.label, args.n)
    elif args.cmd == "compare":
        compare_cmd(args.before, args.after)


if __name__ == "__main__":
    main()
```

Create an empty `tools/__init__.py` if it doesn't exist:
```bash
touch tools/__init__.py  # or equivalent
```

- [ ] **Step 5: Run tests**

```bash
py -3.13 -m pytest tests/test_bench.py -v
```

Expected: all 3 pass.

- [ ] **Step 6: Run full suite**

```bash
py -3.13 -m pytest -q
```

Expected: 166 passed.

- [ ] **Step 7: Commit**

```bash
git add tools/bench_path_a.py tools/__init__.py tools/gen_chime.py tests/test_bench.py requirements.txt
git commit -m "feat(tools): add Path A benchmark harness with Mann-Whitney U + bootstrap CI

tools/bench_path_a.py records N PTT interactions (scraped from
~/.clicky-windows/debug/*/interaction.log) and compares before/after
statistically.

Per-metric output: median (P50) before, median after, delta_ms, one-sided
Mann-Whitney U p-value ('after < before'), 95% bootstrap CI on after median.

Four metrics: stt_finalize_ms, capture_ms, claude_first_token_ms,
first_audible_word_ms. Mann-Whitney is right for right-skewed latency
(no Gaussian assumption); bootstrap median gives interpretable CI.

Adds scipy>=1.11 + soundfile>=0.12 (latter used by Task 11's chime gen)
to requirements.txt."
```

---

## Acceptance Verification (after Task 12)

Manual gate — run after all 12 commits land:

- [ ] **1. Full test suite passes:**
```bash
py -3.13 -m pytest -q
```
Expected: **166 passed**.

- [ ] **2. Record baseline (rerun the branch from BEFORE Task 1 if possible; otherwise estimate from pre-existing debug logs):**

The realistic path: Path A is destructive to latency baselines, so commit this plan in a side branch, run the harness once on main (pre-Path-A) as `before.json`, then merge/cherry-pick Path A and re-run as `after.json`.

- [ ] **3. Apply Path A, record `after`:**
```bash
py -3.13 -m tools.bench_path_a record --label after --n 20
```

- [ ] **4. Compare:**
```bash
py -3.13 -m tools.bench_path_a compare ~/.clicky-windows/bench/before.json ~/.clicky-windows/bench/after.json
```

- [ ] **5. Acceptance criteria:**
  - `first_audible_word_ms` P50 < 2000ms (Aaron's target)
  - `first_audible_word_ms` p-value < 0.01 (clear statistical win)
  - `claude_first_token_ms` P50 < 1500ms (prompt cache effect)
  - `stt_finalize_ms` P50 < 500ms (no regression)
  - No coordinate-precision regression: visually verify 3 interactions' screenshot markers land within ±10px of intended UI elements

- [ ] **6. Update ROADMAP.md** — mark Phase 1.5 Step 2 ✅ Done with the measured P50 numbers.

- [ ] **7. Append DECISIONS.md** — entry documenting actual vs predicted latency + any architectural decisions made during implementation (e.g. "did NOT do speculative LLM because Q1 research showed complexity exceeds payoff").

---

## Self-Review

**1. Spec coverage (ROADMAP.md "Step 2 (Path A parallelism)" locked scope):**

| Scope item | Covered by task |
|---|---|
| STT `end_of_turn` fix | Task 1 |
| Capture-at-press + memory-at-press | Task 4 |
| Sentence-level TTS chunking via queue | Tasks 5 + 6 |
| OpenRouter prompt caching (system + memory) | Task 3 |
| Listening chime on hotkey PRESS | Task 11 |
| Cursor visual overhaul (bezier + waveform only, skip spinner per Q1=B) | Tasks 7 + 8 + 9 + 10 |
| 200ms TTS-to-mic grace period | Task 2 |
| Measurement harness | Task 12 |

All 8 scope items covered. No gaps.

**2. Placeholder scan:** Every task has exact file paths + line numbers, exact code, exact test names, exact commands, exact commit messages. No "TBD" / "TODO" / "implement later" / "add error handling" / "similar to Task N".

**3. Type consistency:**
- `set_tts_grace_until(epoch_ts: float)` — Task 2 definition matches Task 2 call sites
- `on_audio_level(callback: Callable[[float], None])` — Task 7 definition, used by Task 10
- `show_waveform(physical_x: int, physical_y: int, monitor: dict)` — Task 9 Controller method, called by Task 10 via sig_show_waveform
- `set_audio_level(level: float)` — matches across stt callback → app signal → overlay method
- `_waveform_bar_height(bar_index: int, audio_level: float, phase_seconds: float) -> float` — Task 9 helper, signature consistent

All type signatures check out across tasks. No rename bugs.

---

## Execution Handoff

**Plan complete, saved to this plan file.**

After ExitPlanMode approval, copy this plan to `docs/superpowers/plans/2026-04-19-path-a-parallelism.md` and pick execution mode:

1. **Inline execution with checkpoint reviews** — Execute Task 1, show diff + test run, get user nod, proceed to Task 2, repeat. User confirms each major code change per their "auto mode bypasses boring stuff only" rule. Recommended for this plan.
2. **Subagent-driven** — Dispatch a fresh subagent per task. Faster but less user-in-the-loop.

Given the user's explicit "WAIT for my approval for major code changes" instruction, **inline with per-task checkpoint** is the right mode.
