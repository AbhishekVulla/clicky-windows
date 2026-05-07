# Clicky Windows — Product Requirements Document

**Status:** Phase 1 + Phase 1.5 + parallel-capture refactor + **Sprints 0–4 + Sprint 4 ship-gate refinements all SHIPPED + USER-verified at 258/258 tests (was 178 → 182 → 196 → 215 → 216 → 219 → 223 → 255 → 258).** Sprint 4 added the 3-category dropdown settings UX (LLM Anthropic / STT AssemblyAI / TTS Cartesia or ElevenLabs) + ElevenLabsTTS opt-in subclass mirroring Cartesia Option B prefetch+playback architecture with 3 deliberate divergences (iterator-direct streaming, int16→float32 inline conversion, 5-pronged stop) + `tts.create_tts_client(provider, key)` factory + `config.resolve_setting` env→keyring sibling for non-secret config. Ship-gate refinements added first-audible-word debug log via one-shot `tts.arm_first_chunk_callback` armed by `app.py:_pipeline_worker` per interaction + plain-English privacy line ("Nothing leaves your machine." replaces "No server, no telemetry."). **87 MB `Clicky-Windows-Setup-v0.1.0.exe`** at `installer/Output/`. **Next: Sprint 4.7 doc-sync (in flight) → Sprint 5/6 README + CI + repo metadata + SignPath + public flip.**
**Last updated:** 2026-05-07
**Owner:** Abhishek Vulla ([Building 0](file:///C:/Users/Abhis/OneDrive/Documents/2nd%20Brain/wiki/building0.md))

This doc answers **what** and **why** + the full **Codebase Architecture** + **User Journeys** + **Invariants** (the single source of truth against post-compact drift). For **how** see [CLAUDE.md](CLAUDE.md). For **where are we now** see [ROADMAP.md](ROADMAP.md). For **why we chose X over Y** see [DECISIONS.md](DECISIONS.md).

---

## Problem Statement

**Non-technical Windows users learning unfamiliar software are stuck reading static help pages while trying to act on them.** Farza Majeed, creator of the macOS original Clicky that inspired this project, put the user mentality in [his LinkedIn demo post](https://www.linkedin.com/posts/farza-majeed-76685612a_i-built-this-thing-called-clicky-its-an-ugcPost-7447137596067188737-7zJK): *"I've been procrastinating learning for a long time because I don't want to watch 1 hour YouTube video about it — I just want to learn by doing."* The default failure mode: open a new app (Excel, Photoshop, Blender, a game, an accounting package), get lost, Google the problem, read a tutorial in one window, try to follow along in another, lose your place, ask ChatGPT, paste screenshots, get generic instructions that don't match your actual screen. This loop is the default experience for ~76% of desktop users (Windows market share) and has no polished solution.

**Evidence this is a real problem:**
- Farza Majeed built [Clicky](https://github.com/farzaa/clicky) to learn DaVinci Resolve. **4,759 stars (refreshed 2026-04-26, +36% in 2 weeks)**, 852 forks, viral on X/LinkedIn in days.
- Real usage expanded beyond "learn software": a mom building her first Lovable app, a dentist debugging software, traders on live charts, designers getting Figma feedback, chess players getting live coaching.
- [Issue #26 on farzaa/clicky](https://github.com/farzaa/clicky/issues/26) — Windows version — 18 comments, the #1 most-requested feature. **Refreshed 2026-04-26:** four independent Windows port attempts now exist — tekram/clicky-windows (Electron, 38 stars, ACTIVE with installer), JaySmith502/clicky-win (Python, 4 stars, architectural twin to ours), plus two unmerged upstream PRs (#54 sementerleen, #71 PsychoSatsujin). **Farza has 0 comments on Issue #26 ever; he's running Chasi (YC W26) — community-built ports, not official.** Natique Ibrar Alam's *"Day 0 of asking if this is available on Windows"* comment on Farza's LinkedIn remains first-party evidence of the unfulfilled demand.
- **Persistent memory demand** — original [Issue #30](https://github.com/farzaa/clicky/issues/30) titled "stateless Claude wrapper: no memory between sessions" was repurposed/hijacked by 2026-04 into an unrelated "OpenClaw Gateway backend" thread. Stronger 2026 evidence: Karpathy's LLM Wiki tweet went viral early April 2026 (VentureBeat coverage); MemPalace launched 2026-04-05, hit 47K GitHub stars in 2 weeks. **Memory is no longer a niche pattern — it's industry-default. The differentiator is the *trifecta* (voice PTT + visual pointer + persistent per-app auto-memory + Windows-native), not memory alone.**

**The structural problem:** AI help is conversational (ChatGPT) or static (help docs). What's missing is AI that **sees what you see**, **talks to you while you act**, and **remembers what you've already learned**. Point-and-explain, for any software, without setup.

## Target User

**Primary:** Non-technical Windows users learning unfamiliar software alone. A parent building a first Lovable app. A small-business owner learning QuickBooks. A student learning Photoshop. Someone debugging an Excel VLOOKUP.

**Secondary:** Developers wanting an always-on screen-aware assistant for rubber-duck debugging and documentation lookups — this is Abhishek (Phase 1 tester) and anyone else building software on Windows.

**Explicitly NOT the target:** Mac users (Clicky exists), enterprise teams ([Littlebird](https://www.producthunt.com/products/littlebird) exists), 24/7 screen-recording replayers ([Screenpipe](https://github.com/screenpipe/screenpipe) exists), people who want Claude to autonomously control their computer (Claude Cowork / Grunty exist).

**Why Windows:** 76% of desktop market share. Zero polished screen-aware AI buddies with pointing + voice. The macOS equivalent (Clicky, Clippi) exists and is loved — the demand is proven, the Windows gap is real.

## What Clicky Windows IS

1. **An AI teacher that lives as a buddy next to your cursor** (design inspired by [farzaa/clicky](https://github.com/farzaa/clicky)'s macOS original). Press and hold Ctrl+Alt+Space, speak a question, release. Clicky captures your screen(s), asks Claude, and responds with voice while a transparent blue cursor overlay animates to the exact UI element you should click next. **You click it yourself — Clicky never takes control of your mouse.** The "learn by doing" UX promise: point and explain, you act, conversational back-and-forth, build skill through use rather than tutorial-watching.
2. **Persistent memory** — one Markdown file per Windows app at `~/.clicky-windows/memory/<app>.md`. Every interaction appended. Next time you open that app, Clicky recalls what you asked last time and adapts ("I see you're back in Photoshop — last time you were working with the pen tool, need more help with that?").
3. **Windows-native.** Multi-monitor. Mixed DPI. Per-monitor v2 DPI awareness. Win32 layered-window flags for true click-through (clicks pass through to the app underneath).
4. **Latency-first, feels like a buddy next to you.** Target end-to-end perceived latency: **~800-1200ms** from hotkey release to first spoken word (~150ms AssemblyAI `ForceEndpoint` + ~500-800ms Claude TTFT + ~200ms Cartesia TTFB − ~300ms sentence-streaming overlap). The UX promise is sub-second perceived response; every stack choice is driven by this. Privacy is NOT a Phase 1 acceptance criterion — screenshots + transcripts + memory are sent to Anthropic + AssemblyAI + Cartesia regardless. Phase 2 adds opt-in local subclasses (FasterWhisperSTT, Pyttsx3TTS) for users who want offline mode. See [DECISIONS.md § "Priority inversion: latency over local-first" (2026-04-11 session 3)](DECISIONS.md).
5. **Transparent about what it remembers.** You can `cat ~/.clicky-windows/memory/EXCEL.EXE.md` any time and read exactly what Clicky has stored about your Excel interactions. No embeddings, no vector DB, no mystery schemas. Markdown files a human can audit.
6. **BYOK from day 1.** Phase 1 reads Anthropic + AssemblyAI + Cartesia API keys from `.env`. No Cloudflare Worker proxy (unlike Clicky's production, which holds keys server-side). Solves upstream Clicky issues #22/#27/#32/#33 that Farza hasn't shipped.

## What Clicky Windows IS NOT

1. **Not a chatbot.** Push-to-talk with visual pointer, not text conversation. For text-based Claude, use Claude.ai or Claude Desktop.
2. **Not a screen recorder.** Doesn't record 24/7, doesn't index what you do, doesn't watch in the background. Push-to-talk only.
3. **Not Claude Desktop Cowork.** Cowork autonomously controls your computer. Clicky points and explains — doesn't click for you.
4. **Not a coding assistant.** Doesn't auto-complete, doesn't understand git state, doesn't compete with Cursor or Copilot. A dev can ask "what does this error mean?" but it's not a code-completion tool.
5. **Not a meeting assistant.** Doesn't join calls, doesn't transcribe meetings.
6. **Not a productivity dashboard.** Doesn't track time, doesn't generate reports.
7. **Not cloud-based.** No account system, no SaaS pricing. Runs 100% on your machine with BYOK.
8. **Not a Tauri/Rust/Vue app in Phase 1.** Python + PyQt6, Wallee-style. Phase 3 may revisit if Python hits a wall.
9. **Not an autonomous agent. Clicky never takes control of your mouse or keyboard.** We draw a transparent blue cursor overlay pointing at the UI element Claude identifies — we never call `pyautogui.click()` or simulate a keystroke. The user always physically clicks themselves. **This is a hard product boundary.** If you want autonomous computer control, use Claude Cowork or Grunty — different tools for a different job.

## Core Loop

```
 1. User holds Ctrl+Alt+Space → hotkey.py observes via pynput.Listener(suppress=False)
    → stt.py opens AssemblyAI streaming WebSocket + sounddevice mic input

 2. User releases:
    a. stt.stop() sends ForceEndpoint, awaits final transcript (~150ms P50, 2s ceiling)
    b. overlay.hide_for_capture() — so Claude never sees our own blue cursor
    c. capture.capture_all_screens() → list[LabeledCapture] sorted cursor-first,
       each labeled with "primary focus" marker + pixel dimensions
    d. memory.recall(app_name) reads the tail of ~/.clicky-windows/memory/<app>.md

 3. ai.ask_stream(labeled_images, transcript, history,
                   system_prompt=_CLICKY_SYSTEM_PROMPT, max_tokens=1024)
    — plain vision messages.stream() — GA Anthropic API, NOT Computer Use beta
    — 35-line system prompt ported from Clicky's companionVoiceResponseSystemPrompt
    — Claude embeds [POINT:x,y:label(:screenN)?] at end of spoken response
    — Returns a streaming context manager

 4. Progressive text_deltas flow to sentence splitter → tts.speak_sentence()
    while Claude still generates later sentences (300-500ms perceived latency win —
    Clicky has the streaming infrastructure but uses onTextChunk:{_in} empty callback)

 5. Stream closes → parse_point_tag() regex extracts coordinate from accumulated text,
    strips the tag from spoken_text (TTS never reads "POINT colon 640 comma 400")

 6. overlay.show_after_capture() → overlay.point_at() routes (via screen_number
    or cursor-screen fallback) to the correct per-monitor OverlayWindow →
    blue cursor polygon animates to target (tip anchored at pointer_pos via QPropertyAnimation)

 7. memory.record() appends interaction (app, window title, transcript,
    stripped response, [(x, y)]) to ~/.clicky-windows/memory/<app>.md + SQLite index
```

**End-to-end perceived latency budget: ~800-1200ms** from hotkey release to first audible word (aspirational target).

**Measured post-Path-A latency** (2026-04-19/20 debug logs, N=4 sessions across Antigravity + Chrome):
- **Multi-sentence Claude responses** (sentence streaming fires mid-stream): ~1500ms first-audible-word, 150-250ms gaps between sentences. **Net-positive ~3500ms perceived-latency win vs batch.**
- **Single-sentence Claude responses** (no flush boundary hit): ~4000-5000ms first-audible-word (same as batch — sentence streaming fallback to tail-flush).
- **Stage breakdown**: 300-700ms STT finalize (post-force_endpoint wait) + ~0ms capture+memory (reused from press-time thread) + 700-1200ms Claude TTFT (with prompt caching) + 150-250ms Cartesia TTFB per sentence.

See DECISIONS.md entries for [2026-04-11 session 3 (budget derivation)](DECISIONS.md), [2026-04-19/20 late-evening (Path A measured results + state-machine completion)](DECISIONS.md), and [2026-04-20 Option B (HTTP double-buffer) + Option 2 (grace shrink)](DECISIONS.md). **Inter-sentence gaps eliminated 2026-04-20 via Option B (commit `4291401`).** First-sentence TTFB (~300ms for single-sentence or first of multi-sentence) remains — only fixable via Option C WebSocket TTS (deferred until post-installer user testing).

**Visual state machine** (verbatim port of Farza's shipping Clicky, 2026-04-19/20):
- IDLE → cursor polygon only
- LISTENING (PTT held) → WaveformWidget (5-bar RMS meter, replaces cursor)
- THINKING (release → Claude coord) → SpinnerWidget (rotating blue arc, replaces cursor)
- FLYING (coord → arrival) → cursor polygon on quadratic bezier arc
- SPEAKING (TTS playing) → cursor polygon at rest

Exactly one visual per state; waveform + spinner follow cursor at 60Hz via `OverlayController._on_follow_tick`.

**Note on the Claude call:** `ai.py` was originally a verbatim port of Clicky's `ElementLocationDetector.swift` using Computer Use API beta (`computer_20251124` tool, `anthropic-beta: computer-use-2025-11-24` header). During Step 7 brainstorming (2026-04-12 evening 3), research-pass verification discovered that file is **dead code** — zero references across all 11 non-test Clicky Swift files (grep-verified via `gh api`). Clicky's actual shipping path is `ClaudeAPI.analyzeImageStreaming` + `CompanionManager.parsePointingCoordinates` (vision-tag regex). **Refactor completed in commit `425d51e`** (plain vision `messages.stream()` + `_CLICKY_SYSTEM_PROMPT` + `[POINT:x,y:label]` regex). See [DECISIONS.md 2026-04-12 (evening 3) "ai.py refactor"](DECISIONS.md).

---

## Codebase Architecture

**Module map** — each Phase 1 Python file mapped to its role, public API, I/O, threading model, and dependencies. This is the single source of truth against post-compact drift; if PRD describes X and code does Y, the code is authoritative and PRD is stale — update PRD.

| File | Role | Public API | Inputs | Outputs | Threading | Depends on |
|---|---|---|---|---|---|---|
| **`config.py`** | Env loading + constants | `ANTHROPIC_API_KEY`, `ASSEMBLYAI_API_KEY`, `CARTESIA_API_KEY`, `MODEL_ID`, `HOTKEY`, `CANDIDATE_RESOLUTIONS`, `ASSEMBLYAI_SPEECH_MODEL`, `ASSEMBLYAI_STREAMING_URL`, `AUDIO_SAMPLE_RATE`, `AUDIO_CHUNK_FRAMES`, `CARTESIA_MODEL_ID`, `CARTESIA_VOICE_ID`, `CARTESIA_OUTPUT_SAMPLE_RATE`, `MEMORY_DIR`, `INDEX_DB_PATH`, `INSIGHTS_PATH`, `MEMORY_RECALL_MAX_CHARS`, `POINTER_ANIMATION_MS`, `E2E_LATENCY_BUDGET_S` | `.env` file via `python-dotenv` | Module-level constants | Main thread (import-time only) | `python-dotenv` |
| **`capture.py`** | Screen capture + DPI + aspect-ratio resize + multi-screen labels | `capture_active_screen() → CaptureResult`, `capture_all_screens() → list[LabeledCapture]` (post-refactor), `unscale_claude_coords()`, `set_dpi_awareness()`, `get_cursor_position()`, `list_monitors()`, `monitor_containing()`, `pick_resolution()`, `resize_for_claude()` | Cursor position (GetCursorPos ctypes), monitor list (mss) | PIL Images + metadata (labels, pixel dims, scale factors) | Main thread (ctypes Win32 calls) | `mss`, `Pillow`, `ctypes` |
| **`ai.py`** | `AIClient` abstract + `AnthropicClient` concrete | `AnthropicClient.ask()` batch wrapper, `AnthropicClient.ask_stream() → _StreamingAnthropicResponse` (post-refactor), `parse_point_tag() → PointParseResult` (post-refactor), `parse_response_text()`, `image_to_base64_jpeg()` | `list[LabeledCapture]` + transcript string + history list + system_prompt + max_tokens | Streamed text deltas (progressive) + `PointParseResult` (spoken_text, coordinate, element_label, screen_number) | Worker thread (Anthropic SDK HTTP streaming blocks on network I/O) | `anthropic` SDK, PIL |
| **`overlay.py`** | Per-monitor click-through transparent cursor overlay | `OverlayController(overlay_factory, screens)`, `OverlayController.point_at(physical_x, physical_y, monitor)`, `OverlayController.hide_for_capture()`, `OverlayController.show_after_capture()`, `OverlayWindow.animate_pointer_to(local_logical_x, local_logical_y)`, `screen_for_monitor()`, `physical_to_local_logical()`, `apply_clickthrough_styles()` | Physical pixel (x, y) + `CaptureResult.monitor` dict | `QPainter.drawPolygon(QPolygonF([...]))` draws blue cursor (post-refactor) or `drawEllipse` draws blue ball (pre-refactor) on the correct per-monitor `OverlayWindow` | **Main Qt thread ONLY** (PyQt6 is NOT thread-safe) | PyQt6, `ctypes` (Win32 layered-window flags) |
| **`stt.py`** | STT abstract + AssemblyAI streaming concrete | `AssemblyAIStreamingSTT.start()`, `.stop() → str`, `.on_partial_transcript(callback)` | `sounddevice.RawInputStream` mic PCM16 16kHz mono 1024-frame chunks | Final transcript string (blocks ~150ms on stop, 2s ceiling) | Worker thread (WebSocket client + daemon-thread teardown in `stop()` for 500ms SLA) | `assemblyai` SDK, `sounddevice` |
| **`tts.py`** | TTS abstract + Cartesia Sonic-3 concrete | `CartesiaSonicTTS.speak(text)` non-blocking, `.speak_sentence(text)` non-blocking, `.stop()` flag-based | Text string | Audio played via `sounddevice.OutputStream` (PCM float32 44.1kHz) | Worker thread (HTTP streaming + one daemon thread per `speak` call) | `cartesia` SDK, `sounddevice`, `numpy` |
| **`hotkey.py`** | Push-to-talk state machine | `PushToTalkHotkey(on_press, on_release, listener_class=...)`, `.start()`, `.stop()` | Win32 low-level keyboard events (via `pynput.Listener(suppress=False)` low-level hook) | `on_press()` / `on_release()` callbacks fired on state transitions (Ctrl+Alt+Space ALL held → RECORDING; any released → IDLE) | pynput listener thread; callbacks marshaled to Qt main via `pyqtSignal` in `app.py` | `pynput` |
| **`memory.py`** | Karpathy markdown + SQLite WAL index | `MemoryStore()`, `.recall(app_name, max_chars) → str`, `.record(app_name, window_title, user_question, claude_response, pointer_targets)`, `.list_known_apps() → list[dict]` | App name (for routing), interaction data | Markdown file at `~/.clicky-windows/memory/<app>.md` + SQLite row at `~/.clicky-windows/index.db` | Any thread (SQLite WAL + fresh connection per call; Phase 1 has ONE writer which is the Qt main thread via `app.py`) | `sqlite3`, `pathlib` |
| **`app.py`** (shipped 2026-04-13, extended through 2026-04-20) | Qt main orchestrator + thread coordination | `py -3.13 -m app` entry point | Qt main loop wires all the above via `pyqtSignal` | Full PTT loop end-to-end | Main Qt thread + 5+ worker threads (pynput listener + sounddevice input + AssemblyAI WebSocket + Anthropic HTTP streaming + Cartesia HTTP streaming) | All the above + PyQt6 + `pyqtSignal` |
| **`tools/lint_memory.py`** (Step 7.5, **SKIPPED 2026-04-20**) | ~~Karpathy-style weekly health check~~ | N/A | N/A | N/A | N/A | N/A | Skipped per user verdict ("B0-only, not user-facing value"). Real users experience memory via Claude's "you asked this Monday" mid-conversation moments, not by opening insights.md. See DECISIONS.md 2026-04-19 (late-evening) entry D. |
| **`tools/bench_path_a.py`** (Phase 1.5 Step 2 Task 12) | Mann-Whitney U + bootstrap CI latency benchmark harness | `py -3.13 -m tools.bench_path_a record/compare` | Scrapes `~/.clicky-windows/debug/*/interaction.log` | Prints before/after P50 + p-value + 95% CI | Main thread (CLI) | `scipy>=1.11` |

**Thread model rule:** only `pyqtSignal` crosses thread boundaries. No UI calls from worker threads, ever. PyQt6 is not thread-safe. STT/AI/TTS workers emit Qt signals; Qt main thread slot handlers call overlay + memory methods. `app.py` (Step 7) enforces this.

**Provider abstraction rule:** `AIClient`, `STT`, `TTS` abstract base classes exist from day 1 so Phase 2 multi-provider support (`OpenRouterClient`, `GeminiClient`, `ElevenLabsTTS`, `FasterWhisperSTT`, `LocalLMClient`, etc.) is a subclass drop, not a refactor of `app.py`. Mirrors Wallee's `BuddyTranscriptionProvider` protocol pattern.

---

## User Journeys

### Phase 1: First run (developer-tester only, Abhishek)
1. `git clone https://github.com/AbhishekVulla/clicky-windows && cd clicky-windows`
2. `py -3.13 -m pip install -r requirements.txt`
3. `cp .env.example .env`, edit with `ANTHROPIC_API_KEY` + `ASSEMBLYAI_API_KEY` + `CARTESIA_API_KEY`
4. **If Claude Desktop for Windows is installed:** disable its Ctrl+Alt+Space binding in Settings → Keyboard Shortcuts → Ctrl+Alt+Space → None (known conflict documented in DECISIONS.md 2026-04-12 evening).
5. `py -3.13 -m app` in a terminal
6. Terminal stays open as long as Clicky runs. App is now listening globally for Ctrl+Alt+Space.
7. User opens any Windows app (Excel, Notepad, Photoshop, whatever).
8. User holds Ctrl+Alt+Space, speaks a question ("how do I freeze the top row"), releases.
9. Within ~1-2 seconds: blue cursor overlay animates to a UI element (e.g., the View tab) + Clicky's voice starts speaking the answer ("you'll want to head up to the View tab, then click Freeze Panes...").
10. User clicks the pointed-to element themselves (Clicky never clicks for them).
11. `Ctrl+C` in the terminal to quit.

### Phase 1: Typical PTT interaction
See [Core Loop](#core-loop) above.

### Phase 1: Multi-session memory accumulation
1. **Session 1** (Monday 10am): User asks "how do I freeze the top row" in Excel → Clicky points at View → Freeze Panes → voice explains it → `memory.record()` appends the interaction to `~/.clicky-windows/memory/excel.exe.md`.
2. **Session 2** (Thursday 2pm): User opens Excel again, holds Ctrl+Alt+Space, asks "remind me how to freeze panes?"
3. `memory.recall("excel.exe")` reads the tail of the markdown file → injected into the user message text content block before the current transcript.
4. Claude sees the prior interaction in context → references it: *"you asked about this on Monday — same place, View tab → Freeze Panes → Freeze Top Row."*
5. User feels the difference between a stateless Claude wrapper and a buddy who remembers.
6. **Demo video (Step 8 acceptance criterion):** MUST show 2+ sessions of the same app because the memory differentiator only lands when the user experiences cross-session recall. A single-session demo looks identical to Clicky's stateless demo.

### Phase 1: Quit flow
1. User hits `Ctrl+C` in the terminal running `py -3.13 -m app`.
2. Qt main loop receives `KeyboardInterrupt` via signal handler.
3. App stops the pynput listener, stops the STT WebSocket (daemon-thread teardown keeps shutdown within ~500ms), stops any in-flight Cartesia HTTP stream (flag-based stop), hides overlays, closes SQLite connection.
4. Terminal returns to shell. Next run starts fresh; memory files persist on disk.

### Phase 2: First run (non-technical user, PACKAGED — PENDING Phase 2)
Target UX (not yet built):
1. User downloads `Clicky-Windows-Setup.msi` from a release.
2. Double-click → Windows installer walks through accept-license + install-location + "install for all users?" dialogs.
3. On first launch, a PyQt6 `QInputDialog` wizard prompts for API keys: Anthropic (required) + AssemblyAI (required) + Cartesia (required). Keys stored in Windows Credential Manager via `keyring` lib (mirror of Grafyn's `settings.rs` migration pattern).
4. App silently starts as a background process. System tray icon appears (the ONLY visible chrome).
5. Right-click tray icon → "Open Settings" (hotkey rebind, voice selection, memory dir, about) / "Quit" / "Help".
6. User uses Ctrl+Alt+Space as in Phase 1.
7. Optional: user toggles "Start on login" in settings — app auto-launches via `SMAppService`-equivalent.
8. When a new version ships, auto-updater checks a release feed on startup, prompts the user, downloads + restarts.

---

## Invariants (load-bearing rules — breaking any of these breaks the project)

Every rule below is "load-bearing" — it encodes a lesson learned the hard way. Breaking them causes silent failures, security holes, or UX regressions. If you are future-Claude reading this post-compact, treat these as non-negotiable unless the user explicitly tells you to override.

1. **`pynput.Listener(suppress=False)` — observe-only, NEVER `suppress=True`.** `suppress=True` installs a global `WH_KEYBOARD_LL` hook that blocks EVERY key event system-wide, disabling all typing globally. There is NO per-combo opt-out. Caught 2026-04-12 morning — user directly verbatim: *"NO MY KEYBOARD IS DISABBLED, NOTHING WORKS, I CANT TYPE."* See DECISIONS.md 2026-04-12 entries.
2. **Per-monitor overlays, NOT virtual-desktop-spanning.** Qt 6's "islands-of-screens" geometry on mixed-DPI Windows 11 silently puts coordinates in gaps between monitors. `overlay.py` has `OverlayController` managing `list[OverlayWindow]` — one per physical monitor from `QGuiApplication.screens()`. Routed via `screen_for_monitor()` metadata match against `CaptureResult.monitor`. See DECISIONS.md 2026-04-11 "Per-monitor overlays".
3. **`overlay.hide_for_capture()` fires BEFORE every `mss.grab()`.** If Claude sees our own blue cursor in its input screenshot, it'll try to point at itself — infinite feedback loop. `overlay.py` exposes `hide_for_capture()` + `show_after_capture()`; `app.py` calls them around every capture.
4. **Win32 layered-window flags applied via ctypes AFTER `QWidget.show()`.** Flags: `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`. OR'd in, NEVER overwritten (would wipe Qt's own flags). Followed by `SetWindowPos(SWP_FRAMECHANGED)` so changes take effect immediately. `apply_clickthrough_styles` raises `RuntimeError` with `ctypes.WinError()` context if `SetWindowLongW` returns 0 — no silent click-through breakage.
5. **DPI awareness is mandatory.** `ctypes.windll.shcore.SetProcessDpiAwareness(2)` at startup (per-monitor v2). Without it, mixed-scaling multi-monitor setups put the pointer in the wrong place. Idempotent — safe to call every capture.
6. **Three coordinate spaces, always document the conversion:** (A) physical pixels virtual-desktop, (B) Qt logical DIP per screen, (C) screenshot pixel space Claude returns coordinates in. `capture.unscale_claude_coords` maps C→A. `overlay.physical_to_local_logical` maps A→B per-screen via that screen's own `devicePixelRatio` (NEVER cached globally — mixed-DPI setups have different ratios per screen). `ai.parse_point_tag` extracts coordinates in space C from the `[POINT:x,y]` tag.
7. **Memory recall injection goes into the user message text content block, NOT the `system=` param.** `system=` stays fixed at `_CLICKY_SYSTEM_PROMPT` (Clicky's persona + pointing instructions). Memory context is per-turn data, not persona. Matches Clicky's shipping pattern.
8. **`ai.py` uses vision-tag `[POINT:x,y:label(:screenN)?]` pattern, NOT Computer Use API beta** (post-refactor). Clicky's `ElementLocationDetector.swift` (which we originally ported) is dead code — 0 references across all 11 non-test Swift files, grep-verified. The 2026-04-11 "Use Computer Use API beta directly" decision is SUPERSEDED-FOR-PHASE-1 by the 2026-04-12 (evening 3) refactor decision. See DECISIONS.md.
9. **Only `pyqtSignal` crosses thread boundaries.** PyQt6 is not thread-safe. No UI calls from worker threads, ever. STT/AI/TTS all run on worker threads, communicate with Qt main via `pyqtSignal`. `app.py` (Step 7) enforces this.
10. **Clicky NEVER autonomously clicks.** We draw an overlay pointing at (x, y) — the user clicks themselves. Hard product boundary per "What Clicky IS NOT" item 9. If a future contributor proposes adding `pyautogui.click()` or equivalent, reject and point them here.
11. **API keys in `.env` only for Phase 1. Never committed.** `.gitignore` blocks `.env`, `*API*KEY*`, `*.key`, `*.pem`, `*secret*`, `Anthropic API.txt`, `STT TTS API.txt`. Phase 2 migrates to Windows Credential Manager via Python `keyring` lib.
12. **Reference-source read discipline** (learned 2026-04-12 evening 3): for any component that ports code from a reference repo, read every non-trivial source file in the reference LINE-BY-LINE via `gh api` BEFORE drafting any design. Doc-level claims ("Clicky uses X") can be inherited assumptions that don't match the actual source. `ai.py` was a verbatim port of Clicky's DEAD CODE because the 2026-04-11 decision was based on a partial source read. Cost: ~6-9h of pre-Step-7 refactor work. See `feedback_reference_source_read_discipline.md`.
13. **Verification-not-caveating discipline** (learned 2026-04-12 evening 3): never use "note the caveat while still presenting the core finding" as an escape from verifying a non-trivial SDK/API/platform/source claim. WebSearch / `gh api` / installed source grep takes seconds. Caveats rot and mislead future-Claude. See `feedback_brutally_honest_mode.md` Verification discipline rules 4+5.
14. **Hotkey is Ctrl+Alt+Space** (3-finger modifier+key combo). NEVER Ctrl+Space (VS Code IntelliSense), NEVER Ctrl+Shift+Space (Excel/Sheets Select-All), NEVER Alt+Space with `suppress=True` (globally destructive). Claude Desktop for Windows users must disable its Ctrl+Alt+Space binding in its Settings (same pattern Raycast/Flow-Launcher users follow). Phase 1.5 Win32 `RegisterHotKey` subclass deferred.
15. **`MemoryStore` has 4 public methods, NOT 5:** `__init__`, `recall`, `record`, `list_known_apps`. `infer_skill_level` was removed 2026-04-12 per user pushback (*"This is not Khan Academy now is it? The whole value is learn by doing"*). No pedagogical framework. Just raw markdown tail reads. The LLM can infer engagement depth from the raw markdown without pre-digested labels.
16. **Superpowers plans: ONE combined plan doc per component.** No separate `specs/` file. Three docs = bureaucracy (the ai.py ceremony mistake from Step 2). Boris #5 self-critique + `superpowers:code-reviewer` independent pass pre-commit for non-trivial feature commits.

---

## Phase 1 Scope + Acceptance Criteria

**Scope:** 12 code files + 5 docs (CLAUDE + PRD + ROADMAP + DECISIONS + README-at-end) + private GitHub repo. See [ROADMAP.md](ROADMAP.md) for step-by-step execution order.

**Phase 1 is "done" when all of these are true:**

1. **Working loop on a real Windows machine.** Press Ctrl+Alt+Space in Excel (or any real app), speak a question, release. Within ~1-2 seconds: blue cursor animates to the right UI element + voice explains the answer. Works 3 times in a row without crashing.
2. **Multi-monitor + DPI verified.** Tested on at least 2 monitors (ideally with different scaling). Pointer lands within ±5 pixels of the intended target on both monitors. _(Phase 1 tester machine is single-monitor; multi-monitor verification happens when Abhishek plugs in an external display.)_
3. **Memory persists across sessions.** Close the app, reopen it, ask a follow-up about the same Windows app. Clicky references the previous interaction.
4. **Memory is human-readable.** `~/.clicky-windows/memory/EXCEL.EXE.md` opens as a plain markdown file with clear per-interaction sections. No encoded binary, no opaque schema.
5. **5+ real user sessions on a real task.** Not test sessions — actual usage on a real task (learning Blender, debugging in VS Code, using an unfamiliar app, etc.).
6. ~~**`lint_memory.py` produces meaningful insights.**~~ **SKIPPED 2026-04-20** per user verdict. Acceptance gate item dropped — not a real-user-facing feature. If B0 case-study essay specifically needs `insights.md`, revisit then.
7. **~100 pytest unit tests pass** in <3s. Coverage: coordinate math, API response parsing, memory CRUD, hotkey state machine, capture DPI/resize, overlay geometry, STT/TTS DI mocks, vision-tag regex parser. Manual verification for overlay click-through, STT audio loop, TTS playback, full E2E loop (no headless mode).
8. **Demo video recorded.** 30-90 second screen recording showing the full loop on a real task. MUST show 2+ sessions of the same app so the memory differentiator lands.
9. **All 5 docs up to date.** CLAUDE.md, PRD.md (this file), ROADMAP.md, DECISIONS.md, README.md.
10. **Private GitHub repo with full history.** Conventional commits, one commit per step minimum.

**Phase 1 will NOT have:**
- Proactive mode (Phase 2 — Karpathy: wait for the data)
- Settings UI, tray icon, MSI installer, auto-updater, code signing (Phase 2 packaging)
- BYOK keychain migration (Phase 1 is `.env` only — non-tech users can't run Phase 1 anyway)
- ElevenLabs TTS, FasterWhisper STT, Gemini/OpenAI/local-model `AIClient` subclasses (Phase 2 subclasses)
- Clipboard copy, theme toggle, dark mode, hide-overlay-while-typing
- Polished bezier pointer animations (current: QEasingCurve.Type.Linear 400ms)
- Automated tests for the full screen→AI→overlay→voice loop (no headless mode exists)

## Phase 2 Scope (2-4 weeks, only if Phase 1 validates)

**Goal:** match the B0 bar by rigour proportional to problem. Reference: Wallee (3K LOC Python + 517 tests + 60 replay scenarios + safety-critical architecture). Every item below traces to an actual user demand from Clicky upstream issues/PRs (sourced via the Upstream Snapshot below).

- **50-100+ additional pytest tests** across all modules + replay scenarios (Wallee-style mocked-Anthropic deterministic replays)
- **Proactive mode** — idle detection + focused-window capture. **Targets come from `lint_memory.py` real patterns, not guessed.** Validated by [danpeg/clicky](https://github.com/danpeg/clicky) (79 stars in 3 days).
- **BYOK / OpenRouter support** (`OpenRouterClient(AIClient)` subclass). OpenRouter → Anthropic passes through native tool use; OpenRouter → non-Anthropic providers strip beta headers. Our post-refactor vision-tag path works across both. Issues [#27](https://github.com/farzaa/clicky/issues/27), [#33](https://github.com/farzaa/clicky/issues/33), PR [#51](https://github.com/farzaa/clicky/pull/51).
- **Settings panel + system tray icon** via `settings_panel.py` + `system_tray.py`. Tray icon is the ONLY way a non-tech user can see/quit a background process. Issue [#60](https://github.com/farzaa/clicky/issues/60) (new). Grafyn's `settings.rs` pattern wholesale: `keyring` lib for keychain, `platformdirs` for non-sensitive settings.
- **Configurable hotkey UI** — rebind without code change. Issue [#35](https://github.com/farzaa/clicky/issues/35), PR [#16](https://github.com/farzaa/clicky/pull/16).
- **TTS interruption** — second hotkey press cancels current speech. Issue [#36](https://github.com/farzaa/clicky/issues/36). `tts.stop()` stub already exists in Phase 1.
- **Clipboard copy** of responses. Issue [#43](https://github.com/farzaa/clicky/issues/43), PR [#23](https://github.com/farzaa/clicky/pull/23).
- **Listening cue overlay** — focus rectangle on hotkey press. PR [#58](https://github.com/farzaa/clicky/pull/58).
- **Hide overlay while user is typing.** PR [#49](https://github.com/farzaa/clicky/pull/49).
- **Multi-language support.** Issue [#7](https://github.com/farzaa/clicky/issues/7). Claude handles multi-language natively; AssemblyAI u3-rt-pro supports it; adapt `_CLICKY_SYSTEM_PROMPT` per language.
- **Multi-model `AIClient` subclasses:** `GeminiClient`, `OpenAIClient`, `LocalLMClient` (for LM Studio / MLX / Ollama). PR [#40](https://github.com/farzaa/clicky/pull/40), PRs [#39](https://github.com/farzaa/clicky/pull/39) / [#41](https://github.com/farzaa/clicky/pull/41) / [#42](https://github.com/farzaa/clicky/pull/42). Post-refactor vision-tag path makes these all clean subclass drops — no fallback code paths.
- **Additional `STT` / `TTS` subclasses:** `FasterWhisperSTT` (offline), `ElevenLabsTTS`, `EdgeTTS`, `DeepgramNova3STT`. PR [#47](https://github.com/farzaa/clicky/pull/47), PR [#52](https://github.com/farzaa/clicky/pull/52).
- **Security hardening + logging scrubbing.** Issues [#22](https://github.com/farzaa/clicky/issues/22), [#34](https://github.com/farzaa/clicky/issues/34), [#44](https://github.com/farzaa/clicky/issues/44), PR [#50](https://github.com/farzaa/clicky/pull/50).
- **PyInstaller bundle + WiX MSI installer + EV code signing + auto-updater** (pyupdater or custom). Unavoidable for distribution to non-tech users.
- **Diff-and-skip screenshot caching** — hash last screenshot, skip Claude call if unchanged. Karpathy-style "do less work."
- **UIA accessibility tree fast path** for productivity apps (Excel, Word, Chrome, File Explorer). Where Clicky Windows can beat the macOS original — Mac has no UIA equivalent.
- **"Unexpected finding" writeup published** — the B0 editorial standard. One essay about what building this revealed. Not a marketing post.
- **5+ real users beyond Abhishek** with documented feedback.

## Phase 3: Tauri Rewrite (NOT pre-committed)

Only triggered if Phase 2 hits a Python-specific wall:
- PyInstaller bundle too fat / slow / triggers antivirus false positives
- GIL contention causes user-visible latency that can't be optimized away
- PyQt6 overlay reliability issues across Win10/11 + GPU driver combinations
- "Can't install Python" pain non-technical users report that PyInstaller can't solve

If triggered: port to Tauri 2.0 + Rust backend + Vue 3 frontend + Pinia, matching Grafyn's architecture. See the Grafyn patterns in [`WKJBryan/Grafyn`](https://github.com/WKJBryan/Grafyn).

**Most likely: never needed.** Wallee (3K LOC Python, safety-critical) clears the B0 bar by rigour, not language.

## Competitor Landscape

**Last refreshed:** 2026-04-26 via parallel agent research. Memory note: this section is the project's single source of truth for competitor facts. CLAUDE.md just points here. Refresh quarterly or when a major shipped competitor moves the picture.

### Voice + overlay + Windows (closest threat axis)

| Competitor | Platform | Points? | Voice? | Memory? | Stars / activity (2026-04-26) | Notes |
|---|---|---|---|---|---|---|
| **tekram/clicky-windows** | Windows (Electron+TS+Squirrel) | Yes | Yes | **No** | **46 ⭐ (was 14 three weeks ago, growing fast), ACTIVE** | **Refreshed 2026-04-27 verbatim from their README:** Has installer + Squirrel auto-updater + system tray + always-on-top pinned chat + cursor buddy + multi-provider STT (3: AssemblyAI cloud + OpenAI Whisper cloud + Whisper Local offline whisper.cpp) + multi-provider TTS (3: Windows SAPI + OpenAI + ElevenLabs) + multi-provider LLM (Anthropic + OpenAI + OpenRouter 300+ models) + HIPAA mode. **Their gap = our wedge: NO curated KB upload, NO persistent memory, NO documented latency optimizations.** Earlier claim that they have multi-language + clipboard + KB was wrong; not in README. |
| **JaySmith502/clicky-win** | Windows (Python+PySide6+qasync+Cloudflare Worker) | Yes | Yes | **Curated KB only** (NotebookLM-imported `_meta.toml` + `overview.md` + section files per app, 60K-char keyword-ranked budget, system-prompt injection — code-verified 2026-04-27 deep-dive on their `knowledge_base.py`) | 4-5 ⭐ (hobby tier, 73 tests, last push 2026-04-12) | **The cracked underdog with the best KB pattern.** Has installer + tray + Cloudflare Worker proxy (`/chat` + `/tts` + `/transcribe-token` ephemeral AssemblyAI token route — saves Worker bandwidth) + interrupt support + 20-turn deque (text-only history, images only on current turn). **Their gap: NO persistent auto-learn memory, Claude-only LLM (hardcoded `claude-sonnet-4-6`/`claude-opus-4-6` allowlist), AssemblyAI + ElevenLabs hardcoded, no latency optimizations, no multi-language.** Phase 2 cribs their KB pattern (~150 LOC port). |
| **Clicky Agents (Farza, launched 2026-04-23)** | macOS only, Windows = Tally form waitlist | Yes | Yes | **No** | Closed-source, modest launch (~868 LinkedIn reactions, PH #6 137 upvotes) | Voice-spawn-agents iteration on top of original Clicky. Same Cloudflare proxy + AssemblyAI + ElevenLabs + Claude Sonnet 4.6 stack. Buddy + visual pointer still front door; agent mode triggered by "clicky agent" wake phrase, runs background tasks (research IG influencers / build Mac apps / update Notes-Calendar-Reminders). **Open-source `farzaa/clicky` (5,200 ⭐ MIT) explicitly handed to community as "legacy version for hackers."** Brand confusion risk for "Clicky Windows" naming = medium → 1-line README disambiguator handles it. |
| **danpeg/clicky** (macOS fork) | macOS only | Yes, proactive | Yes | No | 88 ⭐, stalled (last push 18 days) | Phase 2 inspiration for proactive mode (target real memory patterns). |
| **Clippi.us** | macOS only ("Windows soon") | Yes | Yes | No | Closed-source | Status unchanged from April 2026. Has not shipped Windows. |

### Autonomous GUI agents (different category — they execute, we point)

| Competitor | Platform | Notes |
|---|---|---|
| **Claude Cowork** (Anthropic) | Windows shipped 2026-02-10 | Chat-side-panel + multi-step exec + scheduled tasks. NOT voice/overlay; product boundary intact. Pro/Max tier. |
| **CursorTouch Windows-MCP** | Windows | 5.3k ⭐, programmatic Windows agent library, MIT. Not consumer-facing. |
| **Claude Computer Use** | API beta | Sonnet 4.6 hit 72.5% OSWorld (vs <15% late 2024). Beta header still required. Cowork is the consumer surface. |
| **Playwright MCP** (microsoft) | Cross-platform | 31.4k ⭐. Browser automation dev tool. Auto-included in GitHub Copilot Coding Agent. |
| **OpenAI Operator** | Browser only | ChatGPT Pro tier. No native desktop. |
| **Google Project Mariner** | Web only | Trusted-tester preview Q1 2026. Explicitly not desktop. |
| **MS Copilot Agent Mode** | M365 apps only | GA Apr 22, 2026 in Word/Excel/PPT. Windows-wide "Agent Workspace" still private preview. |

### Voice + screen-aware on Windows (no overlay / no memory)

| Competitor | Notes |
|---|---|
| **Microsoft "Hey Copilot" + Copilot Vision** | Wake-word + screen-aware chat in Windows 11 itself. **Platform-level commoditizer for non-tech users.** Long-game threat (6-12 months). Position Clicky Windows as the depth / learn-by-doing tool, not mass-market. |
| **GhostDesk 2.0** | Added Nova-3 STT in v2, paid Windows overlay ($5/24h, $9.99/mo). "Interview cheating" angle. No pointing, no per-app memory. Razorpay/Dodo billing, OCR. |

### Adjacent / different category

| Competitor | Notes |
|---|---|
| **Screenpipe** | 24/7 passive recorder, MCP server, MIT. Not interactive buddy. |
| **trili.ai** | Khan-Academy structured tutorials, sidebar. Site is a stub. Opposite design philosophy. |
| **Skywork Desktop** | Windows persistent agent environment (launched Feb 2026). Different category (agent, not buddy). |
| **OpenClaw 2026.4.10** | OSS local LLM harness with "Active Memory" plugin. Different surface (local LLM, no overlay). |
| **Littlebird** | Enterprise cross-platform, $11M raised. Consumer-free differentiator survives. |

### Threats downgraded since April 2026

- ~~**Vercept** (acquired Anthropic Feb 25, 2026)~~ — Vy desktop app shut down 2026-03-25. Team absorbed into Computer Use group. **Threat eliminated.**
- ~~**Farza ships Windows officially**~~ — He's running Chasi (YC W26). 0 comments from him on Issue #26 ever. Last meaningful Clicky commit 2026-04-10 (license + key cleanup). **Window is open; community owns Windows.**

### Differentiator framing (locked 2026-04-26)

The Karpathy markdown memory pattern went viral early April 2026 (VentureBeat coverage; MemPalace 47K stars in 2 weeks). Memory as a *technique* is no longer novel.

**Lead positioning: voice PTT + visual pointer + persistent per-app auto-memory + Windows-native — the trifecta, combined.**

Every shipped competitor occupies at most 2-3 of these 4 axes:
- tekram: voice + overlay + Windows, **no memory**
- JaySmith502: voice + overlay + memory + Windows, but memory is **curated, not auto-learned**
- Claude Cowork: chat-only, **no voice + no overlay**
- MS "Hey Copilot": voice + screen-aware, **no overlay + no memory**
- GhostDesk 2.0: voice + overlay (no pointing) + Windows, **no memory**

The trifecta is uncrowded. See [LAUNCH.md](LAUNCH.md) (gitignored, internal) for full writeup pitch + distribution channels.

## Validated User Demands (sourced from Clicky GitHub issues + forks + Farza's social)

Every Phase 2 Scope bullet traces to one or more of these:

1. **Windows version** — #1 request on farzaa/clicky. [Issue #26](https://github.com/farzaa/clicky/issues/26) (18 comments), [#21](https://github.com/farzaa/clicky/issues/21), [#19](https://github.com/farzaa/clicky/issues/19), [#54](https://github.com/farzaa/clicky/issues/54). Two independent forks.
2. **Persistent memory** — #2 request. Original [Issue #30](https://github.com/farzaa/clicky/issues/30) titled "stateless Claude wrapper: no memory between sessions" was repurposed/hijacked by 2026-04 into an "OpenClaw Gateway backend" thread; original framing only survives in older fork READMEs. Stronger 2026 evidence: Karpathy's LLM Wiki tweet went viral early April 2026 (VentureBeat coverage); MemPalace launched 2026-04-05 → 47K GitHub stars in 2 weeks. **Memory as a pattern is now industry-default; the differentiator is the trifecta** (voice PTT + visual pointer + persistent per-app auto-memory + Windows). Our Phase 1 ships persistent memory; Phase 2 proactive mode trains on real recurring-question patterns from the markdown.
3. **Proactive mode** — validated by the [danpeg/clicky](https://github.com/danpeg/clicky) fork (79 stars in 3 days without marketing).
4. **BYOK / multi-model** — Issues [#22](https://github.com/farzaa/clicky/issues/22), [#27](https://github.com/farzaa/clicky/issues/27), [#32](https://github.com/farzaa/clicky/issues/32), [#33](https://github.com/farzaa/clicky/issues/33); PR [#51](https://github.com/farzaa/clicky/pull/51). Our Phase 1 ships `.env` BYOK; Phase 2 adds keychain UI.
5. **Clipboard copy** — [Issue #43](https://github.com/farzaa/clicky/issues/43), PR [#23](https://github.com/farzaa/clicky/pull/23).
6. **Configurable hotkey** — [Issue #35](https://github.com/farzaa/clicky/issues/35) ("3-finger combo awkward"), PR [#16](https://github.com/farzaa/clicky/pull/16).
7. **TTS interruption** — [Issue #36](https://github.com/farzaa/clicky/issues/36) ("doesn't stop once it starts speaking").
8. **Multi-language** — [Issue #7](https://github.com/farzaa/clicky/issues/7).
9. **Settings UI** — [Issue #60](https://github.com/farzaa/clicky/issues/60) (new).
10. **Linux support** — [Issue #13](https://github.com/farzaa/clicky/issues/13), [Issue #59](https://github.com/farzaa/clicky/issues/59) (new).
11. **Security hardening** — Issues [#22](https://github.com/farzaa/clicky/issues/22), [#34](https://github.com/farzaa/clicky/issues/34), [#44](https://github.com/farzaa/clicky/issues/44), PR [#50](https://github.com/farzaa/clicky/pull/50).
12. **Farza's demo + Natique's "Day 0 of asking if this is available on Windows" LinkedIn comment** — first-party evidence that the "learn by doing" framing lands AND the Windows demand is unfulfilled. Captured verbatim in `I built this thing called Clicky..txt` (dev-session archive, gitignored).

### Upstream Snapshot (2026-04-12)

Pull `gh issue list --repo farzaa/clicky --state all` + `gh pr list --repo farzaa/clicky --state all` periodically to refresh. **Pattern:** Farza rejects most feature PRs (local STT, MLX/LM Studio, ElevenLabs migration, OpenRouter expansion, TFT coaching, Practice mode, clipboard copy all CLOSED UNMERGED). Clicky stays minimal by design. **Strategic implication:** our raise-the-bar angle is shipping the features users want that Farza won't merge upstream.

| Area | Upstream | Phase mapping |
|---|---|---|
| Multi-monitor bug | [#24](https://github.com/farzaa/clicky/issues/24), [PR #48](https://github.com/farzaa/clicky/pull/48) | Phase 1 solved via per-monitor overlay architecture (DECISIONS.md 2026-04-11). |
| Cursor-in-screenshot | [#37](https://github.com/farzaa/clicky/issues/37) | Inherited limitation — screenshot APIs don't capture OS cursor layer. Document in README. |
| Persistent memory | [#30](https://github.com/farzaa/clicky/issues/30) | Phase 1 differentiator. `memory.py` ships. |
| BYOK / custom API keys | [#22](https://github.com/farzaa/clicky/issues/22), [#27](https://github.com/farzaa/clicky/issues/27), [#32](https://github.com/farzaa/clicky/issues/32), [#33](https://github.com/farzaa/clicky/issues/33) | Phase 1 ships `.env` BYOK. Phase 2 adds `keyring`-backed settings panel. |
| OpenRouter | [PR #51](https://github.com/farzaa/clicky/pull/51), [PR #31](https://github.com/farzaa/clicky/pull/31), [PR #6](https://github.com/farzaa/clicky/pull/6) | Phase 2 `OpenRouterClient(AIClient)` subclass. Post-refactor vision-tag path works across all providers. |
| Multi-model (Gemini / OpenAI) | [PR #40](https://github.com/farzaa/clicky/pull/40) | Phase 2 subclass drops. |
| Local models (LM Studio / MLX / Parakeet) | [PR #39](https://github.com/farzaa/clicky/pull/39), [#41](https://github.com/farzaa/clicky/pull/41), [#42](https://github.com/farzaa/clicky/pull/42), [#47](https://github.com/farzaa/clicky/pull/47) | Phase 2+. Vision-tag pattern works with local models (most expose OpenAI-compatible APIs); Phase 1's Computer Use path would have blocked this. |
| ElevenLabs TTS | [PR #52](https://github.com/farzaa/clicky/pull/52) | Phase 2 `ElevenLabsTTS(TTS)` subclass. |
| Configurable hotkey UI | [#35](https://github.com/farzaa/clicky/issues/35), [PR #16](https://github.com/farzaa/clicky/pull/16) | Phase 2 settings panel. |
| TTS interruption | [#36](https://github.com/farzaa/clicky/issues/36) | Phase 2 full cancel. Phase 1 has `tts.stop()` flag-based stub. |
| Clipboard copy | [#43](https://github.com/farzaa/clicky/issues/43), [PR #23](https://github.com/farzaa/clicky/pull/23) | Phase 2 one-line addition. |
| Listening cue overlay | [PR #58](https://github.com/farzaa/clicky/pull/58) | Phase 2 polish. |
| Hide overlay while typing | [PR #49](https://github.com/farzaa/clicky/pull/49) | Phase 2 polish. |
| Multi-language | [#7](https://github.com/farzaa/clicky/issues/7) | Phase 2 system prompt parameterization + locale detection. |
| Security hardening + logs | [#22](https://github.com/farzaa/clicky/issues/22), [#34](https://github.com/farzaa/clicky/issues/34), [#44](https://github.com/farzaa/clicky/issues/44), [PR #50](https://github.com/farzaa/clicky/pull/50) | Phase 2 logging scrubbing + audit pass. Phase 1 is already `.env`-only + no committed keys + hardened `.gitignore`. |
| Linux support | [#13](https://github.com/farzaa/clicky/issues/13), [#59](https://github.com/farzaa/clicky/issues/59) | Phase 2+. Most modules are cross-platform; `capture.py` + `overlay.py` need X11/Wayland code paths. |
| Settings UI | [#60](https://github.com/farzaa/clicky/issues/60) | Phase 2. Unavoidable for non-tech distribution. |
| Windows-specific | [#26](https://github.com/farzaa/clicky/issues/26), [#21](https://github.com/farzaa/clicky/issues/21), [#19](https://github.com/farzaa/clicky/issues/19), [#54](https://github.com/farzaa/clicky/issues/54), [PR #53](https://github.com/farzaa/clicky/pull/53), [PR #54](https://github.com/farzaa/clicky/pull/54) | Clicky Windows = this project. No upstream Windows ports are getting merged, so the polished persistent-memory Windows version (us) has no upstream competition. |
| Upstream SwiftUI bug (doesn't apply) | [#12](https://github.com/farzaa/clicky/issues/12) | N/A — we don't use SwiftUI. |
| Cloudflare Worker replacement | [PR #29](https://github.com/farzaa/clicky/pull/29) | N/A — we're Anthropic-direct, no Worker. |

## Risks

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| 1 | PyQt6 click-through unreliable on Win11 with certain GPU drivers | High | High (no overlay = no demo) | Win32 layered-window flags via ctypes after `show()`. Fallback: tkinter + `-transparentcolor` + pywin32. Verified clean on 2880×1800 @ 200% DPI test machine. |
| 2 | Per-monitor DPI math wrong on mixed-DPI setups | Very High | High (pointer lands wrong place) | `SetProcessDpiAwareness(2)` at startup. Per-monitor overlay architecture. Three coordinate spaces documented in code + Invariant #6. |
| 3 | Ctrl+Alt+Space hotkey conflicts with Claude Desktop for Windows | Medium | Low (UX nit, no crash) | `suppress=False` observe-only means conflicts degrade to "app underneath also sees the key" rather than "key is lost." Phase 1 users must disable Claude Desktop's binding in its Settings (same pattern Raycast / Flow Launcher users follow for Alt+Space / Copilot conflicts). Phase 2 configurable hotkey UI lets users rebind without touching either app. Phase 1.5 Win32 `RegisterHotKey` subclass (deferred) suppresses the combo at the OS level. See DECISIONS.md 2026-04-12 (evening). |
| 4 | AssemblyAI / Cartesia / Anthropic network unreachable | Medium | High | Clear `RuntimeError` with diagnostic at every streaming client construction. Phase 2 offline subclass swap is 1-2h work via abstract base. No preemptive Phase 1 fallback (YAGNI). |
| 5 | Threading deadlocks (Qt main + 5+ worker threads) | High | High (silent freeze) | Invariant #9: only `pyqtSignal` crosses thread boundaries. No UI calls from worker threads. Code-reviewer independent pass on `app.py` (Step 7) specifically looks for violations. |
| 6 | End-to-end latency exceeds budget (>2s feels broken) | Medium | Medium | Per-stage timing prints during dev. Pre-warm sounddevice + Cartesia on startup. Optional listening cue overlay on hotkey press for immediate feedback. |
| 7 | Overlay appears in screenshots sent to Claude → infinite feedback loop | High | High | Invariant #3: `overlay.hide_for_capture()` fires before every `mss.grab()`. `TestOverlayControllerLifecycle` tests protect this. |
| 8 | API token costs spiral during testing | Low | Low | Per-call cost logging. Haiku 4.5 fallback for prompt-engineering iteration (after Phase 1.5 header compatibility work). Budget cap in `config.py`. |
| 9 | Anthropic ships first-party Windows screen-aware AI (Vercept acquisition) while we're building | Strategic | Strategic | Ship Phase 1 in weeks, not months. Memory is the long-term moat — generic "AI sees your screen" will be commoditized; per-app persistent memory + BYOK + user-first framing is harder to copy. |
| 10 | "Unexpected finding" never materializes → no B0 case study angle | Medium | Medium | `lint_memory.py` is the explicit lens. If 5 real sessions produce no insight, that itself is a finding. |
| 11 | Plan underestimates real engineering time | Medium | Medium | Accept the budget: 1-2 weeks for MVP, 2-4 weeks for hardening. Grafyn took 63 days for v0.1.8. |
| 12 | Claude Code makes architectural mistakes that need rework | Medium (VERIFIED by Step 7 research pass catching our Step 2 ai.py dead-code port) | Medium | Superpowers brainstorming HARD-GATE forces design approval before code. Boris #5 + code-reviewer pre-commit passes. **Reference-source read discipline** (Invariant #12) — read the reference repo line-by-line BEFORE any port, not after. Verification-not-caveating discipline (Invariant #13) — verify every non-trivial claim via `gh api` / WebSearch / installed source grep. |

## Success Metrics

**Minimum viable:** all 10 Phase 1 acceptance criteria met. Working loop, 5 real sessions, demo video, docs, tests, private repo.

**Good:** the above + `insights.md` surfaces a non-obvious pattern + Abhishek uses it organically for a week on a real task.

**Great:** the above + insight becomes a shareable writeup (Twitter thread, LinkedIn post, or B0 case study draft) + at least one outside person has tried it and given feedback.

**Moonshot:** the above + a second non-technical user uses it independently without hand-holding and comes back with an observation Abhishek didn't expect.

## Out of Scope (explicitly rejected)

All rejection reasons recorded in DECISIONS.md — see there for full rationale.

- **Tauri rewrite in Phase 2.** Wallee proves Python at 3K LOC clears the B0 bar. Language is not the disqualifier; rigour is.
- **Electron port.** tekram/clicky-windows tried it. Unfinished. Electron buys nothing Python doesn't give us.
- **SQLite-only memory, no markdown.** Karpathy's "human-readable, LLM-maintained" principle beats opaque schemas for a differentiator we need to explain to users.
- **Ctrl+Space hotkey.** Conflicts with VS Code IntelliSense — hard no, breaks developer-users' autocomplete.
- **One giant execution plan upfront.** Superpowers per-component brainstorm → plan → TDD → Boris #5 + code-reviewer for high-risk components (overlay, app.py); lean ceremony for trivial modules.
- **Proactive mode in Phase 1.** Karpathy: wait for the data. Targets come from `lint_memory.py` real usage patterns, not guesses.
- **User-scope Superpowers install.** Local scope only — isolated, reversible, no cross-project bugs.
- **~~Screenshot to Vision only, no Computer Use API~~** — ~~rejected 2026-04-11~~ **SUPERSEDED 2026-04-12 (evening 3).** Vision-only with `[POINT:x,y:label]` regex IS the shipping pattern. Clicky's `ElementLocationDetector.swift` (which we originally ported) is dead code. See DECISIONS.md 2026-04-12 (evening 3) "ai.py refactor" entry — the rejection of the vision-tag path was based on a partial source read and is now overturned.
- **~~Pure `openai-whisper`~~** — ~~rejected in favor of `faster-whisper`~~ **SUPERSEDED 2026-04-11 session 3.** Phase 1 uses AssemblyAI `u3-rt-pro` streaming for latency-first. `FasterWhisperSTT` becomes a Phase 2 offline subclass.
- **`infer_skill_level` method in `memory.py`.** Removed 2026-04-12 per user pushback (*"This is not Khan Academy now is it?"*). No pedagogical framework. The LLM infers engagement depth from raw markdown.
- **Theming (light/dark QSS).** Not vanity as such but irrelevant to Clicky Windows's UX shape — a settings panel opened once a week doesn't need user-configurable theming; PyQt6's default system-chrome-following behavior is fine.

See [DECISIONS.md](DECISIONS.md) for full rationale on each.
