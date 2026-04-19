# Clicky Windows — Roadmap

**Status:** Phase 1 in progress
**Last updated:** 2026-04-11

This doc answers **where are we now**. It combines what other projects split into PLAN.md + PROGRESS.md + TASKS.md + TESTING.md — all of that lives here in status columns and acceptance proof.

For **what and why** → [PRD.md](PRD.md)
For **why we chose X over Y** → [DECISIONS.md](DECISIONS.md)
For **how** → [CLAUDE.md](CLAUDE.md)
For **frozen strategic plan (historical)** → `C:\Users\Abhis\.claude\plans\streamed-tumbling-sunbeam.md`

---

## Files Index

**Your compass.** Every doc and what it's for.

### Already exist (don't delete)

| File | Purpose |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Project contract, auto-loaded into every Claude Code session. Updated with 8 edits on 2026-04-11. |
| `STARTER_PROMPT.md` | Previous session's bootstrap prompt. Archive. |
| `Claude Code Clicky Chat.txt` | Previous session transcript (3,838 lines). Archive. |
| `B0 projects research.txt` | Wallee/Grafyn/TextForm research (12,135 lines). Archive. |

### Living project docs (updated throughout build)

| File | Purpose | Updated |
|---|---|---|
| [`PRD.md`](PRD.md) | What and why. Problem, target user, IS/IS NOT, scope, competitors, risks. | Rarely — only when scope changes |
| [`ROADMAP.md`](ROADMAP.md) | **This file.** Where are we. Files Index + phased plan + status + acceptance proof. | After every single step |
| [`DECISIONS.md`](DECISIONS.md) | Why we chose X not Y. Append-only architectural decision log. | Append-only when non-obvious decisions made |
| `README.md` | User-facing intro. Install, run, hotkey, known issues. | **Written last**, at end of Phase 1 |

### Per-component execution plans (Superpowers-generated, during build)

Only for the 5 hard components. Trivial files (config, stt, tts, hotkey) skip Superpowers ceremony.

| File | Created before | Status |
|---|---|---|
| `docs/superpowers/plans/YYYY-MM-DD-capture.md` | Step 1 (capture.py) | Not started |
| `docs/superpowers/specs/2026-04-11-ai-design.md` | Step 2 (ai.py) | ✅ Done 2026-04-11 |
| `docs/superpowers/plans/2026-04-11-ai.md` | Step 2 (ai.py) | ✅ Done 2026-04-11 |
| `docs/superpowers/plans/2026-04-11-overlay.md` | Step 3 (overlay.py) | ✅ Done 2026-04-11 |
| `docs/superpowers/plans/YYYY-MM-DD-memory.md` | Step 6.5 (memory.py) | Not started |
| `docs/superpowers/plans/YYYY-MM-DD-app.md` | Step 7 (app.py) | Not started |

### Code files (12 total)

| File | Purpose | Superpowers ceremony? | Status |
|---|---|---|---|
| `requirements.txt` | 10 deps | No | Not started |
| `.env.example` | `ANTHROPIC_API_KEY=` template | No | Not started |
| `.gitignore` | .env, __pycache__, debug_*.jpg, whisper-cache | No | Not started |
| `config.py` | Env loading + constants | No | Not started |
| `capture.py` | Screen grab, cursor, DPI, multi-monitor, resize | **Yes** | Not started |
| `ai.py` | `AIClient` abstract + `AnthropicClient` with Computer Use API | **Yes** | Not started |
| `overlay.py` | PyQt6 transparent click-through overlay | **Yes (highest risk)** | Not started |
| `stt.py` | `STT` abstract + `FasterWhisperSTT` | No (trivial) | Not started |
| `tts.py` | `TTS` abstract + `Pyttsx3TTS` | No (trivial) | Not started |
| `hotkey.py` | `pynput` Ctrl+Alt+Space push-to-talk | No (trivial) | Not started |
| `memory.py` | Karpathy markdown + SQLite index | **Yes (differentiator)** | Not started |
| `app.py` | Orchestrator, threading, Qt signals | **Yes (2nd highest risk)** | Not started |
| `tools/lint_memory.py` | Karpathy-style weekly health check | No (standalone CLI) | Not started |

### Test files (target: ~50-80 tests total)

| File | Purpose | Status |
|---|---|---|
| `tests/test_capture.py` | Coordinate math, DPI, resolution picker, scale factors | Not started |
| `tests/test_ai.py` | Response parsing, tool_use extraction, coord clamping, scaling | Not started |
| `tests/test_memory.py` | Markdown append, SQLite CRUD, recall query, skill inference | Not started |
| `tests/test_hotkey.py` | State machine (idle → pressed → recording → released) | Not started |

---

## Phase 1: Python MVP with Persistent Memory

**Goal:** validate "Clicky + persistent memory is meaningfully better than stateless Clicky." Not a Windows port, a differentiated product.

**Target:** 1-2 weeks active work. Current pace: TBD (started 2026-04-11).

