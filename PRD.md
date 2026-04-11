# Clicky Windows — Product Requirements Document

**Status:** Phase 1 in progress
**Last updated:** 2026-04-11
**Owner:** Abhishek Vulla ([Building 0](file:///C:/Users/Abhis/OneDrive/Documents/2nd%20Brain/wiki/building0.md))

This doc answers **what** and **why**. For **how** see [CLAUDE.md](CLAUDE.md). For **where are we now** see [ROADMAP.md](ROADMAP.md). For **why we chose X over Y** see [DECISIONS.md](DECISIONS.md).

---

## Problem Statement

**Non-technical Windows users learning unfamiliar software are stuck reading static help pages while trying to act on them.** The current failure mode: open a new app (Excel, Photoshop, Blender, a game, an accounting package), get lost, Google the problem, read a written tutorial in one window, try to follow along in another window, lose your place, ask ChatGPT, paste screenshots, get generic instructions that don't match what's actually on your screen right now. This loop is the default experience for ~76% of desktop users (Windows' market share) and has no polished solution.

**Evidence this is a real problem, not a speculation:**
- Farza Majeed built [Clicky](https://github.com/farzaa/clicky) (macOS) to learn DaVinci Resolve. 3,500 stars, 609 forks, viral on X/LinkedIn in days.
- Real usage immediately expanded beyond the original "learn software" framing: a mom building her first Lovable app, a dentist debugging software, traders analyzing live charts, designers getting Figma feedback, chess players getting live coaching, Blender learners, Slack-reply writers.
- [Issue #26 on farzaa/clicky](https://github.com/farzaa/clicky/issues/26) — Windows version — 18 comments, the single most-requested feature.
- Two independent Windows port attempts exist: tekram/clicky-windows (Electron, 14 stars, unfinished) and a PhD researcher (Mushtaq Bilal) who vibe-coded his own Windows clone in 2 hours with Claude Code after discovering Clicky was Mac-only.
- [Issue #30 on farzaa/clicky](https://github.com/farzaa/clicky/issues/30) — persistent memory — explicit complaint: "It's a stateless Claude wrapper: no memory between sessions, no tools, no persistent context."

**The structural problem, stated crisply:** AI help is conversational (ChatGPT) or static (help docs). What's missing is AI that **sees what you see**, **talks to you while you act**, and **remembers what you've already learned**. Point-and-explain, adapted to your level, for any software, without setup.

## Target User

**Primary: Non-technical Windows users learning unfamiliar software alone.** A parent wanting to build a first app on Lovable. A small-business owner learning QuickBooks. A student learning Photoshop. Someone trying to figure out a new game's inventory system. Someone debugging why their Excel VLOOKUP isn't working.

**Secondary: Developers wanting an always-on screen-aware assistant.** Code review context, rubber-duck debugging, documentation lookups while reading error messages. This is me (Abhishek) and anyone else building software on Windows.

**Explicitly NOT the target:** Mac users (Clicky exists), enterprise teams ([Littlebird](https://www.producthunt.com/products/littlebird) exists), people doing 24/7 screen recording for replay ([Screenpipe](https://github.com/screenpipe/screenpipe) exists), people who want Claude to autonomously control their computer (Claude Cowork / Grunty exist).

**Why Windows:** 76% of desktop market share. Zero polished screen-aware AI buddies with pointing + voice. The macOS equivalent (Clicky, Clippi) exists and is loved — the demand is proven, the Windows gap is real.

## What Clicky Windows IS

1. **A screen-aware AI buddy you hold a hotkey to talk to.** Press and hold Ctrl+Shift+Space, speak a question, release. Clicky captures your screen, sends it to Claude, and responds with voice while a transparent cursor animates on your screen pointing at the thing you were asking about.
2. **Persistent memory** — one Markdown file per Windows app in `~/.clicky-windows/memory/<app>.md`. Every interaction appended. Next time you open that app, Clicky recalls what you asked last time and adapts ("I see you're back in Photoshop — last time you were working with the pen tool, need more help with that?").
3. **Windows-native.** Multi-monitor. Mixed DPI. Per-monitor v2 DPI awareness. Win32 layered window flags for true click-through (clicks pass through to the app underneath).
4. **Latency-first, feels like a buddy next to you.** The whole UX promise is sub-second perceived response: press Ctrl+Shift+Space, speak, release, and the buddy is already responding before you've even dropped your hand. This drives every Phase 1 stack choice — AssemblyAI `u3-rt-pro` streaming STT with `ForceEndpoint` on hotkey release (~150ms P50 final transcript), Claude Sonnet 4.6 with response streaming, Cartesia Sonic-3 WebSocket TTS (~200ms first audible word), sentence-level TTS chunking overlapping Claude generation. Target end-to-end perceived latency: ~800-1200ms from hotkey release to first spoken word. Privacy is NOT a Phase 1 acceptance criterion — your screenshot + transcript + memory are sent to Anthropic regardless of STT/TTS backend. Phase 2 adds opt-in local subclasses (`FasterWhisperSTT`, `Pyttsx3TTS`) for users who want an offline mode. See [DECISIONS.md § "Priority inversion: latency over local-first" (2026-04-11 session 3)](DECISIONS.md) for the full rationale and why the original "local-first" framing was phantom scope.
5. **Transparent about what it remembers.** You can `cat ~/.clicky-windows/memory/EXCEL.EXE.md` any time and read exactly what Clicky has stored about your Excel interactions. No black-box embeddings, no mystery vector DB. Markdown files a human can audit.

## What Clicky Windows IS NOT

1. **Not a chatbot.** It's push-to-talk with a visual pointer, not a text conversation. If you want text-based Claude, use Claude.ai or Claude Desktop.
2. **Not a screen recorder.** It doesn't record your screen 24/7, doesn't index what you do, doesn't watch you in the background. Push-to-talk only — nothing happens until you press the hotkey.
3. **Not Claude Desktop Cowork.** Cowork is an agent that *controls* your computer autonomously. Clicky points and explains — it doesn't click buttons for you. You stay in control.
4. **Not a coding assistant.** It doesn't auto-complete code, doesn't understand your git state, doesn't compete with Cursor or Copilot. A developer can use it to ask "what does this error message mean?" but it's not a code-completion tool.
5. **Not a meeting assistant.** It doesn't join calls, doesn't transcribe meetings, doesn't compete with GhostDesk.
6. **Not a productivity dashboard.** It doesn't track your time, doesn't index your apps, doesn't generate reports.
7. **Not cloud-based.** No account system, no team features, no SaaS pricing. Runs 100% on your machine with BYOK (bring your own Anthropic API key in `.env`).
8. **Not a Tauri/Rust/Vue app in Phase 1.** Python + PyQt6, like Wallee. Phase 3 may revisit if Python hits a wall, but it's not pre-committed.

## Core Loop

```
1. User holds Ctrl+Shift+Space (push-to-talk) while speaking a question
2. On key release:
   a. AssemblyAI streaming WebSocket receives `ForceEndpoint` message; final transcript arrives ~150ms P50 after release
   b. Screen captured via mss (DPI-aware, monitor under cursor)
   c. Resolution picked from [(1024,768),(1280,800),(1366,768)] by aspect-ratio match
   d. Image resized to exact pixel dims with PIL LANCZOS
   e. Active Windows app detected (GetForegroundWindow + process name)
   f. memory.recall(app_name) reads ~/.clicky-windows/memory/<app>.md, injects into system prompt
3. Claude Sonnet 4.6 call with Computer Use API beta (mirroring Clicky's `ElementLocationDetector.swift` verbatim):
   - User content: image (base64 JPEG) + text (3-line prompt with transcript quoted, instructions co-located with the image — NOT in the SDK `system=` param)
   - Tool: `{"type":"computer_20251124","name":"computer","display_width_px":...,"display_height_px":...}` with declared dimensions matching the resized image exactly
   - Header: `anthropic-beta: computer-use-2025-11-24` (required to activate Computer Use + Claude's specialized pixel-counting training)
   - Phase 2 will inject `memory.recall(app_name)` into the `history` list before the call; Phase 1 uses in-session history only
4. Response parsed (via `ai.py`'s dual-access `parse_tool_use_coordinates` + `parse_response_text` helpers):
   - Text blocks → TTS spoken response
   - `tool_use` blocks with `{"action":"left_click","coordinate":[x,y]}` → pointer targets in declared resolution space
   - Coords clamped to `[0, declared_w-1] × [0, declared_h-1]` before scaling (Claude occasionally returns out-of-bounds values)
5. Coordinate scaling:
   - (declared_resolution) → (physical monitor pixels) via scale factors
   - (physical monitor pixels) → (virtual desktop logical pixels) for the overlay
6. Overlay animates blue arrow from current position to target via QPropertyAnimation (400ms linear)
7. Cartesia Sonic-3 WebSocket streams TTS audio chunks as Claude generates text (sentence-level chunking — flush to TTS on `.`/`!`/`?` boundaries), sounddevice output stream plays them in real time, overlapping pointer animation. First audible word within ~200ms of Claude's first sentence completing.
8. Interaction appended to ~/.clicky-windows/memory/<app>.md
9. SQLite index updated (interaction count, last_seen, first_seen)
```

**End-to-end perceived latency budget: ~800-1200ms from hotkey release to first audible word.** Breakdown: ~150ms AssemblyAI `ForceEndpoint` finalization + ~500-800ms Claude Sonnet 4.6 TTFT + ~200ms Cartesia Sonic-3 TTFB, minus ~300ms sentence-streaming overlap (Claude still generates while TTS plays sentence 1). See [DECISIONS.md § "Priority inversion: latency > local-first"](DECISIONS.md) for the full budget derivation and why the original ≤7s / pyttsx3 / faster-whisper framing was phantom scope.

## Phase 1 Scope + Acceptance Criteria

**Scope:** 12 code files + 4 docs + private GitHub repo. See [ROADMAP.md](ROADMAP.md) for the step-by-step execution order.

**Phase 1 is "done" when all of these are true:**

1. **Working loop on a real Windows machine.** Press Ctrl+Shift+Space in Excel (or any real app), speak a question, release. Within ~7 seconds: pointer animates to the right UI element + voice explains the answer. Works 3 times in a row without crashing.
2. **Multi-monitor + DPI verified.** Tested on at least 2 monitors (ideally with different scaling). Pointer lands within ±5 pixels of the intended target on both monitors.
3. **Memory persists across sessions.** Close the app, reopen it, ask a follow-up question about the same Windows app. Clicky references the previous interaction ("Earlier you asked about the Save button...").
4. **Memory is human-readable.** `~/.clicky-windows/memory/EXCEL.EXE.md` opens as a plain markdown file with clear sections for each interaction. No encoded binary, no opaque schema.
5. **5+ real user sessions on a real task.** Abhishek uses Clicky Windows himself for at least 5 meaningful sessions (e.g., learning Blender, debugging something in VS Code, using an unfamiliar app). Not test sessions — actual usage.
6. **`lint_memory.py` produces meaningful insights.** Running the standalone Karpathy-style health check script scans the memory files and writes `~/.clicky-windows/insights.md` with patterns, common questions, and at least one non-obvious observation — the **unexpected finding** candidate for the B0 case study.
7. **~50-80 pytest unit tests pass.** Coverage: coordinate math (capture.py), API response parsing + clamping (ai.py), memory CRUD + recall (memory.py), hotkey state machine (hotkey.py). Manual verification for overlay, STT audio loop, TTS, and full E2E loop (no automated test for these — no headless mode).
8. **Demo video recorded.** 30-90 second screen recording showing the full loop working on a real task. This is the proof for the B0 enforcement rule ("No task is 'done' without acceptance proof").
9. **All 4 docs up to date.** CLAUDE.md, PRD.md (this file), ROADMAP.md (all steps marked done with acceptance proof), DECISIONS.md (every non-obvious decision logged). README.md written last.
10. **Private GitHub repo with full history.** Conventional commits, one commit per step at minimum. Public release is a Phase 2 decision.

**Phase 1 will NOT have:**
- Proactive mode (Phase 2 — Karpathy: "wait for the data")
- BYOK / OpenRouter (Phase 2 — Computer Use beta is Anthropic-direct only)
- ElevenLabs TTS, AssemblyAI streaming STT (Phase 2 subclasses)
- Clipboard copy, settings UI, tray icon, theme toggle
- Polished bezier pointer animations
- PyInstaller bundle / MSI installer
- Auto-updater, crash reporting, telemetry
- Automated tests for the full screen→AI→overlay→voice loop

## Phase 2 Scope (2-4 weeks, only if Phase 1 validates)

**Goal:** match the B0 bar by rigour proportional to problem. Reference: Wallee (3K LOC Python + 517 tests + 60 replay scenarios + safety-critical architecture).

- **50-100+ pytest unit tests** added across all modules (Phase 1 starts the test discipline, Phase 2 hardens it)
- **Replay scenarios** for the full loop (recorded interactions → assert same output under mocked Anthropic responses)
- **Proactive mode** (idle detection, focused-window capture) — targeted at the specific patterns found in Phase 1's markdown memory files via `lint_memory.py`. Don't guess what to be proactive about; mine it from real usage. *(validated by [danpeg/clicky](https://github.com/danpeg/clicky) 79-star fork)*
- **BYOK / OpenRouter support** (`OpenRouterClient` subclass with vision-tag regex fallback, since OpenRouter can't proxy Computer Use beta) *(Issue [#27](https://github.com/farzaa/clicky/issues/27), PR [#51](https://github.com/farzaa/clicky/pull/51))*
- **ElevenLabs TTS** (`ElevenLabsTTS` subclass of abstract `TTS`) *(PR [#52](https://github.com/farzaa/clicky/pull/52))*
- **AssemblyAI streaming STT** (`AssemblyAISTT` subclass of abstract `STT`) *(PR [#47](https://github.com/farzaa/clicky/pull/47) local-STT validation)*
- **Clipboard copy** of Clicky's responses *(Issue [#43](https://github.com/farzaa/clicky/issues/43), PR [#23](https://github.com/farzaa/clicky/pull/23))*
- **Configurable hotkey UI** *(Issue [#35](https://github.com/farzaa/clicky/issues/35), PR [#16](https://github.com/farzaa/clicky/pull/16))*
- **TTS interruption** — second hotkey press cancels current speech *(Issue [#36](https://github.com/farzaa/clicky/issues/36))*
- **Listening cue overlay** — visible feedback when hotkey pressed *(PR [#58](https://github.com/farzaa/clicky/pull/58))*
- **Hide overlay while typing** — UX polish *(PR [#49](https://github.com/farzaa/clicky/pull/49))*
- **BYOK + settings UI with keyring** — copy Grafyn's pattern wholesale *(DECISIONS.md § "Defer settings UI / keychain-backed BYOK to Phase 2")*
- **Multi-model: Gemini / OpenAI chat providers** *(PR [#40](https://github.com/farzaa/clicky/pull/40))*
- **Security hardening + logging hygiene** *(Issues [#22](https://github.com/farzaa/clicky/issues/22) / [#34](https://github.com/farzaa/clicky/issues/34) / [#44](https://github.com/farzaa/clicky/issues/44), PR [#50](https://github.com/farzaa/clicky/pull/50))*
- **PyInstaller bundle** + clean install path (no antivirus warnings; possibly code-signed)
- **Tray icon** with minimal settings panel
- **Diff-and-skip screenshot caching** (Karpathy-style "do less work" — hash last screenshot, skip Vision API if unchanged)
- **UIA accessibility tree fast path** for productivity apps (Excel, Word, Chrome, File Explorer) — fall back to screenshot for creative apps (Photoshop, Blender). This is where Clicky Windows can actually beat the original Clicky — Mac has no equivalent to Windows UIA.
- **"Unexpected finding" writeup published** — the B0 editorial standard. One essay about what building this revealed. Not a marketing post.
- **5+ real users beyond Abhishek** with documented feedback you can quote

## Phase 3: Tauri Rewrite (NOT pre-committed)

Only triggered if Phase 2 hits a Python-specific wall:
- PyInstaller bundle too fat / slow / triggers too many antivirus false positives
- GIL contention causes user-visible latency that can't be optimized away
- PyQt6 overlay reliability issues across Win10/11 + GPU driver combinations
- Sharing with non-technical users surfaces "I can't install Python" pain that PyInstaller can't solve

If triggered: port to Tauri 1.8 + Rust backend + Vue 3 frontend + Pinia, matching Grafyn's architecture shape. See the Phase 2 checklist in `streamed-tumbling-sunbeam.md` (the frozen strategic plan) for the specific Grafyn parity items if we get here.

**Most likely: never needed.** The honest bet is Python + hardening clears the B0 bar like Wallee did.

## Competitor Landscape

| Competitor | Platform | Points at screen? | Voice? | Memory? | Open source? | Price | Our edge |
|---|---|---|---|---|---|---|---|
| **Clicky** (farzaa/clicky) | macOS only | Yes | Yes | No | Yes, MIT | Free | Windows + memory |
| **Clippi.us** | macOS only, "Windows soon" | Yes | Yes | No | No | Free | Windows + memory + open source + shipped |
| **GhostDesk** | Windows | No | Voice out only | No | No | $9.99/mo | Points, free, memory |
| **Screenpipe** | Win + Mac | No | No | Records everything | Yes | Free | Interactive push-to-talk, not passive recording |
| **tekram/clicky-windows** | Windows (Electron) | Partial | Yes | No | Yes | Free | Memory, polished, shipped (tekram has 14 stars and unfinished PLAN docs) |
| **danpeg/clicky** (fork) | macOS | Yes, proactive | Yes | No | Yes | Free | Windows. (Copy their proactive-mode idea in Phase 2, targeted at real memory patterns.) |
| **Claude Cowork / Grunty** | Cross-platform | N/A (controls, doesn't point) | No | No | Grunty yes | Varies | Different category — we guide, they act |
| **Microsoft Copilot Vision** | Win 11 | No | No | No | No | Bundled | Points, voice, memory |
| **Littlebird** | Cross-platform | No | No | Yes | No | Enterprise, $11M raised | Consumer, free, visual pointer |
| **Precogni** | macOS alpha | No | Limited | Privacy-focused | No | Alpha | Windows, ships sooner |
| **Vercept** (acquired by Anthropic Feb 2026) | ??? | Unknown | Unknown | Unknown | No | Not yet shipped | **STRATEGIC THREAT** — Anthropic will ship first-party Windows screen-aware AI. Phase 1 must ship before they do. Memory is the long-term moat. |

**Key insight from competitive analysis:** nobody has shipped *Windows + pointing + voice + memory* as a single product. tekram has Windows + pointing + voice but no memory and is unfinished. danpeg has macOS + pointing + voice + proactive but no memory. Clicky/Clippi have macOS + pointing + voice but no memory. **The combination is open.**

## Validated User Demands (sourced from Clicky GitHub issues + forks + social)

1. **Windows version** — #1 request on farzaa/clicky. [Issue #26](https://github.com/farzaa/clicky/issues/26), 18 comments. Two independent forks attempting it.
2. **Persistent memory** — #2 request. [Issue #30](https://github.com/farzaa/clicky/issues/30). Explicit quote: "It's a stateless Claude wrapper: no memory between sessions, no tools, no persistent context."
3. **Proactive mode** — validated by viral fork. [danpeg/clicky](https://github.com/danpeg/clicky) got 79 stars in 3 days without marketing by adding idle-detection + focused-window capture. Phase 2.
4. **BYOK / multi-model** — [Issue #27](https://github.com/farzaa/clicky/issues/27), 5 comments. OpenRouter, local models, own API keys. Phase 2.
5. **Clipboard copy** — [Issue #43](https://github.com/farzaa/clicky/issues/43). Can't paste responses (e.g., when Clicky returns code). Phase 2.
6. **Configurable hotkey** — [Issue #35](https://github.com/farzaa/clicky/issues/35). 3-finger combo is awkward. Phase 2 (Phase 1 ships with `config.py` override).
7. **Security** — [Issues #22/#34/#44](https://github.com/farzaa/clicky/issues). No shared proxy, no baked-in API keys, no credential leaks. Phase 1 compliant (`.env` only, Anthropic-direct, no proxy).

### Upstream Snapshot (2026-04-11)

Full pull of `farzaa/clicky` open issues + all PRs via `gh api` on this date. This table is the Phase 2 shopping list — every bullet in the Phase 2 Scope section below should trace back to one of these rows. If Farza closes/deletes/renames issues later, re-pull via `gh issue list --repo farzaa/clicky --state all` and `gh pr list --repo farzaa/clicky --state all`.

**Core loop / accuracy:**

| Upstream | Demand | Clicky status | Our response |
|---|---|---|---|
| [#24](https://github.com/farzaa/clicky/issues/24) | "not working on multiple monitors" | Open bug in upstream | **Phase 1 already handles.** Step 1 `capture.py` uses `SetProcessDpiAwareness(2)` + `mss.monitors` enumeration + cursor-based monitor selection, verified via the Step 1 crosshair test. |
| [#37](https://github.com/farzaa/clicky/issues/37) | "Make the cursor vanish" (from screenshots) | Open — screenshot APIs can't capture the OS cursor layer | **Inherited limitation.** We document it in README at end of Phase 1; not fixable at the screenshot-API level without drawing a decoy marker. |
| [PR #48](https://github.com/farzaa/clicky/pull/48) | "Fix cursor overlay invisible on secondary monitors" | Open upstream bug | **Phase 1 Step 3 risk.** Our `overlay.py` must span full virtual desktop to avoid this; we fail the Step 3 verification gate if pointer can't render on all monitors. |

**Memory / differentiator:**

| Upstream | Demand | Clicky status | Our response |
|---|---|---|---|
| [#30](https://github.com/farzaa/clicky/issues/30) | "Stateless Claude wrapper: no memory between sessions" | Open, no plans from upstream | **Phase 1 differentiator.** `memory.py` (Step 6.5) — Karpathy markdown + SQLite index. See [DECISIONS.md § "Persistent memory is IN Phase 1"](DECISIONS.md). |
| [OpenClaw Gateway mention in #30](https://github.com/farzaa/clicky/issues/30) | Memory, tools, multi-model backend | Open | Validates our markdown-memory direction but we don't depend on OpenClaw; we own our storage. |

**BYOK / multi-model:**

| Upstream | Demand | Clicky status | Our response |
|---|---|---|---|
| [#27](https://github.com/farzaa/clicky/issues/27) | "Running out of credit — how can I use my own Codex or Claude API key?" | Open | **Phase 2 BYOK work.** Spec already locked in [DECISIONS.md § "Defer settings UI / keychain-backed BYOK to Phase 2"](DECISIONS.md): copy Grafyn's `keyring` + `platformdirs` + QInputDialog pattern. |
| [PR #51](https://github.com/farzaa/clicky/pull/51) | "Add OpenRouter provider support" | Open, unmerged | **Phase 2 target.** Exactly what our `AIClient` provider abstraction enables — `OpenRouterClient` subclass with vision-tag regex fallback (since OpenRouter can't proxy Computer Use beta). |
| [PR #47](https://github.com/farzaa/clicky/pull/47) | "local STT via Parakeet WebSocket server" | Closed, unmerged | **Validates local-STT demand.** Phase 1 uses AssemblyAI `u3-rt-pro` streaming (latency-first). Phase 2 adds `FasterWhisperSTT` + `ParakeetSTT` subclasses for users who want offline mode.
| [PR #52](https://github.com/farzaa/clicky/pull/52) | "Migrate transcription from AssemblyAI to ElevenLabs Scribe v2 Realtime" | Closed, unmerged | **Phase 2 STT upgrade target.** `ElevenLabsScribeSTT(STT)` subclass. |
| [PR #40](https://github.com/farzaa/clicky/pull/40) | "Add Gemini and OpenAI chat providers" | Open, unmerged | **Phase 2 multi-model.** Another `AIClient` subclass direction. |
| [PR #39, #41, #42](https://github.com/farzaa/clicky/pulls?q=LM+Studio) | "LM Studio / MLX local inference" | Closed/open, unmerged | **Phase 2 local-inference exploration, maybe never.** Computer Use beta won't work with local models, so these'd have to use the vision-tag fallback path. |
| [PR #31](https://github.com/farzaa/clicky/pull/31) | "Local OpenRouter ElevenLabs settings" | Closed, unmerged | Phase 2 multi-provider settings UI direction. |

**Reliability / UX:**

| Upstream | Demand | Clicky status | Our response |
|---|---|---|---|
| [#35](https://github.com/farzaa/clicky/issues/35) | "Talking to Clicky requires 3 fingers" | Open | **Phase 1 Ctrl+Shift+Space decision validated.** [DECISIONS.md § "Ctrl+Shift+Space over Ctrl+Space"](DECISIONS.md). |
| [#36](https://github.com/farzaa/clicky/issues/36) | "It doesn't stop once it starts speaking" | Open | **Phase 2 TTS interruption** — second hotkey press cancels the current Cartesia WebSocket stream (`tts.stop()` API wired in Phase 1, activated in Phase 2). |
| [#38](https://github.com/farzaa/clicky/issues/38) | "If it can't type on my behalf what's the USP?" | Open | **Stays out of scope.** We guide + explain, not act. [PRD § "What Clicky Windows IS NOT"](PRD.md#what-clicky-windows-is-not) — we are explicitly not Claude Computer Use / Cowork. |
| [#7](https://github.com/farzaa/clicky/issues/7) | "Non-English languages, context retention, audio" | Open | Multi-language is Phase 3 (Whisper supports it, prompt engineering needed). **Context retention IS our Phase 1 memory differentiator.** |
| [PR #49](https://github.com/farzaa/clicky/pull/49) | "Hide cursor overlay while user is typing" | Open, unmerged | **Phase 2 overlay UX polish.** |
| [PR #58](https://github.com/farzaa/clicky/pull/58) | "focus-rectangle drawing on push-to-talk" | Closed, unmerged | **Phase 2 listening cue.** Visible feedback the instant the hotkey is pressed. |
| [PR #23](https://github.com/farzaa/clicky/pull/23) | "auto-copy response to clipboard" | Open, unmerged | **Phase 2, links to [#43](https://github.com/farzaa/clicky/issues/43).** |
| [PR #45](https://github.com/farzaa/clicky/pull/45) | "Practice mode" | Closed, unmerged | **Phase 3** (quiz-style learning mode). |
| [PR #18](https://github.com/farzaa/clicky/pull/18) | "TFT coaching" | Closed, unmerged | **Out of scope** — game-specific, not a general product direction. |

**Hotkey / config:**

| Upstream | Demand | Clicky status | Our response |
|---|---|---|---|
| [#1](https://github.com/farzaa/clicky/issues/1) | "What is the hotkey combo?" | Open — docs gap | **Documented in README** (written at end of Phase 1). Default Ctrl+Shift+Space via `pynput.Listener(suppress=False)` (observe-only). Alt+Space was rejected for Phase 1 because pynput's `suppress=True` is globally destructive — see [DECISIONS.md 2026-04-12](DECISIONS.md). Phase 1.5 may add a Win32 `RegisterHotKey` subclass restoring Alt+Space. |
| [PR #16](https://github.com/farzaa/clicky/pull/16) | "Allow user to configure push-to-talk shortcut from panel" | Open, unmerged | **Phase 2** after settings UI exists. |

**Security:**

| Upstream | Demand | Clicky status | Our response |
|---|---|---|---|
| [#22](https://github.com/farzaa/clicky/issues/22) | "Anthropic API key committed" | Open | **Phase 1 compliant.** `.env` only, hardened `.gitignore`, rotate after Phase 1. |
| [#34](https://github.com/farzaa/clicky/issues/34), [#44](https://github.com/farzaa/clicky/issues/44) | Security/privacy audit notes | Open | **Phase 2 security review pass.** |
| [PR #50](https://github.com/farzaa/clicky/pull/50) | "Remove sensitive data from debug logs" | Open, unmerged | **Phase 2 logging hygiene.** |
| [PR #15](https://github.com/farzaa/clicky/pull/15) | "Harden Cloudflare Worker" | Open, unmerged | **Not applicable.** We don't run a proxy — Phase 1 and 2 are Anthropic-direct + `.env`. |

**Platform ports:**

| Upstream | Demand | Clicky status | Our response |
|---|---|---|---|
| [#21](https://github.com/farzaa/clicky/issues/21), [#26](https://github.com/farzaa/clicky/issues/26) | "Add Windows version" | Open — #1 demand | **Clicky Windows = this project.** |
| [#19](https://github.com/farzaa/clicky/issues/19) | "I don't have a mac" | Open | Same as above. |
| [#13](https://github.com/farzaa/clicky/issues/13) | "Debian/Linux support" | Open | **Phase 3 maybe.** Qt is cross-platform but pynput hotkey suppression + Windows-specific DPI code would need Linux equivalents. |
| [PR #54](https://github.com/farzaa/clicky/pull/54), [PR #53](https://github.com/farzaa/clicky/pull/53) | Competing "Add Windows port" PRs | Closed, unmerged — Farza isn't accepting them | **Validates open lane.** No upstream Windows port is getting merged, so the polished persistent-memory Windows version (us) has no upstream competition. |

**Clicky bugs that are NOT our problem:**

| Upstream | Demand | Clicky status | Our response |
|---|---|---|---|
| [#12](https://github.com/farzaa/clicky/issues/12) | "Can't open settings — empty screen" | Upstream SwiftUI bug | Doesn't apply — we have no settings panel in Phase 1, Phase 2 will use PyQt6. |
| [PR #29](https://github.com/farzaa/clicky/pull/29) | "Replace Cloudflare Worker with local Claude Agent SDK" | Open, unmerged | Doesn't apply — we're already Anthropic-direct, no Worker. |

**The `[POINT]` tag question (resolved here for completeness):** zero issues or PRs in `farzaa/clicky` mention `[POINT]` tags. The pattern exists only as a hypothetical OpenRouter-compatibility fallback I considered during brainstorming and deferred to Phase 2. See [DECISIONS.md § "Use Claude Computer Use API beta directly, not vision-tag regex fallback"](DECISIONS.md) and `docs/superpowers/specs/2026-04-11-ai-design.md`.

## Risks

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| 1 | PyQt6 click-through unreliable on Win11 with certain GPU drivers | High | High (no overlay = no demo) | Apply Win32 layered window flags via ctypes after `show()`. Fallback: tkinter + `-transparentcolor` + pywin32. Document in DECISIONS.md which approach won on test machine. |
| 2 | Per-monitor DPI math wrong on mixed-DPI setups | Very High | High (pointer lands in wrong place) | `SetProcessDpiAwareness(2)` at startup. Document all 3 coordinate spaces (physical, logical, Claude) in code comments. Step 1 acceptance: mouse over known UI element, verify printed coords ±2 px. |
| 3 | Ctrl+Shift+Space hotkey conflicts (VS Code Parameter Hints, rare third-party mappers, IME) | Low | Low (minor UX nit, no crash) | `pynput.Listener(suppress=False)` observe-only — we do NOT consume key events, so conflicts degrade to "app underneath also sees the key" rather than "app underneath loses the key." Ctrl+Shift+Space has no default Windows OS behavior, so no menu pops. Phase 2 configurable hotkey UI (PR #16) lets users rebind if the VS Code Parameter Hints overlap becomes annoying. See [DECISIONS.md 2026-04-12 "Ctrl+Shift+Space over Alt+Space"](DECISIONS.md). |
| 4 | AssemblyAI / Cartesia network unreachable (no internet, firewall block, provider outage) | Medium | High | Clear `RuntimeError` with diagnostic instructions at streaming client construction. Reactive fallback to `FasterWhisperSTT` + `Pyttsx3TTS` is a 1-2 hour Phase 2 subclass swap via the abstract base pattern. No preemptive Phase 1 fallback (YAGNI). |
| 5 | Threading deadlocks (PyQt main loop + pynput thread + audio + Whisper + Anthropic workers) | High | High (silent freeze) | Single strict rule: only Qt signals cross thread boundaries. No UI calls from worker threads. Code review every cross-thread call. |
| 6 | End-to-end latency > 8s feels broken | Medium | Medium (UX perception) | Print per-stage timing during dev. Pre-warm Whisper + audio. Optional: add audible "listening" cue on hotkey press so user gets immediate feedback. |
| 7 | Overlay appears in screenshots sent to Claude | High | High (Claude tries to point at its own pointer) | Always `hide_for_capture()` before `mss.grab()`. Re-show after response. Small timing window — verify in Step 3. |
| 8 | Computer Use API token costs spiral during testing | Low | Low | Print token counts after every call. Use `claude-haiku-4-5-20251001` for prompt engineering iteration, swap to Sonnet 4.6 for real tests. Budget cap in `config.py`. |
| 9 | Anthropic ships first-party Windows screen-aware AI (Vercept) while we're building | Strategic | Strategic | Ship Phase 1 in 1-2 weeks, not months. The memory differentiator is the moat — generic "AI sees your screen" will be commoditized. |
| 10 | The "unexpected finding" never materializes and there's no B0 case study angle | Medium | Medium (Phase 2 loses the editorial anchor) | `lint_memory.py` is the explicit lens for finding it. If 5 real sessions don't produce one, that itself is a finding ("screen-aware memory is less differentiating than expected in practice — here's why"). |
| 11 | The plan underestimates real engineering time | Medium | Medium | Accept the budget: 1-2 weeks for MVP, 2-4 weeks for hardening. Grafyn took 63 days for v0.1.8. Don't beat yourself up if Phase 1 extends to 3 weeks. |
| 12 | Claude Code makes architectural mistakes that need rework | Medium | Medium | Superpowers brainstorming HARD-GATE forces design approval before code is written. Per-component user verification gate. Worst case: one component gets rewritten. |

## Success Metrics (what makes Phase 1 a "success")

**Minimum viable:** all 10 Phase 1 acceptance criteria above met. Working loop, 5 real sessions, demo video, docs, tests, private repo.

**Good:** the above + the `insights.md` surfaces a non-obvious pattern + Abhishek actually uses it organically for a week on a real task (not a demo).

**Great:** the above + the insight turns into a shareable writeup (Twitter thread, LinkedIn post, or B0 case study draft) + at least one outside person has tried it and given feedback.

**Moonshot:** the above + a second non-technical user (someone in Abhishek's family or from SUTD) uses it independently without hand-holding and comes back with an observation Abhishek didn't expect.

## Out of Scope (explicitly rejected)

Things that have been proposed and rejected with reasons recorded in DECISIONS.md:

- **Tauri rewrite in Phase 2.** Rejected because Wallee proves Python at 3K LOC clears the B0 bar. Language is not the disqualifier; rigour is. (DECISION: "Why Python through Phase 2")
- **Electron port.** Rejected because tekram already tried it and it's unfinished. Electron buys nothing Python doesn't give us, loses binary size advantage. (DECISION: "Why not Electron")
- **Screenshot to Vision only, no Computer Use API.** Rejected because original Clicky proves Computer Use is meaningfully more accurate. (DECISION: "Use Computer Use API beta directly")
- **SQLite-only memory, no markdown.** Rejected because Karpathy's principle of "human-readable, LLM-maintained" beats opaque schemas for a differentiator we need to explain to users. (DECISION: "Karpathy markdown memory + SQLite index hybrid")
- **Ctrl+Space hotkey.** Rejected because it conflicts with VS Code IntelliSense which would break developer users' autocomplete. (DECISION: "Ctrl+Shift+Space over Ctrl+Space")
- **Pure `openai-whisper`.** Rejected in favor of `faster-whisper` (CTranslate2 backend, 4× faster, drop-in replacement). (DECISION: "faster-whisper over openai-whisper")
- **One giant execution plan upfront.** Rejected in favor of Superpowers per-component brainstorm → plan → TDD for the 5 hard components, skipping ceremony for the 4 trivial files. (DECISION: "Superpowers selective ceremony")
- **Proactive mode in Phase 1.** Rejected per Karpathy: you don't know what to be proactive ABOUT until you have data. Build memory first, mine the patterns, then target proactive mode at the real patterns in Phase 2. (DECISION: "Proactive mode stays in Phase 2")
- **User-scope Superpowers install.** Rejected in favor of local scope. Isolated to this project, fully reversible, no cross-project bugs, solo dev doesn't need the "shared with collaborators" behavior. (DECISION: "Superpowers local scope")

See [DECISIONS.md](DECISIONS.md) for full rationale on each.
