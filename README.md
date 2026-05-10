<p align="center">
  <img src="assets/clicky-logo.png" alt="Clicky Windows" width="180" />
</p>

<h1 align="center">Clicky Windows</h1>

<p align="center">
  A voice-driven, screen-aware AI buddy for Windows. Hold a hotkey, ask anything about whatever app you are looking at, and Clicky talks back and points at the answer with a blue cursor.
</p>

<p align="center">
  <a href="https://github.com/AbhishekVulla/clicky-windows/actions/workflows/test.yml"><img src="https://github.com/AbhishekVulla/clicky-windows/actions/workflows/test.yml/badge.svg" alt="tests" /></a>
  <img src="https://img.shields.io/badge/license-MIT-f4d35e" alt="MIT" />
  <img src="https://img.shields.io/badge/python-3.13-2563eb" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/tests-258%20passing-22c55e" alt="258 tests passing" />
  <img src="https://img.shields.io/badge/installer-87%20MB-7c3aed" alt="87 MB installer" />
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4" alt="Windows 10/11" />
</p>

> *"I just want to learn by doing."*
> Farza Majeed, on why he built the original Clicky

The #1 community request on [Farza's Clicky](https://github.com/farzaa/clicky) was a Windows version. I shipped it, plus the two features users asked for that the original does not have: persistent per-app memory, and a drop-in knowledge folder so Clicky understands obscure or company-internal software Claude does not already know about.

<!-- TODO: replace this placeholder with the YouTube demo link or assets/demo.gif once recorded -->
<p align="center">
  <em>Demo video coming soon. In the meantime, the section below walks through what it does.</em>
</p>

## What it does

You are working in some app. Excel, Fusion 360, Blender, Photoshop, a niche piece of materials engineering software you have never opened before. You hit a wall. You hold `Ctrl+Alt+Space`, ask a question out loud, release. Within about 1.7 seconds you hear the answer, and a blue cursor lands on the exact button or menu item you needed to click.

Three concrete examples:

- **In Excel.** Hold the hotkey, say *"how do I make this a pivot table"*, release. Clicky sees your spreadsheet, walks you through Insert → PivotTable, and points at the menu while it talks.
- **In Fusion 360.** Same thing. *"How do I extrude this sketch?"* Clicky sees the sketch, points at the Extrude tool in the ribbon, narrates the steps.
- **In Granta EduPack** (or any niche/proprietary software Claude has never seen training data for). Drop a markdown file with the docs into `~/Documents/Clicky Wiki/edupack.exe.md`. Clicky reads it on every interaction and now knows your software better than a generic AI assistant ever could.

Everything else, including your screenshots and your voice, runs through your own API keys and goes directly to Anthropic / AssemblyAI / Cartesia or ElevenLabs. Nothing routes through me.

## Quick install