| Step | Component | Status | Acceptance proof | Commit |
|---|---|---|---|---|
| A | CLAUDE.md: 8 specific edits | ✅ Done | CLAUDE.md updated 2026-04-11: faster-whisper, memory in Phase 1, Computer Use beta, memory.py + tools/lint_memory.py in file structure, Ctrl+Alt+Space hotkey, testing target, threading rule, three-coordinate-space doc | TBD |
| B | PRD.md | ✅ Done | Written 2026-04-11. Problem statement, target user, IS/IS NOT, core loop, 10 acceptance criteria, Phase 2 scope, Phase 3 (not pre-committed), competitor landscape, validated demands, 12 risks, success metrics, rejected alternatives | TBD |
| C | ROADMAP.md (this file) | 🟡 In progress | Written 2026-04-11. Files Index + all step statuses | TBD |
| D | DECISIONS.md | ⏳ Pending | Initial decision entries from the plan | — |
| 0 | Scaffold (requirements.txt, .env.example, .gitignore, config.py) | ✅ Done | All 12 deps verified on Python 3.13.7: anthropic 0.94.0, mss 10.1.0, PyQt6 6.11.0, pynput 1.8.1, sounddevice 0.5.5, numpy 2.4.2, faster-whisper 1.2.1, pyttsx3 2.99, Pillow 11.3.0, python-dotenv 1.2.1, pytest 9.0.2, pytest-mock 3.15.1. `config.py` loads with `HOTKEY=alt+space`, 3 candidate resolutions, memory dir `~/.clicky-windows/memory`. | 5eed343 |
| 0.5 | Git init + private GitHub repo | ✅ Done | `gh repo view clicky-windows` returns PRIVATE, main branch, URL https://github.com/AbhishekVulla/clicky-windows. First commit pushed (12 files, 17,412 insertions). | 5eed343 |
| 1 | `capture.py` | ✅ Done | **22/22 pytest unit tests green (0.22s)**. Manual verification on real 2880×1800 @ 200% DPI desktop: monitor `(0, 0) 2880×1800`, resolution `1280×800` (16:10), scale `(2.25, 2.25)`. **Cursor-math verified end-to-end via red-crosshair marker drawn on `debug_capture.jpg`** — user mouse-over-Start-button test confirmed crosshair lands ON the Start icon at the printed coord `(591, 1742)` → image-space `(262, 774)` = `591/2.25, 1742/2.25` pixel-accurate. GetCursorPos + scale-back math validated. 100% runtime-detected. | 9a222ce, 57a95e7 |
| 2 | `ai.py` | ✅ Done (⚠️ **SUPERSEDED 2026-04-12 evening 3** — refactored in 425d51e) | **17/17 pytest unit tests green (full suite 39/39 in 1.06s)**. `AIClient` abstract + `AnthropicClient` with Computer Use API beta, verbatim Clicky `ElementLocationDetector.swift` mirror. **Live-API acceptance passed:** loaded `debug_capture.jpg`, `claude-sonnet-4-6` returned pixel-accurate coordinate `(263, 779)` — within 5 pixels of Step 1 ground truth `(262, 774)`. **⚠️ SUPERSEDED 2026-04-12 (evening 3):** Step 7 brainstorming research pass discovered `ElementLocationDetector.swift` is **dead code** (zero references across all 11 non-test Clicky Swift files, grep-verified via `gh api`). Clicky's actual shipping path is plain vision streaming + `[POINT:x,y:label]` regex via `ClaudeAPI.analyzeImageStreaming` + `CompanionManager.parsePointingCoordinates`. Our `ai.py` is functionally correct but architecturally wrong (ports dead code). Pre-Step-7 refactor replaces Computer Use with vision-tag + 35-line system prompt + 1024 max_tokens + streaming context manager. See [DECISIONS.md 2026-04-12 (evening 3) "ai.py refactor"](DECISIONS.md) for the full research pass + refactor plan. Original commit stays in history; refactor landed later. Docs: [`docs/superpowers/plans/2026-04-11-ai.md`](docs/superpowers/plans/2026-04-11-ai.md) (historical) + plan file `C:\Users\Abhis\.claude\plans\streamed-tumbling-sunbeam.md` (current refactor plan). | 4ad6306 (original) + 425d51e (refactor) |
| 3 | `overlay.py` **(highest risk)** | ✅ Done | **14/14 pytest unit tests green (full suite 53/53 in 1.89s)**. Per-monitor overlay architecture (overrode CLAUDE.md's "spans full virtual desktop" wording — see DECISIONS.md 2026-04-11 entry on Qt "islands-of-screens" gotcha). `OverlayController` + `OverlayWindow(QWidget)` + `apply_clickthrough_styles` ctypes helper + `screen_for_monitor` + `physical_to_local_logical` pure math functions. **Manual verification passed on 2880×1800 @ 200% DPI machine (all 5 checklist items):** blue pointer visible + smooth 400ms animation, clicks pass through to apps underneath, no taskbar entry, no focus stealing, correct 4-corner + center positions. **Boris #5 "Verification Before Done" self-critique pass applied pre-commit** — 5/5 Tier 1 cleanup items fixed: `itertools.cycle` replaces mutable-list closure, proper return types (`QScreen`, `OverlayWindow \| None`), `paintEvent(_event)` PEP 8 rename, `SetWindowLongW` error check raises `RuntimeError` with `ctypes.WinError()` context, 2 new `TestOverlayControllerLifecycle` tests covering `hide_for_capture()` / `show_after_capture()` (screenshot-integrity invariant). Full design + Boris self-critique at [`docs/superpowers/plans/2026-04-11-overlay.md`](docs/superpowers/plans/2026-04-11-overlay.md). | 03bc0db |
| 4 | `stt.py` **(latency-first pivot)** | ✅ Done | **7/7 pytest unit tests green (full suite 77/77)**. `STT` abstract + `AssemblyAIStreamingSTT` concrete using AssemblyAI `u3-rt-pro` + `ForceEndpoint` via `assemblyai.streaming.v3.StreamingClient`. Matches Clicky's `AssemblyAIStreamingTranscriptionProvider.swift:447-451` verbatim (`speech_model=u3-rt-pro`, `sample_rate=16000`, `encoding=pcm_s16le`, `format_turns=true`). Daemon-thread teardown in `stop()` moves blocking `disconnect(terminate=True)` off the 500ms critical path (R1 fix — `assemblyai/streaming/v3/client.py:126-137` confirms `disconnect()` internally joins 2 SDK worker threads with 1s queue timeouts = 1-2s worst-case blocking on the calling thread). B2 + B3 Boris #5 fixes applied: streaming errors captured via `_stream_error` and surfaced from `stop()`; `api_key` truthiness validated at `start()` with actionable diagnostic. **Manual live-API gate passed**: real mic → real WebSocket → ~4ms finalization latency verified (target <500ms, absolute ceiling <2s). Latency-first pivot: this supersedes the original "faster-whisper local CPU" PRD framing — see [DECISIONS.md 2026-04-11 session 3 "Priority inversion: latency > local-first"](DECISIONS.md). | ab3c992 |
| 5 | `tts.py` **(latency-first pivot)** | ✅ Done | **7/7 pytest unit tests green (full suite 77/77)**. `TTS` abstract + `CartesiaSonicTTS` concrete using Cartesia `sonic-3` HTTP chunked streaming via `client.tts.generate(...).iter_bytes()` (NOT the deprecated `client.tts.bytes()` — Cartesia SDK 3.0.2 emits `DeprecationWarning` on the old path). Default voice: "Brooke - Big Sister" `e07c00bc-4134-4eae-9ea4-1a55fb45746b` — confident adult female "for conversational use cases" per Cartesia's own catalog (verified via `client.voices.list()`, not hallucinated). `speak()` non-blocking — spawns daemon thread, iterates chunks, plays via `sounddevice.OutputStream` 44.1kHz float32. `speak_sentence()` wired for Step 7 app.py sentence-chunking. `stop()` flag wired for Phase 2 Issue #36 TTS interruption. No `Pyttsx3TTS` fallback in Phase 1 (YAGNI per DECISIONS.md 2026-04-11 session 3). **Manual live-API gate passed**: Brooke voice speaks "Hello, I am Clicky Windows..." naturally (non-robotic), `speak()` returned non-blocking, first audible word within ~400ms. | ab3c992 |
| 6 | `hotkey.py` **(Alt+Space → Ctrl+Shift+Space → Ctrl+Alt+Space three-step pivot)** | ✅ Done | **11/11 pytest unit tests green (93/93 full suite)** after the 2026-04-12 evening Ctrl+Shift+Space → Ctrl+Alt+Space pivot. `PushToTalkHotkey` with 3-flag state machine (`_ctrl_down`, `_alt_down`, `_space_down`) — RECORDING requires all 3 held, any release while RECORDING fires `on_release` and returns to IDLE. `pynput.keyboard.Listener(suppress=False)` — observe-only, does NOT consume key events (this is LOAD-BEARING — `suppress=True` installs a `WH_KEYBOARD_LL` hook that globally disables ALL typing). `_is_ctrl/_is_alt/_is_space` helpers normalize left/right/AltGr variants. Thread-safe via `threading.Lock`. **Initial commit ab3c992 shipped Ctrl+Shift+Space; 37a9a30 (evening same day) pivoted to Ctrl+Alt+Space** after empirically confirming Ctrl+Shift+Space conflicts with Excel + Google Sheets "Select entire worksheet" binding. Fn+Space researched and rejected (AutoHotkey community + pynput docs confirm firmware-level invisibility to `WH_KEYBOARD_LL`). **Manual gate passed 2026-04-12**: Ctrl+Alt+Space hold → PRESSED, release → RELEASED, Notepad typing still works globally while listener is active, **Excel does NOT Select-All on the combo** (the whole reason for the evening pivot). Alt+Space-via-Win32-RegisterHotKey deferred to Phase 1.5 as a drop-in subclass. See [DECISIONS.md 2026-04-12 (evening) "Ctrl+Alt+Space replaces Ctrl+Shift+Space"](DECISIONS.md). | ab3c992, 37a9a30 |
| 6.5 | `memory.py` **(differentiator)** | ✅ Done | **15/15 pytest unit tests green (93/93 full suite)**. `MemoryStore` with 4 public methods: `__init__`, `recall()`, `record()`, `list_known_apps()` — **not 5**. `infer_skill_level()` was removed during build after user pushback ("This is not Khan Academy now is it? The whole value is learn by doing is it not?") — see [DECISIONS.md 2026-04-12 "Removed infer_skill_level from memory.py"](DECISIONS.md) for the full rationale. Karpathy-style markdown files at `~/.clicky-windows/memory/<app>.md` + SQLite index at `~/.clicky-windows/index.db` (WAL mode). Mock-free tests via `tmp_path` fixture — real filesystem + real SQLite round-trip. Boris #5 self-critique caught 2 Tier 1 issues pre-commit: `recall(max_chars<=0)` silently returned full file via Python's `text[-0:]` quirk (fixed with defensive guard + regression test); `_HEADER_TEMPLATE.format()` was format-injection vulnerable if app name contained literal `{` or `}` (fixed via f-string interpolation, constant renamed to `_HEADER_TRANSPARENCY_LINE`). **Manual live gate passed**: `py -3.13 -m memory` seeded 3 fake interactions for synthetic `CLICKY_GATE_TEST.EXE` app, `recall()` returned 779-char tail, `list_known_apps()` returned correct dict shape, delete-and-rerun idempotency verified. File format is human-readable per Karpathy transparency promise. Per-component plan archived at [`docs/superpowers/plans/2026-04-12-memory.md`](docs/superpowers/plans/2026-04-12-memory.md) with explicit DEVIATION FROM PLAN section documenting the `infer_skill_level` deletion. | 221d2ca |
| 7 | `app.py` **(2nd highest risk)** | ✅ Done | **118/118 tests green.** Full PTT orchestrator: ClickyApp(QObject), pyqtSignal threading, ctypes GetForegroundWindow, one pipeline worker per press, cancel-on-re-press. STT pre-opened at startup (0ms on press). TTS three-pronged instant kill. Cursor mouse-following with lerp + glow + state machine. Debug logging to `~/.clicky-windows/debug/`. Manually tested across Excel, Clipchamp, EduPack, Photoshop, Pixlr, Fusion360. OpenRouter support via env var. Known issues: 4-6s latency (Claude model-bound), STT partial truncation on short utterances, cursor straight-line not bezier arc. Boris #5 NOT done. | 8b3710c |
| **7.1** | **Phase 1.5 Step 1 — GeminiClient + factory + dual-SDK routing (CLOSED, opt-in infra only)** | ✅ Shipped, not default | **138/138 tests.** `GeminiClient(AIClient)` via OpenAI SDK + OpenRouter OpenAI-compat endpoint. `create_ai_client(model_id)` factory dispatches by MODEL_ID prefix. Head-to-head A/B showed Claude is 340ms FASTER + pixel-precise vs Gemini 230px miss → `.env` default stays Claude. Gemini opt-in via `MODEL_ID=google/...`. See DECISIONS.md 2026-04-19 for empirical data + rationale. | 02196e7 → 3988a51 (8 commits) |
| 7.5 | `tools/lint_memory.py` | ⏳ Pending | Seed 10 fake interactions across 2 apps, run lint, verify `insights.md` contains non-trivial summary (not just "you used Excel 5 times") | — |
| 8 | Phase 1 polish | ⏳ Pending | 5+ real user sessions on a real task. `lint_memory.py` run surfaces an insight. README.md written. Demo video recorded. All 4 docs up to date. | — |

**Status legend:** ✅ Done · 🟡 In progress · ⏳ Pending · ❌ Blocked · 🔄 Revised

**Commit discipline:** one commit per step at minimum, conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`, `test:`). Commit SHA recorded in the "Commit" column above.

---

## Phase 1 Acceptance Gate (all 10 must be true before declaring Phase 1 done)

From [PRD.md § Phase 1 Scope + Acceptance Criteria](PRD.md#phase-1-scope--acceptance-criteria):

- [ ] **1. Working loop on real Windows machine.** Press Ctrl+Alt+Space in Excel, ask a question, release, see pointer + hear answer. Works 3× in a row without crashing.
- [ ] **2. Multi-monitor + DPI verified.** Tested on 2+ monitors. Pointer lands within ±5 px of target on both.
- [ ] **3. Memory persists across sessions.** Close app, reopen, ask follow-up. Clicky references previous interaction.
- [ ] **4. Memory is human-readable.** `cat EXCEL.EXE.md` shows clear interaction log.
- [ ] **5. 5+ real user sessions.** Not test sessions — actual usage on a real task.
- [ ] **6. `lint_memory.py` produces meaningful insights.** `insights.md` contains a non-obvious observation — the **unexpected finding** candidate for B0 case study.
- [ ] **7. ~50-80 pytest unit tests pass.** Coverage across capture, ai, memory, hotkey.
- [ ] **8. Demo video recorded.** 30-90s screen recording.
- [ ] **9. All 4 docs up to date.** CLAUDE.md, PRD.md, ROADMAP.md, DECISIONS.md. README.md written last.
- [ ] **10. Private GitHub repo with full history.** Conventional commits.

---

## Phase 1.5: Latency optimization (the senior's advice track)

**Goal:** Drop end-to-end PTT latency from 5-9s → ~1-2s so Clicky Windows matches Vapi's 465ms and Clicky macOS's sub-2s feel. Based on Aaron's feedback at SUTD InspireCon 2026-04-18 + independent research.

Two steps, each standalone-shippable. Ship Step 1 first, measure, then decide if Step 2 is still needed.

| Step | Goal | Expected latency win | Status |
|---|---|---|---|
| **1** | Swap Claude Sonnet 4.6 → Gemini 3 Flash Preview via OpenRouter | 5-9s → 3-4s (hypothesis — REJECTED by measurement) | ✅ **CLOSED — infrastructure shipped, default stays Claude.** GeminiClient + factory + dual-SDK routing shipped and pushed (8 commits on 2026-04-19: `02196e7` → `3988a51`, 138/138 tests). **Head-to-head measurement on identical workload ("how do I make my repo public"):** Gemini 2.5 Flash 4669ms total + 230px coordinate miss. Claude Sonnet 4.6 4325ms total (**340ms FASTER**) + 0px miss (bullseye on Settings tab). Gemini has no real-world latency advantage via OpenRouter AND is far less precise. Latency variance through OpenRouter (±400ms per run) completely swamps Gemini's theoretical TTFT edge. `.env` stays on Claude. Gemini kept as opt-in via `MODEL_ID=google/...`. See DECISIONS.md 2026-04-19 (evening) + late-evening entries. |
| **2** | Path A parallelism — 8-item locked scope (STT end_of_turn fix + capture-at-press + sentence-level TTS chunking + OpenRouter prompt caching + listening chime + cursor visual overhaul [waveform/spinner/bezier] + TTS-to-mic grace + measurement harness) | 5-9s → ~1.6-1.9s actual | 🟡 **Next sprint — research complete 2026-04-19, superpowers ceremony pending.** Research pass across 12 Explore agents (Aaron's InspireCon advice + Optimistic UI patterns + Clicky shipping-source verification). Rejected: Cerebras (no vision), Groq (unproven precision), ElevenLabs (Cartesia wins blind tests), Gemini Live (same 230px floor), speculative LLM (negative EV at 3.7s round-trip), filler phrases (yappy + costly). See "Step 2" section below for locked scope + rejected items + realistic latency math. |

### Step 1 (Gemini 3 Flash swap) — this sprint

- Dual-SDK routing via `ai.create_ai_client(MODEL_ID, ...)` factory
- `AnthropicClient` (anthropic SDK) for `anthropic/*` or `claude*` IDs
- `GeminiClient` (openai SDK via OpenRouter OpenAI-compat endpoint) for `google/*` or `gemini*` IDs
- Same .env key, same OpenRouter BYOK abstraction, user-swappable via MODEL_ID
- **Acceptance:** Gemini 3 Flash coordinate accuracy within ±20px of ground truth on `debug_capture.jpg` (Step 1 verification). If passes → set as Phase 1.5 default. If fails → keep Claude as default, code stays as opt-in alternative.
- See DECISIONS.md 2026-04-19 "Gemini 3 Flash Preview via OpenRouter" for rationale + alternatives considered.

### Step 2 (Path A parallelism) — next sprint

Deep research pass completed 2026-04-19 late-evening across v1+v2+v3 waves (12 Explore agents). Aaron's InspireCon advice was grounded in source where possible; vendor picks that don't clear Clicky's vision-precision floor were rejected. Three visual-state research rounds verified Farza's actual Clicky behavior from `OverlayWindow.swift` + `CompanionManager.swift` + `BuddyDictationManager.swift`.

**Locked Path A scope (8 commits, ~521 LOC):**

1. **STT `end_of_turn` fix** (stt.py:429, 1 line) — correctness. The current `_on_turn` handler sets `_final_event` only when `turn_is_formatted=True`, but `format_turns=False` means that event never fires. Real fix: `if is_formatted:` → `if getattr(event, "end_of_turn", False):`. AssemblyAI docs confirm `end_of_turn` is the only reliable completion signal. Kills the 9-char "How do I—" cutoff bug (verified in `~/.clicky-windows/debug/2026-04-13_03-24-32_chrome.exe/interaction.log`).
2. **Capture-at-press + memory-at-press** (app.py, ~40 LOC) — **-250ms actual**. Current capture stage is 238ms (overlay-hide + 50ms wait + `mss.grab` + PIL LANCZOS + overlay-show). Shift to press-time. Re-capture-on-release only if cursor moved >50px (cheap safeguard).
3. **Sentence-level TTS chunking via queue** (tts.py + app.py, ~100 LOC) — **-2000ms perceived**. Current `speak_sentence()` delegates to `speak()` which cancels the prior thread, so consecutive calls cancel each other (see tts.py:168-174). Real fix: add `queue.Queue` + dedicated worker thread in `CartesiaSonicTTS` that consumes sentences sequentially. Wire `flush_sentences` in app.py pipeline so TTS starts on first `.!?` boundary while Claude still generates the rest.
4. **OpenRouter prompt caching** on system prompt + memory block (ai.py, ~30 LOC) — **-50-100ms actual**. OpenRouter passes through Anthropic-native `cache_control: {"type": "ephemeral"}` breakpoints for `anthropic/*` routes. Cache system prompt + memory only (never per-turn content to avoid the full-context-caching latency paradox). 5-min TTL. Breaks even on first cache hit.
5. **Listening chime on hotkey PRESS** (app.py, ~10 LOC) — 0ms latency, pure UX. 50-80ms tone via async `sounddevice.play()` (non-blocking). Standard pattern (Alexa/Siri/Google) to reduce "did it hear me" anxiety.
6. **Cursor visual overhaul** (overlay.py + stt.py, ~230 LOC) — port Farza's shipping state machine verbatim. Three animations mapped to three states:
   - **LISTENING (PTT held)** — triangle hides; 5-bar blue waveform widget rendered in its place. Bars = RMS × profile `[0.4, 0.7, 1.0, 0.7, 0.4]` + 0.57Hz sine idle-pulse. 36fps. Port from `OverlayWindow.swift:705-743`. RMS computed in stt.py audio callback (sum-of-squares / frame_count, ×10.2 boost, clamp[0,1], 0.72 decay filter) and emitted via new `pyqtSignal(float)`. Port from `BuddyDictationManager.swift:687-721`.
   - **THINKING (release → coord returned)** — waveform fades out (150ms), spinning arc fades in. 14×14pt circle, 2.5pt blue stroke, `trim(15%-85%)`, angular gradient, rotates 360° every 800ms. Port from `OverlayWindow.swift:745-774`.
   - **FLYING (coord arrived → arrival)** — quadratic bezier arc + smoothstep easing + tangent rotation + 1.3x mid-flight scale pulse. Duration scales with distance (600-1400ms). Port verbatim from `OverlayWindow.swift:491-568`.
   - **SPEAKING** — zero animation by design. Voice is the feedback. Matches "non-yappy" priority.
7. **200ms TTS-to-mic grace period** (stt.py + app.py, ~10 LOC) — correctness. After `tts.stop()`, discard mic chunks for 200ms so speaker decay doesn't loop into the next PTT's transcript. Verified needed from debug logs.
8. **Measurement harness** (tools/bench_path_a.py, ~100 LOC) — Mann-Whitney U + bootstrap-CI on median, N=20 before/after. Four metrics: release→first-partial, release→final-transcript, release→Claude-first-token, release→first-audible-word. Validated via `scipy.stats.mannwhitneyu` + `scipy.stats.bootstrap`. Workload via `CannedSTT` mock (deterministic) for STT-downstream isolation + real mic for end-to-end.

**Rejected from Path A scope (with reasons):**

- **Cerebras inference (Aaron's #1 pick)** — Cerebras Cloud serves zero vision models as of 2026 (verified in their own OpenRouter integration docs). Text-only TPS irrelevant; hard blocker for Clicky's `[POINT:x,y]` requirement.
- **Groq Llama 4 Scout** — has vision + 27x cheaper, but zero published pixel-precision benchmarks on ScreenSpot. Gambling on unproven precision after already rejecting Gemini on that exact axis. Fallback-only candidate, not primary.
- **ElevenLabs Flash TTS (Aaron's pick)** — blind tests favor Cartesia Sonic-2 61.4% vs ElevenLabs Flash V2 38.6% for conversational agents. Flash v2.5 TTFB ~75ms vs Sonic-3 ~40-90ms (Cartesia wins). Aaron anchored on ElevenLabs brand rep for audiobooks, not voice agents.
- **Gemini Live Flash API ("near instant")** — uses the same vision model as the chat API already rejected on 230px miss. Live collapses STT/LLM/TTS but doesn't fix the precision floor. Also: no OpenRouter pass-through (WebSocket vs HTTP), synchronous-only tool calls (can't emit coord JSON + speak simultaneously).
- **Speculative LLM on partial STT transcripts** — break-even at 33% miss rate given our 3.7s Claude round-trip. Vapi's 465ms works because Groq Llama-4 is ~200ms; our round-trip makes the miss cost punitive. Negative EV until LLM stage drops to ~1s.
- **Filler phrase during TTFT ("hm, let me see")** — contradicts user's "non-yappy" priority + adds API cost. Listening chime on PRESS already provides instant acknowledgment.
- **Memory recall reduction to 500-800 chars** — absorbed into #4 (prompt caching). The memory block is cached on round 1; size no longer drives per-turn cost after that. Revisit only if cache-miss rate is high.

**Realistic post-Path-A latency (no perception gimmicks):**

| Stage | Today | Post-Path-A |
|---|---|---|
| STT finalize (release → transcript) | 300ms | 300ms (unchanged — the `end_of_turn` fix is correctness, not speed) |
| Capture + memory + hide | 240ms (post-release) | 0ms (done during utterance) |
| Claude TTFT | 1200ms | ~1100ms (with prompt cache) |
| Claude first-sentence stream | — (collected to end) | +300ms |
| TTS TTFB (starts on first sentence, not full response) | 250ms (after full response) | 250ms (after first sentence) |
| **Total release → first audible word** | **~4.9s** | **~1.6-1.9s** |

Beats Aaron's <2s target. Matches Clicky macOS shipping feel. Precision moat (Claude Sonnet 4.6) preserved.

### Not doing in Phase 1.5

- Gemini Live API (WebSocket speech-to-speech): same vision floor as Gemini chat (already rejected on 230px). Also locks us into Google + WebSocket refactor. Hard no.
- Cerebras / Groq: vision blocker (Cerebras) or precision-unproven (Groq). Revisit only if vision-capable fast inference emerges.
- ElevenLabs TTS: blind-tests favor Cartesia Sonic-3 for conversational voice agents. Sonic stays.
- WebSocket TTS sentence chunking via Cartesia's `websocket_connect()`: not needed — queue-based sequential HTTP streaming achieves the same latency win with simpler code.
- Speculative LLM on partial transcripts: negative-EV at current 3.7s round-trip.

### Phase 1.5 acceptance (run all three before declaring done)

1. `py -3.13 -m app` PTT interaction in Excel: hotkey release → first audible word within 2.0 seconds, measured from `~/.clicky-windows/debug/*/interaction.log`.
2. 3 successful runs in a row without crashing, regression on any Phase 1 acceptance criterion.
3. All existing 118+ tests still green. New tests for any new code.

---

## Phase B: Ship-to-real-users polish (parity + installer + competitive gaps)

**Why this section exists.** 2026-04-19 competitive research found 12+ Clicky clones shipped in 12 days since Farza open-sourced macOS Clicky. The space is saturating fast. Our engineering rigor + persistent memory are worth nothing if a non-tech user googles "clicky windows" and installs `tekram/clicky-windows` first. These gaps MUST close before the demo video (Step 8) ships, in roughly this order.

**Target window:** 2-3 weeks after Phase 1.5 Step 2 lands.

### B1. PyInstaller bundle + simple installer (REMOVES the #1 barrier — "download 4 tools + edit .env")

- `pyinstaller --onedir --windowed app.py` producing a self-contained folder with bundled Python + all deps (PyQt6, cartesia SDK, assemblyai SDK, anthropic SDK, openai SDK, mss, pynput, sounddevice, numpy, Pillow, python-dotenv).
- Simple NSIS or Inno Setup installer wrapping the PyInstaller output → `Clicky-Windows-Setup.exe`.
- First-launch QInputDialog wizard for the 3 API keys (ANTHROPIC_API_KEY, ASSEMBLYAI_API_KEY, CARTESIA_API_KEY) stored in Windows Credential Manager via the `keyring` lib (mirror Grafyn's `settings.rs` migration pattern).
- Acceptance: double-click `.exe`, enter 3 keys once, hotkey works globally on next launch. Zero terminal required.
- **Effort:** 3-5 days.
- **Competitive parity:** tekram/clicky-windows has Squirrel (Electron Forge), tornikegomareli/clicky-desktop has release tarballs, mo-tunn/OpenGuider has full .exe/.dmg. We have NOTHING. This is existential for demo-video adoption.

### B2. System tray icon + minimal settings panel (QUIT without terminal)

- `QSystemTrayIcon` in PyQt6 with 3 menu items: Open Settings, About, Quit.
- Minimal `QWidget` settings panel: change hotkey (rebindable), change voice (`CARTESIA_VOICE_ID` dropdown), change LLM (`MODEL_ID` — dropdown of tested-known-working options: Claude Sonnet 4.6, Gemini 2.5/3 with a precision-warning), clear memory button (per-app + global nuke).
- Acceptance: non-tech user can change voice + quit app without touching terminal.
- **Effort:** 3-5 days.
- **Competitive parity:** tekram has tray + settings UI. We have stdout + Ctrl+C. Non-tech users won't find how to quit.

### B3. Real demo video showing memory differentiator (THE "wow" moment)

- 30-90 second screen recording. MUST include 2+ sessions of the SAME app with memory landing. Example script:
  - Session 1 (Monday): "how do I freeze the top row" in Excel → Clicky points at View, voice explains.
  - Session 2 (Thursday, same Excel workbook): "remind me how to freeze panes?" → Clicky says *"you asked about this Monday — same spot, View → Freeze Panes → Freeze Top Row"* and points.
- This is the ONE frame no other Clicky clone can reproduce. Memory is the moat.
- **Effort:** 1 day of recording + editing.
- **Publish to:** GitHub README, LinkedIn post (mirror Farza's original LinkedIn demo format), X/Twitter.

### B4. OpenRouter UI / provider-swap settings (part of B2 settings panel, listed separately for priority)

- Covered partly by B2 — a Model dropdown in settings reads/writes `MODEL_ID`.
- Expand to show "custom model" text field for any OpenRouter-supported `provider/model-id`.
- **Competitive parity:** tekram + mo-tunn let users pick provider. We force `.env` editing.

### B5. HIPAA / offline mode (local STT + TTS fallbacks — feature parity with tekram)

- Subclass drops on existing abstract bases:
  - `FasterWhisperSTT(STT)` — `faster-whisper` library, offline CT2 Whisper-base, no network.
  - `Pyttsx3TTS(TTS)` — local Windows SAPI, no network.
- Settings panel toggle: "Offline mode (local STT + TTS only)" — disables all cloud providers, uses local subclasses. Visual persistent indicator so user knows they're offline.
- **Effort:** ~1 week.
- **Competitive parity:** tekram has this (HIPAA mode — whisper.cpp + Windows SAPI). Differentiator for enterprise / healthcare / regulated users.

### B6. Linux port (cross-platform = 3x addressable market)

- `mss` already works on Linux. PyQt6 works on Linux. `pynput` works on Linux (X11 + Wayland).
- Win32 layered-window ctypes in `overlay.py` is the main Windows-specific piece — replace with Qt-native attribute-based transparency on Linux + compositor hints.
- Ctypes `GetForegroundWindow` in `app.py` replaced with a `wmctrl` / `xdotool` equivalent.
- **Effort:** ~1-2 weeks.
- **Competitive parity:** tornikegomareli/clicky-desktop ships Linux. mo-tunn ships Linux. Farza's Issue #13 + #59 explicitly request Linux. Windows-only story becomes weak if Rust clones continue to dominate cross-platform.
- **Decision gate:** only start Linux port after B1-B3 ship (installer + tray + demo video). Cross-platform is worthless if nobody can install Windows version first.

### Phase B execution order (commit to this order, don't skip)

1. **B1 installer** — removes the install barrier. Until this ships, you're invisible to 99% of users.
2. **B3 demo video** — now that install is trivial, show the memory "wow" moment. Requires B1 to be credible ("I can actually try this?" → yes, one click).
3. **B2 tray + settings** — polish on top of shipped installer. Iteration loop with early users.
4. **B4 OpenRouter UI** — subset of B2 scope.
5. **B5 HIPAA / offline** — feature parity with tekram. Unlocks regulated-industry conversations.
6. **B6 Linux port** — cross-platform. Only if B1-B5 shipped AND there's demonstrable Linux-user demand.

---

## Future visual + TTS refinements (revisit after B1-B3 ship)

Manual testing after Path A shipped (2026-04-19 evening) surfaced a few UX questions that are not urgent but worth revisiting before the demo video:

### F1. IDLE-state buddy visibility

Current behavior: buddy cursor is always visible, follows the OS mouse at ~60fps with a 35×25px offset. User feedback: *"blue halo + actual mouse pointer kinda weird"* — the OS arrow and our buddy polygon both on screen at all times feels cluttered when Clicky isn't in use.

Farza's shipping Clicky does the same (always-visible buddy) — it's a deliberate design choice. Three options to consider:

- **(a) Keep as-is** — match Farza's Clicky pattern. Familiar to anyone who saw his LinkedIn demo.
- **(b) Hide buddy during IDLE** — only show during LISTENING / THINKING / FLYING / SPEAKING. Reduces visual noise when not in use. Trade-off: buddy "pops into existence" on hotkey press which may feel abrupt.
- **(c) Fade buddy opacity to ~30% during IDLE**, full opacity when active. Compromise — buddy stays present as a subtle hint without being visually loud.

**Decision gate**: revisit before recording the demo video (Phase B3). If (b) or (c) wins, it's a ~10-20 LOC change in overlay.py `_on_follow_tick` setting `_pointer_visible` / painter opacity based on state.

### F2. WebSocket TTS for gap-free sentence streaming

Path A's sentence-level TTS via HTTP has a ~150-250ms TTFB gap between sentences (each `speak_sentence` call opens a new Cartesia HTTP connection). Observed in manual testing as audible silence between sentences.

Options ranked by fix quality:

1. **Cartesia WebSocket** (`client.tts.websocket()`) — one persistent connection, multiple transcript messages, no per-sentence TTFB. Gap drops from ~200ms to ~10ms. Effort: 4-8 hours (SDK surface verification + reconnection logic + thread-safety + tests rewrite).
2. **HTTP double-buffering** — worker pops sentence N+1 from queue + starts its HTTP request WHILE sentence N's audio is still draining. By the time N ends, N+1 is ready. Gap ≈ 0. Effort: 1-2 hours in `tts.py` `_queue_worker`.
3. **Prompt Claude for period-terminated sentences** — avoids em-dashes-as-connectors so our flush triggers hit more often. Doesn't fix the gap, but makes sentence streaming fire in the first place. (Ships in current sprint.)

**Decision gate**: revisit if manual testing after the current spinner/waveform sprint still shows sentence-streaming gaps feel bad. Otherwise defer to post-demo.

### F3. Adaptive sentence splitter

Current: `flush_sentences` matches `[.!?]\s`. Claude's response style uses em-dashes + colons as connectors, so the splitter rarely fires mid-stream. Options:

- **Broaden splitter** to treat `—\s`, `:\s`, long `,\s` (after >60 chars) as boundaries — makes sentence streaming fire more often BUT creates more sentences, which makes the WebSocket / double-buffer fix (F2) more urgent.
- **Combine with F2** — only broaden once gap problem is solved.

**Decision gate**: pair with F2.

### F4. STT stutter dedup — ✅ FIXED 2026-04-19 late-evening

**Root cause (verified from source, not dedup-heuristic as first hypothesized):**

1. **VAD too aggressive** — default `end_of_turn_confidence_threshold=0.4` + `min_turn_silence=400ms` (verified from docs.assemblyai.com) fires `end_of_turn=true` on natural mid-sentence pauses, splitting one utterance into multiple Turns.
2. **Handler fallback misfired** — our `_on_turn` used `if end_of_turn OR is_formatted:`. AssemblyAI emits a separate formatted-revision event after each end_of_turn (with `end_of_turn=false, turn_is_formatted=true`), and that event passed the `or` branch → handler fired twice → concatenation.

**Fix — two complementary changes** (committed as part of the spinner-sprint commit):

- `stt.py _on_turn`: fire ONLY on `end_of_turn=True`. AssemblyAI docs: *"Do not use turn_is_formatted for end-of-turn detection. The only reliable way to detect turn completion is end_of_turn: true."*
- `stt.py StreamingParameters`: raise to Conservative preset (`end_of_turn_confidence_threshold=0.7`, `min_turn_silence=800`, `max_turn_silence=3600`). VAD won't fire mid-hold; `force_endpoint()` on release remains the authoritative turn-end trigger.

**Verified fix test**: `tests/test_stt.py::test_on_turn_ignores_formatted_revision_without_end_of_turn` — simulates AssemblyAI's two-event pattern (real end_of_turn then formatted revision) and asserts the revision is NOT appended to `_final_transcript`.

**Manual verification**: re-speak "That's kind of weird" in a PTT session; debug log should show ONE final transcript, no concatenation.

---

## Competitive landscape snapshot (2026-04-19 research pass)

**12+ Clicky clones shipped in 12 days since Farza open-sourced macOS Clicky.** Space is saturating fast. Full findings in DECISIONS.md 2026-04-19 (late evening) "Competitive landscape + don't-fork decision."

**Direct competitors by tier:**
- **Tier 1 (serious threats):**
  - `tekram/clicky-windows` — Electron, 26 ⭐, actively developed, **shipped installer, 3 STT providers, 3 TTS providers, OpenRouter 300+ models, HIPAA mode.** Missing: persistent memory.
  - `tornikegomareli/clicky-desktop` — Rust + Raylib, 8 ⭐, Linux + Windows binaries. Ported the `ElementLocationDetector.swift` dead code we already caught. Zero tests. Missing: persistent memory.
  - `mo-tunn/OpenGuider` — Electron, 66 ⭐, Windows+Mac+Linux, Claude+OpenAI+Gemini+Groq+OpenRouter+Ollama. Structured tutorial mode (trili.ai-style). Missing: persistent memory.
- **Tier 2 (lower-threat, single-language Windows clones):**
  - `shreshth-s/clicky-windows` (C#/WPF, 11 ⭐), `Arnie936/zippy-windows` (C#/WinForms rebranded "Zippy", 28 ⭐), `NReyes22/clicky-windows` (C#, 2 ⭐), `jvaught01/flicky` (Electron, 7 ⭐), `annasba07/clicky-windows` (Rust incomplete, 0 ⭐), `JaySmith502/clicky-win` (Python — **only one with per-app context, but static user-curated docs, not learned memory**).
- **Tier 3 (adjacent / inspiration):**
  - `danpeg/clicky` — macOS fork, 86 ⭐, proactive tutor mode (Phase 2 reference).
  - `rishabhsai/glance` — UIA structured screen understanding library (Phase 2 replacement for pixel coords).
  - `mediar-ai/terminator` — Rust UIA cross-platform library (Phase 2 accessibility upgrade).

**What no one else has shipped:** Karpathy-style per-app persistent markdown memory (`~/.clicky-windows/memory/<app>.md` + SQLite index). This remains unclaimed territory. **Our moat.**

**What everyone else has that we don't** (now tracked in Phase B above):
- Installer (B1) — tekram Squirrel, tornikegomareli tarballs, mo-tunn .exe/.dmg
- Tray icon + settings UI (B2) — tekram
- OpenRouter UI / provider-swap (B4) — tekram, mo-tunn, jvaught01
- HIPAA / offline mode (B5) — tekram (whisper.cpp + SAPI)
- Linux support (B6) — tornikegomareli, mo-tunn

**Decision: don't fork, keep building.** Forking tornikegomareli requires a Rust rewrite + inherits the dead-code mistake we already fixed + still requires building memory from scratch. Electron forks are heavier than our PyQt6 stack. Our differentiator (memory) is real and unclaimed. See DECISIONS.md 2026-04-19 (late evening) for full rationale.

---

## Phase 2: Harden (2-4 weeks, only if Phase 1 validates)

**Not started.** Will be planned in detail when Phase 1 is done.

Key Phase 2 items from [PRD.md § Phase 2 Scope](PRD.md#phase-2-scope-2-4-weeks-only-if-phase-1-validates):
- 50-100+ pytest unit tests (grown from Phase 1's starter set)
- Replay scenarios (mirror Wallee's 60-scenario pattern)
- Proactive mode (idle detection, focused-window capture — **targeted at patterns found in Phase 1's markdown memory**, not guessed)
- BYOK / OpenRouter (`OpenRouterClient` with vision-tag regex fallback)
- ElevenLabs TTS + AssemblyAI streaming STT (subclasses)
- Clipboard copy, configurable hotkey UI, tray icon, settings panel
- Diff-and-skip screenshot caching (Karpathy-style "do less work")
- UIA accessibility tree fast path for productivity apps (where Clicky Windows can beat original Clicky — Mac has no equivalent)
- PyInstaller bundle + clean install path
- **"Unexpected finding" writeup published** (B0 editorial standard)
- 5+ outside users with documented feedback

---

## Phase 3: Tauri Rewrite (NOT pre-committed)

Only triggered if Phase 2 hits a Python-specific wall. Most likely never needed. See [PRD.md § Phase 3](PRD.md#phase-3-tauri-rewrite-not-pre-committed).
