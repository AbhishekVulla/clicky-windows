# Clicky Windows — Screen-Aware AI Buddy with Persistent Memory

## What This Is
Windows desktop app inspired by [Clicky](https://github.com/farzaa/clicky) (macOS, Farza Majeed). An AI buddy that sees your screen, listens to your voice, responds with speech, and physically points at UI elements via a transparent click-through overlay. **Differentiator:** per-app persistent memory — Clicky Windows remembers what you've struggled with and adapts over time.

## Why This Exists
Clicky is macOS-only. Windows has 76% of desktop users and zero polished screen-aware AI buddies with pointing + voice. #1 community request on Clicky's GitHub ([Issue #26](https://github.com/farzaa/clicky/issues/26), 18 comments) is a Windows version. We also solve upstream's unshipped BYOK demands ([#22](https://github.com/farzaa/clicky/issues/22), [#27](https://github.com/farzaa/clicky/issues/27), [#32](https://github.com/farzaa/clicky/issues/32), [#33](https://github.com/farzaa/clicky/issues/33)) — Clicky's production uses a Cloudflare Worker proxy that holds the API keys server-side; we're BYOK via `.env` from day 1.

## How It Works (Core Loop)
```
 0. App startup: stt.connect() pre-opens mic + AssemblyAI WebSocket (one-time
    1-3s cost). Overlay cursor starts following mouse at 60fps.

 1. User holds Ctrl+Alt+Space — hotkey.py observes via pynput.Listener(suppress=False)
    → tts.stop() kills any playing audio instantly (abort + response.close)
    → stt.start_recording() flips _recording=True (<1ms, mic already hot)
    → app detects foreground app via ctypes GetForegroundWindow

 2. User releases:
    a. stt.stop_recording() sends ForceEndpoint, waits for longest transcript
       within 2s window (format_turns=False for speed)
    b. overlay.hide_for_capture() — so Claude never sees our own blue cursor
    c. capture.capture_all_screens() → list[LabeledCapture] sorted cursor-first
    d. memory.recall(app_name) reads tail of ~/.clicky-windows/memory/<app>.md
       (1500 chars max, injected with "use silently, don't summarize")

 3. ai.ask_stream(images, transcript, history)
    — plain vision messages.stream() (GA, NOT Computer Use beta)
    — Clicky's verbatim 35-line system prompt (companionVoiceResponseSystemPrompt)
    — Claude embeds [POINT:x,y:label(:screenN)?] at end of spoken response
    — Supports OpenRouter via ANTHROPIC_BASE_URL env var (zero code changes)

 4. tts.speak(full_response) plays the complete response via Cartesia Sonic-3
    (Phase 1 speaks full response; sentence-level chunking deferred to Phase 2
    because speak_sentence() cancels previous sentence — needs a queue)

 5. Stream closes → parse_point_tag() regex extracts (x, y) from accumulated text,
    strips the tag from spoken_text (TTS never reads "POINT colon 640 comma 400")

 6. overlay.show_after_capture() → overlay.point_at() routes (via screen_number
    or cursor-screen fallback) to the correct per-monitor OverlayWindow →
    blue cursor polygon animates to target (tip anchored at x, y via QPropertyAnimation)

 7. memory.record() appends interaction (app, window title, transcript,
    stripped response, [(x, y)]) to ~/.clicky-windows/memory/<app>.md + SQLite index
```

Target end-to-end latency: **~800-1200ms** perceived first-audible-word (aspirational). **Measured post-Path-A** (2026-04-19 debug logs): ~1.5s first-audible-word for multi-sentence Claude responses (sentence streaming fires mid-stream), ~4-5s for single-sentence responses (falls back to batch tail-flush). Sentence streaming has 150-250ms gaps between sentences from per-sentence Cartesia HTTP TTFB — planned fix tracked as ROADMAP.md F2 (WebSocket TTS).

**Visual state machine** (verbatim port of Farza's shipping Clicky — see DECISIONS.md 2026-04-19/20 entries). Exactly one visual element per state, all follow the cursor at 60Hz via `OverlayController._on_follow_tick`:
- IDLE: blue cursor polygon only
- LISTENING (PTT held): WaveformWidget (5-bar RMS-driven meter replacing cursor)
- THINKING (release → Claude coord): SpinnerWidget (rotating blue arc replacing cursor)
- FLYING (coord → arrival): cursor polygon + quadratic bezier arc
- SPEAKING (TTS playing): cursor polygon at rest, zero animation

## Build Phases

### Phase 1: Python MVP with Persistent Memory (in progress)
**Goal:** validate "Clicky + persistent memory is meaningfully better than stateless Clicky." Not just a Windows port.

- `mss` screen capture, DPI-aware via `ctypes.windll.shcore.SetProcessDpiAwareness(2)`
- PyQt6 transparent **per-monitor** overlays with Win32 layered-window flags via `ctypes`
- `pynput.Listener(suppress=False)` for Ctrl+Alt+Space — **observe-only, never consume keys**
- **STT:** AssemblyAI Universal-3 realtime-pro streaming WebSocket + `ForceEndpoint` on hotkey release (~150ms P50 finalization). `sounddevice.RawInputStream` PCM16 16kHz mono 1024-frame chunks — matches Clicky's `AVAudioEngine.installTap(bufferSize:1024)` exactly so Phase 2 provider swaps are drop-in. Key: `ASSEMBLYAI_API_KEY` in `.env`.
- **TTS:** Cartesia Sonic-3 HTTP streaming via `client.tts.generate(...).iter_bytes()` (~150-250ms TTFB). PCM float32 44.1kHz output via `sounddevice`. Default voice: "Brooke - Big Sister" (conversational expressive buddy voice). Key: `CARTESIA_API_KEY` in `.env`.
- **Claude:** plain vision `messages.stream()` with a 35-line system prompt + `[POINT:x,y:label]` regex parser. **NOT Computer Use API beta.** Our Step 2 `ai.py` originally ported Clicky's `ElementLocationDetector.swift` verbatim; Step 7 brainstorm discovered that file is dead code (0 refs across 11 non-test Swift files). Clicky's actual shipping path is `ClaudeAPI.analyzeImageStreaming` + `CompanionManager.parsePointingCoordinates`. Refactor in progress — see `DECISIONS.md` 2026-04-12 (evening 3).
- **Sentence-level TTS chunking** (Step 7 `app.py` requirement): progressive `content_block_delta` text events → sentence splitter → `tts.speak_sentence()` flush on `.`/`!`/`?` boundaries while Claude still generates. Legitimate improvement over Clicky's production (which streams but discards progressive chunks).
- **Memory:** Karpathy-style per-app markdown at `~/.clicky-windows/memory/<app>.md` + SQLite WAL index at `~/.clicky-windows/index.db`. THE DIFFERENTIATOR — Phase 1 not Phase 2.
- **Provider abstraction** (`AIClient`, `STT`, `TTS` abstract bases) from day 1 so Phase 2 OpenRouter / Gemini / ElevenLabs / FasterWhisper are subclass drops.
- **Phase 1 done when:** 5+ real sessions on a real task where memory recall noticeably improves experience, demo video recorded. (Originally also required `lint_memory.py` output — **skipped** per user verdict 2026-04-20 as "B0-essay-only, not user-facing value". Unblocked; not shipping unless B0 writeup specifically needs the generated `insights.md`.)

### Phase 2: Harden in Python (2-4 weeks, only if Phase 1 validates)
Source: every user-demand issue/PR in Clicky upstream (see `PRD.md` § Upstream Snapshot).
- 50-100+ tests; replay scenarios (Wallee-style)
- Proactive mode — targets come from `lint_memory.py` real patterns, not guessed
- BYOK keychain migration via `keyring` lib (mirror Grafyn's `settings.rs` pattern)
- `settings_panel.py` + `system_tray.py` — tray icon is the ONLY way a non-tech user sees/quits a background process
- Configurable hotkey UI (rebind without code change)
- TTS interruption (second hotkey press cancels stream — Issue #36)
- Clipboard copy (Issue #43), hide-overlay-while-typing (PR #49), listening cue (PR #58)
- Multi-language + additional `AIClient`/`STT`/`TTS` subclasses (OpenRouter PR #51, Gemini/OpenAI PR #40)
- PyInstaller bundle + WiX MSI + EV code signing + auto-updater
- Security hardening + logging scrubbing (Issues #22/#34/#44/#50)
- "Unexpected finding" writeup (B0 editorial standard)

### Phase 3: Tauri Rewrite (NOT pre-committed)
Only triggered if Phase 2 hits a Python-specific wall (install experience rough, GIL contention, PyQt6 reliability). Most likely: never needed. Wallee proves 3K LOC Python clears the B0 bar via rigour, not language.

### Explicitly NOT Phase 1
- Proactive mode (Karpathy: wait for the data)
- BYOK UI / keychain migration (`.env` only for Phase 1's one tester)
- Settings panel, tray icon, installer, auto-updater
- ElevenLabs / FasterWhisper / local-model subclasses
- Automated tests for the full screen→AI→overlay→voice loop (no headless mode — manual verification via per-module `py -3.13 -m <module>` gates + recorded demo video)

## Key Technical Facts

- **`ai.py` uses plain vision `messages.stream()` + system prompt + `[POINT:x,y:label(:screenN)?]` regex parser.** NOT Computer Use API beta. Clicky's `ElementLocationDetector.swift` (which we originally ported) is dead code — zero references across all 11 non-test Swift files, grep-verified via `gh api`. Clicky's actual shipping path is `ClaudeAPI.analyzeImageStreaming` + `CompanionManager.parsePointingCoordinates`. See DECISIONS.md 2026-04-12 (evening 3) for the full research pass.
- **macOS → Windows equivalents:** ScreenCaptureKit → `mss`; NSPanel click-through overlay → PyQt6 + Win32 layered-window flags via `ctypes`; CGEvent listen-only tap → `pynput.Listener(suppress=False)`; AVAudioEngine → `sounddevice` (WASAPI via portaudio); NSBezierPath triangle cursor → `QPolygonF` blue cursor polygon.
- **Recommended screenshot resolutions:** `[(1024,768), (1280,800), (1366,768)]`. `capture.pick_resolution` picks closest-aspect-ratio match to the actual display. Mirrors Clicky's logic in `CompanionScreenCaptureUtility.swift` (the real capture layer), NOT `ElementLocationDetector.swift` (dead code we originally but mistakenly mirrored).
- **Three coordinate spaces:** (A) physical pixels in virtual-desktop, (B) Qt logical DIP, (C) screenshot pixel space Claude returns coordinates in. `capture.unscale_claude_coords` maps C→A. `overlay.physical_to_local_logical` maps A→B per-screen via each screen's own `devicePixelRatio` (NEVER cached globally — mixed-DPI setups have different ratios per screen).
- **Memory recall injection goes into the user message text content block, NOT the `system=` param.** `system=` stays fixed at `_CLICKY_SYSTEM_PROMPT`. Memory context is per-turn data, not persona. Matches Clicky's shipping pattern.
- **Memory is Karpathy-style markdown**, NOT a vector DB. One `.md` per app, human-readable, user can `cat EXCEL.EXE.md`. SQLite WAL index only for fast `list_known_apps()` lookups, not retrieval. Zero embeddings, zero RAG, zero retrieval complexity.

## Validated User Demands (Clicky upstream issues + forks, source for Phase 2 scope)
1. **Windows version** — Issue #26 (18 comments) + 2 independent forks
2. **Persistent memory** — Issue #30 ("stateless Claude wrapper")
3. **Proactive mode** — danpeg/clicky fork (79 stars in 3 days)
4. **BYOK / custom API keys** — Issues #22, #27, #32, #33; PR #51 (OpenRouter)
5. **Configurable hotkey** — Issue #35, PR #16
6. **TTS interruption** — Issue #36
7. **Clipboard copy** — Issue #43, PR #23
8. **Multi-language** — Issue #7
9. **Settings UI** — Issue #60 (new)
10. **Linux support** — Issues #13, #59 (new)

## Competitors
- **Clippi.us** — macOS only, "Windows coming soon"
- **GhostDesk** — Windows, $9.99/mo, no pointing / no voice
- **Screenpipe** — passive 24/7 recorder, not interactive
- **tekram/clicky-windows** — Electron, 14 stars, unfinished
- **trili.ai** — Windows sidebar + text chat + "STEP 1 OF 7" structured tutorials. Khan-Academy design philosophy — opposite of Clicky Windows's overlay + voice + conversational learn-by-doing model. Our edges: open source + BYOK + zero screen-cost overlay + voice-first + conversational + persistent memory.
- **Vercept** — acquired by Anthropic Feb 2026; strategic threat. Ship Phase 1 before they do.

## File Structure (Phase 1)
```
Clicky Windows/
├── CLAUDE.md           ← this file — project contract, auto-loaded every session
├── PRD.md              ← what + why + Codebase Architecture + User Journeys + Invariants
├── ROADMAP.md          ← step status + acceptance proof per step
├── DECISIONS.md        ← append-only architectural decision log
├── README.md           ← user-facing (written LAST, end of Phase 1)
├── app.py              ← Qt main orchestrator + PTT pipeline + debug logging
├── debug_log.py        ← Per-interaction debug folders (screenshots + timing + coords)
├── capture.py          ← screen capture + cursor + DPI + aspect-ratio resize + multi-screen
├── ai.py               ← AIClient abstract + AnthropicClient + GeminiClient + create_ai_client() factory (dual-SDK routing by MODEL_ID prefix — anthropic/* via anthropic SDK, google/* via openai SDK + OpenRouter OpenAI-compat endpoint)
├── overlay.py          ← per-monitor PyQt6 + Win32 click-through + cursor polygon + WaveformWidget (LISTENING) + SpinnerWidget (THINKING) + bezier flight arc (FLYING). All widgets follow cursor at 60Hz via _on_follow_tick. State machine: IDLE / LISTENING / THINKING / FLYING / SPEAKING — exactly one visual per state (verbatim port of farzaa/clicky OverlayWindow.swift).
├── stt.py              ← STT abstract + AssemblyAIStreamingSTT (u3-rt-pro + ForceEndpoint)
├── tts.py              ← TTS abstract + CartesiaSonicTTS (Sonic-3 streaming via iter_bytes())
├── hotkey.py           ← pynput Ctrl+Alt+Space PTT (suppress=False observe-only)
├── memory.py           ← MemoryStore — Karpathy markdown + SQLite WAL
├── config.py           ← .env loader + constants (API keys, MODEL_ID, HOTKEY, CANDIDATE_RESOLUTIONS, MEMORY_*, CARTESIA_*, ASSEMBLYAI_*)
├── requirements.txt    ← Phase 1 deps
├── .env.example        ← key placeholders
├── .gitignore          ← .env, API key files, debug_*.jpg, __pycache__, .superpowers/
├── tools/
│   ├── bench_path_a.py ← Mann-Whitney U + bootstrap CI latency benchmark (Phase 1.5 Step 2 Task 12)
│   └── lint_memory.py  ← Step 7.5. **SKIPPED 2026-04-20** per user verdict ("B0-only, dumb"). Not shipping unless B0 writeup specifically needs it.
├── tests/              ← ~100 tests target, mock-based, <3s full suite
│   ├── test_capture.py
│   ├── test_ai.py
│   ├── test_overlay.py
│   ├── test_stt.py
│   ├── test_tts.py
│   ├── test_hotkey.py
│   └── test_memory.py
└── docs/
    └── superpowers/
        └── plans/      ← per-component Superpowers plans (one combined plan doc per component)
```

## Rules (load-bearing — breaking any of these breaks the project)

- **API keys:** `.env` only for Phase 1. Never commit. `.gitignore` blocks `.env`, `*API*KEY*`, `*.key`, `*.pem`, `*secret*`, `Anthropic API.txt`, `STT TTS API.txt`. Phase 2 migrates to OS keychain via Python `keyring` lib.
- **Screenshots sent to Claude:** `capture.pick_resolution` picks from `[(1024,768),(1280,800),(1366,768)]` by closest aspect-ratio. PIL LANCZOS resize. **`overlay.hide_for_capture()` BEFORE every `mss.grab()`** — if Claude sees our own blue cursor in its input, it tries to point at itself (infinite feedback loop).
- **Overlay:** click-through (not focus-stealing), always-on-top, transparent background, no taskbar entry. **Per-monitor architecture** — one `QWidget` per physical monitor from `QGuiApplication.screens()`, routed via `screen_for_monitor()` metadata match. NEVER virtual-desktop-spanning (Qt 6 "islands-of-screens" gotcha on mixed-DPI Windows 11 — see DECISIONS.md 2026-04-11 "Per-monitor overlays"). Win32 layered-window flags (`WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`) applied via `ctypes` AFTER `show()`, OR'd in (never overwritten), followed by `SetWindowPos(SWP_FRAMECHANGED)`. `apply_clickthrough_styles` raises `RuntimeError` with `ctypes.WinError()` context on `SetWindowLongW` failure — no silent click-through breakage.
- **Hotkey:** Ctrl+Alt+Space via `pynput.Listener(suppress=False)` — **observe-only, never consume**. NEVER `suppress=True` (globally destructive — disables all typing system-wide). NEVER Ctrl+Space (VS Code IntelliSense). NEVER Ctrl+Shift+Space (Excel/Sheets Select-All). Claude Desktop for Windows users must disable its Ctrl+Alt+Space binding in Settings → Keyboard Shortcuts (same pattern Raycast/Flow-Launcher users follow). Full pivot history in DECISIONS.md 2026-04-12 (morning + evening).
- **DPI:** `ctypes.windll.shcore.SetProcessDpiAwareness(2)` at startup. Per-monitor v2. Mandatory for correct pointer placement on mixed-scaling multi-monitor setups.
- **Memory:** `MemoryStore` has 4 public methods (`__init__`, `recall`, `record`, `list_known_apps`) — NO `infer_skill_level` (removed 2026-04-12 per "not Khan Academy" user pushback). Markdown files + SQLite WAL. `recall()` returns the file tail capped at `MEMORY_RECALL_MAX_CHARS=3000`. Caller (Step 7 `app.py`) injects into the **user message text content block**, NOT `system=` param. User can `cat EXCEL.EXE.md` — transparency is the UX contract.
- **Threading:** only `pyqtSignal` crosses thread boundaries. No UI calls from worker threads. PyQt6 is not thread-safe. STT/AI/TTS all run on worker threads, communicate with the Qt main thread via `pyqtSignal`.
- **Clicky NEVER autonomously clicks.** We draw an overlay pointing at (x, y) — the user clicks themselves. Hard product boundary. See PRD.md § What Clicky Is NOT item 9.
- **`ai.py` uses vision-tag `[POINT:x,y:label]` pattern, NOT Computer Use API beta.** Clicky's `ElementLocationDetector.swift` is dead code (0 refs across 11 non-test Swift files). The 2026-04-11 "Use Computer Use API beta directly" decision is SUPERSEDED-FOR-PHASE-1. See DECISIONS.md 2026-04-12 (evening 3).
- **Reference-source read discipline** (2026-04-12 evening 3): for any component that ports code from a reference repo, read every non-trivial source file in the reference LINE-BY-LINE via `gh api` BEFORE drafting any design. Doc-level claims ("Clicky uses X") can be inherited assumptions that don't match the actual source. "100% context" means source reads, not doc reads. See `feedback_reference_source_read_discipline.md`.
- **Verification-not-caveating discipline** (2026-04-12 evening 3): never use *"note the caveat"* as an escape from verifying a non-trivial SDK / API / platform claim. WebSearch / `gh api` / installed source grep takes seconds. Caveats rot and mislead future-Claude. See `feedback_brutally_honest_mode.md` Verification discipline section.
- **Testing:** ~100 mock-based pytest tests target (post-refactor). DI pattern. Full suite green in <3s. No automated tests for the full screen→AI→overlay→voice loop — manual per-module `py -3.13 -m <module>` gates + demo video for E2E.
- **Provider abstraction from day 1:** `AIClient`, `STT`, `TTS` abstract bases. Phase 2 adds `OpenRouterClient` / `GeminiClient` / `ElevenLabsTTS` / `FasterWhisperSTT` as subclass drops — no refactor of `app.py` required.
- **Superpowers plans:** ONE combined plan doc per component at `docs/superpowers/plans/YYYY-MM-DD-<component>.md`. NO separate `specs/` doc (per the ceremony-vs-lean rule — three docs is bureaucracy). Boris #5 self-critique + `superpowers:code-reviewer` independent pass pre-commit for non-trivial feature commits.
- **Git:** private GitHub repo. Conventional commits. Never `--no-verify`, never `--force` to main. Commit at end of each step with acceptance proof. Never push without explicit user OK.

## Dependencies (Phase 1)
```
anthropic        # Claude SDK — plain vision messages.stream(). Supports OpenRouter via ANTHROPIC_BASE_URL env var
openai           # OpenRouter OpenAI-compat endpoint for Gemini + future providers (GeminiClient subclass, 2026-04-19 Phase 1.5 Step 1)
mss              # Multi-monitor DPI-aware screen capture
PyQt6            # Transparent per-monitor overlay + QPropertyAnimation
pynput           # Global hotkey (Listener suppress=False observe-only)
sounddevice      # Audio I/O (WASAPI via portaudio)
numpy            # Audio buffer manipulation
assemblyai       # STT streaming u3-rt-pro + ForceEndpoint
cartesia         # TTS Sonic-3 streaming via iter_bytes()
Pillow           # Image resize LANCZOS
python-dotenv    # .env loader

# Dev / test
pytest
pytest-mock
```

## Reference Repos
- [farzaa/clicky](https://github.com/farzaa/clicky) — original macOS Clicky (~5.2K LOC Swift, 3500 stars). Read line-by-line via `gh api` before designing any component port.
- [tekram/clicky-windows](https://github.com/tekram/clicky-windows) — unfinished Electron Windows port (14 stars)
- [danpeg/clicky](https://github.com/danpeg/clicky) — proactive-mode fork (79 stars in 3 days)
- [WKJBryan/Grafyn](https://github.com/WKJBryan/Grafyn) — Tauri 2.0 + Rust + Vue 3 reference for Phase 2 patterns (keychain migration, MCP server, settings UI, packaging)