1. Download `Clicky-Windows-Setup-v0.1.0.exe` from the [Releases](https://github.com/AbhishekVulla/clicky-windows/releases) page (~87 MB).
2. Run it. Windows SmartScreen will warn you (the EXE is unsigned for v0; SignPath OSS application is in flight). Click **More info** → **Run anyway**.
3. Launch Clicky from the Start Menu. A modal asks for three API keys:
   - [Anthropic](https://console.anthropic.com/settings/keys) for Claude Sonnet 4.6 (vision and reasoning)
   - [AssemblyAI](https://www.assemblyai.com/dashboard/signup) for Universal-3 streaming speech-to-text
   - [Cartesia](https://play.cartesia.ai/sign-in) for Sonic-3 voice output (or pick ElevenLabs from the dropdown)
4. Hit `Ctrl+Alt+Space`, ask something, release.

Free tier signups exist for all three. Total cost for a typical 30-second interaction is around $0.016.

<!-- TODO: USER to add screenshots: assets/screenshots/installer.png, assets/screenshots/settings-dialog.png -->

## How it works

```mermaid
graph TD
    USER[User holds Ctrl Alt Space]

    subgraph OS_Layer [OS Layer Win32]
        HOTKEY[pynput observe-only hook<br/>never suppresses keys]
        DPI[Per-monitor v2 DPI awareness]
        MUTEX[Single-instance mutex<br/>no multi-PTT chaos]
    end

    subgraph Local_Pipeline [Local Pipeline 4 things kick off in parallel]
        STT[AssemblyAI WebSocket<br/>~466ms finalize via ForceEndpoint]
        CAP[mss multi-monitor capture<br/>overlay hidden first]
        MEM[Memory recall<br/>per-app markdown tail]
        KB[KB lookup<br/>Clicky Wiki folder]
    end

    subgraph Cloud_APIs [Cloud APIs BYOK never proxied]
        CLAUDE[Claude Sonnet 4.6 streaming<br/>2x cache_control breakpoints]
        TTS[Cartesia or ElevenLabs<br/>sentence-level prefetch]
    end

    subgraph Output [Output Qt main thread]
        OVERLAY[PyQt6 per-monitor overlay<br/>Win32 layered click-through]
        AUDIO[sounddevice playback]
        REC[Memory record<br/>markdown append + SQLite WAL]
    end

    USER --> HOTKEY
    HOTKEY --> STT
    HOTKEY --> CAP
    HOTKEY --> MEM
    HOTKEY --> KB

    STT --> CLAUDE
    CAP --> CLAUDE
    MEM --> CLAUDE
    KB --> CLAUDE

    CLAUDE -->|streaming text| TTS
    CLAUDE -->|POINT x y label tag| OVERLAY
    TTS --> AUDIO
    CLAUDE --> REC

    classDef os fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef local fill:#dbeafe,stroke:#2563eb,color:#0f172a
    classDef cloud fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef output fill:#ede9fe,stroke:#7c3aed,color:#3b0764

    class HOTKEY,DPI,MUTEX os
    class STT,CAP,MEM,KB local
    class CLAUDE,TTS cloud
    class OVERLAY,AUDIO,REC output
```

The hotkey listener observes Ctrl+Alt+Space without consuming the keys. On release, four things kick off in parallel: speech-to-text finalizes, the screen gets captured, per-app memory gets recalled, and a knowledge-base file gets looked up if one exists. Claude Sonnet 4.6 receives the screenshot plus the transcript plus the memory plus the KB, and streams a response. Sentences flush to the TTS provider as soon as a `.!?` boundary is hit, so the user starts hearing audio while Claude is still generating. A `[POINT:x,y:label]` tag in the response drives a per-monitor PyQt6 overlay to point at the exact pixel.

## Engineering decisions worth highlighting

The interesting parts. Each of these is a problem I hit, the gotcha I had to figure out, and the measured win.

### 1. Sub-2s first-audible-word despite three sequential APIs

The naive pipeline is hotkey → STT (wait) → screenshot (wait) → Claude vision (wait) → TTS (wait). That is roughly 3.7 seconds of latency for a one-sentence response. Unusable.

What fixed it:

- **Parallel kick-off.** STT, screen capture, memory recall, and KB lookup all start the moment the user releases the hotkey. They run on separate worker threads and feed Claude as soon as all four finish. Capture is the slowest at ~50ms, so the wall-clock cost is roughly 50ms instead of the sum.
- **Sentence-level streaming.** The Claude response is consumed token by token. As soon as a `.!?` boundary lands, that sentence gets flushed to the TTS provider. By the time Claude generates sentence three, sentence one is already playing.
- **Cartesia "Option B" HTTP double-buffer.** Two background threads on the TTS side: one prefetches the next sentence while the other plays the current one. Inter-sentence gaps drop to roughly zero. Implementation is in [`tts.py`](tts.py) (`CartesiaSonicTTS._prefetch_worker` and `_playback_worker`).

Measured first-audible-word for a multi-sentence response: about 1.7 seconds. For a single-sentence response it is closer to 4-6 seconds because the first-sentence TTFB dominates and there is nothing to overlap.

### 2. Win32 layered click-through overlay, per-monitor DPI-aware

The blue cursor that points at things has to do four things at once:
- always on top
- click-through (mouse events pass to the app underneath)
- never steal focus
- correct pixel position on mixed-DPI multi-monitor setups (a 4K external monitor at 200% scaling next to a 1080p laptop screen at 100%)

Qt 6 has a known gotcha here. If you make one giant overlay that spans the virtual desktop, it renders at the wrong size on at least one of the monitors. The fix is to spawn one `QWidget` per physical screen and route the pointer to the correct one via `QGuiApplication.screens()` metadata. See [`overlay.py`](overlay.py).

The click-through behavior comes from Win32 layered-window flags applied via `ctypes` (`WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`). They have to be applied AFTER `show()`, OR'd in (never overwritten), and followed by `SetWindowPos(SWP_FRAMECHANGED)`. Get any of those wrong and the overlay either disappears, eats clicks, or starts blocking the taskbar.

### 3. A hotkey that does not break your typing

`pynput.Listener(suppress=False)` is observe-only. It sees keypresses, the OS still delivers them to whatever app is focused. This is load-bearing. Setting `suppress=True` installs a `WH_KEYBOARD_LL` hook that globally blocks every keystroke from reaching anything. Your typing breaks system-wide. Do not do this.

The hotkey choice itself was a three-step pivot:

- **Alt+Space.** Conflicts with Windows window menu and Copilot. Killed.
- **Ctrl+Shift+Space.** Conflicts with Excel's "Select entire worksheet" binding. Because the listener is observe-only, Excel ALSO receives the keypress and wipes your selection every time you invoke Clicky. Killed during the Excel demo.
- **Ctrl+Alt+Space.** No known conflicts. Ergonomic enough. Three fingers but all on the left side. Shipped.

A clean solution for Alt+Space exists: Win32 `RegisterHotKey` claims the combo at the OS level so other apps never see it. That is a Phase 1.5 drop-in replacement.

### 4. Multi-provider TTS via progressive-disclosure UX

The naive way to add a second TTS provider is to add another field to the settings dialog. Three required keys becomes four. Then you add a second STT provider and it becomes five. By the time you have one option per category you are at six required password fields on a first-launch dialog. That is well past the documented onboarding-abandonment cliff.

What shipped instead: three category rows (LLM / STT / TTS), each with a dropdown for provider plus a single API key field for whichever provider is currently selected. Switch the dropdown, the field rebinds to that provider's keyring slot. One key visible at a time, vendor flexibility preserved.

The `ElevenLabsTTS` class mirrors `CartesiaSonicTTS` Option B prefetch+playback architecture verbatim with three deliberate differences:

- `_generate_response` calls `client.text_to_speech.stream()` which returns an `Iterator[bytes]` directly, instead of Cartesia's `generate(...)` which blocks for the full body and then exposes `.iter_bytes()`.
- `_play_response` converts each int16 PCM chunk to float32 inline (`np.frombuffer(chunk, np.int16).astype(np.float32) / 32768.0`). Cartesia emits float32 directly so no conversion is needed.
- `stop()` is 5-pronged instead of 6-pronged. The ElevenLabs SDK does not expose a `response.close()` method, so cancellation is just "set the cancel event, break the for-loop, let Python GC close the underlying httpx connection."

Sample rate is per-provider (Cartesia 44.1kHz, ElevenLabs 22.05kHz) because ElevenLabs free tier only ships 22.05kHz PCM. Each subclass owns its own `sample_rate` attribute and constructs its own `sounddevice.OutputStream`. Switching providers in the dialog requires a Clicky restart but no code change.

### 5. Single-instance mutex preventing the multi-PTT chaos

A user reported double-clicking the installed Start Menu shortcut and seeing three blue cursor icons stacked in the system tray. Worse, every Ctrl+Alt+Space press triggered three overlapping voice responses to the same question.

The cause: `pynput.Listener(suppress=False)` is observe-only, which means multiple Clicky processes coexist as independent `WH_KEYBOARD_LL` hooks. Windows broadcasts every keypress to every installed hook. N processes means N parallel STT → Claude → TTS pipelines.

The fix is the canonical Win32 named-mutex pattern that Spotify, Slack, Discord, and Raycast all use: acquire a mutex named `Local\\ClickyWindows-SingleInstance-v1` BEFORE constructing the QApplication. First instance gets the mutex. Second instance sees `ERROR_ALREADY_EXISTS` (183), shows a `MessageBoxW` directing the user to the existing tray icon, and exits with `sys.exit(0)`.

Three ctypes details that all matter:

- `kernel32.CreateMutexW.restype = wintypes.HANDLE` to prevent x64 HANDLE truncation. Without explicit `restype`, ctypes defaults to `c_int` which is 32-bit, which silently corrupts 64-bit handles.
- `bInitialOwner=False`. We want existence-as-a-flag, not ownership semantics. `True` would make the first instance pointlessly own a mutex it never releases.
- `Local\\` namespace prefix. Per-logon-session, not per-machine. So two different Windows users on the same RDP host can each run their own Clicky.

Implementation in [`app.py`](app.py).

### 6. Markdown memory and a drop-in knowledge folder

Two stores, both human-readable markdown, no vector DB.

**Auto-learned memory.** One `.md` file per app at `~/.clicky-windows/memory/<app>.exe.md`. Every interaction appends a structured block (timestamp, app, window title, transcript, response, pointer target). On the next interaction in the same app, the last 1500 characters get tail-truncated and injected into Claude's user-message text block. Not the system prompt. The transparency contract is "you can `cat EXCEL.EXE.md` and read everything Clicky knows about you."

**User-uploadable KB.** One `.md` file per app at `~/Documents/Clicky Wiki/<app>.exe.md`. Up to 60K characters get injected as a second `cache_control` breakpoint in the system prompt, marked as "authoritative reference for this app." This is how you teach Clicky niche or company-internal software Claude has never seen.

The pattern is in the lineage of [Andrej Karpathy's LLM Wiki idea](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). Deliberately simplified for runtime context injection rather than long-form synthesis. I considered shipping the full three-layer wiki + ingest + lint pipeline pattern early on and retracted it as overengineered for this use case. Per-app files do the job.

## Privacy

Nothing leaves your machine, except the things you explicitly send to your own APIs.

- API keys live in Windows Credential Manager via DPAPI per-user encryption. Better than plaintext `.env` but does not protect against malware running as your user account.
- Screenshots, voice, transcripts, and Claude responses go directly from your machine to Anthropic / AssemblyAI / Cartesia or ElevenLabs using YOUR keys. No proxy, no logging server, nothing routes through anyone else.
- Per-app memory and the KB folder live on your local disk in plain markdown. You can read them, edit them, delete them.

This is the BYOK model from day 1, by deliberate contrast with the upstream Clicky which uses a Cloudflare Worker proxy that holds the API keys server-side.

## Limitations

- **Unsigned EXE.** Windows SmartScreen will warn the first time you run the installer. Click "More info" → "Run anyway". A SignPath Foundation OSS application is in flight; once approved, the warning will go away.
- **Windows-only.** Per-monitor DPI awareness, layered click-through windows, and Win32 mutex are all Windows APIs. A Tauri rewrite for cross-platform is on the long-term roadmap, not this version.
- **No auto-updater.** Check the Releases page for new versions. Auto-update is a Phase 2 add if real demand emerges.
- **Multi-monitor works but my dev box is single-monitor.** The per-monitor architecture is correct in principle and tested via `tests/test_overlay.py`. End-to-end multi-monitor verification at scale is on the testing backlog.
- **BYOK costs money.** A typical 30-second interaction is around $0.016. Free tiers exist for all three providers but you will eventually have to pay if you use it heavily.

## Acknowledgments

Built on top of the original [Clicky by Farza Majeed](https://github.com/farzaa/clicky), which is the macOS version this is a Windows port of. The memory pattern is in the lineage of [Andrej Karpathy's LLM Wiki idea](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), simplified to per-app files for the runtime context-injection use case rather than long-form synthesis.

## License and support

[MIT](LICENSE). Personal project. PRs welcome but I make no SLA on review timing. If you find a bug, please file an issue and attach the contents of `~/.clicky-windows/debug/` from a recent interaction so I can see what happened.
