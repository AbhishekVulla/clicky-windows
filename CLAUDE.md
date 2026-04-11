# Clicky Windows — Screen-Aware AI Buddy with Persistent Memory

## What This Is
A Windows desktop app inspired by [Clicky](https://github.com/farzaa/clicky) (macOS, by Farza Majeed). An AI buddy that sees your screen, listens to your voice, responds with speech, and physically points at UI elements using a transparent overlay. The differentiator: **persistent memory** — it remembers what you've struggled with and adapts over time.

## Why This Exists
Clicky is macOS-only (Swift, ScreenCaptureKit, AppKit). Windows has 76% of desktop users and zero polished screen-aware AI buddies with pointing + voice. The #1 community request on Clicky's GitHub (Issue #26, 18 comments) is a Windows version. tekram/clicky-windows (Electron, 14 stars) exists but is early/unpolished.

## How It Works (Core Loop)
```
1. User presses global hotkey (Ctrl+Shift+Space, push-to-talk) — NEVER Ctrl+Space (VS Code IntelliSense conflict)
2. App captures screenshot of monitor under cursor (DPI-aware, multi-monitor)
3. Screenshot resized to aspect-ratio-matched resolution from [(1024,768),(1280,800),(1366,768)]
4. Screenshot + voice transcript + recalled memory for this app sent to Claude Sonnet 4.6
5. Call uses Claude Computer Use API beta directly (tool: computer_20251124, header: anthropic-beta: computer-use-2025-11-24)
   — mirror Clicky's ElementLocationDetector.swift for pixel-perfect coordinates
6. Claude returns text + tool_use blocks with {"action":"left_click","coordinate":[x,y]} in declared resolution
7. Coordinates clamped, scaled back to physical pixels, routed to overlay
8. Transparent click-through overlay animates pointer to (x,y) on the correct monitor
9. TTS speaks the response (background thread, concurrent with animation)
10. Interaction persisted to ~/.clicky-windows/memory/<app>.md (Karpathy-style markdown)
11. SQLite index updated for fast lookup on next interaction
```

## Build Phases (revised — no Tauri rewrite pre-committed)

### Phase 1: Python MVP with Persistent Memory (1-2 weeks)
**Goal:** validate the differentiated hypothesis — "Clicky + persistent memory is meaningfully better than stateless Clicky." Not just a Windows port (Mushtaq Bilal already vibe-coded that in 2 hours).

- `mss` for screen capture (multi-monitor, DPI-aware via `SetProcessDpiAwareness(2)`)
- PyQt6 transparent overlay with Win32 layered window flags via `ctypes` for true click-through
- `pynput.Listener(suppress=True)` for Ctrl+Shift+Space push-to-talk
- **STT — AssemblyAI Universal-3 realtime-pro streaming** via WebSocket with `ForceEndpoint` on hotkey release for ~150ms P50 PTT finalization. Python SDK: `assemblyai` (official). Audio capture: `sounddevice.RawInputStream` PCM16 16kHz mono 1024-frame chunks (matches Clicky's `AVAudioEngine.installTap` buffer exactly so Phase 2 provider swap is drop-in). API key: `ASSEMBLYAI_API_KEY` in `.env`. [Phase 2 fallbacks: `FasterWhisperSTT` local CPU offline, `GroqWhisperSTT` batch cloud simpler code, `DeepgramNova3STT` streaming — all kept as subclass candidates for future offline/pricing preferences.]
- **TTS — Cartesia Sonic-3 WebSocket streaming** for ~150-250ms TTFB with the most expressive "buddy" voice quality in the cloud TTS field as of April 2026. Python SDK: `cartesia` (official, async WebSocket). Output format: PCM float32 44.1kHz streamed chunks played via `sounddevice` output stream. API key: `CARTESIA_API_KEY` in `.env`. No `Pyttsx3TTS` fallback in Phase 1 (YAGNI — reactive fix is a 1-hour subclass). [Phase 2 candidates: `Pyttsx3TTS` SAPI offline, `EdgeTTS` free Microsoft Neural, `ElevenLabsFlashTTS`, `DeepgramAura2TTS`.]
- **Claude response streaming + sentence-level TTS chunking** (Step 7 `app.py` requirement) — subscribe to `content_block_delta` events with `delta.type == "text_delta"`, accumulate into a buffer, flush complete sentences to `tts.speak_sentence()` while Claude still generates remaining tokens. Tool_use block stays buffered until `content_block_stop` for it, then fires the overlay pointer animation. Saves ~300-500ms of perceived latency on multi-sentence responses. This is a genuine latency win Clicky does NOT do (they wait for the full response then play).
- Anthropic SDK with **Claude Computer Use API beta** (`computer_20251124` tool, `computer-use-2025-11-24` header) — mirror Clicky's `ElementLocationDetector.swift`
- **SQLite + Karpathy-style markdown memory** in `~/.clicky-windows/memory/<app>.md` — THE DIFFERENTIATOR, in Phase 1 not Phase 2
- Provider abstraction classes (`AIClient`, `STT`, `TTS`) so Phase 2 can swap implementations without touching `app.py`
- **Phase 1 done when:** 5+ real user sessions on a real task where memory recall noticeably improves experience, recorded as demo video, `lint_memory.py` surfaces the unexpected finding

### Phase 2: Harden in Python (2-4 weeks, only if Phase 1 validates)
**Goal:** match the B0 bar by rigour proportional to problem — mirror Wallee (3K LOC Python + 517 tests + 60 replay scenarios), not by rewriting to Rust.

- 50-100+ pytest unit tests across capture, ai, memory, hotkey
- Replay scenarios for the full loop (recorded interactions → assert same output)
- Proactive mode (idle detection, focused-window capture) — targeted at the specific patterns found in Phase 1's markdown memory
- BYOK / OpenRouter support (`OpenRouterClient` subclass with vision-tag fallback since OpenRouter can't proxy Computer Use beta)
- ElevenLabs TTS, AssemblyAI streaming STT (subclass the abstract base)
- Clipboard copy of responses (Issue #43)
- Configurable hotkey UI
- PyInstaller bundle + clean install path
- "Unexpected finding" writeup published — the B0 editorial standard

### Phase 3: Tauri Rewrite (ONLY if Phase 2 hits a Python-specific wall)
**Not pre-committed.** Only triggered if Python install experience is too rough, performance too slow, or threading deadlocks become unfixable. Most likely: never needed.

### Explicitly NOT Phase 1
- Proactive mode (Karpathy: "wait for the data — you don't know what to be proactive ABOUT until you've used memory yourself for 1-2 weeks")
- BYOK / OpenRouter (Computer Use beta requires Anthropic-direct)
- ElevenLabs, AssemblyAI, clipboard copy, settings UI, tray icon, bezier animations, auto-updater, installer
- Automated tests for the full screen→AI→overlay→voice loop (no headless mode exists — manual verification with demo video is the strategy)

## Key Technical Facts
- **Claude Computer Use API is platform-agnostic.** You send a screenshot + declared display dimensions, get back `{"action":"left_click","coordinate":[x,y]}` in tool_use blocks. It doesn't care what OS the screenshot came from.
- **Phase 1 uses Computer Use API beta directly** (`tools=[{"type":"computer_20251124","name":"computer","display_width_px":...,"display_height_px":...}]`, header `anthropic-beta: computer-use-2025-11-24`) — mirror Clicky's `ElementLocationDetector.swift`. The beta activates Claude's specialized pixel-counting training, which is meaningfully more accurate than vision-tag regex fallback.
- **What's macOS-only in Clicky (and what Windows equivalents we use):** ScreenCaptureKit → `mss`; NSPanel overlay → PyQt6 + Win32 layered window flags via ctypes; CGEvent tap → `pynput.Listener(suppress=True)`; AVAudioEngine → `sounddevice` (WASAPI via portaudio).
- **Recommended screenshot resolution (per Anthropic docs):** one of `(1024,768)`, `(1280,800)`, `(1366,768)`. Clicky picks by closest aspect-ratio match to the actual display — we mirror this to avoid distortion that degrades X-axis accuracy.
- **DPI awareness is mandatory.** Call `ctypes.windll.shcore.SetProcessDpiAwareness(2)` at startup for per-monitor v2 DPI. Without it, multi-monitor + mixed-scaling setups put the pointer in the wrong place.
- **The memory differentiator is Karpathy-style markdown**, not a vector DB. One `.md` per Windows app in `~/.clicky-windows/memory/`. Claude reads the file directly into system prompt. SQLite only indexes "which apps exist and how many interactions each has." Zero retrieval complexity, fully human-readable, trivial to lint.

## Validated User Demands (from Clicky GitHub issues + forks + social)
1. **Windows version** — #1 request, 18 comments, 2 independent forks
2. **Persistent memory** — "stateless Claude wrapper, no memory between sessions" (Issue #30)
3. **Proactive mode** — danpeg fork got 79 stars in 3 days without marketing
4. **BYOK / multi-model** — OpenRouter, local models (Issue #27, 5 comments)
5. **Clipboard copy** — can't paste AI responses (Issue #43)
6. **Configurable hotkey** — 3-finger combo is awkward (Issue #35)
7. **Security** — no shared proxy, no baked-in API keys

## Competitors
- **Clippi.us** — polished Clicky, Mac-only, "Windows coming soon"
- **GhostDesk** — Windows, $9.99/mo, screen-share invisible, NO pointing/voice
- **Screenpipe** — 200K installs, 24/7 recording, not interactive
- **tekram/clicky-windows** — Electron, 14 stars, BYOK + HIPAA mode, early
- **Vercept** — acquired by Anthropic (Feb 2026), building screen-aware AI internally

## File Structure (Phase 1)
```
Clicky Windows/
├── CLAUDE.md           ← this file (project contract)
├── PRD.md              ← what and why
├── ROADMAP.md          ← where are we (status + acceptance proof per step)
├── DECISIONS.md        ← architectural decision log, append-only
├── README.md           ← user-facing (written LAST, after Phase 1 demo works)
├── app.py              ← main orchestrator (Qt signals, thread discipline)
├── capture.py          ← screen capture + cursor + DPI + aspect-ratio resize
├── ai.py               ← AIClient abstract + AnthropicClient (Computer Use API beta)
├── overlay.py          ← PyQt6 + Win32 layered window transparent click-through
├── stt.py              ← STT abstract + FasterWhisperSTT
├── tts.py              ← TTS abstract + Pyttsx3TTS
├── hotkey.py           ← pynput Ctrl+Shift+Space push-to-talk (suppress=True)
├── memory.py           ← Karpathy markdown (~/.clicky-windows/memory/<app>.md) + SQLite index
├── config.py           ← env loading + constants (HOTKEY, WHISPER_MODEL, CANDIDATE_RESOLUTIONS, MODEL_ID, COMPUTER_USE_BETA)
├── requirements.txt    ← dependencies
├── .env.example        ← ANTHROPIC_API_KEY=
├── .gitignore          ← .env, __pycache__/, debug_*.jpg, whisper-cache/
├── tools/
│   └── lint_memory.py  ← standalone CLI: Karpathy-style weekly health check
├── tests/              ← pytest unit tests (target ~50-80)
│   ├── test_capture.py
│   ├── test_ai.py
│   ├── test_memory.py
│   └── test_hotkey.py
└── docs/
    └── superpowers/
        └── plans/      ← per-component Superpowers execution plans (generated during build)
```

## Rules
- **API keys:** Never commit to git. Use `.env` file, add to `.gitignore`. Anthropic-direct only in Phase 1 (Computer Use beta requires it).
- **Screenshots sent to Claude:** Pick resolution from `[(1024,768),(1280,800),(1366,768)]` by closest aspect-ratio match to the monitor (mirror Clicky). Resize with PIL `LANCZOS` to exact pixel dims. **Hide overlay before capture** (otherwise Claude sees its own pointer).
- **Overlay:** Must be click-through (not steal focus), always-on-top, transparent background, no taskbar entry. **Per-monitor architecture**: one `QWidget` overlay per physical monitor from `QGuiApplication.screens()`, routed via `screen_for_monitor()` metadata match against `CaptureResult.monitor`. (Originally CLAUDE.md said "spans full virtual desktop" — overridden 2026-04-11 because Qt 6's "islands-of-screens" geometry on mixed-DPI Windows 11 makes single-widget spanning unreliable. See DECISIONS.md entry of the same date.) PyQt6's `WA_TransparentForMouseEvents` alone is NOT enough on Windows — you MUST also apply Win32 layered window flags via `ctypes`: `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW` AFTER `show()` (OR in, never overwrite), followed by `SetWindowPos` with `SWP_FRAMECHANGED` so the change takes effect immediately. `apply_clickthrough_styles` raises `RuntimeError` with `ctypes.WinError()` context if `SetWindowLongW` returns 0 — no silent click-through failures.
- **Hotkey:** Ctrl+Shift+Space push-to-talk via `pynput.keyboard.Listener(suppress=True)` low-level hook. Fallback: Ctrl+Shift+Space. **NEVER Ctrl+Space** — conflicts with VS Code IntelliSense which would break developer users' autocomplete.
- **DPI:** Call `ctypes.windll.shcore.SetProcessDpiAwareness(2)` (per-monitor v2) at startup. Per-monitor DPI is mandatory on modern Windows — without it, the pointer lands in the wrong place on the secondary monitor.
- **Coordinate spaces:** Three of them, document every conversion: (1) physical pixels on the monitor, (2) logical pixels in Qt, (3) declared resolution Claude returns coords in. Clamp Claude's coords before scaling.
- **Memory (Phase 1, not Phase 2):** Karpathy-style — markdown files in `~/.clicky-windows/memory/<app>.md` + SQLite index at `~/.clicky-windows/index.db`. Each interaction appended to the app's markdown file with timestamp, window title, user question, Claude response, pointer targets. `recall()` reads the file directly into Claude's system prompt (no embeddings, no RAG). User can `cat EXCEL.EXE.md` to see what Clicky remembers about them.
- **Threading:** Single strict rule — only Qt signals cross thread boundaries. **No UI calls from worker threads, ever.** PyQt6 is not thread-safe. Audio/Whisper/Anthropic API all run on worker threads, communicate with the Qt main thread via `pyqtSignal`.
- **Testing target:** ~50-80 pytest unit tests covering coordinate math, API response parsing, memory CRUD, hotkey state machine. **No automated tests for the full screen→AI→overlay→voice loop** — there's no headless mode for it. Manual verification per component + recorded demo video for E2E.
- **Provider abstraction:** Wrap external services in abstract classes (`AIClient`, `STT`, `TTS`) from day 1 so Phase 2 multi-provider support is a subclass, not a refactor. Mirror Wallee's `BuddyTranscriptionProvider` protocol pattern.
- **Git:** GitHub repo (private initially). Conventional commits. Commit at end of each step. Superpowers plugin (local scope only) for brainstorm → TDD → review discipline on the 5 hard components (capture, ai, overlay, memory, app). Skip ceremony for trivial files.
- **Superpowers plans:** Generated per-component to `docs/superpowers/plans/YYYY-MM-DD-<component>.md` before writing any code for that component.

## Dependencies (Phase 1)
```
anthropic        # Claude SDK (Computer Use API beta)
mss              # Screen capture, multi-monitor
PyQt6            # Transparent overlay + QPropertyAnimation
pynput           # Global hotkey with low-level hook suppression
sounddevice      # Audio capture (WASAPI via portaudio)
numpy            # Audio buffers
assemblyai       # Streaming STT via u3-rt-pro WebSocket + ForceEndpoint for push-to-talk (~150ms P50 finalization). Matches what Clicky uses.
cartesia         # Streaming TTS via Sonic-3 WebSocket (~150-250ms TTFB, expressive "buddy" voice). Python async SDK with WebSocket support built-in.
sounddevice      # Audio capture (PCM16 16kHz mono 1024-frame chunks for AssemblyAI) + audio playback (Cartesia PCM float32 44.1kHz output stream)
Pillow           # Image resize with LANCZOS
python-dotenv    # Load .env for API keys
```

Dev/test dependencies:
```
pytest           # Unit tests (target ~50-80)
pytest-mock      # Mock Anthropic client, audio device, screen capture in tests
```

## Reference Repos
- [farzaa/clicky](https://github.com/farzaa/clicky) — original macOS app (5,200 LOC Swift)
- [tekram/clicky-windows](https://github.com/tekram/clicky-windows) — Electron Windows port (14 stars)
- [danpeg/clicky](https://github.com/danpeg/clicky) — proactive tutor mode fork (79 stars)
- [WKJBryan/Grafyn](https://github.com/WKJBryan/Grafyn) — Tauri+Rust reference for Phase 2 architecture
