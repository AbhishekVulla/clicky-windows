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
| `docs/superpowers/plans/YYYY-MM-DD-overlay.md` | Step 3 (overlay.py) | Not started |
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
| `hotkey.py` | `pynput` Alt+Space push-to-talk | No (trivial) | Not started |
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
| A | CLAUDE.md: 8 specific edits | ✅ Done | CLAUDE.md updated 2026-04-11: faster-whisper, memory in Phase 1, Computer Use beta, memory.py + tools/lint_memory.py in file structure, Alt+Space hotkey, testing target, threading rule, three-coordinate-space doc | TBD |
| B | PRD.md | ✅ Done | Written 2026-04-11. Problem statement, target user, IS/IS NOT, core loop, 10 acceptance criteria, Phase 2 scope, Phase 3 (not pre-committed), competitor landscape, validated demands, 12 risks, success metrics, rejected alternatives | TBD |
| C | ROADMAP.md (this file) | 🟡 In progress | Written 2026-04-11. Files Index + all step statuses | TBD |
| D | DECISIONS.md | ⏳ Pending | Initial decision entries from the plan | — |
| 0 | Scaffold (requirements.txt, .env.example, .gitignore, config.py) | ✅ Done | All 12 deps verified on Python 3.13.7: anthropic 0.94.0, mss 10.1.0, PyQt6 6.11.0, pynput 1.8.1, sounddevice 0.5.5, numpy 2.4.2, faster-whisper 1.2.1, pyttsx3 2.99, Pillow 11.3.0, python-dotenv 1.2.1, pytest 9.0.2, pytest-mock 3.15.1. `config.py` loads with `HOTKEY=alt+space`, 3 candidate resolutions, memory dir `~/.clicky-windows/memory`. | a4f9df1 |
| 0.5 | Git init + private GitHub repo | ✅ Done | `gh repo view clicky-windows` returns PRIVATE, main branch, URL https://github.com/AbhishekVulla/clicky-windows. First commit a4f9df1 pushed (12 files, 17,412 insertions). | a4f9df1 |
| 1 | `capture.py` | ✅ Done | **22/22 pytest unit tests green (0.22s)**. Manual verification on real 2880×1800 @ 200% DPI desktop: monitor `(0, 0) 2880×1800`, resolution `1280×800` (16:10), scale `(2.25, 2.25)`. **Cursor-math verified end-to-end via red-crosshair marker drawn on `debug_capture.jpg`** — user mouse-over-Start-button test confirmed crosshair lands ON the Start icon at the printed coord `(591, 1742)` → image-space `(262, 774)` = `591/2.25, 1742/2.25` pixel-accurate. GetCursorPos + scale-back math validated. 100% runtime-detected. | bf848ad, 30f06c7 |
| 2 | `ai.py` | ✅ Done | **17/17 pytest unit tests green (full suite 39/39 in 1.06s)**. `AIClient` abstract + `AnthropicClient` with Computer Use API beta, verbatim Clicky `ElementLocationDetector.swift` mirror (verified via `gh api`). **Live-API acceptance passed:** loaded `debug_capture.jpg`, `claude-sonnet-4-6` returned pixel-accurate coordinate `(263, 779)` — within 5 pixels of Step 1 verified Start-button ground truth `(262, 774)` on 2880×1800 @ 200% DPI machine. Response text correctly described visible content. Docs: [`docs/superpowers/specs/2026-04-11-ai-design.md`](docs/superpowers/specs/2026-04-11-ai-design.md) + [`docs/superpowers/plans/2026-04-11-ai.md`](docs/superpowers/plans/2026-04-11-ai.md). | TBD |
| 3 | `overlay.py` **(highest risk)** | ⏳ Pending | `python -m overlay` animates through 3 hardcoded coords. User confirms: (a) clicks pass through to underlying apps, (b) pointer visible + smooth, (c) no taskbar entry, (d) no focus stealing | — |
| 4 | `stt.py` | ⏳ Pending | `python -m stt` records 5s, prints transcript matching spoken words, latency <2s | — |
| 5 | `tts.py` | ⏳ Pending | `python -m tts` speaks "Hello, I am Clicky Windows." audibly | — |
| 6 | `hotkey.py` | ⏳ Pending | `python -m hotkey` prints PRESSED/RELEASED on Alt+Space hold. Windows window menu does NOT appear. | — |
| 6.5 | `memory.py` **(differentiator)** | ⏳ Pending | `python -m memory` seeds 3 fake interactions for `EXCEL.EXE`, `recall()` returns chronological entries, `infer_skill_level()` returns correct bucket. `~/.clicky-windows/memory/EXCEL.EXE.md` is human-readable. SQLite index updated. | — |
| 7 | `app.py` **(2nd highest risk)** | ⏳ Pending | Open Excel or any real app, hold Alt+Space, ask "what's on screen and how do I save", release. Within ~7s: pointer animates to Save button, voice describes it. **3 successful runs in a row without crashing.** Recorded as demo video. | — |
| 7.5 | `tools/lint_memory.py` | ⏳ Pending | Seed 10 fake interactions across 2 apps, run lint, verify `insights.md` contains non-trivial summary (not just "you used Excel 5 times") | — |
| 8 | Phase 1 polish | ⏳ Pending | 5+ real user sessions on a real task. `lint_memory.py` run surfaces an insight. README.md written. Demo video recorded. All 4 docs up to date. | — |

**Status legend:** ✅ Done · 🟡 In progress · ⏳ Pending · ❌ Blocked · 🔄 Revised

**Commit discipline:** one commit per step at minimum, conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`, `test:`). Commit SHA recorded in the "Commit" column above.

---

## Phase 1 Acceptance Gate (all 10 must be true before declaring Phase 1 done)

From [PRD.md § Phase 1 Scope + Acceptance Criteria](PRD.md#phase-1-scope--acceptance-criteria):

- [ ] **1. Working loop on real Windows machine.** Press Alt+Space in Excel, ask a question, release, see pointer + hear answer. Works 3× in a row without crashing.
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
