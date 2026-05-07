# Clicky Windows — Architectural Decision Records

**Append-only.** Never delete entries. If a decision is reversed, add a NEW entry referencing the old one.

Format: `## YYYY-MM-DD: Short title` → Context → Decision → Alternatives considered → Consequences → References.

For **what and why** → [PRD.md](PRD.md)
For **where are we now** → [ROADMAP.md](ROADMAP.md)
For **how** → [CLAUDE.md](CLAUDE.md)

---

## 2026-05-07: Sprint 4 ship-gate UX refinements (first-audible-word log + plain-English privacy line)

**Context:** Two USER-driven refinements after the Sprint 4 manual UX verification:

1. USER's bundled-EXE per-interaction debug log showed `[+13307ms] CLAUDE: done` and reasonably read it as "13s latency." That timestamp is the END of the Claude stream, NOT the moment the user actually hears something — TTS streams sentence-by-sentence and starts speaking on the first `.`/`!`/`?` boundary, ~1.7s after release per [DECISIONS.md 2026-04-20]. But the per-interaction `interaction.log` had no log line for first-audible-word, so the real perceived latency was invisible. USER asked: *"just add the log bruh."*
2. USER tested the Settings dialog from the bundled install and flagged `"No server, no telemetry."` as jargon — non-technical users don't know what telemetry means. Locked replacement (USER pick from 3 candidates 2026-05-07): `"Nothing leaves your machine."`

**Decision:** One combined commit (`3d3f5a0`) covering both. 5 files, +157/−3.

**(1) First-audible-word log via one-shot armed callback.** New `arm_first_chunk_callback(cb)` method on the `TTS` abstract base, default-implemented as `self._first_chunk_callback = cb`. Both `CartesiaSonicTTS.__init__` and `ElevenLabsTTS.__init__` add a `_first_chunk_callback: Callable[[], None] | None = None` slot. In each `_play_response` chunk loop, AFTER the first successful `play(samples)`, fire-and-clear:

```python
play(samples)
cb = self._first_chunk_callback
if cb is not None:
    self._first_chunk_callback = None
    try:
        cb()
    except Exception:
        pass  # never let a logging error break audio
```

`app.py:_pipeline_worker` arms the callback once per interaction, just before entering the Claude streaming context: `self._tts.arm_first_chunk_callback(lambda: dbg.log("TTS: first audible chunk played"))`. `dbg` captures by reference; `DebugSession.log` prepends elapsed-ms automatically. Slot clears after firing → subsequent sentences in the same interaction don't re-fire; next interaction re-arms.

3 new tests cover Cartesia + ElevenLabs callback firing + slot-clearing + exception-resilience (a callback that raises must NOT break audio playback). Test count 255 → 258.

**(2) Privacy line:** `"No server, no telemetry."` → `"Nothing leaves your machine."` at `settings_dialog.py:180-186`. Test `TestSettingsDialogRender::test_dialog_has_privacy_line` updated to tolerate both old and new wording — asserts on `"encrypted"` (stable) AND any of `"leaves your machine"` / `"no telemetry"` / `"no server"` so a future copy tweak doesn't break the test silently.

**Alternatives considered:**

1. *pyqtSignal from TTS thread → main thread → log via dbg* — more thread-safe than direct `dbg.log` call from playback thread. Rejected: `DebugSession.log` is a single-line file-IO append (atomic in CPython), and we never read the log from another thread within the same interaction. Overkill for a diagnostic log.
2. *Console `print()` instead of `dbg.log()`* — would land in stdout, not the per-interaction debug folder. Rejected: defeats the diagnostic purpose; user wants the timing in the same `interaction.log` they were already reading.
3. *Three privacy-line candidates: "Nothing leaves your machine." / "Clicky doesn't track or upload anything." / "We don't collect or send any data."* — USER picked option 1 as cleanest. Locked.
4. *Reusing the existing `tts.stop()` to know when audio kicked in* — semantically wrong; `stop()` is for cancellation, not for "audio just started." First-chunk-callback is the right primitive.

**Consequences:**

- Per-interaction debug log now surfaces first-audible-word timing directly. Closes the measurement gap that prompted the latency confusion. Future debugging of perceived-latency complaints can read this number without instrumentation work.
- Privacy line is plain English with the same no-egress assurance — no jargon, no "what is telemetry?" friction.
- Test suite: 255 → 258 (+3). Bundle still 280 MB, Setup.exe still 87 MB (no new SDK deps, only logic + UI tweaks).
- The one-shot armed-callback pattern is reusable for future per-interaction TTS-event hooks (e.g. "first-sentence-finished," "playback-complete") without further breaking changes to the TTS abstract base.

**References:**

- Commit `3d3f5a0`
- TTS callback pattern: `tts.py` — `arm_first_chunk_callback` on the `TTS` abstract base + `_first_chunk_callback` slot in `CartesiaSonicTTS` + `ElevenLabsTTS`
- `app.py:566-577` — arming site (just before `with self._ai.ask_stream(...)` block)
- `settings_dialog.py:180-186` — privacy QLabel text
- USER decisions recorded in plan file `streamed-tumbling-sunbeam.md` "STATUS (2026-05-07 ship-gate punch list)" section

---

## 2026-05-06: Sprint 4 — Multi-provider settings UX + privacy framing + ElevenLabs TTS (planning ADR; not yet shipped)

**Context:** USER pushback after Sprint 3.8 verification revealed three coupled UX problems that the original Sprint 4 plan ("drop a 4th OPTIONAL `ELEVENLABS_API_KEY` field next to the existing 3 required fields") would have compounded:

1. HTML rendering bug in current settings dialog — `<a href="...">...</a>` displayed as literal escaped text in QLabel rows because `setTextFormat(RichText)` was never called. Visible in USER's post-Sprint-3.8 screenshot.
2. 6 input fields = onboarding-abandonment cliff. The dialog already had 3 required keys; adding ElevenLabs as a flat 4th + future Deepgram = 6 password boxes on first-launch dialog. >3 fields hits documented onboarding abandonment.
3. Privacy framing too quiet — single buried sentence about Windows Credential Manager wasn't sufficient reassurance for users pasting API keys into an unsigned `.exe`. But a wall of reassurance starts to sound suspicious.

**Decision:** Restructure the settings dialog around a **3-category dropdown UX with progressive disclosure** matching the pattern shipped by tekram/clicky-windows + Cursor + OpenInterpreter. Each category (LLM / STT / TTS) gets a single dropdown row + key field for the SELECTED provider only. Dropdown change handler swaps the keyring slot the key field reads/writes. ElevenLabs ships as the second TTS provider in the same sprint to demonstrate the dropdown architecture works with ≥2 options.

Concrete decisions locked 2026-05-06 via USER answers:

1. **ONE provider per category at any time** — no saved fallbacks. Switching provider = re-enter dialog, change dropdown, re-paste new key. Power-user multi-key UX deferred indefinitely (not a v0 portfolio concern).
2. **LLM dropdown shows Anthropic only** — GeminiClient infrastructure stays in `ai.py` (Phase 1.5 Step 1) but doesn't appear in the dropdown. Verified A/B data from 2026-04-19 shows Gemini 2.5 Flash 230px miss + 340ms slower than Claude on identical workload; Gemini 3 Flash returns coords in [0,1000] normalized space (not pixel space). Re-benchmark when Gemini 4 ships or when normalized-coord issue closes.
3. **Lean privacy line** — ONE sentence in the dialog ("🔒 Stored locally, encrypted via Windows Credential Manager. No server, no telemetry."). NO separate first-launch privacy splash. Source-code link deferred to Sprint 6 post-public-flip (link 404s while repo private).
4. **Tray menu stays at 4 items** — NO TTS Provider submenu. Two choice points (tray submenu + Settings dropdown) = confusion. Settings dialog is the single source of truth for provider selection.
5. **Sprint 4 ships dropdown UX + ElevenLabs together** (not staged across two sprints) — half the value of a dropdown is showing it works with multiple options.
6. **Deepgram STT parked for post-launch** — keeps Sprint 4 scope tight, validates dropdown architecture with one second-option (ElevenLabs in TTS) before adding more.

**ElevenLabs SDK choices (verified 2026-05-06 research pass via official docs + GitHub SDK README + DeepWiki):**

- Streaming method: `client.text_to_speech.stream(text=..., voice_id=..., model_id=..., output_format=...)` returns `Iterator[bytes]` directly (TRUE streaming — chunks arrive incrementally; no body pre-fetch like Cartesia)
- Low-latency model: `eleven_flash_v2_5` (~75ms model TTFB, ElevenLabs official recommendation over Turbo)
- Default voice: Rachel `21m00Tcm4TlvDq8ikWAM` (American female, conversational — matches Cartesia "Brooke" warmth)
- Output format: `pcm_22050` (int16 PCM, broadly free-tier-available; 44.1kHz requires Pro tier — NOT float32 like Cartesia, so playback path converts inline via `np.frombuffer(chunk, np.int16).astype(np.float32) / 32768.0`)
- No `response.close()` exposed — cancellation = break the for-loop (cancel event already drives this in our existing pattern)
- Env var: `ELEVENLABS_API_KEY` (matches our keyring slot convention)

**Architecture mirror:** `ElevenLabsTTS(TTS)` mirrors `CartesiaSonicTTS` Option B prefetch+playback two-thread architecture verbatim with three deliberate divergences:
- `_generate_response` calls `client.text_to_speech.stream(...)` returning iterator directly
- `_play_response` converts each int16 chunk to float32 inline
- `stop()` is 5-pronged (epoch++, drain sentence queue, drain prefetch queue, set cancel event, abort sounddevice) — NOT 6-pronged because no `response.close()` exists. Cancel event check at each chunk + Python GC of httpx connection on iterator drop is functionally equivalent kill latency.

Sample rate becomes per-provider (Cartesia 44100, ElevenLabs 22050) — each TTS subclass owns its own `sample_rate` attribute, each constructs its own `sounddevice.OutputStream` via existing `_build_player` lazy hook. No global state change.

**Provider-selection persistence:** `LLM_PROVIDER` / `STT_PROVIDER` / `TTS_PROVIDER` constants resolved via new `config.resolve_setting(name, default)` helper (sibling to `resolve_api_key`, env→keyring with one-shot migration; returns string with default fallback rather than None). Required per Sprint 3.6 dotenv-trap lesson — env-only would silently fall back to defaults in bundled EXE.

**Tray menu:** stays at 4 items (Settings... / Open Knowledge Folder / Open Memory Folder / Quit Clicky). NO TTS Provider submenu added. Single choice point in Settings dialog only.

**Alternatives considered:**

1. *Drop ElevenLabs key as 4th flat field next to existing 3* — original Sprint 4 plan. Rejected because it compounds the >3-fields onboarding cliff + doesn't fix the underlying scaling problem (Deepgram + future providers would just keep adding flat fields).
2. *Add tray submenu for TTS provider AS WELL AS Settings dropdown* — original Sprint 4 plan included this. Rejected — two choice points = user confusion ("did I save the right one?"). Single source of truth.
3. *First-launch privacy splash screen* — considered as belt-and-suspenders for trust. Rejected per USER lean preference: a wall of reassurance starts to sound suspicious. One sentence in dialog is enough; users can verify via source code post-public-flip.
4. *Source-code link in privacy line right now* — would 404 because repo is still private until Sprint 6 public flip. Self-defeating. Defer to Sprint 6 README + dialog update batch.
5. *Multi-key save (both Cartesia AND ElevenLabs persisted simultaneously)* — power-user feature. Rejected for v0 portfolio scope per USER decision (simpler UX wins for the target audience).
6. *Brainstorming skill + writing-plans skill ceremony* — Brainstorming skipped (we already discussed inline + USER answered the locked-decisions clarifying questions). Writing-plans WAS used to produce the executable plan at `docs/superpowers/plans/2026-05-06-sprint-4-multi-provider.md` per the ceremony rule (multi-file architecture + threading silent-failure mode + integration mirroring).

**Consequences (planned, will be verified post-Sprint-4-ship):**

- 12 TDD tasks → ~31 net new tests (223 → ~254 expected)
- 4 source files modified (settings_dialog, config, tts, app), 2 build files (requirements.txt, clicky.spec). NO new files (factory + helper live in existing modules).
- Bundle size grows ~5-10MB from elevenlabs SDK
- Manual gate adds dropdown UX test + audible-voice-swap test to existing PTT acceptance flow
- Sprint 4.7 doc-sync (this commit + post-Sprint-4 follow-up) consolidates all ADRs

**Execution mode:** Option 2 — inline `superpowers:executing-plans` with USER checkpoints at end of Task 5 (backend done), end of Task 9 (UI done), before Task 12 (ship gate). Per USER selection 2026-05-06.

**References:**

- Plan doc: `docs/superpowers/plans/2026-05-06-sprint-4-multi-provider.md`
- Strategic narrative: `C:\Users\Abhis\.claude\plans\streamed-tumbling-sunbeam.md` Sprint 4 (REVISED 2026-05-06) section
- ElevenLabs SDK: https://elevenlabs.io/docs/api-reference/streaming, https://github.com/elevenlabs/elevenlabs-python
- Voice catalog: https://elevenlabs.io/app/voice-library
- Free-tier signup: https://elevenlabs.io/app/sign-up (10k chars/month, no credit card)
- Lesson memory: `feedback_ceremony_vs_lean.md` (full ceremony was right call for Sprint 4)
- Architecture pattern reference: `tts.CartesiaSonicTTS` Option B prefetch+playback (commit `4291401`)

**Sprint 4 SHIPPED 2026-05-07** — 11 TDD-task commits + 1 review-feedback commit landed (`e1d84f9..a6d1ecf`). Final test count came in at 255/255 (plan estimate ~254, came in +1 because Task 3 had 6 ElevenLabsTTS tests not the planned 5). `/review` (`superpowers:code-reviewer`) cleared with 0 T1 issues; T2-1 (stale provider_id MessageBox) + T2-3 (dated voice/model verification anchors) applied as commit `a6d1ecf`. Bundle grew 275 MB → 280 MB (+5 MB elevenlabs SDK + transitive deps); Setup.exe 84 MB → 87 MB (+3 MB compressed). Construction-order Qt-signal hang caught + fixed in-flight at commit `d8c6390` — `_update_save_enabled` got a `hasattr(self, "_buttons")` guard because `setText()` during `_refresh_key_field_for_category` was firing `textChanged` BEFORE the QDialogButtonBox was constructed (Qt swallowed the AttributeError → tests hung). USER manual UX gate verified the dialog renders correctly + "Get key →" buttons open the right URLs; live voice-swap test (Task 11 step 5-7) deferred — USER doesn't have an ElevenLabs key, locked decision was to ship now since 10 mock + factory tests cover the contract.

---

## 2026-05-05: Sprint 3.8 — Single-instance mutex prevents multiple Clicky processes spawning on shortcut multi-click

**Context:** USER reported via screenshot that double-clicking the installed Start Menu shortcut spawned multiple `Clicky.exe` processes — visible as 3 stacked blue cursor icons in the Windows system tray overflow popup. Worse symptom: all instances reacted to the same `Ctrl+Alt+Space` press in unison, so one PTT triggered N parallel STT→Claude→TTS pipelines and the user heard N overlapping Cartesia voices answering one question.

Diagnosis (line-by-line read of `app.py:809-923` main block): zero process-uniqueness check. Each shortcut click unconditionally constructs its own `QApplication`, `ClickyApp`, `AssemblyAIStreamingSTT` (with its own WebSocket), `pynput.keyboard.Listener(suppress=False)` (which is observe-only — multiple listeners coexist as independent Win32 `WH_KEYBOARD_LL` hooks; Windows broadcasts every keypress to every installed hook), and `QSystemTrayIcon`. N callbacks fire per keypress → N parallel `_pipeline_worker` threads spawn → N independent Claude API calls + N TTS playbacks.

Alternatives ruled out via diagnosis:
- Single Clicky misbehaving (re-entrancy): rejected — `_handle_release` explicitly cancels prior worker via `_cancel_event` + `tts.stop()` before spawning new. Within a single process, re-press kills in-flight pipeline. Multiple voices ⇒ multiple processes.
- Tray icon ghosts (process died but icon lingered): rejected — ghosts are inert, can't react to hotkeys. User's "all of them react" rules this out.
- Multiple `Clicky.exe` binaries from botched install: rejected — all shortcuts point to the one binary at `%LOCALAPPDATA%\Programs\Clicky Windows\Clicky.exe`.
- PyInstaller bootloader leaking child processes: rejected — `--onedir` mode launches Python in-process, no subprocess fan-out.

**Decision:** Win32 named-mutex single-instance guard acquired BEFORE `QApplication` construction. First instance gets the mutex; second sees `ERROR_ALREADY_EXISTS` (183), shows a Win32 `MessageBoxW` directing the user to the existing tray icon, and exits with `sys.exit(0)`. Same canonical pattern Spotify, Slack, Discord, Raycast all use.

Implementation at `app.py:761-833` (helper) + `app.py:885-901` (main-block wiring):

```python
_MUTEX_NAME = "Local\\ClickyWindows-SingleInstance-v1"
_ERROR_ALREADY_EXISTS = 183

def _acquire_single_instance_mutex(kernel32=None):
    if kernel32 is None:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return "fail-open"  # rare CreateMutexW failure — don't block startup
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle
```

Critical implementation details (caught by pre-commit Boris #5 review via `superpowers:code-reviewer`):

- Explicit ctypes `restype` / `argtypes` — without these, ctypes defaults to `c_int` (32-bit) which silently truncates the 64-bit HANDLE on x64 Windows.
- `bInitialOwner=False` — single-instance detection wants the kernel object's *existence* as a flag, not ownership/synchronization semantics. Setting True would make first instance pointlessly own a mutex it never releases.
- `Local\` namespace prefix scopes per-logon-session — admin and non-admin in same session see the same mutex (correct), but different Windows users on the same machine each get their own Clicky (also correct). `Global\` would block second user on shared RDP host (wrong for portfolio scope).
- `"fail-open"` string return for rare `CreateMutexW` genuine failure — better to risk a duplicate than block the user with a broken installer.
- Kernel auto-releases mutex on `ExitProcess` regardless of how the process terminates (clean exit, crash, Task Manager kill) — no explicit cleanup needed at shutdown.
- Test mock fidelity: the pre-commit review caught that real ctypes c_void_p NULL maps to Python `None` (NOT integer `0`). Test was updated to model `None` as the primary case + a defensive 4th test covers `0` for belt-and-suspenders.

**Alternatives considered:**

- *Lock file with PID*: rejected. Fragile under app crash (stale lock files); requires explicit cleanup on shutdown; cross-process race conditions on lock-file-create.
- *TCP socket bind on localhost port*: rejected. Requires a free port; firewall warnings; overkill for a presence flag.
- *Qt's `QSharedMemory`*: rejected. Native Win32 mutex is more reliable across Windows configurations and matches the canonical industry pattern.
- *Spotify-style "surface existing tray + exit silently" instead of messagebox*: deferred to post-Sprint-4 polish. Current messagebox tells the user where to look. Surface-existing-tray would need IPC or a `RegisterWindowMessage` round-trip (heavier).
- *Cross-platform port guard (`if sys.platform == "win32"`)*: deferred. Phase 1 is Windows-only; the guard is cheap to add when Phase 2 ports to Linux/Mac.

**Consequences:**

- Bug eliminated: multi-clicking the shortcut now shows a messagebox + exits cleanly. Tray contains exactly one Clicky icon. One Ctrl+Alt+Space press = one Claude response.
- 4 new unit tests in `TestSingleInstanceMutex` class (DI-mocked kernel32). Test count 219 → 223.
- Pre-commit `/review` (Boris #5) gate flagged 1 must-fix (T1.1 mock fidelity) + 1 inline-comment recommendation (T1.2 GetLastError ordering) + 1 nit (T3.4 NULL representation comment). All applied before commit.
- Bundle rebuilt + Setup.exe recompiled (84 MB at `installer/Output/Clicky-Windows-Setup-v0.1.0.exe`).
- Trade-off accepted: cannot run two Clickys simultaneously for testing (e.g. different MODEL_ID + voice for A/B). Trivial to add `--no-single-instance` CLI flag later if needed; not requested for v0.
- USER manual gate verified post-install: messagebox fires, second instance exits, tray contains one icon.

**References:**

- Commit: `e457905`
- Files NEW: tests in `tests/test_app.py` `TestSingleInstanceMutex` class
- Files MODIFIED: `app.py` (helper + main-block wiring)
- Microsoft Learn: [CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw), [Object Namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces) (Local\ vs Global\ scope)
- USER acceptance screenshot 2026-05-05 — messagebox fired, second-instance exited, tray showed exactly one cursor

---

## 2026-05-05: Sprint 3.6 — auto-detect OpenRouter `sk-or-` key prefix in `create_ai_client` (fixes bundled-EXE 401)

**Context:** USER tested the installed bundled `Clicky.exe` and every PTT failed with `AuthenticationError 401: invalid x-api-key` (verified in `~/.clicky-windows/debug/2026-05-05_04-57-45_chrome.exe/interaction.log` line 14). STT worked, capture worked, KB recall worked, memory recall worked — only the Claude API call failed. Root cause: user's `.env` has `ANTHROPIC_API_KEY=sk-or-v1-...` (OpenRouter key) PLUS `ANTHROPIC_BASE_URL=https://openrouter.ai/api` to route it. In dev mode (`py -3.13 -m app` from repo root), python-dotenv reads `.env`, both env vars are set, AnthropicClient routes to OpenRouter correctly. In bundled-EXE mode, cwd is `%LOCALAPPDATA%\Programs\Clicky Windows\` — no `.env` there, python-dotenv silently finds nothing, ANTHROPIC_BASE_URL unset, Anthropic SDK falls back to `api.anthropic.com` default, OpenRouter-namespaced key is rejected. Sprint 3's keyring migration (config.resolve_api_key) handles the API KEY across env→keyring transitions but ANTHROPIC_BASE_URL has no equivalent resolution path.

**Decision:** Surgical 5-LOC fix in `ai.create_ai_client`: when `api_key.startswith("sk-or-")` AND `base_url is None`, auto-set `base_url="https://openrouter.ai/api"`. Direct Anthropic keys (`sk-ant-*`) leave `base_url=None` so the SDK uses its default (which is correct for those keys). Explicit `base_url` passed by caller still wins (no override). Implementation at `ai.py:635-645`.

**Alternatives considered:**
- Store ANTHROPIC_BASE_URL in keyring + add UI field to settings dialog (more thorough but UI work; defer — prefix-detect heuristic handles 95% of real users).
- Bundle `.env` in installer (NEVER — security disaster; user keys would land in known location on disk).
- Check `os.environ` proactively at startup, set ANTHROPIC_BASE_URL if sk-or- key found (similar effect, more invasive than client-construction-time fix).

**Consequences:**
- Bundled EXE works end-to-end without `.env` for users with OpenRouter keys. Verified via `~/.clicky-windows/debug/2026-05-05_05-16-19_chrome.exe/interaction.log`: STT 464ms, CLAUDE done in 4.1s, audible response, no 401.
- Direct Anthropic users unaffected (their keys keep working through SDK default endpoint).
- 3 new tests (`test_anthropic_with_openrouter_key_auto_routes_to_openrouter`, `test_anthropic_with_direct_key_does_not_set_base_url`, `test_explicit_base_url_overrides_openrouter_auto_detect`).
- 219/219 tests green.

**References:**
- Failing log: `~/.clicky-windows/debug/2026-05-05_04-57-45_chrome.exe/interaction.log` (401)
- Working log: `~/.clicky-windows/debug/2026-05-05_05-16-19_chrome.exe/interaction.log` (CLAUDE done in 4.1s)
- Implementation: commit `e484ca9`, `ai.py:635-645`
- Lesson memory: `feedback_bundled_exe_dotenv_trap.md`

---

## 2026-05-05: Sprint 3.5 — Icon iteration journey (5 commits): hand-drawn pixel-art retracted → GPT Image v2 chroma-keyed locked

**Context:** Sprint 3 shipped `assets/clicky_tray.ico` as a hand-drawn 16×16 pixel-art cursor. After installing in real Windows tray, user flagged the icon as "kinda weird" (head-tail neck gap visible at large sizes — rendered as two separate shapes rather than one cursor). Multi-res mechanism was ALSO wrong initially (only 16×16 frame embedded, Windows stretching to fill larger surfaces = visible blur on dialog title bars + Apps & features list). Five icon-related commits this session resolved it.

**Decision:** Use user's GPT Image v2-generated PNG as source. Convert to multi-res ICO via aggressive chroma-key (RGB > 230 → fully transparent) + crop to opaque bbox + LANCZOS resize to all 6 sizes (16/32/48/64/128/256) + post-resize alpha snap (alpha < 32 → 0; alpha > 224 → 255) for clean transparency. Multi-res mechanism: pass 256×256 as base, native frames in `append_images`, with explicit `sizes=[(s,s)]` parameter so PIL embeds each size natively (not by downscaling base).

Also fix EXE-resource icon (`clicky.spec icon="assets/clicky_tray.ico"` — embeds icon as Windows EXE resource for taskbar / Alt-Tab / Start Menu shortcut / Apps & features) and Qt-app icon (`qt_app.setWindowIcon(QIcon(...))` in app.py main — belt-and-suspenders for Qt-managed surfaces). Path resolution via `Path(__file__).parent / "assets" / ...` for dev/bundle parity.

**Alternatives considered:**
- Smooth squircle (Claude Design F variant) — looked good in Claude Design preview but user wanted pixel-art aesthetic.
- 359 KB Archive ICO B variant (user-supplied alternate) — comparable quality, slightly different positioning. Kept as fallback.
- Hand-drawn pixel-art with cleaner head-tail proportions — failed visual review at 256×256 (still looked off).
- SVG output via Claude Design with raster conversion — overcomplicated for the scale; PNG-direct is simpler.

**Consequences:**
- Icon locked. User confirmed "the new cursor looks a lot cleaner, so that's fine."
- In-app overlay cursor (`overlay.py` QPolygonF rendered at 60Hz) stays smooth-vector for now. Pixelating that = bigger overlay change, deferred to optional post-Sprint 4+5 polish.
- GitHub repo hero logo (large brand statement for README) is separate concern — user generates pre-launch.
- Lesson learned: at 16×16 native resolution, hand-drawn classic cursor proportions are genuinely hard. AI-generated source + careful chroma-key pipeline is more reliable than artisanal pixel-by-pixel grids.

**References:**
- Commits: `981622b` (multi-res mechanism), `d201960` (hand-drawn redesign attempt), `5a26e15` (GPT Image v2 + chroma-key v1), `f14d59e` (aggressive white removal + alpha snap)
- Source PNG: `Clicky Windows Archive/ChatGPT Image May 5, 2026, 04_31_33 AM.png` (1254×1254 RGB)
- Output: `assets/clicky_tray.ico` (~45 KB, 6 native frames)

---

## 2026-05-04: Sprint 3 — System tray + first-launch keyring dialog + env→keyring migration; closes "no clean exit path" UX gap

**Context:** Pre-Sprint-3, the only way to close Clicky was Task Manager (Ctrl+Shift+Esc → End task). Reasons: windowed PyQt6 app with `WS_EX_TOOLWINDOW` (no taskbar entry) + `console=False` (no SIGINT path) + no menu / hotkey to quit. User flagged this as a real UX gap. Also: API keys were `.env`-only (Phase 1 BYOK pattern) — fine for dev, awkward for end users who'd need to manually edit a file.

**Decision:** Three coupled features in one sprint commit (`fd8e476`):

1. **`tray.py` (~110 LOC)** — `QSystemTrayIcon` with 4-item menu (Settings... / Open Knowledge Folder / Open Memory Folder / Quit Clicky). Quit callback invokes `clicky.stop()` BEFORE `qt_app.quit()` so STT WebSocket + TTS playback + hotkey listener all disconnect cleanly. Folder menu items use `mkdir(parents=True, exist_ok=True)` + `os.startfile()` to open in Explorer (auto-create-if-missing for first-launch UX). System-tray-availability check raises `RuntimeError` if Windows config has no tray (rare kiosk/VM scenarios) — caught in app.py main with `QMessageBox.critical` + `sys.exit(1)`.

2. **`settings_dialog.py` (~155 LOC)** — modal `QDialog` with 3 password fields (Anthropic / AssemblyAI / Cartesia) + reveal checkbox + masked previews of existing keys (`first-5 + ****** + last-4`). Save persists each non-empty field to keyring under service name `"clicky-windows"`. Reusable: shown at first-launch when keys missing AND from tray "Settings..." menu (rotation flow).

3. **`config.resolve_api_key()`** — env-then-keyring resolver with one-shot migration. On `.env`-present, the value gets written to keyring as backup so user can later delete `.env` without losing keys. All 3 module-level constants (`ANTHROPIC_API_KEY`, `ASSEMBLYAI_API_KEY`, `CARTESIA_API_KEY`) now resolve via this helper. Failures in keyring (locked vault, no backend) are swallowed — env path always works as fallback.

App.py main block restructure: create QApplication FIRST → `setQuitOnLastWindowClosed(False)` (prevent overlay-close from killing app) → check `required_keys_present()`, show `SettingsDialog` if missing → re-resolve keys via `resolve_api_key()` (module-level constants captured at import time and may be stale after modal save) → construct `ClickyApp` with explicit api_key kwargs → instantiate `ClickyTray` with quit + settings callbacks.

**Alternatives considered:**
- `pystray` for tray icon — verified abandoned (last release Sept 2023). `QSystemTrayIcon` is native + zero new deps.
- Keep `.env`-only — fails the end-user installer story (users won't manually edit a config file).
- Auto-update keys mid-session — explicit defer; restart-required is documented in tray Settings... callback log line.
- Storage-location wizard at first launch (offer `~/Documents/Clicky Wiki/` vs custom) — rejected per 2026-04-27 plan retraction (extra modal = friction; default is universally fine).

**Consequences:**
- Right-click tray → Quit cleanly exits Clicky. UX gap closed.
- Keyring uses Windows Credential Manager backend (`WinVaultKeyring`, DPAPI per-user encryption). README disclosure: "API keys stored in Windows Credential Manager (DPAPI per-user encryption). Better than plaintext `.env` but does NOT protect against malware running as your user account."
- 19 new tests across `test_config_keyring.py` (env-only / keyring-only / both / neither / set_failure_swallowed / get_failure_returns_none) + `test_settings_dialog.py` (`_mask` edge cases + `required_keys_present` probe behavior) + `test_tray.py` (menu structure + Quit/Settings callbacks + KB/Memory folder auto-create + RuntimeError on tray unavailable).
- Multi-res ICO bundling needed `keyring.backends.Windows` as PyInstaller hidden import (dynamic entry-point loading).
- 3 review fixes applied in same commit: settings dialog icon path uses `Path(__file__).parent` (was CWD-relative bug in bundled EXE), tray availability guard (raise RuntimeError before QSystemTrayIcon construction), wrong noqa comment removed.

**References:**
- Commit: `fd8e476`
- Files NEW: `tray.py`, `settings_dialog.py`, `assets/clicky_tray.ico` (initial pixel-art version), `tests/test_tray.py`, `tests/test_settings_dialog.py`, `tests/test_config_keyring.py`
- Files MODIFIED: `config.py` (KEYRING_SERVICE + resolve_api_key), `app.py` (main-block modal+tray flow), `clicky.spec` (assets data + keyring hidden imports), `requirements.txt` (`keyring>=25.0`)

---

## 2026-05-04: Sprint 2 — PyInstaller `--onedir` + Inno Setup per-user installer; aggressive bundle excludes (1.1 GB → 275 MB)

**Context:** Phase B1 milestone — produce `Clicky-Windows-Setup.exe` so non-developer Windows users can install Clicky in one double-click without needing Python / SDKs / a `.env` file. Two-stage build pipeline needed: PyInstaller bundles Python code + deps into `dist/Clicky/` folder; Inno Setup wraps the folder into a single distributable `Setup.exe`.

**Decision:** Crib JaySmith502/clicky-win's `clicky.spec` verbatim (verified via `gh api repos/JaySmith502/clicky-win/contents/clicky-py/clicky.spec`). Adapt for our stack:
- Replace PySide6 → PyQt6 throughout (collect_data_files, hidden imports, excludes)
- Drop `qasync` (we don't use async-Qt bridge)
- Add explicit hidden imports for our SDKs: `anthropic`, `openai`, `cartesia`, `assemblyai`, `sounddevice`, `numpy`, `keyring`, `keyring.backends.Windows`
- Keep platform-shim hidden imports: `pynput.keyboard._win32`, `pynput.mouse._win32`, `mss.windows`
- `--onedir` (NOT `--onefile`) — preserves fast startup vs the 2-5s extraction penalty `--onefile` pays per launch (PyQt6 + many deps = bad UX with `--onefile`)
- Output bundle: `dist/Clicky/Clicky.exe` (15 MB launcher) + `dist/Clicky/_internal/` (260 MB of bundled site-packages)

Inno Setup `installer/clicky.iss` cribbed from `doug-101/TreeLine` v3.2.1 pattern (PyQt6 + Inno reference). Per-user install (`PrivilegesRequired=lowest`, `DefaultDirName={userpf}\Clicky Windows`) — no UAC prompt = lower friction for portfolio-tier users on locked-down machines. Files spec: `Source: "..\dist\Clicky\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs`. Optional desktop shortcut. Preserves user data on uninstall (`~/.clicky-windows/` and `~/Documents/Clicky Wiki/` not touched).

**Aggressive bundle excludes** drop the bundle 1.1 GB → 275 MB (75% reduction):
- `torch` (315 MB), `torchvision`, `torchaudio` — pulled transitively by some package's deep dep graph; never used
- `llvmlite` (102 MB), `numba` — JIT compilers, not used
- `pyarrow` (76 MB) — Apache Arrow, not used
- `av` (65 MB) — PyAV / FFmpeg bindings, not used
- `scipy` (53 MB) — scientific computing; only `tools/bench_path_a.py` uses it (dev script, NOT bundled)
- `onnxruntime` (32 MB), `pandas` (17 MB) — not used
- Dev-tooling: `IPython`, `jedi`, `parso`, `jupyter`, `notebook`, `matplotlib`

Confirmed safe via grep: scipy is the only "excluded but project-imported" item, and only by `tools/bench_path_a.py` which is not in the EXE entry path.

**Alternatives considered:**
- `briefcase` (BeeWare) for cross-platform packaging — overkill, adds its own Qt wrapper, defer.
- `fbs` — last shipped 2025-01-06, effectively stalled.
- WiX MSI (Friture pattern) — heavier toolchain, makes sense only for enterprise GPO deploys; we don't need this.
- `--onefile` + Sparkle — `--onefile` startup penalty makes PyQt6 apps feel sluggish; rejected.
- Code-signing cert ($90-400/yr) — defer; SignPath Foundation OSS application is the long-term path. Ship unsigned with README screenshot of SmartScreen "More info → Run anyway" flow.

**Consequences:**
- 84 MB `Clicky-Windows-Setup-v0.1.0.exe` (LZMA2 compresses 275 MB onedir to ~84 MB).
- Inno Setup installs to `%LOCALAPPDATA%\Programs\Clicky Windows\` (per-user, no admin).
- SmartScreen "Windows protected your PC" warning appears on first install for unsigned EXE — user clicks "More info → Run anyway." Acceptable for portfolio scope; SignPath OSS application defers signing to post-launch.
- Bundle keyring directory not visible in `dist/Clicky/_internal/` (only `keyring-25.7.0.dist-info/`) — `keyring` package is packed into PYZ archive via hidden imports. Verified at runtime via 5-second smoke test (Clicky.exe stays alive after launch = no `ImportError`).
- Build commands: `py -3.13 -m PyInstaller clicky.spec --noconfirm --clean` (~5 min) → `iscc installer/clicky.iss` (~1 min). `iscc.exe` at user-scope path: `C:\Users\Abhis\AppData\Local\Programs\Inno Setup 6\ISCC.exe` (per-user install, not system-wide).

**References:**
- Commit: `03e41f7`
- Files NEW: `clicky.spec`, `installer/clicky.iss`
- Files MODIFIED: `.gitignore` (add `installer/Output/`)
- Reference repos: `JaySmith502/clicky-win/clicky-py/clicky.spec`, `doug-101/TreeLine/win/treeline-all.iss`

---

## 2026-05-04: Sprint 1 — KB upload feature shipped (lean per-app `.md`, retracts JaySmith verbatim folder+TOML pattern)

**Context:** Phase 2 differentiator from 2026-04-27 strategic re-eval — user-uploadable docs per app so Clicky can answer questions about obscure / company-internal software Claude doesn't already know. Original plan called for shipping JaySmith502/clicky-win's full pattern (`<app>/_meta.toml` + `overview.md` + section files + 60K-char keyword-ranked budget). After mid-session pushback from user (*"is this actually useful or just complex?"*), the JaySmith verbatim pattern was retracted as overengineered for our scale.

**Decision:** Lean per-app pattern — single `.md` file per app at `~/Documents/Clicky Wiki/<app>.md`, named to match the foreground `.exe` basename (e.g. `edupack.exe.md`). Same sanitization as `memory.py` (`_sanitize_app_name`: lowercase + replace `:\\/`) so users see consistent filenames in both `~/.clicky-windows/memory/` and `~/Documents/Clicky Wiki/`. If file missing → `recall()` returns `('', '')` (graceful empty path; this is the "Claude already knows that software" case). If file > 60,000 chars → tail-truncate (mirrors `memory.recall` overflow handling).

Injection: Anthropic `AnthropicClient.ask_stream` adds `kb_content` + `kb_app_name` kwargs. When non-empty, appends a SECOND `cache_control: ephemeral` system block alongside the persona block, with marker text *"app knowledge base: you are helping the user with {display_name}. here is reference documentation that you should treat as authoritative:\n\n{kb_content}"*. Within Anthropic's 4-block max (persona + KB + memory-prefix + 1 spare for auto-cache). Per-app cache hit on subsequent turns within the same app session (~50-100ms TTFT saved); cache miss on app switch (acceptable).

`GeminiClient.ask_stream` mirrors the kwargs but concatenates KB into the single system string (Gemini via OpenRouter OpenAI-compat endpoint doesn't support multi-block `cache_control`).

App.py `_pipeline_worker` calls `kb.recall(app_name)` after STT returns transcript, threads results to `ai.ask_stream`. Wrapped in `try/except` (KB files are user-controlled and could be malformed — bad encoding, permission errors, symlink loops; failure must not crash pipeline).

**Alternatives considered:**
- JaySmith502 verbatim folder + TOML + overview.md + ranking — code-verified at source level (`gh api repos/JaySmith502/clicky-win/contents/clicky-py/clicky/knowledge_base.py`, 160 LOC). RETRACTED mid-session as overengineered: setup friction (folder + TOML config) per app conflicts with the demo voiceover *"drop the docs into Clicky's knowledge folder"* (singular). Most users will drop one NotebookLM-converted .md per app, not multi-file curated KBs.
- Karpathy LLM Wiki full pattern — already retracted at 2026-04-27 strategic re-eval.
- User message injection (instead of system block) — rejected. KB is "authoritative documentation" semantically belonging to system instructions. Also, system blocks have 4-cache_control breakpoint allowance which accommodates persona + KB + memory cleanly.
- Keyword-ranked sections with budget allocation — only matters if file > 60 KB. NotebookLM output typically 20-50 KB. YAGNI for v0; tail-truncate handles overflow.
- Auto-create the KB folder at startup — rejected. Users discover the folder via tray menu "Open Knowledge Folder" which auto-creates on click. Empty folder at startup confuses users (looks like a missing feature).

**Consequences:**
- ~30 LOC `kb.py` (vs ~150 LOC for JaySmith pattern) + 12 tests (10 in test_kb.py, 2 in test_ai.py for Anthropic + 2 review-added in test_ai.py for Gemini). 219/219 tests at session end.
- File location is in user's Documents folder (visible, easy to discover). Unlike `~/.clicky-windows/memory/` (hidden by `.` prefix on macOS/Linux, less discoverable on Windows but works).
- Memory recall and KB recall are now BOTH active per PTT. Memory injects into user message text content block; KB injects into 2nd cache_control system block. Different injection points = different cache scopes (memory is per-session-tail, KB is per-file-content).
- Code review fixes layered in (commit `b9c9f78`): try/except around `kb.recall` in app.py, 2 new Gemini KB tests closing test-coverage gap.

**References:**
- Commits: `d34b5f2` (initial), `b9c9f78` (review-fix)
- Files NEW: `kb.py`, `tests/test_kb.py`
- Files MODIFIED: `config.py` (KB_DIR + KB_RECALL_MAX_CHARS), `ai.py` (kb_content + 2nd cache block in AnthropicClient + concat in GeminiClient), `app.py` (kb.recall in _pipeline_worker), `tests/test_ai.py` (4 new tests covering both clients)
- Reference: `gh api repos/JaySmith502/clicky-win/contents/clicky-py/clicky/knowledge_base.py` (the pattern we retracted)

---

## 2026-04-27: Farza launched Clicky Agents → Karpathy LLM Wiki cargo-cult RETRACTED → Phase 2 locked as right-sized curated KB upload (JaySmith502 pattern) + sprint reorder

**Context:** Farza Majeed launched Clicky Agents (~Apr 23, 2026) as a closed-source iteration on macOS Clicky. Voice-driven multi-agent task spawner ("clicky agent" wake phrase → background agent does research / Mac apps / Calendar updates). Same Cloudflare Worker proxy + AssemblyAI + ElevenLabs + Claude Sonnet 4.6 stack. Open-source `farzaa/clicky` repo (5,200 stars, MIT) explicitly framed as "the legacy version for those who want to hack on it." User asked "should I abandon Clicky Windows?" — triggered comprehensive strategic re-evaluation.

**Decisions (multiple, locked 2026-04-27):**

### A. NOT abandoning. Phase 1 ships.

User stated goal: portfolio + Building 0 case study + 30-50 real users (NOT viral product, NOT startup). Farza's pivot doesn't change Clicky Windows's product fit because (a) original concept still active (5.2k MIT repo not archived — Farza explicitly handed it to community), (b) Windows gap is now LARGER (Mac users have buddy + agents, Windows users have nothing), (c) memory differentiator untouched (Clicky Agents has no per-app memory). Verdict: keep building. Real timeline pressure is Microsoft Copilot Vision + Razer AVA (H2 2026), not Farza.

### B. Karpathy LLM Wiki proposal — RETRACTED (cargo-cult error)

Earlier this same session I proposed shipping Karpathy's full LLM Wiki pattern (raw → compiled wiki layer + entity pages + concept pages + index.md + log.md + lint pass + schema doc). User correctly called it cargo-cult: *"why did you copy Andrej Karpathy's architecture verbatim without even verifying if this is even relevant to our project in the first place?"*

**Why it was wrong:** Karpathy's wiki pattern is built for LONG-FORM RESEARCH SYNTHESIS over articles/papers/books, browsed in Obsidian, with the LLM compiling raw sources into a maintained wiki of entity/concept pages. Clicky's use case is SHORT-FORM RUNTIME ASSISTANCE during active work (PTT → screen capture → answer). The "compiled wiki" assumes a synthesis use case Clicky doesn't have. The "browse in Obsidian" assumption doesn't fit Clicky's voice-during-active-work UX. The "lint for contradictions / orphan pages" solves wiki-maintenance for a wiki nobody browses. Wrong use case, wrong UX, wrong scale.

**Retracted:** three-layer architecture (raw/wiki/schema), ingest pipeline (LLM updates 5-15 wiki files per PTT), query pipeline, lint pass, `index.md` + `log.md`, schema CLAUDE.md inside the wiki, 1-2 weeks of work. The earlier proposed `tools/lint_memory.py` revival is also retracted — it had no real user even with the wiki framing.

### C. Phase 2 LOCKED: right-sized 3c — user-uploadable docs per app (JaySmith502 pattern, code-verified)

**Architecture (verified from JaySmith502/clicky-win source):**

```
~/.clicky-windows/  (existing — keep transcript dump as raw substrate)
└── memory/<app>.md       ← existing auto-learned (unchanged)

~/Documents/Clicky Wiki/  (default; override later via settings)
└── knowledge/
    └── <app>/
        ├── _meta.toml    ← name + window_titles list
        ├── overview.md   ← ALWAYS injected
        ├── *.md          ← section files, keyword-ranked
        └── ...
```

**Implementation pattern (cribbed from JaySmith's `knowledge_base.py`, ~150 LOC):**
- `_meta.toml` schema: `name = "Wild Apricot"` + `window_titles = ["Wild Apricot", "wildapricot.org"]`
- Window matching: case-insensitive substring, first-match-wins
- Re-read fresh on every turn (no cache — files are small)
- 60K-char token budget for KB content
- `overview.md` ALWAYS included even if over budget
- Other sections ranked by `len(heading_words & transcript_keywords)`, greedy fit
- Empty-transcript-over-budget → overview-only fallback
- Injected into SYSTEM prompt with marker: *"app knowledge base: you are helping the user with {app_name}. here is reference documentation that you should treat as authoritative:\n\n{kb_content}"*

**Cost: ~150 LOC + ~10 tests. ~3-4 hours of focused work** (Claude Code parallel pace, not solo-dev pace).

**Demo headline framing (locked 2026-04-27, mirrors Farza's "learn by doing" original Clicky pitch):**

> *"I've got this 100-page documentation for software I have to use, and I don't want to read it or watch a tutorial. I just want to learn by doing. So I drop the docs into Clicky's knowledge folder, and now it's like having a friend who already knows the software sitting next to me — I just ask, and Clicky points + explains. I learn by using the tool, not by reading about it."*

Concrete demo: GrantaEdu Pack (niche materials-engineering software the user used for SUTD Chem 1D project; existing `edupack.exe.md` already has demo context).

### D. Auto-learn memory verdict: marginal. Stop framing as differentiator.

User pushed on this 3 times in one session. Honest answer: the auto-learn cross-session per-app transcript memory has ~5-15% hit rate (interactions where Claude actually benefits from prior context). For 85-95% of interactions, prior memory is context bloat that costs latency without changing the response. **It's technically unique (nobody else has it) but practically marginal.** Stop listing it as the headline differentiator. Keep saving transcripts (free — just append) but the value claim is honest only as "transparency / debugging aid for the user," not "Clicky remembers you in a useful way."

### E. Tests are not a user-facing differentiator

Stop listing "182 tests" as a differentiator in user-facing positioning. Internal engineering quality signal only. Zero users care.

### F. Sprint sequence REORDERED (KB-first per user direction 2026-04-27)

| Day | Sprint | Effort (Claude Code parallel) |
|---|---|---|
| 1 | **Phase 2: KB upload feature** — port JaySmith pattern | ~half day |
| 2 | **Phase B1: PyInstaller + Inno Setup installer** | ~1 day (DLL bundling unknown) |
| 3 | **Phase B1: System tray + auto-updater (PyUpdater)** + minimal API key dialog | ~half day |
| 4 | **Phase B2: Multi-provider STT (Deepgram subclass)** + **TTS (ElevenLabs subclass)** | ~half day each |
| 5 | **Phase B3 (was Phase 3): HIPAA mode** — whisper.cpp local STT + Windows SAPI local TTS subclasses | ~1-2 days |
| 6 | Demo video (GrantaEdu Pack flow) + public flip + Issue #26 comment + X post | ~half day |

**Total: ~3-5 focused days with Claude Code parallel pace, not 2-3 weeks.** Earlier 5-7 day per-phase estimates were solo-dev pace.

**Rationale for KB-first:** ship the differentiator while motivation is high, then grind installer with the satisfaction of having the unique feature in main. Even if installer takes longer than expected, the KB feature is shippable.

### G. HIPAA mode promoted from Phase 3 to Phase B3

Was deferred to Phase 3 in DECISIONS 2026-04-26 (under Private Mode). Reconsidered 2026-04-27 because (a) tekram has it as a shipped feature differentiator, (b) it's an alternative for users who refuse to BYOK, (c) Phase 3 timing was indefinite. Bring forward to Phase B3 as concrete subclass drops:
- `WhisperCppSTT(STT)` — local whisper.cpp via `whispercpp-py` or direct subprocess, ~250MB-1.5GB models
- `Pyttsx3TTS(TTS)` or `WindowsSAPITTS(TTS)` — local Windows SAPI via `pyttsx3` lib, no model download, ~free quality

Vision-LLM (Claude) stays cloud since no consumer-hardware-viable local option per DECISIONS 2026-04-26.

### H. Auto-updater reconsidered (was wrongly deferred earlier today)

Earlier in this same session I deferred auto-updater to "when you have 100+ users." That was wrong — with Claude Code parallel, PyUpdater integration is ~half day, not multi-week. For a real Windows app, manual re-download per release IS friction even at low user counts. Ship in Phase B1.

### I. Cloudflare Worker proxy — explicitly NOT pursued for v0

Both Farza's Clicky and JaySmith502 use Cloudflare Worker proxy to avoid BYOK friction (developer's API keys hidden server-side, user just installs + runs). UX win is real but operational burden is significant: developer eats API costs (~$0.02/interaction × N users × M interactions/day = real money fast), abuse risk if someone discovers the URL with no auth (JaySmith's Worker has zero auth, anyone could drain it), need rate limiting + per-user caps. For portfolio scope (target 30-100 users), BYOK with minimal first-launch keyring popup is fine. Revisit if project ever needs friction-less mass distribution.

### J. Verified competitor inventories (sources of truth, refreshed 2026-04-27)

**farzaa/clicky** — 5,200 ⭐, MIT, NOT archived (Farza explicitly handed open-source to community). Last meaningful commit 2026-04-27 (license + key cleanup). farzaa himself has 0 comments on Issue #26 ever. Running Chasi (YC W26) as his actual company; Clicky is now also a real product (clicky.so / heyclicky.com / @FarzaTV team integrated Gmail MCP per direct user observation).

**Clicky Agents** (Farza, launched 2026-04-23) — closed-source iteration. Voice-spawn-agents on macOS only. Windows = Tally form waitlist. Same Cloudflare proxy + Claude Sonnet 4.6 + AssemblyAI + ElevenLabs stack. **NO persistent memory. NO BYOK option. Visual pointer + voice + screen-aware buddy still the front door, agents additive on top.** Launch traction: ~868 LinkedIn reactions, PH #6 (137 upvotes). Brand confusion risk for "Clicky Windows" naming = medium → 1-line README disambiguator handles it.

**tekram/clicky-windows** — **46 ⭐ (was 14 three weeks ago, growing)**, MIT, 32 commits, Electron + TypeScript + Squirrel auto-updater. Active. Verified feature inventory:
- ✅ Multi-provider STT (3): AssemblyAI cloud + OpenAI Whisper cloud + Whisper Local offline (whisper.cpp)
- ✅ Multi-provider TTS (3): Windows SAPI offline + OpenAI TTS cloud + ElevenLabs cloud
- ✅ Multi-provider LLM: Anthropic + OpenAI + OpenRouter (300+ models)
- ✅ HIPAA mode (forces local STT + TTS)
- ✅ System tray + always-on-top pinned chat + cursor buddy following mouse
- ✅ Squirrel auto-updater + installer
- ❌ No curated KB / NotebookLM-style upload feature
- ❌ No persistent memory across sessions
- ❌ No latency optimizations documented
- ❌ Multi-language UI / clipboard copy / hide-overlay-while-typing / listening cue — NOT in README (earlier claim was wrong, retracted)

**JaySmith502/clicky-win** — 4-5 ⭐, MIT, 97 commits, Python + PySide6 + qasync + Cloudflare Worker proxy. 73 tests. Last push 2026-04-12. Verified at code level via deep-dive 2026-04-27:
- ✅ **Curated KB / NotebookLM upload feature** (the cracked underdog feature) — `_meta.toml` + `overview.md` + section files + keyword-ranked 60K-char budget, code-verified 150 LOC port path
- ✅ Cloudflare Worker proxy (`/chat` + `/tts` + `/transcribe-token` ephemeral AssemblyAI token route — no audio proxying, saves bandwidth)
- ✅ System tray + cursor buddy + Squirrel-equivalent installer
- ✅ Interrupt support (re-press cancels mid-TTS)
- ✅ 20-turn conversation deque (text-only history, images only on current turn — cheap token win pattern)
- ❌ NO persistent auto-learn memory across sessions (KB is curated-input only)
- ❌ Multi-LLM: Claude only (hardcoded `claude-sonnet-4-6` / `claude-opus-4-6` allowlist)
- ❌ Multi-provider STT/TTS: AssemblyAI + ElevenLabs hardcoded
- ❌ Latency optimizations not documented

### K. Combined feature gap analysis

After Phase 2 (KB) + Phase B1 (installer + tray + auto-updater) + Phase B2 (multi-provider) + Phase B3 (HIPAA) ship, the feature set is:

| Feature | tekram | JaySmith | mine (after) |
|---|---|---|---|
| Curated KB upload (JaySmith pattern) | ❌ | ✅ | ✅ |
| Auto-learn memory cross-session | ❌ | ❌ | ✅ (marginal value but unique) |
| Multi-provider STT/TTS/LLM | ✅ | ❌ | ✅ |
| HIPAA / local mode | ✅ | ❌ | ✅ |
| Installer + tray + auto-updater | ✅ | ✅ | ✅ |
| Latency optimization stack | ❌ | ❌ | ✅ |
| Cloudflare Worker proxy | ❌ | ✅ | ❌ (deliberate; BYOK fine) |

**Net:** matches both on shipped features + adds curated KB (vs tekram) + adds auto-learn (vs both) + adds latency stack (vs both). Defensibly the most-feature-complete Windows AI buddy by code-level comparison. Not "winning the market" — but defensible portfolio claim.

**Time estimate:** ~3-5 focused days with Claude Code parallel + auto + subagents + superpowers. Earlier 5-7 day per-phase estimates were solo-dev pace and inflated.

**References:**
- Farza Clicky Agents: [LinkedIn launch](https://www.linkedin.com/posts/farza-majeed-76685612a_introducing-clicky-agents-this-is-the-simplest-activity-7454552863227285504-vQrQ), [clicky.so landing](https://www.clicky.so/), [PH listing](https://www.producthunt.com/products/clicky-2)
- Karpathy LLM Wiki gist: `C:\Users\Abhis\OneDrive\Documents\Maritime Project\Claude Code TIPS\Andrej Karpathy KB\llm-wiki.md` — verbatim text used to verify cargo-cult error
- tekram/clicky-windows: [README](https://github.com/tekram/clicky-windows) verified 2026-04-27 via WebFetch
- JaySmith502/clicky-win: code-level deep-dive via Agent 2026-04-27 — `clicky-py/clicky/knowledge_base.py`, `prompts.py`, `conversation_history.py`, `config.py`, `companion_manager.py`, `clicky.spec`, `worker/src/index.ts`
- LAUNCH.md (gitignored, internal) — has GrantaEdu Pack demo voiceover script

---

## 2026-04-26: Considered fully-local stack — tiered (cloud default + Phase 3 Private Mode opt-in) chosen

**Context:** User-prompted by Microsoft VibeVoice release ("local MIT-licensed STT + TTS, 90-min generation from 10s clip, 60-min transcription"). Question: should Clicky bundle local alternatives so users don't need to BYOK API keys for AssemblyAI / Cartesia / Anthropic? Three parallel research agents evaluated (1) VibeVoice specs from primary Microsoft sources, (2) real user reactions on Reddit / HN / GitHub Issues, (3) end-to-end fully-local stack feasibility on 5 consumer hardware tiers.

**Decision:** **Stay tiered. Cloud default for Phase 1/2; Private Mode as future Phase 3 opt-in subclass drop on existing `AIClient` / `STT` / `TTS` abstractions.** Do not pursue fully-local as default. The provider abstraction (CLAUDE.md "Provider abstraction from day 1") was forward-looking design; Phase 3 Private Mode is a 3-subclass drop, not a rewrite.

**Alternatives considered:**

### VibeVoice — REJECTED

Microsoft TTS family: VibeVoice-1.5B / 7B / Realtime-0.5B (TTS), plus VibeVoice-ASR (separate 9B STT sibling). MIT-licensed code. The user's "60-min transcription + 90-min generation in one model" framing is a conflation — they are sibling models with different params and use cases.

Rejection reasons:
- **Wrong product shape.** Base 1.5B/7B target long-form podcast generation (90 min, 4 speakers), not <500ms PTT-response latency.
- **Realtime-0.5B variant TTFB ~200-300ms** matches Cartesia on a beefy GPU but at 24kHz vs Cartesia's 44.1kHz — audible quality regression.
- **Hard CUDA dependency.** ~2GB VRAM floor (Realtime-0.5B), ~7GB (1.5B). Fails on the 60-70% of Windows boxes without discrete NVIDIA GPU.
- **VibeVoice-ASR is 9B params BF16 (~18GB on disk).** Designed for batch hour-long structured transcription, not PTT finalization. Wrong tool.
- **License gray area.** Microsoft's own model-card terms prohibit *"real-time or low-latency voice conversion for live deepfake applications"* — flagged for desktop voice assistants.
- **Supply-chain risk.** Microsoft disabled the GitHub repo Aug 2025 over voice-cloning misuse; community forks (vibevoice-community, shijincai, arpy8) host backups. Precedent that they could pull again.
- **No successful desktop-bundle stories** found across HN / Reddit / Issues. Every deployment is Gradio + Python + CUDA or ComfyUI nodes.

### Kokoro-82M — Phase 3 candidate

Apache 2.0, ~95MB total weights (80MB INT8 ONNX + voices), 24kHz. **TTS Arena Elo 1059 (#1 open-weight model overall).** 54 built-in voices, **NO voice cloning**. CPU-friendly via `kokoro-onnx` PyPI package + onnxruntime (~50MB) — total bundle delta ~150-200MB.

Trade-offs:
- **TTFB streaming on CPU: 1-2s** (vs Cartesia's measured 150-250ms) — 4-10x slower
- **Sample rate 24kHz** vs Cartesia 44.1kHz — audible quality regression
- **"Stilted" prosody** per real user reports; flat intonation on >20-word sentences
- **Production-proven**: TinyReadAloud (Windows tray app) + dTelecom (M4 production swap, ElevenLabs → Kokoro saved $11,826 over 3 years)
- **Real user verdict**: *"If you're trying to build a local AI assistant, Kokoro is perfect."* (HN [item 45116238](https://news.ycombinator.com/item?id=45116238))

### Parakeet TDT 0.6B v3 — Phase 3 candidate

CC-BY-4.0 (commercial OK), ~480MB INT8 ONNX, English-focused. **6.32% WER avg vs Whisper-large-v3's 7.44%** on the Open ASR Leaderboard — beats Whisper on average. Production-proven by Handy (cjpais), Chirp, SilentKeys.

Trade-offs:
- **Finalize latency 400-2000ms on i5-class CPU** (vs AssemblyAI's 150-300ms ForceEndpoint) — 2-10x slower for short utterances
- **80-400ms on Intel NPU (Core Ultra)** — closes the gap on Meteor Lake+ silicon only
- **Streaming partials require non-trivial chunked-inference plumbing** (parakeet-rs, NeMo streaming, or batch-on-PTT-release)
- **Silence hallucination** failure mode — outputs "Yeah" / "Mm-hmm" on silent audio per NVIDIA NIM docs; pair with Silero VAD (Handy already does this)

### Qwen3-VL 8B — Phase 3 vision-LLM candidate (the binding constraint)

Q4 GGUF ~6GB VRAM. **ScreenSpot 92% / ScreenSpot-Pro 50%** (vs Claude Sonnet 4.6's **72.5% OSWorld**). **Known coordinate drift on click predictions** ([Qwen issue #1780](https://github.com/QwenLM/Qwen3-VL/issues/1780)).

This is the actual hard problem for fully-local Clicky. STT and TTS have viable local alternatives today; the vision-LLM that has to look at a 1024×768 screenshot and emit accurate `[POINT:x,y:label]` coordinates does not run on consumer integrated graphics, and even on a 4070 it regresses ~20pp vs Sonnet on UI grounding. Cursor landing ~50px off the intended button is a worse UX failure than 1s of extra latency.

### End-to-end latency budget (release → first audible word, 800-1200ms target)

| Tier | Stack | Total | vs target |
|---|---|---|---|
| **RTX 4090** | Parakeet + Qwen3-VL 8B Q4 + Kokoro | 600-900ms | beats target |
| **RTX 4070 (12GB)** | same | 1000-1400ms | roughly meets |
| **Apple M3/M4** (no NVIDIA) | Parakeet-mlx + Qwen3-VL 4B MLX + Kokoro | 1200-1700ms | misses + degraded grounding |
| **Snapdragon X NPU** (Copilot+ PC) | Phi Silica multimodal + ONNX | 1200-1500ms | marginal but power-efficient |
| **Integrated GPU / CPU-only** | whisper.cpp + Phi-4 MM CPU + Kokoro | 5-10s | unusable (image prefill alone is 3-8s) |

~10-15% of Windows users have RTX 4070+ today.

**Why tiered won:**

1. **Vision-LLM is the binding constraint** — STT (Parakeet) and TTS (Kokoro) are real, shipping options today; the vision-LLM that has to emit accurate coordinate tags for a screenshot is not viable on consumer integrated graphics
2. **Even on RTX 4070+, Qwen3-VL regresses ~20pp on UI grounding vs Sonnet** — Clicky's whole UX is accurate pointing; quality regression hurts the differentiator more than cloud dependency does
3. **~10-15% of Windows users** have eligible GPUs today; mainstream not viable until Q2 2027 per current trajectory
4. **Provider abstraction makes Phase 3 cheap** — `AIClient` / `STT` / `TTS` abstract bases from CLAUDE.md mean Private Mode is a 3-subclass drop, not a rewrite. Cost of deferring is near-zero.

**Consequences:**

- Phase 1 + Phase 2 stay cloud-default. No code changes from this decision.
- Phase 3 stub added to ROADMAP.md ("Private Mode subclass drops") — documented but NOT actively planned.
- If Phase 3 ships, bundle delta ~6.5GB extra weights (Parakeet 480MB + Qwen3-VL Q4 ~6GB + Kokoro 95MB) vs cloud's ~5KB HTTP clients. Likely separate "Clicky Private" installer.
- Anthropic does not offer on-device Claude Sonnet deployment. Closest "private cloud" option is AWS Bedrock + customer-managed VPC (enterprise-only, not consumer).
- Snapdragon X NPU (Phi Silica multimodal) is the only "small enough to bundle, big enough to be useful, runs on a normal laptop" path emerging. Microsoft is doing the heavy lifting; Clicky just needs an ONNX `AIClient` subclass when Phi Silica exposes vision officially.

**Revisit triggers (check periodically — when any of these flip, re-evaluate Phase 3):**

1. **Vision-LLM closes the gap** — open vision model hits within 5pp of Sonnet's 72.5% OSWorld
2. **Mid-range GPU sufficient** — 6GB-VRAM model TTFT < 500ms on RTX 3060-class hardware
3. **NPU path matures** — Phi Silica multimodal exposes vision officially on Snapdragon X / Copilot+ PCs
4. **Coordinate drift fixed** — [Qwen3-VL issue #1780](https://github.com/QwenLM/Qwen3-VL/issues/1780) closes; click predictions become reliable
5. **TTS quality closes** — Kokoro successor or new entrant ships voice cloning + 44.1kHz + sub-300ms CPU TTFB
6. **STT finalize closes** — Parakeet successor or new entrant matches AssemblyAI 150-300ms ForceEndpoint on consumer CPU without NPU dependency
7. **Anthropic ships on-device** — currently doesn't exist for consumers
8. **User signal** — Phase 2/B real users start asking for offline mode (Issue-class demand, similar to upstream Clicky issues #22/#27/#32/#33 about BYOK)

Realistic timeline: viable on RTX 4070+ today; mid-range GPUs Q2 2027; integrated graphics never (image prefill floor is 3-8s).

**References:**

- VibeVoice: [GitHub microsoft/VibeVoice](https://github.com/microsoft/VibeVoice), [HF VibeVoice-1.5B](https://huggingface.co/microsoft/VibeVoice-1.5B), [HF VibeVoice-ASR](https://huggingface.co/microsoft/VibeVoice-ASR), [arXiv 2508.19205 technical report](https://arxiv.org/abs/2508.19205), [HN thread 45114245](https://news.ycombinator.com/item?id=45114245)
- Kokoro: [HF hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), [thewh1teagle/kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), [TinyReadAloud reference deployment](https://github.com/dorofino/TinyReadAloud), [Artificial Analysis TTS leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard), [dTelecom production swap](https://blog.dtelecom.org/we-replaced-elevenlabs-with-kokoro-tts-on-an-m4-gpu-latency-fell-to-100-ms-and-tts-cost-nearly-68bcc3313cdd)
- Parakeet: [HF nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [FluidInference INT8 OpenVINO build](https://huggingface.co/FluidInference/parakeet-tdt-0.6b-v3-ov), [Canary + Parakeet paper arXiv 2509.14128](https://arxiv.org/abs/2509.14128), [Handy (production reference)](https://github.com/cjpais/handy), [Chirp HN thread](https://news.ycombinator.com/item?id=45930659)
- Vision-LLM: [Qwen3-VL GitHub](https://github.com/QwenLM/Qwen3-VL), [Qwen3-VL coord drift issue #1780](https://github.com/QwenLM/Qwen3-VL/issues/1780), [ScreenSpot leaderboard](https://llm-stats.com/benchmarks/screenspot), [ScreenSpot-Pro paper](https://arxiv.org/html/2504.07981v1), [GUI-Actor (NeurIPS '25, Microsoft)](https://microsoft.github.io/GUI-Actor/), [OSWorld 2026 leaderboard](https://airank.dev/benchmarks/os-world)
- NPU path: [Phi Silica multimodal on Copilot+ NPU](https://blogs.windows.com/windowsexperience/2025/04/25/enabling-multimodal-functionality-for-phi-silica/)

---

## 2026-04-20 (late-afternoon): Option 2 — shrink `stop_recording` grace window 300ms → 100ms

**Context:** Post-Option-B latency analysis across 15 pre-fix + 10 post-fix logs showed STT finalize median was 723ms vs pre-Phase-1.5's 301ms. The 2s outer deadline almost never fired; the real waste was the 300ms grace window `stop_recording` waits AFTER the first `end_of_turn=True` event, in case a trailing multi-utterance final follows.

**Decision:** Shrink the inner grace window (`_final_event.wait(timeout=...)` on the second wait in `stop_recording`) from 300ms to 100ms. With Conservative VAD (`min_turn_silence=800ms`) the mid-PTT multi-utterance case is rare enough that 100ms is sufficient to catch a trailing event clustered right behind the first. The outer 2s deadline is untouched — still an edge-case safety net for AssemblyAI hiccups.

**Alternatives considered:**
- **Option 1: Tune VAD less conservatively** (threshold=0.5, min_silence=600) — rejected. Pre-fix logs had 100% em-dash stutter rate with default VAD (threshold=0.4, min_silence=400). Relaxing confidence = re-introducing stutter risk for ~200ms win.
- **Option 3: Disable VAD entirely, force_endpoint is the only trigger** — researched, NOT supported by AssemblyAI Universal-Streaming. No `disable_vad` / `turn_detection_mode=manual` flag exists. Closest workaround (near-zero thresholds + silence-burst PCM injection) has real stutter risk and unknown server-side clamping behavior. SDK source: [`assemblyai/streaming/v3/client.py:172-174`](https://github.com/AssemblyAI/assemblyai-python-sdk/blob/master/assemblyai/streaming/v3/client.py) confirms `force_endpoint()` is fire-and-forget; server runs its own VAD pipeline before firing `end_of_turn`.

**Measured impact (bulk log analysis):**
- Pre-Option-2 (Conservative VAD + 300ms grace): 723ms median STT finalize
- Post-Option-2 (Conservative VAD + 100ms grace): ~466ms median STT finalize
- Net saving: ~257ms median per PTT release

**Consequences:**
- Trade-off: if user pauses 100-300ms between two sentences mid-PTT-hold, trailing sentence gets cut. Rare case; easy tune-back if observed.
- Test: `test_stop_recording_grace_window_is_100ms` uses Event handshake + deterministic timing (fails if someone restores 300ms).
- 178/178 tests green.

**References:** Commit `d29e6dd`. AssemblyAI API surface research via general-purpose agent (Option 3 feasibility).

---

## 2026-04-20 (afternoon): Option B — HTTP double-buffer for seamless sentence playback

**Context:** Post-Path-A manual testing revealed 150-250ms audible gaps between sentences in multi-sentence Claude responses. Path A's sentence-level TTS via HTTP pays Cartesia TTFB (~200-400ms per sentence) after each playback finishes, before the next sentence's audio arrives. Research verified Cartesia SDK's `tts.generate()` returns `BinaryAPIResponse` which eagerly fetches the full body (docstring: *"If you want to stream the response data instead of eagerly reading it all at once then you should use `.with_streaming_response`"*). Our non-streaming call blocks ~200-400ms per sentence — this is the gap.

**Decision:** Split the single queue worker in `tts.py` into two daemon threads with a size-1 handoff queue:
- **Prefetch worker**: pops sentence N+1 from `_sentence_queue`, calls `generate()` (blocks for full-body fetch), puts `(epoch, sentence, response)` into `_prefetch_queue`.
- **Playback worker**: pops tuple, iterates `iter_bytes()` → sounddevice. Compares `epoch` against current `_epoch` and closes-without-playing stale responses.

When N is playing (1-2s of audio), prefetch is already fetching N+1 in parallel. By the time N ends, N+1 is buffered and plays instantly.

**Alternatives considered:**
- **Option A: Keep as-is** — accepted gap costs ~400ms of dead air across 3-sentence response. Rejected per user preference.
- **Option C: Cartesia WebSocket TTS** — 1-2 days work, eliminates TTFB entirely (including first sentence). Rejected for now: reconnection logic + test rewrites + state-machine complexity. Deferred until post-installer public user testing reveals first-sentence gap as a top complaint.

**Race condition fixed:** if `stop()` fires while prefetch is mid-`generate()`, the eventual `put()` carries the OLD epoch. Playback worker rejects stale-epoch items at pop time and calls `response.close()`. Prevents orphaned audio playing after user-triggered abort.

**Measured impact (manual PTT, multi-sentence response):**
- Before: S1 [250ms gap] S2 [200ms gap] S3 = ~450ms dead air
- After: S1 S2 S3 = 0ms dead air (fetch hidden under audio playback)

**Important note for single-sentence responses:** Option B does NOT help. Prefetch has no previous sentence to hide behind. First-sentence TTFB (~300ms) remains. Only Option C WebSocket (deferred) closes that.

**Consequences:**
- Refactor: `_do_speak` split into `_generate_response` + `_play_response`. `speak()` path preserved via thin wrapper (keeps test surface for 4 `test_tts.py` tests that call `_do_speak` directly).
- Tests: +2 (prefetch-timing via Event handshake, prefetch-error resilience) = 177.
- `stop()` extended from 4-pronged → 6-pronged kill: +epoch bump + prefetch-queue drain.

**References:** Commit `4291401`. Research on cartesia SDK: [`resources/tts.py:87-163`](C:/Users/Abhis/AppData/Local/Programs/Python/Python313/Lib/site-packages/cartesia/resources/tts.py), [`_response.py:472-478`](C:/Users/Abhis/AppData/Local/Programs/Python/Python313/Lib/site-packages/cartesia/_response.py).

---

## 2026-04-20 (early morning): stop_recording wait-loop fix — remove premature `else: break`

**Context:** After 51ff788 correctly restricted the STT handler to `end_of_turn=True`, manual user-testing surfaced a cutoff regression: all 3 PTT interactions returned stale `_latest_partial` text ("How do I add—", "Where is the—", "Wer ist—") instead of real finals. Debug logs showed the real `end_of_turn=True` event arriving ~500-700ms AFTER `force_endpoint()`; `stop_recording`'s wait loop had `else: break` that exited after the FIRST 300ms with no event → returned `_latest_partial`.

**Decision:** Remove the `else: break` from the wait loop. Keep iterating 300ms waits until the 2s deadline OR a final event arrives. After first event, still do 300ms grace wait for any trailing end_of_turn (multi-utterance PTT hold).

**Root-cause layer-1 — latent bug masked by 51ff788:** Before the stutter fix, the old `or is_formatted` branch set `_final_event` prematurely on interim partial events (which had `turn_is_formatted=True` from AssemblyAI's formatted-revision emissions). The wait loop ALWAYS saw the event set on the first iteration and proceeded. `else: break` never fired. The latent bug was invisible because `or is_formatted` kept masking it.

When 51ff788 correctly dropped `or is_formatted`, interim partials stopped setting `_final_event`. The wait loop now had to wait for the REAL `end_of_turn=True`. And the `else: break` gave up after 300ms.

**Regression test:** `tests/test_stt.py::test_stop_recording_waits_for_delayed_end_of_turn` simulates the exact real-session timing via `threading.Thread` + `time.sleep(0.5)` — partial arrives immediately, real final arrives 500ms later. Prior code would have returned the partial; new code returns the final.

**Consequences:**
- stop_recording SLA: still bounded at 2s (`_FINAL_TRANSCRIPT_TIMEOUT_S`); steady-state typically 500-700ms
- If AssemblyAI ever fails to emit end_of_turn post-force_endpoint, falls back to `_latest_partial` after 2s
- Pre-commit test suite missed this because mocks fired turn events synchronously inside `force_endpoint.side_effect` (zero latency) — real server has latency. New regression test uses realistic timing.

**References:** Commit `ecc5d0a`. Live debug session 2026-04-19 23:05-23:07 (terminal log in user conversation).

---

## 2026-04-19 (late-evening): Visual state machine completion + STT stutter root-cause

**Context:** After Path A shipped 12 commits, manual testing surfaced 3 UX defects:
1. STT stutter artifact: user said ONE clean sentence "That's kind of weird", AssemblyAI emitted TWO Turn events during the hold, our handler concatenated → "That's kind of— That's kind of weird." Claude reacted to the stutter in its response.
2. LISTENING-state double render: waveform bars + cursor polygon both visible during PTT hold.
3. THINKING-state dead air: ~4-7s between release and Claude coord with no visual feedback.

**Decisions (all in commit 51ff788):**

### A. STT stutter root-cause fix

Previously hypothesized as a "dedup heuristic needed" problem. ACTUAL root cause (verified from AssemblyAI SDK source + docs):

1. **VAD too aggressive**: default `end_of_turn_confidence_threshold=0.4` + `min_turn_silence=400ms` fires `end_of_turn=true` on natural mid-sentence pauses, splitting one utterance into multiple Turn events.
2. **Handler fallback misfired**: our `_on_turn` fired on `end_of_turn=True OR is_formatted`. AssemblyAI emits a separate formatted-revision event after each end_of_turn (with `end_of_turn=false, turn_is_formatted=true`); that second event passed the `or` branch → handler fired twice → concatenation.

**Fix (two complementary changes):**
- `stt.py _on_turn`: drop `or is_formatted`. Per AssemblyAI docs: *"The only reliable way to detect turn completion is end_of_turn: true."*
- `stt.py StreamingParameters`: Conservative preset (`end_of_turn_confidence_threshold=0.7`, `min_turn_silence=800`, `max_turn_silence=3600`). For PTT, we want end_of_turn to fire ONLY from `force_endpoint()` on release, not from mid-hold VAD.

Regression test: `tests/test_stt.py::test_on_turn_ignores_formatted_revision_without_end_of_turn`.

### B. Visual state machine completion — port Clicky verbatim

Verified from `farzaa/clicky leanring-buddy/OverlayWindow.swift` (3× `gh api` reads):

- **LISTENING**: `BlueCursorWaveformView` (lines 705-743) — 5-bar waveform REPLACES triangle (triangle opacity 0 during `.listening`). Position bound to `.position(cursorPosition)` updated at 60Hz by `startTrackingCursor()` (lines 411-438). **Our bug**: `OverlayWindow.show_waveform(x, y)` pinned widget at press-time position — fixed to follow cursor at 60Hz.
- **THINKING**: `BlueCursorSpinnerView` (lines 749-774) — 14×14pt arc, trimmed 15%-85% (70% visible), 2.5px stroke, 0.8s rotation period, 6px glow at 60% opacity. Shown at line 333 when `voiceState == .processing`. Ported verbatim as `SpinnerWidget` in `overlay.py`.
- **Exclusivity**: triangle / waveform / spinner NEVER coexist. Fix: `show_waveform()` + `show_spinner()` both set `_pointer_visible = False`. `_on_follow_tick` gates cursor-visibility against widget-visible flags.

### C. Cloudflare Worker is NOT a latency optimization

Prior claim corrected: Farza's CompanionManager.swift:73-76 uses `workerBaseURL` that proxies to api.anthropic.com. It's a **key-hiding proxy** (hides ANTHROPIC_API_KEY from the client). Cloudflare Workers do route at edge locations which CAN reduce latency marginally, but they do NOT make Anthropic's TTFT faster. Our BYOK `.env` approach has ~same Claude latency as Clicky's shipping code.

### D. lint_memory.py skipped

User verdict 2026-04-20: *"bruh does any1 actually want this? linty_memory is just for b0 newsletter is it? bruh that is so dumb?"*

Reframed: `lint_memory.py` was overscoped in the original PRD as a "Phase 1 acceptance" requirement, but its output (`insights.md`) is a dev-facing / essay-writing artifact, not user-facing value. Real users experience memory via Claude's "you asked this Monday" moments mid-conversation, not by opening insights.md. Skipping unless B0 writeup specifically needs the generated patterns.

### E. Sentence-streaming TTS verdict (empirical)

From 4 debug logs on 2026-04-19/20:
- **Multi-sentence responses** (2/4): mid-stream flushes fired, first audible word ~1500ms post-release (vs ~5000ms batch). ~3500ms net perceived-latency win even with 150-250ms inter-sentence gaps.
- **Single-sentence responses** (2/4): no flush boundaries hit → falls back to batch tail-flush → neutral (same as pre-Path-A).

Net positive on average. Gap between sentences (150-250ms) is audible and annoying but doesn't outweigh the first-word savings on longer responses. Fix tracked as ROADMAP.md F2.

**Rejected: prompt-engineer Claude for period-terminated short sentences.** Would TRIGGER more mid-stream flushes → MORE 150-250ms gaps. Gap problem is per-TTFB, not per-sentence-count. Splitting more ≠ better without WebSocket underneath.

**References:** Commits `ed34b58` (ROADMAP future section), `51ff788` (all-in-one fix), `ecc5d0a` (wait-loop regression). ROADMAP.md F4 marked fixed with verification test cited.

---

## 2026-04-11: Python through Phase 2, no pre-committed Tauri rewrite

**Context:** The original CLAUDE.md committed to "Phase 1 Python prototype → Phase 2 Tauri rewrite" as a two-phase strategy. Grafyn (Bryan's project) is Tauri+Rust+Vue3 at ~30K LOC with 123 tests and 4-platform CI — it was the reference "B0 bar" project. My initial instinct was: Python can't clear that bar, so rewrite.

**Decision:** Build in Python (PyQt6 + mss + pynput + faster-whisper + pyttsx3 + anthropic SDK) for Phase 1 AND Phase 2. **No Tauri rewrite pre-committed.** Phase 3 Tauri rewrite is a contingency, only triggered if Phase 2 hits a Python-specific wall (install experience, threading deadlocks, GIL contention).

**Alternatives considered:**
1. **Python Phase 1 → Tauri Phase 2** (original plan). Pros: matches Grafyn bar. Cons: 1-2 weeks of "throwaway" Python work; learning curve on Rust.
2. **Skip Python, Tauri from day 1.** Pros: single codebase. Cons: every OS-integration unknown becomes a Rust unknown with less mature libraries and no fallback. Claude Code's fluency in Python >> Rust.
3. **2-day Python "spike" → Tauri.** Pros: de-risks unknowns cheaply. Cons: requires discipline to stop at 2 days; I kept recommending this until discovering the Wallee counter-example.
4. **Python through both phases (chosen).** Pros: proves B0 bar is about rigour not language; Wallee is the existence proof (3K LOC Python + 517 tests + 60 replay scenarios, safety-critical, clears the bar). Faster iteration, more mature libs, Claude fluency. Cons: Python desktop install experience is worse than Tauri's single-binary MSI.

**Why this won:** Wallee (Anieyrudh's 3D-printer safety agent) is a 3K LOC Python project that clears the B0 bar via rigour (517 tests, 60 replay scenarios, multi-process safety architecture), not via language choice. The bar is "engineering rigour proportional to the problem," not "use Tauri+Rust." Language is NOT the disqualifier. Wallee's existence refutes the "must rewrite to Rust" instinct I was operating on.

**Consequences:**
- Phase 2 hardening happens in the same Python codebase (add tests, replay scenarios, real users, polish) rather than a rewrite
- PyInstaller bundle is the distribution strategy, not MSI
- Provider abstraction (`AIClient`, `STT`, `TTS`) becomes important for future swaps
- If Phase 2 install experience is too rough, Phase 3 Tauri is the escape hatch — not pre-committed

**References:** `B0 projects research.txt` lines 9580-11721 (Wallee deep dive), the user's pushback message "wait, why does python lesser time imply..."

---

## 2026-04-11: Persistent memory is IN Phase 1, not Phase 2

**Context:** The original CLAUDE.md put memory in Phase 2 ("Phase 2 Tauri rewrite + memory"). Phase 1 was scoped as "prove the loop works" — capture + AI + overlay + STT + TTS + hotkey, with in-memory session history only.

**Decision:** Persistent memory moves INTO Phase 1 MVP. Specifically: Karpathy-style markdown files in `~/.clicky-windows/memory/<app>.md` + SQLite index in `~/.clicky-windows/index.db`. Memory is built alongside the core loop, not deferred.

**Alternatives considered:**
1. **Memory in Phase 2** (original plan). Pros: smaller Phase 1 scope. Cons: Phase 1 MVP is then just a Windows clone of Clicky — Mushtaq Bilal already vibe-coded that hack in 2 hours. No differentiation. The "what are we building better than Clicky" question has no answer.
2. **Memory in Phase 1** (chosen). Pros: differentiates from day 1. The MVP actually answers "does memory make Clicky meaningfully better?" which is the hypothesis worth testing. Cons: larger Phase 1 scope (+2-3 hours for memory.py + wiring).

**Why this won:** The user's direct quote: *"if we just copy existing clicky then what is even the point, don't even bother building. The goal is to build something 'better'."* Without memory, Phase 1 is a clone. Clones don't validate hypotheses — differentiators do.

**Consequences:**
- `memory.py` is Step 6.5 in the build sequence, before `app.py`
- `app.py` orchestrator calls `memory.recall(app_name)` before the Anthropic API call and `memory.record(...)` after
- Phase 1 acceptance criteria include "Memory persists across sessions" and "Memory is human-readable"

**References:** Conversation in plan-mode session 2026-04-11.

---

## 2026-04-11: Karpathy-style markdown memory + SQLite index hybrid

**Context:** Having decided memory is in Phase 1, the question became: how to store it. Options ranged from pure SQLite to vector DB (Pinecone/Chroma) to Obsidian-compatible markdown.

**Decision:** Hybrid — **markdown files are the primary substrate**, SQLite is a lightweight index only. One `.md` file per Windows app executable (`EXCEL.EXE.md`, `chrome.exe.md`, `photoshop.exe.md`). Each interaction appended with timestamp, window title, user question, Clicky response, and pointer targets. SQLite `apps` table indexes `(app_name, first_seen, last_seen, interaction_count, md_path)` for fast lookup. `recall(app_name)` reads the markdown file directly and injects it into Claude's system prompt.

**Alternatives considered:**
1. **Pure SQLite** with conversation rows keyed by `app_name`. Pros: standard, fast joins, easy querying. Cons: opaque to users — they can't `cat` a file to see what Clicky remembers. Schema lock-in.
2. **Vector DB (Pinecone, Chroma, Weaviate)**. Pros: semantic recall. Cons: infrastructure complexity, embedding costs, black-box retrieval, Karpathy's explicit rejection: "I thought I had to reach for fancy RAG, but the LLM has been pretty good about auto-maintaining index files and brief summaries."
3. **Obsidian vault format** (wiki links, YAML frontmatter). Pros: compatible with Obsidian GUI. Cons: overkill for Phase 1, would require Obsidian to be installed.
4. **Markdown + SQLite index hybrid** (chosen). Pros: human-readable substrate (Karpathy's principle), SQLite for fast "which apps have I seen" queries without reading all files, easy to lint, easy to debug, zero retrieval complexity. Cons: slightly more complex than pure SQLite.

**Why this won:** Karpathy's explicit pattern: *"The LLM writes and maintains all of the data, you rarely touch it directly, but it's human-readable when you want it."* A user can open `~/.clicky-windows/memory/EXCEL.EXE.md` and read exactly what Clicky remembers about them — the transparency is a feature, not a bug. Contrast with "Clicky knows something about me but I can't see what."

**Consequences:**
- `memory.py` has two responsibilities: markdown I/O + SQLite index updates
- `recall()` is a simple file read (last 2-3 KB of the app's markdown) — no vector similarity, no top-K retrieval
- Phase 2 `lint_memory.py` script can trivially iterate over the markdown dir and feed it to Claude for pattern-finding
- Users can manually edit their own memory files if they want to (e.g., "remove that embarrassing question I asked last week")

**References:** `Andrej Karpathy KB/Karpathy Tweet.txt`, `Karpathy's Obsidian RAG + Claude Code = CHEAT CODE.txt`. Karpathy's original LLM Knowledge Bases post.

---

## 2026-04-11: Use Claude Computer Use API beta directly, not vision-tag regex fallback

**Context:** Original CLAUDE.md described the flow as "Claude Vision API responds with text + [POINT:x,y:label] coordinate tags, Computer Use API refines element coordinates." This implies Vision is primary and Computer Use is a secondary refinement step. My first plan was to skip Computer Use entirely for Phase 1 as an optimization.

**Decision:** **Use Claude Computer Use API beta as the primary and only coordinate detection path in Phase 1.** Anthropic header: `anthropic-beta: computer-use-2025-11-24`. Tool definition: `tools=[{"type":"computer_20251124","name":"computer","display_width_px":declared_w,"display_height_px":declared_h}]`. Parse `tool_use` blocks from the response with `{"action":"left_click","coordinate":[x,y]}`. Mirror Clicky's `ElementLocationDetector.swift` exactly.

**Alternatives considered:**
1. **Vision-only with `[POINT:x,y:label]` regex extraction.** Pros: no beta headers, works on any Vision-capable model including OpenRouter-proxied. Cons: meaningfully less accurate. Original Clicky's code comment states: *"The Computer Use tool definition activates Claude's specialized pixel-counting training, which is significantly more accurate than regular vision API coordinate extraction."*
2. **Computer Use API directly** (chosen). Pros: pixel-perfect accuracy (Anthropic-trained for this specifically). Cons: beta header required, Anthropic-direct only (OpenRouter can't proxy beta features), slightly more token cost.
3. **Vision primary + Computer Use refinement for ambiguous cases.** Pros: cost optimization. Cons: two API calls per interaction (doubles cost and latency), complex branching logic, original Clicky doesn't do this.

**Why this won:** Original Clicky's Swift source (read directly from the previous chat's deep dive) uses Computer Use API as the single coordinate detection path. If it's good enough for a 3,500-star reference implementation, it's good enough for Phase 1. The accuracy win outweighs the token cost and the OpenRouter incompatibility (OpenRouter support is Phase 2 anyway).

**Consequences:**
- Phase 1 is locked to Anthropic-direct API (cannot use OpenRouter until Phase 2 adds vision-tag fallback)
- `ai.py`'s `AnthropicClient` hardcodes the beta header — this is explicit, not a mistake
- `AIClient` abstract base is structured so Phase 2 `OpenRouterClient` can subclass and implement the vision-tag regex fallback path
- The system prompt mirrors Clicky's `ElementLocationDetector.swift` prompt: *"Look at the screenshot. If there is a specific UI element (button, link, menu item, text field, icon, etc.) that the user should interact with or is asking about, click on that element."*

**References:** `Claude Code Clicky Chat.txt` lines 400-740 (Clicky's ElementLocationDetector.swift read via previous chat), Anthropic docs on Computer Use tool.

---

## 2026-04-11: Aspect-ratio-aware resolution picking from [(1024,768),(1280,800),(1366,768)]

**Context:** Anthropic docs recommend screenshot resolutions ≤1280×800 for Computer Use. Original CLAUDE.md said "resize to 1280x800 before sending." But monitors come in many aspect ratios (16:10 laptops, 16:9 external monitors, 4:3 legacy displays). Fixing the resolution to 1280×800 means 16:9 content gets stretched/squished when resized.

**Decision:** Pick the closest-aspect-ratio resolution from `[(1024,768), (1280,800), (1366,768)]` based on the actual monitor's width:height ratio. Mirror Clicky's `bestComputerUseResolution()` method exactly.

**Alternatives considered:**
1. **Fixed 1280×800** (CLAUDE.md original). Pros: simple. Cons: distorts 16:9 and 4:3 monitors, degrades X-axis coordinate accuracy.
2. **Always match the native aspect ratio** (e.g., 1280×720 for 16:9). Pros: no distortion. Cons: Anthropic's Computer Use training was done on specific resolutions, non-listed ones degrade accuracy in a different way.
3. **Closest Anthropic-recommended aspect ratio** (chosen). Pros: no distortion + trained resolutions. Mirrors Clicky's proven approach. Cons: slightly more code for the picker function.

**Why this won:** Original Clicky's code comment: *"Instead of always resizing to 1024x768 (4:3), we pick the Anthropic-recommended resolution closest to the display's actual aspect ratio. Most Macs are 16:10 → 1280x800. This avoids distorting the image Claude sees, which significantly improves X-axis coordinate accuracy."* Same logic applies to Windows, where aspect ratios are more varied than Mac.

**Consequences:**
- `config.py` exposes `CANDIDATE_RESOLUTIONS = [(1024,768), (1280,800), (1366,768)]`
- `capture.py` has `pick_resolution(width: int, height: int) -> (int, int)` that returns the closest-aspect-ratio pair
- `ai.py` declares whatever resolution was chosen (not hardcoded 1280×800) when creating the Computer Use tool
- Scale-back math in `app.py` uses the chosen resolution, not a constant

**References:** Original Clicky `ElementLocationDetector.swift` lines 430-550 (`supportedComputerUseResolutions` array and `bestComputerUseResolution()` function).

---

## 2026-04-11: `faster-whisper` over `openai-whisper`

**Context:** Original CLAUDE.md listed `openai-whisper` as the STT dependency. `faster-whisper` is a CTranslate2-based reimplementation that's ~4× faster on CPU with int8 quantization, drop-in compatible API.

**Decision:** Use `faster-whisper` with the `base` model, `device="cpu"`, `compute_type="int8"`. Lazy singleton at module import to avoid 2-3s reload latency per call. Pre-warm audio device at app startup to avoid first-record dropout.

**Alternatives considered:**
1. **`openai-whisper`** (CLAUDE.md original). Pros: official OpenAI impl. Cons: slower on CPU, larger memory footprint, no int8 quantization.
2. **Apple Speech / Windows SAPI recognition.** Pros: OS-native, zero dependencies. Cons: quality is much worse, Windows SAPI recognition is particularly weak.
3. **AssemblyAI streaming** (what original Clicky uses). Pros: real-time, high quality. Cons: cloud API, cost per minute, Phase 1 wants local-first privacy.
4. **`faster-whisper`** (chosen). Pros: ~4× faster, same model quality, int8 quantization, drop-in replacement for openai-whisper. Cons: one more indirection layer (CTranslate2).

**Why this won:** End-to-end latency budget for Clicky Windows is ≤7 seconds. Whisper transcription is a significant chunk (~2s for a 5-second recording). Shaving that with `faster-whisper` makes the UX feel responsive instead of laggy. Same base model = same accuracy.

**Consequences:**
- `requirements.txt` lists `faster-whisper` not `openai-whisper`
- `stt.py`'s `FasterWhisperSTT.__init__` uses `faster_whisper.WhisperModel("base", device="cpu", compute_type="int8")`
- Whisper model files live at `~/.cache/huggingface/hub/` on first use (~150 MB download on first run — slow initial startup, fast subsequent startups)
- If the CPU can't handle `base` at real-time speed, fall back to `tiny` (worse accuracy but 3× faster)

**References:** Guillaume Klein's `faster-whisper` repo. CTranslate2 benchmarks.

---

## 2026-04-11: Alt+Space hotkey, NEVER Ctrl+Space

**Context:** Original CLAUDE.md suggested Ctrl+Space as the default hotkey. During planning, the question came up whether to switch.

**Decision:** **Alt+Space is the default, suppressed via `pynput.keyboard.Listener(suppress=True)` low-level hook.** Fallback is Ctrl+Shift+Space if Alt+Space suppression turns out to be flaky at build time. **Ctrl+Space is explicitly rejected and must never be used.**

**Alternatives considered:**
1. **Ctrl+Space.** Pros: single modifier, ergonomic, matches Spotlight-like convention. Cons: **it's the VS Code IntelliSense keybinding.** Installing a low-level hook that suppresses Ctrl+Space would break VS Code autocomplete for any developer user — a severe regression. Also collides with Windows IME toggle, JetBrains IDE basic completion, most text editors' autocomplete.
2. **Alt+Space** (chosen). Pros: single modifier, ergonomic, and the only thing it normally does is open the Windows title-bar window menu (which nobody uses). Cons: requires low-level hook to suppress the window menu — potentially flaky with antivirus or Logitech G HUB drivers.
3. **Ctrl+Shift+Space.** Pros: no conflicts. Cons: 3-finger combo, awkward to hold for push-to-talk, matches the complaint in [Clicky Issue #35](https://github.com/farzaa/clicky/issues/35) about awkward combos.
4. **Ctrl+`** (backtick). Pros: no major conflicts. Cons: conflicts with VS Code terminal toggle (same severe regression for dev users).

**Why this won:** The developer user experience matters. Abhishek codes in VS Code daily. Losing IntelliSense to run Clicky is a deal-breaker. Alt+Space's only cost is the window menu, which is essentially free.

**Consequences:**
- `config.py` defaults `HOTKEY = "alt+space"`
- `hotkey.py` uses `pynput.Listener(suppress=True)` and explicitly handles Alt+Space
- If at Step 6 the suppression proves too flaky (antivirus intercepting, etc.), fall back to Ctrl+Shift+Space and add a new decision entry here documenting the reason
- `CLAUDE.md` Rules section explicitly says "NEVER Ctrl+Space"

**References:** VS Code keybinding docs for "editor.action.triggerSuggest".

---

## 2026-04-11: Provider abstraction from day 1 (AIClient, STT, TTS classes)

**Context:** Phase 1 uses Anthropic-direct + faster-whisper + pyttsx3. Phase 2 will add OpenRouter, AssemblyAI, ElevenLabs. The question: hardcode Phase 1 implementations and refactor later, or build the abstraction upfront.

**Decision:** Build the abstraction upfront. Each external-service file (`ai.py`, `stt.py`, `tts.py`) defines an abstract base class + one Phase 1 concrete implementation. Phase 2 adds new subclasses without touching `app.py`.

- `ai.py`: `class AIClient` abstract + `class AnthropicClient(AIClient)` → Phase 2 adds `class OpenRouterClient(AIClient)` with vision-tag regex fallback
- `stt.py`: `class STT` abstract + `class FasterWhisperSTT(STT)` → Phase 2 adds `class AssemblyAISTT(STT)` for streaming
- `tts.py`: `class TTS` abstract + `class Pyttsx3TTS(TTS)` → Phase 2 adds `class ElevenLabsTTS(TTS)`

**Alternatives considered:**
1. **Hardcode Phase 1 impls, refactor in Phase 2.** Pros: ~30 min faster now. Cons: every Phase 2 multi-provider feature becomes a refactor touching `app.py`. Estimated ~10 hours of Phase 2 pain.
2. **Abstract base classes upfront** (chosen). Pros: Phase 2 additions are subclasses, not refactors. Mirrors Wallee's `BuddyTranscriptionProvider` protocol pattern which they use to swap AssemblyAI/OpenAI/Apple Speech. Cons: 30 min extra now for the base classes.
3. **Full plugin system with entry_points and dynamic loading.** Pros: true plugin architecture. Cons: overkill for 2-3 implementations, hurts readability.

**Why this won:** 30 minutes now vs 10 hours later is an obvious trade. The base classes are tiny (one method each: `ask()`, `transcribe()`, `speak()`). The pattern is proven — Wallee uses it.

**Consequences:**
- `app.py` imports abstract types (`from ai import AIClient` not `from ai import AnthropicClient`)
- A factory function picks the concrete implementation based on `config.py` settings
- Phase 2 OpenRouter addition is: (a) write `OpenRouterClient` subclass, (b) add config flag, (c) update factory — no changes to `app.py`
- Testing is easier: `AIClient` can be mocked without monkeypatching the Anthropic SDK

**References:** Wallee's `BuddyTranscriptionProvider` protocol — `B0 projects research.txt` lines 1333-1414.

---

## 2026-04-11: Superpowers plugin, local scope only

**Context:** Superpowers (by obra, 144K stars) is a Claude Code plugin that enforces a brainstorm → spec → plan → TDD → code review workflow. The question was whether to install it and at what scope (user = all projects, project = shared with collaborators, local = just this project).

**Decision:** Install Superpowers at **local scope only** (`.claude/settings.local.json` entry, `~/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.7/` on disk, gitignored). Use selectively for the 5 hard components (capture.py, ai.py, overlay.py, memory.py, app.py). Skip the ceremony for the 4 trivial files (config.py, stt.py, tts.py, hotkey.py).

**Alternatives considered:**
1. **Skip Superpowers entirely.** Pros: less ceremony, faster iteration. Cons: lose the TDD discipline on the high-risk components (overlay, threading) where it matters most.
2. **User scope install.** Pros: active in all projects. Cons: introduces Superpowers ceremony into unrelated work (LearnLoop sprint work, MU Exemption tweaks) where fast iteration matters more than discipline.
3. **Project scope install.** Pros: shared with collaborators. Cons: no collaborators (Abhishek is solo). Adds `.claude/settings.json` entry to git, creates "install?" prompts for anyone who ever clones the repo.
4. **Local scope install** (chosen). Pros: zero risk to other projects, zero git footprint (`.claude/settings.local.json` is gitignored by default), fully reversible, avoids the cross-project bugs in Claude Code issues [#26513](https://github.com/anthropics/claude-code/issues/26513) and [#16174](https://github.com/anthropics/claude-code/issues/16174). Cons: nothing visible in git to remind future-me that this project uses Superpowers — solved by mentioning it in README.md and in this decision log.

**Why this won:** Local scope is the minimum-risk option that delivers all the benefits. Can be promoted to user scope later with one command (`/plugin uninstall` → `/plugin install` with user scope) if Abhishek decides he wants it everywhere.

**Consequences:**
- Claude Code session loads Superpowers skills only when this project is open
- Per-component execution plans are saved to `docs/superpowers/plans/YYYY-MM-DD-<component>.md`
- The brainstorming skill's HARD-GATE forces design approval before any code is written for a component
- The 4 trivial files (config, stt, tts, hotkey) skip Superpowers to avoid ceremony overhead on 50-100 LOC modules
- If the install proves annoying, uninstall is a single `/plugin uninstall` command with zero residue

**References:** Superpowers v5.0.7 from `github.com/obra/superpowers`, installed 2026-04-11 at git SHA `917e5f53b16b115b70a3a355ed5f4993b9f8b73d`. Claude Code plugin install docs at `code.claude.com/docs/en/discover-plugins`.

---

## 2026-04-11: Four docs, not ten — PRD + ROADMAP + DECISIONS + README-at-end

**Context:** The Claude Code FILES.md pattern suggests PRD.md + ARCHITECTURE.md + AI_RULES.md + PLAN.md + PROGRESS.md + DISCOVERY.md + RESEARCH.md = 7 files. The user's GPT Instructions mention DECISIONS.md, TESTING.md, ROADMAP.md — 3 more. Total: 10 files to maintain.

**Decision:** Four files only. [`PRD.md`](PRD.md), [`ROADMAP.md`](ROADMAP.md), [`DECISIONS.md`](DECISIONS.md) written upfront. [`README.md`](README.md) written last (after Phase 1 demo actually works). [`CLAUDE.md`](CLAUDE.md) already exists and stays.

**Alternatives considered:**
1. **The full 10-file pattern.** Pros: maximum documentation surface. Cons: every extra file is another file to keep in sync; ARCHITECTURE.md duplicates CLAUDE.md; PLAN.md/PROGRESS.md/TASKS.md/TESTING.md all answer "where are we" and can collapse into one file.
2. **Minimum: just README.md.** Pros: absolute minimum ceremony. Cons: loses the "why" layer (decisions), loses the "status" layer (where are we), loses the "acceptance proof" enforcement.
3. **Four-file set** (chosen). Pros: clean separation of concerns — PRD = what/why, ROADMAP = where, DECISIONS = why X not Y, README = user-facing intro. No duplication. Cons: some readers may look for ARCHITECTURE.md and not find it; mitigation is CLAUDE.md doubles as architecture context.

**Why this won:** Every extra file is friction. ROADMAP.md's status column eats PLAN/PROGRESS/TASKS/TESTING — they all answer "where are we now, and is it done?" DISCOVERY.md would just duplicate the chat history archives already in the project dir. RESEARCH.md lives in `B0 projects research.txt` already. ARCHITECTURE.md folds into CLAUDE.md. AI_RULES.md is already a section in CLAUDE.md.

**Consequences:**
- Four docs to keep updated (CLAUDE + PRD + ROADMAP + DECISIONS + README-at-end)
- No separate TESTING.md — acceptance proof lives next to each step in ROADMAP.md
- No separate PLAN.md — the frozen strategic plan lives at `C:\Users\Abhis\.claude\plans\streamed-tumbling-sunbeam.md` as historical reference, ROADMAP.md is the live version
- If a future B0 reviewer asks "where's the ARCHITECTURE doc?", the answer is "CLAUDE.md serves that role"

**References:** Claude Code FILES.md pattern, GPT Instructions file.

---

## 2026-04-11: Proactive mode stays in Phase 2 (Karpathy: "wait for the data")

**Context:** The danpeg/clicky fork added proactive mode (idle detection + focused-window capture + volunteer guidance without push-to-talk) and got 79 GitHub stars in 3 days — a validated demand. The question: include proactive mode in Phase 1 MVP or defer to Phase 2?

**Decision:** Proactive mode stays in Phase 2. Phase 1 is push-to-talk only.

**Alternatives considered:**
1. **Proactive mode in Phase 1.** Pros: combines all 3 validated demands (Windows + memory + proactive) in one MVP; bigger "wow" factor. Cons: adds ~1 week of scope, requires guessing what to be proactive about, introduces VAD / idle detection / false-positive management complexity.
2. **Proactive mode in Phase 2** (chosen). Pros: Phase 1 is smaller and shippable faster. Phase 1 markdown memory acts as the data substrate for Phase 2 proactive mode — by Phase 2 we'll have real patterns to target instead of guessing. Cons: defers the "wow" factor.
3. **Hybrid: minimal "Clicky is available, press Alt+Space" notification after N minutes of idle time in an app Clicky has memory of.** Pros: cheap, tests the concept. Cons: still guessing at the right trigger.

**Why this won:** Channeled Karpathy's philosophy directly: *"You don't know yet what to be proactive ABOUT. Build the minimal thing, use it yourself for two weeks, then notice the patterns in your own markdown memory files ('huh, every time I open Photoshop I ask the same question about the pen tool'). THAT observation is what tells you what proactive mode should actually do. Building proactive mode now is guessing what to be proactive about. Guessing is what you do when you don't have data. You're about to have data — wait for it."*

This also aligns with `lint_memory.py`'s role: the whole point of Step 7.5 is to generate the patterns that Phase 2 proactive mode targets. Building proactive mode before running `lint_memory.py` is backwards.

**Consequences:**
- Phase 1 is push-to-talk only; no idle detection, no background polling
- `memory.py` records every interaction so Phase 2 has a dense dataset to mine
- `tools/lint_memory.py` (Step 7.5) is the bridge between Phase 1 data collection and Phase 2 proactive targeting
- The B0 editorial "unexpected finding" is most likely to come from running lint_memory.py on real Phase 1 usage

**References:** Karpathy's LLM Knowledge Bases tweet and YouTube walkthrough. danpeg/clicky repo (79 stars, 3 days, proactive mode fork).

---

## 2026-04-11 (session 2): Defer settings UI / keychain-backed BYOK to Phase 2 — Phase 1 stays on `.env` only

**Context:** Phase 1 scaffold uses `.env` + `python-dotenv` for the Anthropic API key. User asked whether to instead add a first-launch GUI dialog that writes to `%APPDATA%\clicky-windows\config.json` (a "friendlier BYOK flow"). I also proposed a middle path: 10 LOC of config.json fallback logic now, dialog deferred to Phase 2.

**Decision:** **No middle path.** Phase 1 stays on `.env` only. No config.json fallback, no GUI dialog, no keychain integration. Phase 2 will copy Grafyn's pattern wholesale: `keyring` lib for OS Credential Manager storage + `platformdirs` for non-sensitive settings + QInputDialog for first-launch key entry + legacy `.env` → keychain migration.

**Alternatives considered:**
1. **Full GUI dialog in Phase 1** (~100 LOC + tests, 2-3 hours). Pros: friendlier for non-technical users from day 1. Cons: dilutes Phase 1's single hypothesis (memory > no memory) with a second one (onboarding UX works); Phase 1's only real tester is Abhishek who is a developer and can edit `.env`; adds new dependencies (`keyring`, `platformdirs`).
2. **10 LOC config.json fallback now, dialog in Phase 2** (middle path I proposed). Pros: structured for Phase 2 expansion. Cons: premature scaffolding — Grafyn's actual Phase 2 pattern uses OS keychain, not plaintext config.json, so the 10-LOC fallback would get thrown away in Phase 2 anyway. Write code you'll delete = waste.
3. **`.env` only in Phase 1** (chosen). Pros: zero extra work, zero wasted code, Phase 1 hypothesis stays clean. Cons: Phase 1 has no non-technical user onboarding path — but PRD acceptance criterion #5 explicitly says "Abhishek uses Clicky Windows himself for at least 5 meaningful sessions" (no non-technical users in Phase 1 scope).

**Why this won:** User said it plainly: *"I am okay with deferring to phase 2 and only proving the loop works in phase 1."* Every feature added to Phase 1 delays the memory hypothesis test. Vercept is racing. Keep Phase 1 minimal. Copy Grafyn's BYOK pattern properly in Phase 2, don't half-build it now.

**Phase 2 BYOK spec (reference, locked for when we get there):**

Copy Grafyn's pattern from `frontend/src-tauri/src/services/settings.rs`:
1. **API key → OS keychain** via the `keyring` Python lib. Windows Credential Manager encrypts under user's login session. Not plaintext on disk.
   ```python
   import keyring
   keyring.set_password("clicky-windows", "anthropic_api_key", key)
   ```
2. **Non-sensitive settings → `%APPDATA%\clicky-windows\settings.json`** via `platformdirs.user_config_dir()`. Stores hotkey, theme, model ID — NOT the key.
3. **First-launch flow in `app.py`:** `if not load_api_key(): show QInputDialog → keyring.set_password()`. ~15 LOC total.
4. **Legacy migration:** upgraded installs move `.env` plaintext → keychain on first launch, then clear `.env`. Mirrors Grafyn's `migrated_legacy_plaintext_key`.
5. **Masked display** in settings UI: `sk-a...x4n2` format, never show full key.
6. **Load order:** keyring → settings.json non-key prefs → `.env` (legacy fallback) → `ANTHROPIC_API_KEY` env var (dev override).

**Phase 2 work estimate:** ~100 LOC + ~30 test LOC, 2-3 hours. Already in [PRD.md § Phase 2 Scope](PRD.md) as "Configurable hotkey UI ... Tray icon with minimal settings panel."

**Consequences:**
- `config.py` stays as-is for Phase 1 — no code changes needed
- `requirements.txt` does NOT add `keyring` or `platformdirs` in Phase 1
- `.gitignore` hardened to also exclude `Anthropic API.txt` and common key-file patterns (`*.key`, `*api*key*`, `*.pem`, etc.) as defense in depth
- `Anthropic API.txt` is the user's temporary holding file for the key — they plan to rotate the key after testing; the hardened `.gitignore` ensures this file never reaches git even if it remains in the working directory
- Phase 2's BYOK work has a concrete, locked spec already written — when we get there, no design decisions remain, just implementation

**References:**
- Grafyn `frontend/src-tauri/src/services/settings.rs` (read via `gh api repos/WKJBryan/Grafyn/contents/...`) — specifically `KEYRING_SERVICE` constant, `load_openrouter_api_key()` / `store_openrouter_api_key()` helpers, `migrated_legacy_plaintext_key` migration flag, `get_api_key_masked()` for display
- Grafyn `frontend/src-tauri/src/services/openrouter.rs` for how the loaded key is actually used by the service
- Python `keyring` lib: https://pypi.org/project/keyring/
- Python `platformdirs` lib: https://pypi.org/project/platformdirs/

---

## 2026-04-11: Per-monitor overlays instead of virtual-desktop-spanning (Qt "islands-of-screens" gotcha)

**Context:** CLAUDE.md Rules section originally specified that `overlay.py` should create one `QWidget` covering the full Windows virtual desktop `(virtual_left, virtual_top, virtual_width, virtual_height)`. This was written before the Step 3 research pass.

**Decision:** Create **one `QWidget` overlay per physical monitor** by iterating `QGuiApplication.screens()`. Each overlay covers only its own screen's `geometry()` in DIP coords. Claude's returned coordinates get routed to whichever screen was the capture target via `screen_for_monitor()` — a metadata match against `CaptureResult.monitor`'s physical bounds.

**Alternatives considered:**
1. **Virtual-desktop-spanning overlay** (original CLAUDE.md wording). Pros: simpler code (~30-50 fewer LOC), works fine on single-monitor. Cons: Qt 6's High DPI docs explicitly warn about "islands-of-screens" geometry on mixed-DPI Windows — coordinates near monitor boundaries land in gaps. Silent failure mode.
2. **Per-monitor overlays** (chosen). Pros: works on any monitor setup regardless of DPI, matches reference implementations (danpeg/clicky, OBS Studio, Ammad-Younas/Screen_Annotation), no gap bugs. Cons: ~30-50 extra LOC for the controller loop and screen-metadata lookup.
3. **Single overlay on primary + fall back to per-monitor on multi-monitor detection**. Pros: simpler common case. Cons: branching logic, two code paths to test, no real benefit — per-monitor collapses to "one overlay" on single-monitor anyway.

**Why this won:** Qt 6 official docs ([doc.qt.io/qt-6/highdpi.html](https://doc.qt.io/qt-6/highdpi.html)) verbatim: *"Application code should not assume that a position immediately adjacent to and outside one screen is a valid position on the neighboring screen."* Mixed-DPI is the default state on modern Windows 11 setups (laptop at 200% + external monitor at 100% is a common configuration — e.g., when presenting a demo with Windows+P Extend mode). The gap bug silently produces wrong coordinates — worst possible failure mode for a screen-aware AI whose entire UX is visual pointing. Pre-emptively fixing it costs 30-50 LOC; not fixing it costs a failed demo in front of real users.

**Consequences:**
- `overlay.py` has an `OverlayController` class managing `list[OverlayWindow]`, created via `QGuiApplication.screens()` iteration at app startup
- Routing via `screen_for_monitor(monitor, screens)` metadata match, then `physical_to_local_logical(x, y, screen)` coordinate conversion
- Each `OverlayWindow` operates entirely in its own screen's local DIP coordinate space — no global virtual-desktop coords
- Per-screen `devicePixelRatio()` (never cached globally — mixed-DPI setups have different ratios per screen)
- Manual verification on single-monitor only in Phase 1 (user's 2880×1800 @ 200% DPI machine); real multi-monitor spanning verified in Phase 2 when user plugs in an external display
- CLAUDE.md Rules section updated in the same commit to match (no contradictions between docs)
- PyQt6 dependency injection in `OverlayController` (overlay_factory + screens params) enables unit testing without a real `QApplication`

**References:**
- Qt 6 High DPI documentation: https://doc.qt.io/qt-6/highdpi.html
- Microsoft Win32 Extended Window Styles: https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles
- [Ammad-Younas/Screen_Annotation](https://github.com/Ammad-Younas/Screen_Annotation) PyQt6 reference implementation
- [PythonOverlayLib](https://pypi.org/project/PythonOverlayLib/) PyQt5 reference (exact Win32 flag recipe)
- `farzaa/clicky` upstream `ElementLocationDetector.swift` (per-monitor macOS pattern via `NSScreen.screens`)
- Explore agent research pass during Step 3 brainstorm this session
- docs/superpowers/plans/2026-04-11-overlay.md (full Step 3 design + Boris #5 self-critique)

---

## 2026-04-11: Boris Chenry #5 "Verification Before Done" applied to Step 3 as a pre-commit review gate

**Context:** After Step 3 `overlay.py` functional implementation was complete (12/12 unit tests green, manual 5-point verification confirmed working on user's 2880×1800 @ 200% DPI machine), the user pointed at [Boris Chenry's CLAUDE.md tips](file:///C:/Users/Abhis/OneDrive/Documents/Maritime%20Project/Claude%20Code%20TIPS/Boris%20Chenry%20TIPS/CLAUDE%20MD.jpeg) — specifically #5 "Verification Before Done": *"Never mark complete without proving it works. Diff behavior, explain the elegant solution. Ask 'would a staff engineer ship this?' Skip the obvious fixes. Challenge your own work before presenting it."*

**Decision:** Apply Boris's #5 as a **mandatory self-critique pass** before every commit going forward. Not just for high-risk components — for every non-trivial feature commit. The pass must produce a concrete list of items that would get flagged in code review, tiered into do-now / defer / never categories, with the user approving the tier before the commit lands.

**Alternatives considered:**
1. **Ship when tests pass** (original flow). Pros: fastest. Cons: tests-pass doesn't mean staff-engineer-ship. Misses silent failure modes (items 3 and 4 from the Step 3 critique would've shipped unfixed).
2. **Boris #5 pass for high-risk components only.** Pros: targeted. Cons: defining "high-risk" after the fact is subjective; most bugs ship in code I thought was low-risk.
3. **Boris #5 pass for every non-trivial feature commit** (chosen). Pros: catches real issues consistently; takes ~10-15 min per commit; forces honest self-assessment. Cons: adds process step.

**Why this won:** The Step 3 Boris pass caught **2 real issues** in code that passed 12 unit tests:
- `apply_clickthrough_styles` silently swallowed Win32 failures (return code ignored). Silent click-through break with zero diagnostic signal.
- `hide_for_capture()` / `show_after_capture()` had no test coverage despite being the screenshot-integrity invariant (if they ever fail, Claude sees our pointer in its own screenshot, creating an infinite feedback loop).

Both would have shipped unfixed without the pass. Both are exactly the kind of "tests pass but broken in production" bug that makes users ragequit a demo.

**What the pass looks like in practice:**
1. After all unit tests pass AND manual verification passes, BEFORE staging files for commit
2. Open the changed files and read them with fresh eyes
3. List every item a staff engineer would flag in code review (style, types, coverage, error handling, naming, comments)
4. Tier into: Tier 1 (do now, worth the time), Tier 2 (defer to Phase 2 cleanup), Tier 3 (user noticed but answer stands)
5. Present tiered list to user via AskUserQuestion
6. User picks scope (all Tier 1 / critical only / ship as-is)
7. Apply the chosen cleanup, re-run tests, relaunch manual verification if applicable
8. Commit with body noting "Boris #5 Tier N cleanup applied pre-commit per staff-engineer-ship review"

**Consequences:**
- Every non-trivial feature commit gets ~10-15 extra minutes of self-review + cleanup time
- Commit messages note which Boris tier was applied
- Over time, fewer silent failure modes ship in Phase 1 → Phase 2 hardening passes are easier
- User retains veto power (can always "ship as-is" if the issues are minor)
- The process does NOT apply to pure documentation changes, trivial typo fixes, or revert commits

**References:**
- Boris Chenry's CLAUDE.md tips image at `C:\Users\Abhis\OneDrive\Documents\Maritime Project\Claude Code TIPS\Boris Chenry TIPS\CLAUDE MD.jpeg`
- Step 3 `overlay.py` Boris #5 self-critique in `docs/superpowers/plans/2026-04-11-overlay.md`
- `feedback_ceremony_vs_lean.md` memory file (complementary rule for when to invoke Superpowers ceremony at all)

---

## 2026-04-11 (session 3): Priority inversion — latency over local-first

**Context:** PRD.md § "What Clicky Windows IS" item 4 originally said *"Local-first by default. faster-whisper for STT runs on your CPU... pyttsx3 TTS runs on your CPU."* This framing was inherited from previous-session PRD drafting and was never an explicit user decision — it implied a privacy-first Phase 1 philosophy. Phase 1 Acceptance Criteria (PRD.md § Phase 1 Scope) contains ZERO privacy requirements: #1 "working loop," #2 "multi-monitor + DPI," #3-4 "memory persists + human-readable," #5 "5+ real sessions," #6 "lint_memory insights," #7 "~50-80 tests," #8 "demo video," #9-10 "docs + repo." Privacy was phantom scope that I carried forward without questioning.

User called this out 2026-04-11 session 3 after I drafted Steps 4-6 plan assuming `pyttsx3` + `faster-whisper` were fine: *"BRO THE ENTIRE THING WHICH MADE THE CLICKY DEMO VIDEO SEXY WAS THE ALMOST INSTANT RESPONSE, AS IF QUITE LITERALLY TALKING TO A BUDDY NEXT TO YOU WHO HAS KNOWLEDGE ABOUT THE SOFTWARE U ARE TRYING TO LEARN. THE PRIORITY IS LATENCY FIRST. SHOULD FEEL IMMEDIATE."*

**Decision:** Phase 1 priority is **LATENCY-FIRST**, not privacy-first. The UX promise is "feels like a buddy next to you who knows the software" — which requires sub-second perceived response from hotkey release to first spoken word.

**Phase 1 stack (locked after 3 parallel research agents + WebSearch fill-in):**

- **STT: AssemblyAI `u3-rt-pro` streaming + `ForceEndpoint`** — ~150ms P50 finalization after hotkey release. WebSocket to `wss://streaming.assemblyai.com/v3/ws` with query params matching Clicky's Swift source: `speech_model=u3-rt-pro`, `sample_rate=16000`, `encoding=pcm_s16le`, `format_turns=true`. Python SDK `assemblyai`. Audio format: PCM16 16kHz mono 1024-frame chunks from `sounddevice.RawInputStream` (matches Clicky's `AVAudioEngine.installTap(bufferSize:1024)` exactly).
- **TTS: Cartesia Sonic-3 WebSocket streaming** — ~150-250ms TTFB, state-space model architecture (Mamba derivative) that's architecturally faster than transformer TTS. Most expressive "buddy" voice quality in the cloud TTS field per independent benchmarks (April 2026). Python SDK `cartesia` with async WebSocket support built in. Output format: PCM float32 44.1kHz streamed chunks played via `sounddevice` output stream.
- **Claude model: Sonnet 4.6 (unchanged)** with `stream=True`. Swapping to Haiku 4.5 would require downgrading `computer-use-2025-11-24` beta header to `computer-use-2025-01-24` AND tool type `computer_20251124` to `computer_20250124`, losing November 2025 pixel-counting training improvements with unknown accuracy cost. Add `CLICKY_MODEL` config knob for Phase 2 benchmarking; default stays Sonnet.
- **Response streaming + sentence-level TTS chunking (Step 7 `app.py` requirement):** subscribe to `content_block_delta` events with `delta.type == "text_delta"`, accumulate, flush complete sentences to `tts.speak_sentence()` on `.`/`!`/`?` boundaries. Tool_use block stays buffered until `content_block_stop` then fires overlay pointer. Saves ~300-500ms of perceived latency — a genuine latency win Clicky does NOT implement.

**Expected end-to-end perceived latency:** ~800-1200ms from hotkey release to first audible word (~150ms STT + ~500-800ms Claude TTFT + ~200ms TTS TTFB, minus ~300ms sentence-streaming overlap). **5-6× faster than the original `faster-whisper + pyttsx3` plan** (~5-7s).

**Alternatives considered:**

1. **Keep local-first framing, ship `faster-whisper + pyttsx3`** (original PRD). Pros: zero network dependency, zero API key costs, privacy. Cons: invalidates the UX hypothesis — the Phase 1 demo video would feel laggy (~5-7s), users would write it off as "same as Clippi.us but slower." Privacy was never in the acceptance criteria; this was phantom scope I inherited from a previous session's PRD draft.
2. **Deepgram single-vendor (STT + TTS):** Deepgram Nova-3 streaming + Deepgram Aura-2 WebSocket. Pros: only 1 new API key beyond Anthropic, $200 free credit no credit card, mature Python SDK, single vendor relationship. Cons: Aura-2 voice is "professional enterprise" (built for IVR systems) not "expressive buddy" — hurts demo video vibe. STT is ~300ms vs AssemblyAI's ~150ms (both under perceptual threshold but AssemblyAI is faster). Loses on latency-to-human-sounding ratio, wins on dev convenience.
3. **Groq batch STT + Deepgram Aura-2 TTS:** Groq `whisper-large-v3-turbo` batch (simple HTTP POST, no WebSocket) + Deepgram streaming TTS. Pros: simpler STT code than WebSocket streaming, generous free tiers on both. Cons: Groq batch latency 300-800ms vs AssemblyAI 150ms; Aura-2 enterprise voice hurts buddy-feel demo.
4. **Hybrid — local default, cloud opt-in.** Pros: covers both users. Cons: doubles Phase 1 scope, forces factory pattern before we know which default is right, premature flexibility.
5. **Latency-first cloud streaming mandatory in Phase 1** (chosen). Pros: single stack, ships the UX hypothesis, benchmarked against real third-party data (AssemblyAI official docs for ForceEndpoint + 150ms P50 claim, Cartesia official Sonic-3 docs for ~90ms model TTFB, independent Pipecat/LiveKit/Vapi community benchmarks). Cons: requires 2 new API keys in `.env` (acceptable — Abhishek is the only Phase 1 tester; Phase 2 BYOK will let users bring their own keys for all 3 providers).

**Why this won:** User's "sexy demo" directive + Phase 2 BYOK framing + abstract base pattern making provider swap a 1-2 hour subclass means we can pick the best-for-demo stack now without locking out alternatives later. Cartesia Sonic-3's voice quality is the single biggest axis for "does the demo video make people go holy shit." AssemblyAI's `ForceEndpoint` + 150ms P50 is the absolute fastest PTT finalization available. Both are proven integrations (AssemblyAI is literally what Clicky uses; Cartesia is the default recommendation for Pipecat and LiveKit Agents for latency-critical voice agents).

**Don't blindly copy Clicky:** Clicky uses AssemblyAI `u3-rt-pro` streaming (same as us, ✅) + ElevenLabs `flash_v2_5` via Cloudflare Worker proxy (buffered-then-play despite the comment claim, 500-1200ms real-world — we improve on this with Cartesia Sonic-3 at ~200ms) + probably Claude Sonnet batch (we improve with response streaming + sentence chunking). Two of Clicky's three pipeline stages are beaten by us on latency. That's a genuine "build something better" argument for Phase 1.

**Consequences:**

- Phase 1 requires `ASSEMBLYAI_API_KEY` and `CARTESIA_API_KEY` in `.env` in addition to `ANTHROPIC_API_KEY`. Both have generous free tiers (AssemblyAI $50 credit = ~330 hours streaming; Cartesia 20k credits/month = ~20-30 min TTS). Neither requires a credit card for the free tier.
- `stt.py` is a streaming WebSocket client, NOT a blocking `sounddevice` recorder. Architecture: open WebSocket → start sounddevice stream → forward audio chunks in real time → on hotkey release send `ForceEndpoint` → await final transcript (~150ms).
- `tts.py` is a streaming WebSocket client with sentence-chunk support. Public API: `speak(text)` for full-response mode, `speak_sentence(sentence)` for Step 7 sentence-chunking integration, `stop()` for Phase 2 TTS interruption (wire the API now, use later per Issue #36).
- `app.py` orchestration (Step 7): streaming Claude response → sentence splitter → TTS chunks, overlapping audio playback with Claude token generation. Tool_use block buffered separately; overlay fires on `content_block_stop`. Threading: Qt signals only across thread boundaries per CLAUDE.md rule.
- Latency budget in PRD updates: old `≤7s total E2E` → new `≤500ms STT finalization + ≤1500ms Claude TTFT + ≤300ms TTS TTFB + sentence-streaming overlap = target ~800-1200ms perceived first-audible-word`.
- `PRD.md` "Local-first by default" bullet replaced with "Latency-first, feels like a buddy next to you."
- `CLAUDE.md` updated to match.
- `requirements.txt` adds `assemblyai` + `cartesia`, removes `faster-whisper` + `pyttsx3` (neither used in Phase 1).
- `.env.example` adds `ASSEMBLYAI_API_KEY=` and `CARTESIA_API_KEY=` placeholders.
- `feedback_ceremony_vs_lean.md` memory updated with "research is not ceremony" clarification — the reason this decision got missed until session 3 was that I skipped research for Steps 4-6 claiming "lean mode."
- New `feedback_plan_mode_discipline.md` memory file saved — user explicitly corrected me that "I cannot in plan mode" is a deflection, the honest framing is "ask to exit plan mode."

**Relationship to prior decisions:**

- Supersedes `"faster-whisper over openai-whisper"` decision for Phase 1 (still valid rationale if/when a `FasterWhisperSTT` subclass is added in Phase 2 for offline mode).
- Supersedes `"pyttsx3 for TTS"` as the Phase 1 default (kept as Phase 2 subclass candidate for offline mode).
- Does NOT affect the `"Alt+Space hotkey, NEVER Ctrl+Space"` decision (unchanged).
- Does NOT affect the `"Computer Use API beta directly"` decision (unchanged — we're still using Sonnet 4.6 with the November 2025 beta).
- Reinforces the `"Provider abstraction from day 1 (AIClient, STT, TTS classes)"` decision — the whole reason we have abstract bases is so this kind of stack pivot is a 1-2 hour subclass swap.

**References:**

- User verbatim, 2026-04-11 session 3 (all-caps latency priority + "don't blindly copy Clicky" + "sexy demo" directive)
- PRD.md § Phase 1 Acceptance Criteria (zero privacy mentions)
- DECISIONS.md § "faster-whisper over openai-whisper" (latency-motivated, not privacy-motivated — supports the priority inversion)
- DECISIONS.md § "Provider abstraction from day 1" (makes the swap cheap)
- Research Agent B (streaming TTS benchmarks, 2026-04-11): *"Cartesia Sonic-3: ~90ms model-internal TTFB, 150-250ms real-world; best latency-to-natural-voice ratio in the cloud TTS field as of April 2026; state-space model architecture is architecturally faster than transformer TTS; 20k free credits no credit card."*
- Research Agent C (Claude model latency, 2026-04-11): *"Haiku 4.5 does NOT support `computer-use-2025-11-24` beta header — only older `computer-use-2025-01-24`. Switching requires downgrading beta header + tool type with unknown accuracy cost on November 2025 pixel-counting training. Recommend keeping Sonnet 4.6 as default."*
- WebSearch fill-in for STT research (2026-04-11): AssemblyAI docs confirm `ForceEndpoint` message for PTT-style manual end-of-utterance signal; `u3-rt-pro` ~150ms P50 after force-endpoint.
- `farzaa/clicky/leanring-buddy/AssemblyAIStreamingTranscriptionProvider.swift` lines 447-451 (verbatim query params we're copying for Phase 1)
- `farzaa/clicky/leanring-buddy/ElevenLabsTTSClient.swift` lines 40-50, 63-68 (Clicky's buffered-then-play pattern we're beating with Cartesia streaming)
- `feedback_ceremony_vs_lean.md` memory (updated 2026-04-11 session 3 with "research is not ceremony")
- `feedback_plan_mode_discipline.md` memory (new, 2026-04-11 session 3)

---

## 2026-04-12: Ctrl+Shift+Space over Alt+Space — pynput suppress=True is globally destructive, not per-combo

**Context:** The 2026-04-11 decision "Alt+Space hotkey, NEVER Ctrl+Space" locked Alt+Space as the Phase 1 default with `pynput.keyboard.Listener(suppress=True)` to prevent the Windows title-bar menu from opening on every press. That decision had Ctrl+Shift+Space listed as a fallback *"if Alt+Space suppression proves too flaky (antivirus intercepting, etc.)."*

During Step 6 manual verification on 2026-04-12, the user ran `py -3.13 -m hotkey`, tried to type in Notepad in another window while the listener was running, and found: **their entire keyboard was disabled globally**. Not just Alt+Space — ALL keys. Direct user quote: *"NO MY KEYBOARD IS DISABBLED, NOTHING WORKS, I CANT TYPE."*

**Root cause (verified via pynput source + web research):** pynput's `suppress=True` on a `Listener` installs a Windows `WH_KEYBOARD_LL` low-level hook that suppresses EVERY key event regardless of which combo we care about. pynput's API has no per-combo opt-out. The handler cannot return "suppress this one but let that one through" — it's all-or-nothing. The "fallback if flaky" framing in the original decision understated the problem: it's not *flaky*, it's *fundamentally global*.

**Research performed 2026-04-12 before pivoting:**

- [NVDA Issue #3472](https://github.com/nvaccess/nvda/issues/3472) documents the same Alt+Space pain: *"if you bind a keyboard gesture which uses the alt and/or Windows modifiers, the menu bar or Start Menu will often appear when the key is released."* Even `RegisterHotKey` doesn't cleanly solve it.
- [Microsoft `WM_HOTKEY` docs](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-hotkey) + [Qt Forum](https://forum.qt.io/topic/92983/how-to-detect-hot-key-release-event-when-using-qxtglobalshortcut): *"there is no way to use WM_HOTKEY and get an event on button released."* Push-to-talk release detection via `RegisterHotKey` requires `GetAsyncKeyState` polling with a timer (25ms+ avg latency overhead + CPU waste).
- [AutoHotkey `#MenuMaskKey`](https://autohotkey.com/docs/commands/_MenuMaskKey.htm) workaround: send a synthetic Ctrl before Alt release to "mask" it — fragile, can interfere with other apps.
- [boppreh/keyboard Issue #22](https://github.com/boppreh/keyboard/issues/22) *"Support for key suppression"* is **still open** — the `keyboard` library's per-hotkey suppression claim is ambiguous and may have the same global-suppress bug as pynput.

**Conclusion from research:** A clean Alt+Space push-to-talk on Windows is an 8-12 hour project involving `RegisterHotKey` + observe-only low-level hook for release detection + masking-Ctrl trick for menu suppression. Every layer has fragile edge cases. NOT a 60-90 minute fix as originally estimated.

**Decision:** Phase 1 hotkey is **Ctrl+Shift+Space** via `pynput.keyboard.Listener(suppress=False)`. We observe but never consume keys. Ctrl+Shift+Space has no default Windows OS behavior, so we don't need to block it. Global typing continues to work normally.

**Alternatives considered (enumerated for Phase 2 reference):**

1. **Keep Alt+Space with pynput suppress=True** (original plan). Pros: 2-finger ergonomics. Cons: VERIFIED FAIL — globally disables all keyboard input during PTT sessions. Unshippable.
2. **Win32 `RegisterHotKey` + GetAsyncKeyState polling + masking-Ctrl for menu suppression.** Pros: proper Windows-native 2-finger Alt+Space. Cons: 8-12 hours of ctypes code with fragile workarounds, plus polling overhead for release detection. Deferred to Phase 1.5/2 as an opt-in subclass.
3. **`boppreh/keyboard` library with `add_hotkey('alt+space', suppress=True)`.** Pros: claims per-hotkey suppression. Cons: GitHub Issue #22 is open, suggesting the claim is aspirational. Untrustworthy without empirical testing.
4. **Ctrl+Shift+Space with pynput suppress=False** (chosen). Pros: reuses existing pynput code, zero suppression issues, Ctrl+Shift+Space has no default Windows behavior. Cons: 3-finger combo (Clicky Issue #35 complains about these), minor VS Code Parameter Hints conflict. Acceptable for Phase 1.
5. **Single-key hold (Pause/Break, F13-F24).** Pros: 1-finger. Cons: unusual muscle memory, F-key conflicts with some apps, keyboard physical-availability varies.
6. **Shift+Space.** Pros: 2-finger. Cons: page-down in browsers/readers, Shift+Space produces a literal " " character that apps also consume — fundamentally bad.

**Why #4 won:** The user's exact words: *"for Phase 1, I don't think we should be debating this much on a simple hotkey"* + *"ship and move on."* Phase 1 ergonomics are not the primary value — persistent memory is. The `PushToTalkHotkey` abstract base class makes a Phase 1.5 upgrade to Win32 `RegisterHotKey`-based Alt+Space a drop-in subclass swap without touching `app.py`. Clicky's Issue #35 "3-finger combo awkward" is real but targets PR #16 (configurable hotkey UI) which is Phase 2 scope. Phase 1 has ONE tester (Abhishek) who can tolerate Ctrl+Shift+Space for 1-2 weeks while building the differentiator.

**Consequences:**

- `hotkey.py` rewritten: state machine tracks `_ctrl_down` + `_shift_down` + `_space_down`. RECORDING state requires all 3 held. Any release of any of the 3 while RECORDING ends the session. `Listener(suppress=False)` — no global suppression.
- `tests/test_hotkey.py` rewritten: 10 tests (was 8) covering all 6 press orders + 2 release paths + 2 lifecycle tests. New edge cases: `test_ctrl_shift_without_space_does_not_fire` + `test_release_ctrl_while_recording_also_fires_on_release`. Critical assertion added: `kwargs["suppress"] is False` with comment citing this decision entry.
- `config.py` HOTKEY default: `"alt+space"` → `"ctrl+shift+space"`.
- `.env.example` HOTKEY comment updated.
- `CLAUDE.md`, `PRD.md`, `ROADMAP.md` updated to reference Ctrl+Shift+Space throughout the Core Loop and acceptance criteria.
- The 2026-04-11 "Alt+Space hotkey, NEVER Ctrl+Space" decision is **SUPERSEDED for Phase 1** by this entry, but the "NEVER ctrl+space" rule (plain Ctrl+Space) still stands — that remains rejected for VS Code IntelliSense conflict.
- Phase 1.5 upgrade path: add a `RegisterHotKeyPushToTalk(PushToTalkHotkey)` subclass using Win32 `RegisterHotKey` + observe-only low-level hook. Same abstract interface, swap via env var or config knob.
- PR #16 (configurable hotkey UI) becomes more valuable as a Phase 2 item since users with different preferences can now pick from both implementations.

**Minor known conflict:** VS Code binds Ctrl+Shift+Space to "Trigger Parameter Hints" (not IntelliSense — that's plain Ctrl+Space). Holding Ctrl+Shift+Space in VS Code will briefly show parameter hints while Clicky also records. Acceptable Phase 1 UX nit. Phase 2 configurable UI lets user rebind.

**References:**

- User quote, 2026-04-12: *"NO MY KEYBOARD IS DISABBLED, NOTHING WORKS, I CANT TYPE"*
- User quote, 2026-04-12: *"are you 100% sure alt + space u can make it work this time? Do research/web search to get the latest information, not a hypothesis"*
- User quote, 2026-04-12: *"for Phase 1, I don't think we should be debating this much on a simple hotkey"*
- [NVDA Issue #3472](https://github.com/nvaccess/nvda/issues/3472) — Alt/Windows modifier menu activation on release
- [Microsoft Learn: WM_HOTKEY](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-hotkey) — no release event
- [AutoHotkey #MenuMaskKey](https://autohotkey.com/docs/commands/_MenuMaskKey.htm) — masking-Ctrl workaround
- [Qt Forum: QxtGlobalShortcut release detection](https://forum.qt.io/topic/92983/how-to-detect-hot-key-release-event-when-using-qxtglobalshortcut) — GetAsyncKeyState polling requirement
- [boppreh/keyboard Issue #22](https://github.com/boppreh/keyboard/issues/22) — per-hotkey suppression still open
- [pynput docs](https://pynput.readthedocs.io/en/latest/keyboard.html) — confirms suppress is a Listener-level flag, not per-handler
- Previous decision 2026-04-11 "Alt+Space hotkey, NEVER Ctrl+Space" (superseded for Phase 1)

---

## 2026-04-12: Removed `infer_skill_level` from memory.py — no pedagogical framework matches Clicky's learn-by-doing UX

**Context:** The original memory.py plan (`docs/superpowers/plans/2026-04-12-memory.md` + pre-compact `memory py.txt`) included an `infer_skill_level(app_name) -> Literal["beginner", "intermediate", "expert"]` public method on `MemoryStore`. Buckets: `< 5` interactions = beginner, `5..20` = intermediate, `> 20` = expert. Unknown app → beginner fallback. Rationale at the time: "inject a 'user appears to be intermediate at this app' line into Claude's prompt alongside the recalled markdown so Claude adapts vocabulary + assumed knowledge."

During the build on 2026-04-12, right after Boris #5 self-critique + the test suite hit 99/99 green, the user pushed back hard, verbatim: *"Why does beginner intermediate expert even matter for clicky? This is not Khan academy now is it? The whole value is learn by doing is it not? There is no reward system so why would we want to do that?"*

The critique was correct and surfaced a design leak I hadn't caught during the plan review.

**Decision:** `infer_skill_level` is **removed entirely** from memory.py. No "mark for Phase 2," no feature flag, no deprecation comment. The method, its 7 tests, its `typing.Literal` import, its manual-gate print output, and every mention in the plan doc are deleted or marked historical. `MemoryStore` now has exactly four public methods: `__init__`, `recall()`, `record()`, `list_known_apps()`.

**Why it was wrong:**

1. **Clicky's product model is "press hotkey, ask, see pointer, done."** There is no course, no curriculum, no progression ladder, no XP, no unlock gate. Slapping a skill label on the user imports pedagogical framing from apps (Khan Academy, Duolingo, Codecademy) that have nothing structurally in common with what we're building.
2. **The bucket thresholds were invented without evidence.** I picked `<5` / `5..20` / `>20` because they sounded reasonable. There is no data that says 5 interactions is where "beginner" ends. Phase 1's job is to validate the memory hypothesis, NOT to validate arbitrary skill thresholds invented during a planning session.
3. **Interaction count is not a skill signal.** A user who asked "how do I open this file" 30 times is not an "expert" — they have a recurring problem. Count alone tells you engagement depth, not comprehension.
4. **The LLM can already infer this from the raw markdown.** If Claude reads the recalled tail of `excel.exe.md` and sees five interactions all about the same feature, it can infer the user is stuck. If it sees interactions spanning complex workflows, it can infer depth. We don't need a pre-digested label — that's exactly the kind of "reach for fancy RAG" anti-pattern the Karpathy LLM-KB tweet warns against. Trust the LLM, put the raw data in, don't pre-process.
5. **It wasn't in the Phase 1 acceptance criteria.** PRD.md § Phase 1 Scope does not mention skill-level adaptation. The hypothesis is *"persistent memory makes Clicky Windows meaningfully better than stateless Clicky"* — full stop. Skill-level injection was extra scope that snuck in during planning because it sounded clever.

**Alternatives considered:**

1. **Keep it, mark as experimental** — rejected. Phase 1 shouldn't ship experimental features that aren't part of the validation hypothesis. Every extra surface is another thing that can fail + distracts from the real test.
2. **Defer to Phase 2 behind a feature flag** — rejected. YAGNI. If we don't need it in Phase 1, we might not need it ever. Add it back IF AND WHEN 5+ real sessions show Claude adapts poorly without an explicit skill signal. At that point we'd also have real data to calibrate the thresholds, instead of guessing.
3. **Replace with a smarter heuristic (question complexity, time-between-sessions, repetition detection)** — rejected. Same problem: we're pre-digesting data the LLM can read directly. If we build any of these, it should be motivated by observed Phase 1 failure modes, not speculative design.
4. **Delete it entirely** (chosen) — ships the minimum surface that tests the real hypothesis. If the hypothesis is wrong, we find out faster because there's one less variable. If the hypothesis is right, we don't have to justify the extra methods in the demo.

**Why this won:** The user's one-line critique was dispositive: *"This is not Khan Academy."* That's a product-framing pushback that can't be answered with engineering arguments. I had not grounded the method in any actual UX hypothesis — I added it because the pre-compact design doc said to, not because it was the right thing to ship. Karpathy "wait for the data" applies literally. Adding code that isn't tied to a testable hypothesis is premature optimization.

**Consequences:**

- `memory.py`: `infer_skill_level` method deleted (~35 LOC). `typing.Literal` import deleted. Module docstring updated to remove skill-level mention in the responsibility-boundary section. `__main__` manual-gate block no longer prints skill level; checklist is 4 items instead of 5.
- `tests/test_memory.py`: 7 tests deleted (6-parametrize `test_infer_skill_level_buckets` + 1 `test_infer_skill_level_unknown_app_returns_beginner`). Test count drops from 22 to 15. Full suite: 99 → 92 green.
- `docs/superpowers/plans/2026-04-12-memory.md`: "DEVIATION FROM PLAN" section added at top explaining the deletion. Original plan content (including skill-level sections) preserved below as historical record so future Claude reading post-/compact doesn't get confused about the source of truth.
- `app.py` (Step 7, not yet built): the Step 7 plan + eventual implementation will NOT inject a skill-level line into the Claude prompt. Prompt construction will be: image + text content block with `[Previous interactions in <app>:]\n<recall() output>\n\n[Current question:]\n<transcript>`. Simpler, more Karpathy-pure.
- `tools/lint_memory.py` (Step 7.5, not yet built): may still compute skill-level-like patterns in the weekly insights report if the data reveals them, but it does NOT inject anything back into the runtime memory store. Lint output is for the user's eyes (via `insights.md`), not for Claude's prompt.
- No impact on `capture.py` / `ai.py` / `overlay.py` / `stt.py` / `tts.py` / `hotkey.py`.

**References:**

- User quote, 2026-04-12: *"Why does beginner intermediate expert even matter for clicky? This is not Khan academy now is it? The whole value is learn by doing is it not? There is no reward system so why would we want to do that?"*
- `docs/superpowers/plans/2026-04-12-memory.md` § "DEVIATION FROM PLAN" (added 2026-04-12)
- `feedback_brutally_honest_mode.md` memory: *"Do NOT agree by default. Challenge the user's assumptions. If the user's plan is weak, say so."* — I should have challenged my own plan on this point, not needed the user to do it.
- Karpathy LLM-KB tweet (cited in DECISIONS.md 2026-04-11 "Karpathy-style markdown memory + SQLite index hybrid"): *"the LLM has been pretty good about auto-maintaining index files and brief summaries... it reads all the important related data fairly easily at this small scale."* — pre-digesting skill level violates this principle.
- PRD.md § Phase 1 Acceptance Criteria (zero mentions of skill-level adaptation)

---

## 2026-04-12 (evening): Ctrl+Alt+Space replaces Ctrl+Shift+Space — Excel/Sheets Select-All conflict + Windows launcher industry research

**Context:** Earlier today (2026-04-12 morning), we pivoted from Alt+Space (globally destructive with `pynput.Listener(suppress=True)`) to **Ctrl+Shift+Space** with `suppress=False` (observe-only), documented in the `## 2026-04-12: Ctrl+Shift+Space over Alt+Space — pynput suppress=True is globally destructive` entry above. That pivot solved the global-keyboard-blackout bug but introduced a new one: **Microsoft Excel and Google Sheets both bind Ctrl+Shift+Space to "Select entire worksheet"**, equivalent to Ctrl+A's second-press behavior. Because our listener is observe-only, the spreadsheet underneath ALSO receives the keypress every time the user holds Ctrl+Shift+Space to invoke Clicky — which means every Clicky question in Excel wipes the user's cell selection. Excel is the #1 demo example in `PRD.md` ("learning Excel") so this is a showstopper for Phase 1 validation.

User caught this during review on 2026-04-12 evening. Rather than assume the fix, we did research-backed evaluation of alternatives via three WebSearch passes (see "Research" section below).

**Decision:** Phase 1 hotkey is **Ctrl+Alt+Space** via `pynput.keyboard.Listener(suppress=False)`. 3-flag state machine (`_ctrl_down`, `_alt_down`, `_space_down`), RECORDING requires all 3 held, any release of any of the 3 fires `on_release`. `_is_alt()` helper normalizes `Key.alt`, `Key.alt_l`, `Key.alt_r`, `Key.alt_gr` (international AltGr keyboards included). Same `suppress=False` observe-only model as the morning pivot — the load-bearing property is that the combo has no default Windows OS behavior so the underlying apps can safely see the keypress without any user-visible side effect.

**Alternatives considered (research-backed, not speculated):**

1. **Fn+Space** — rejected. AutoHotkey community, pynput docs, and Microsoft `SetWindowsHookExA` docs all confirm: *"the Fn key does not (as a general rule) generate any scan code that can be used by AHK, as the key is intercepted and interpreted directly by the PC's BIOS."* The Fn key is handled by the keyboard Embedded Controller BELOW the OS layer; `WH_KEYBOARD_LL` does not see it. On many laptops, Fn+Space produces a hardware action (brightness / backlight / airplane mode) instead of a Space event. Even when it happens to work on a specific laptop model, it's OEM-specific and non-portable. Desktop keyboards don't have Fn at all. Hard no.

2. **Pause/Break single key** — rejected. One-finger, zero conflicts, but missing from many modern laptop keyboards (compact / MacBook-style layouts). Non-portable for the Phase 2 audience even if Abhishek's specific Phase 1 laptop has it.

3. **Win+Alt+Space** — Microsoft PowerToys Command Palette 0.93 uses this specifically to dodge the Alt+Space battleground. Microsoft-endorsed, zero conflicts, but three-finger and requires holding the Win key which is awkward for extended PTT usage. Ergonomically worse than Ctrl+Alt+Space (all-left-hand) for no real gain.

4. **Alt+Space with Win32 `RegisterHotKey` + manual Windows-settings-disable** — the industry-standard 2-finger combo (Raycast, Flow Launcher, PowerToys Run, Launchy all use it). Pros: best ergonomics, aligns with user muscle memory from other launchers. Cons: requires 8-12h of fragile ctypes code (`RegisterHotKey` + `GetAsyncKeyState` polling for release detection + AutoHotkey-style masking-Ctrl tricks), AND requires users to manually disable the Windows window menu + Copilot bindings via `Settings > Hotkeys`. Pulls Phase 1.5 work forward into Phase 1. **Deferred to Phase 1.5 as a drop-in `RegisterHotKeyPushToTalk(PushToTalkHotkey)` subclass.** The abstract interface makes the Phase 1 → Phase 1.5 swap a trivial factory change without touching `app.py`.

5. **Tilde (\`) single key** — rejected. Discord community recommendation for push-to-talk, one-finger universal, but conflicts with terminal shell command-substitution syntax which is Abhishek's primary developer workflow.

6. **Ctrl+Shift+Space** (status quo from morning) — rejected. Excel/Sheets conflict confirmed empirically.

7. **Ctrl+Alt+Space** (chosen) — 10-minute pivot from Ctrl+Shift+Space (just swap `_shift_down` → `_alt_down` + `_is_shift` → `_is_alt`), zero known conflicts (verified against Excel, Sheets, Windows window menu, Copilot, VS Code), three-finger but all on the left side for one-handed holding, reuses the existing `suppress=False` observe-only model unchanged. VS Code binds Ctrl+Shift+Space to "Trigger Parameter Hints" — that was a minor conflict with the previous pivot but is NOT a conflict with Ctrl+Alt+Space, which has no VS Code binding.

**Research performed before this decision (WebSearch, 2026-04-12 evening):**

- *"pynput detect Fn key Windows Python Listener"* — confirmed pynput cannot see Fn; returns `None` or is unrecognized. pynput is built on `SetWindowsHookEx` + `WH_KEYBOARD_LL` which doesn't see Fn natively.
- *"Windows 'Fn key' SetWindowsHookEx WH_KEYBOARD_LL detectable scan code"* — confirmed the low-level hook doesn't have reliable scan code info for Fn; firmware-handled.
- *"'Fn+Space' Windows hotkey register AutoHotkey detect"* — authoritative AutoHotkey community answers confirming Fn is BIOS-intercepted and most key combos involving Fn produce either a hardware action or no event at all.
- *"Windows popular desktop apps global hotkey defaults PowerToys Flow Launcher Raycast alternatives 2026"* — mapped Raycast / Flow Launcher / PowerToys Run / PowerToys Command Palette / Launchy defaults. All use Alt+Space except PowerToys Command Palette (Microsoft's newest, which uses Win+Alt+Space specifically to dodge the Alt+Space battleground).
- *"Windows app launcher 'default hotkey' 'Alt+Space' vs 'Ctrl+Space' conflict"* — confirmed Alt+Space fights with Windows window menu + Copilot reassignment (late 2024); most apps require users to manually disable these via Settings > Hotkeys before Alt+Space works reliably.
- *"Discord 'push to talk' default hotkey Windows global"* — Discord ships no default; users pick their own; common recommendations are tilde or mouse side buttons.

**Why this won:** The research-backed option matrix landed on exactly two defensible Phase 1 picks — Ctrl+Alt+Space or Win+Alt+Space. Ctrl+Alt+Space is slightly more ergonomic (all-left-hand) with no loss of authority support (neither combo is an established industry standard; Win+Alt+Space is Microsoft-endorsed for launchers but we're not a launcher). User picked Ctrl+Alt+Space via AskUserQuestion on 2026-04-12 evening, with explicit "Phase 1.5 still delivers Alt+Space via Win32 RegisterHotKey subclass" lineage preserved from the earlier entry.

**Consequences:**

- `hotkey.py` rewritten: `_shift_down` → `_alt_down`, `_is_shift()` → `_is_alt()` (with `Key.alt_gr` support for international keyboards), module docstring fully rewritten to explain the Ctrl+Alt+Space rationale + Fn+Space research-backed rejection + reference this entry.
- `tests/test_hotkey.py` rewritten: 11 tests (was 10) — added `test_alt_gr_is_treated_as_alt` as a bonus for international keyboard layouts. All state-machine tests renamed from `test_ctrl_shift_*` to `test_ctrl_alt_*`. `test_start_creates_listener_with_suppress_false` assertion message updated to cite this entry.
- `config.py` `HOTKEY` default: `"ctrl+shift+space"` → `"ctrl+alt+space"`. Docstring fully rewritten with the three-level pivot history (Alt+Space → Ctrl+Shift+Space → Ctrl+Alt+Space) and the research-backed Fn+Space rejection.
- `.env.example` HOTKEY comment updated.
- `CLAUDE.md`, `PRD.md`, `ROADMAP.md` — all "Ctrl+Shift+Space" references replaced with "Ctrl+Alt+Space" via replace_all.
- `project_phase1_current_state.md` memory file — hotkey subsection updated to reflect Ctrl+Alt+Space as the shipped Phase 1 hotkey. The earlier Ctrl+Shift+Space decision entry in that file is explicitly marked superseded by this one for Phase 1.
- The earlier `## 2026-04-12: Ctrl+Shift+Space over Alt+Space` entry is **SUPERSEDED for Phase 1** by this entry, but the reasoning about `suppress=True` being globally destructive remains valid — just that Ctrl+Shift+Space was the wrong fallback choice. The proper Phase 1.5 solution remains Win32 `RegisterHotKey` + manual disable of Windows window menu + Copilot bindings, restoring Alt+Space.
- PR #16 (configurable hotkey UI) becomes even more valuable as a Phase 2 item now that we have three hotkey history points to show in the writeup: the Alt+Space attempt that failed globally, the Ctrl+Shift+Space attempt that failed in Excel, and the Ctrl+Alt+Space pragmatic ship.
- Phase 1.5 writeup angle: we literally ran into the Alt+Space battleground the same way Flow Launcher, Launchy, and PowerToys Run did, documented it, and shipped Ctrl+Alt+Space while building toward the "proper" `RegisterHotKey` + manual-Windows-settings-disable solution. Genuine build-in-public material for the B0 case study.

**Known conflict (surfaced 2026-04-12 evening during the hotkey manual gate):**

- **Claude Desktop for Windows binds Ctrl+Alt+Space to its "What can I help you with today?" quick-access prompt.** Discovered when the user ran `py -3.13 -m hotkey` with Claude Desktop installed — pynput PRESSED/RELEASED fired correctly (7 clean cycles), AND Claude Desktop's prompt overlay appeared at the same time. Our `suppress=False` listener is observe-only so both apps receive the keypress. **Phase 1 mitigation:** require users to disable Claude Desktop's global hotkey in its settings (`Claude Desktop Settings > Keyboard Shortcuts > Ctrl+Alt+Space = None` or reassigned). This is the same setup pattern Raycast + Flow Launcher + PowerToys Run require their users to follow for the Alt+Space / Windows menu / Copilot conflicts. Phase 1 has ONE tester (Abhishek) so a one-time Settings tweak is acceptable. Phase 2's configurable-hotkey UI (PR #16) lets users rebind without touching either app. Phase 1.5's Win32 `RegisterHotKey` subclass (deferred) would suppress the combo globally at the OS level, eliminating the conflict entirely.
- **No other conflicts verified.** Ctrl+Alt+Space tests clean against Excel, Sheets, VS Code, Notepad, Windows window menu, and Copilot. Manual gate (`py -3.13 -m hotkey`) includes explicit Excel + Notepad verification as item #4 of the 6-item checklist.

**References:**

- User quote, 2026-04-12 evening: *"When i press ctrl shift space in excel it selects all?"* (empirical catch)
- User quote, 2026-04-12 evening: *"do web search and actually see if it is possible instead of assuming"* (forced research-backed evaluation of Fn+Space instead of speculation)
- User quote, 2026-04-12 evening: *"do web search and find what are the common and easy to use hotkeys that other custom desktop apps use?"* (forced industry scan)
- [AutoHotkey — Using fn key as a modifier](https://www.autohotkey.com/boards/viewtopic.php?t=26471)
- [AutoHotkey — How to activate "FN" key via AHK?](https://www.autohotkey.com/boards/viewtopic.php?t=82163)
- [pynput docs — keyboard handling](https://pynput.readthedocs.io/en/latest/keyboard.html)
- [Microsoft Learn — SetWindowsHookExA](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowshookexa)
- [PowerToys Issue #13860 — PowerToys Run hijacks Alt+Space](https://github.com/microsoft/PowerToys/issues/13860)
- [Microsoft gives Alt+Space to Copilot — Hacker News](https://news.ycombinator.com/item?id=42407426)
- [Flow Launcher Issue #2622 — Failed to register Alt+Space](https://github.com/Flow-Launcher/Flow.Launcher/issues/2622)
- [Raycast for Windows](https://www.raycast.com/windows)
- [PowerToys Command Palette 0.93](https://windowsforum.com/threads/powertoys-command-palette-0-93-fast-sleek-windows-launcher-vs-flow-raycast.378624/)
- Previous decision 2026-04-12 "Ctrl+Shift+Space over Alt+Space" (superseded for Phase 1 by this entry; reasoning about suppress=True still valid)

---

## 2026-04-12 (evening 3): ai.py refactor — adopt Clicky's actual vision-tag shipping pattern (supersedes 2026-04-11 "Use Computer Use API beta directly" + "Mirror ElementLocationDetector.swift exactly")

**Context.** The 2026-04-11 entries locked us to Clicky's `ElementLocationDetector.swift` (Computer Use API beta, `computer_20251124` tool, `anthropic-beta: computer-use-2025-11-24` header, `max_tokens=256`, no system prompt). Our `ai.py` Step 2 implemented this verbatim. Live-API gate passed — pixel-accurate `(263, 779)` within 5 pixels of ground truth on the 2880×1800 @ 200% DPI test machine. Functionally correct. But during Step 7 brainstorming, user pushback forced a line-by-line read of Clicky's Swift source via `gh api`.

**Research pass finding (verified, not inherited).** `ElementLocationDetector.swift` is **dead code** — zero references across all 11 non-test Swift files (verified by grep-ing every Clicky Swift file for `ElementLocationDetector`). Clicky's actual shipping path is `ClaudeAPI.analyzeImageStreaming` called from `CompanionManager.sendTranscriptToClaudeWithScreenshot` (lines 590-720 of CompanionManager.swift): plain vision streaming, `max_tokens: 1024`, 35-line system prompt (`companionVoiceResponseSystemPrompt`, lines 544-581) instructing Claude to embed `[POINT:x,y:label(:screenN)?]` at the end of its streamed text response. Coordinates parsed via `parsePointingCoordinates` regex (line 784). NO Computer Use tool, NO beta header. The 2026-04-11 "Computer Use is more accurate" claim came from a comment block INSIDE the dead `ElementLocationDetector.swift` file asserting Computer Use's specialized pixel-counting training is superior — that comment describes code Clicky never shipped in production. The 2026-04-11 decision inherited the claim as ground truth without verifying.

**Decision.** Refactor `ai.py` to match Clicky's actual shipping pattern. Specifically:
1. Remove `tools=[computer_20251124]` + `extra_headers={"anthropic-beta": "computer-use-2025-11-24"}`. Use plain `self.client.messages.stream(...)` (GA, not beta).
2. Add `_CLICKY_SYSTEM_PROMPT` constant — port Clicky's 35-line `companionVoiceResponseSystemPrompt` verbatim, adapt only closing references ("Control+Option" → "Ctrl+Alt+Space", "Clicky" → "Clicky Windows").
3. Add `_POINT_TAG_RE` regex + `parse_point_tag()` helper + `PointParseResult` dataclass — Python port of Clicky's `parsePointingCoordinates`.
4. Add `_StreamingAnthropicResponse` context manager + `AnthropicClient.ask_stream(labeled_images, transcript, history, system_prompt=_CLICKY_SYSTEM_PROMPT, max_tokens=1024)` returning it. Exposes `text_deltas()` iterator + `final_result() -> PointParseResult`.
5. Retain `ask()` as a thin batch wrapper (internally calls `ask_stream`, consumes the whole stream, returns same `{"text", "points"}` dict) for backwards compat with `__main__` gate.
6. Bump `_CLICKY_MAX_TOKENS` 256 → 1024 as a parameter default.
7. Delete dead helpers: `parse_tool_use_coordinates`, `build_user_prompt`.
8. Delete `config.py` constants `COMPUTER_USE_BETA` + `COMPUTER_USE_TOOL_TYPE`.
9. Bundle `capture.py` multi-screen extension (`LabeledCapture` dataclass + `capture_all_screens()` returning `list[LabeledCapture]` sorted cursor-screen-first). Locks `ai.py`'s final API signature now instead of a Phase 2 breaking change. Our single-monitor test machine exercises `len == 1` in practice.
10. Bundle `overlay.py` ball → real cursor polygon upgrade. Replace `drawEllipse` with `drawPolygon(QPolygonF([...]))` using a classic Windows arrow shape (~8 vertices). Dodger-blue fill, 2px white stroke. **Tip-anchored** at `pointer_pos` — semantically correct for "point at (x, y)" (the ball's center-anchoring was 20px offset from the actual target).

**Alternatives considered:**
- **Option A (keep Computer Use, add streaming + system prompt + bump max_tokens):** ~2-3h smaller surgery. Rejected — retains an unvalidated accuracy claim (Clicky never shipped Computer Use), retains beta-header risk (header already shifted once `2025-01-24` → `2025-11-24`), and Phase 2 multi-provider subclasses (OpenRouter→non-Anthropic, Gemini, local models) need a vision-tag fallback code path anyway since non-Anthropic providers strip beta headers. Keeping Computer Use means maintaining TWO code paths in Phase 2.
- **Option B (refactor to Clicky's vision-tag pattern) — CHOSEN.** ~9-12h including Docs-Comprehensive PRD rewrite + cursor upgrade + multi-screen capture.
- **Option C (hybrid two parallel calls — plain vision for speech + Computer Use batch for coordinates):** ~6-10h. ~2x API cost. Most complex orchestration. Overkill for Phase 1's ONE tester.

**Why Option B won.** (1) Empirical validation — 3500-star Clicky ships this exact pattern in production. (2) Conversational quality matches Farza's demo cadence ("Since you're shooting on Black Magic RAW...") because of 1024 tokens + persona system prompt + plain vision training, which Computer Use's coordinate-focused training bias fights. (3) Sentence-level TTS chunking over the progressive stream becomes a legitimate improvement over Clicky's `onTextChunk: { _ in }` empty callback (they stream for network efficiency but discard progressive value). (4) Phase 2 OpenRouter / Gemini / local-model subclasses drop in cleanly without fallback paths. (5) Plain `messages.stream()` is GA — Computer Use is beta. (6) Cheaper per interaction (no Computer Use tool-definition overhead in the prompt). (7) User explicit decision: *"looks like vision tags is the way then since he has already played and experimented with it."*

**OpenRouter correction (was unverified caveat, now WebSearch-verified).** DECISIONS.md 2026-04-11 claim *"OpenRouter can't proxy Computer Use beta features"* is **partially wrong**. OpenRouter → Anthropic passes through native tool use + beta headers (per [OpenRouter Anthropic docs](https://openrouter.ai/anthropic)). OpenRouter → non-Anthropic (Gemini, GPT-5, local) strips beta headers. So if we'd kept Computer Use, Phase 2 OpenRouter→Anthropic would still work; only OpenRouter→other-provider would need fallback. The *forcing* reasons for Option B remain (1)-(6) above; (4) is weaker than originally stated but still relevant.

**Consequences.** Docs-first execution order (to survive `/compact` mid-refactor):
- **Phase D (docs FIRST, this session before compact):** DECISIONS.md this entry, CLAUDE.md updates, ROADMAP.md Step 2 footnote, memory files (2 research-discipline lessons + `project_phase1_current_state.md` snapshot update + `MEMORY.md` index), PRD.md comprehensive rewrite (Core Loop update + new Codebase Architecture + User Journeys + Invariants sections).
- **Phase C (code, next session after compact + rehydration):** `capture.py` multi-screen, `ai.py` vision-tag refactor, `overlay.py` ball→cursor polygon, `config.py` Computer Use constants deletion.
- **Phase T (tests, bundled with C):** `test_ai.py` full rewrite (~20-23 tests), `test_capture.py` extend (~25-27), `test_overlay.py` extend (~16). Full suite target ~98-105.
- **Phase V (verify, pre-commit):** Boris #5 self-critique + `superpowers:code-reviewer` + manual live-API A/B gate on `debug_capture.jpg` (old Computer Use path vs new vision-tag path, document quality delta) + manual overlay gate for the new cursor shape.
- **Phase S (ship):** 3-commit batch (docs, code refactor, cursor upgrade) + push to origin/main after user explicit OK.

**Superseded entries (marked for future-Claude, retained per append-only rule):**
- 2026-04-11 "Use Claude Computer Use API beta directly, not vision-tag regex fallback" — **SUPERSEDED-FOR-PHASE-1** by this entry. Accuracy claim was inherited from dead-code comments, never validated in Clicky production.
- 2026-04-11 "Aspect-ratio-aware resolution picking from [(1024,768),(1280,800),(1366,768)]" — **STILL VALID for the resolution-picking logic**, but the "Mirror Clicky's `ElementLocationDetector.swift`" framing is wrong (dead code). Aspect-ratio matching in Clicky actually lives in `CompanionScreenCaptureUtility.swift`, the capture layer. Our `capture.pick_resolution` logic is correct; only the attribution was wrong.

**Research-discipline lessons (promoted to memory files this session):**
1. **Reference-source read discipline** (`feedback_reference_source_read_discipline.md`, new this session): For any project porting code from a reference repo, read every non-trivial source file in the reference LINE-BY-LINE before drafting any component's design. "100% context" means source reads, not doc reads. Doc-level claims about the reference can be inherited assumptions that don't match the actual source.
2. **Verification-not-caveating discipline** (appended to `feedback_brutally_honest_mode.md` Verification section): Never use *"note the caveat while still presenting the core finding"* as an escape from verifying a claim via WebSearch / `gh api` / installed source grep. Verification takes seconds; caveats rot and mislead future-Claude. Concrete failure this session: OpenRouter+Computer Use compatibility was treated as "unverified caveat" for multiple turns when a single WebSearch call would have given the verified answer (which turned out to be the opposite of the inherited claim).

**References:**
- Clicky source: `leanring-buddy/CompanionManager.swift` lines 544-720 (system prompt + orchestrator + `parsePointingCoordinates`)
- Clicky source: `leanring-buddy/ClaudeAPI.swift` lines 95-200 (`analyzeImageStreaming`)
- Clicky source: `leanring-buddy/ElementLocationDetector.swift` (dead code, 0 refs grep-verified across all 11 non-test Swift files)
- WebSearch [Anthropic Computer Use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool), [OpenRouter Anthropic provider](https://openrouter.ai/anthropic), [OpenRouter Claude Code integration](https://openrouter.ai/docs/guides/coding-agents/claude-code-integration)
- Plan file: `C:\Users\Abhis\.claude\plans\streamed-tumbling-sunbeam.md` (full task breakdown + Phase 2 readiness audit + cross-file impact audit)
- User quotes 2026-04-12 (evening): *"did you even check the actual Clicky GitHub repo?"* / *"looks like vision tags is the way then since he has already played and experimented with it"* / *"the PRD should contain the entire codebase architecture and userflow/journey and have ZERO ambiguity"*

---

## 2026-04-13: Step 7 app.py architecture decisions (bundled from manual testing session)

**Context.** Step 7 app.py built and manually tested across Excel, Clipchamp, Granta EduPack, Photoshop Online, Pixlr, Fusion360. Multiple architecture decisions made based on debug log data, not assumptions.

**Decisions (all verified by debug logs at `~/.clicky-windows/debug/`):**

1. **STT pre-open mic + WebSocket at startup.** Debug logs showed `sounddevice.RawInputStream` creation takes 360-1065ms and WebSocket connect takes 800-1200ms. Total 1.2-2.3s of dead air per press. Fix: `connect()` at startup, `start_recording()`/`stop_recording()` on press/release. Result: 0-1ms on press.

2. **AssemblyAI `format_turns=False`.** Debug logs showed ForceEndpoint returns partial transcript ("So—") while full transcript arrives 1-2s later. AssemblyAI docs confirm: "Avoid using format_turns as it will significantly increase latency." Switched to False. Result: full transcript arrives within 300ms of ForceEndpoint.

3. **TTS three-pronged instant kill.** `tts.stop()` sets cancel event + calls `audio_stream.abort()` (Pa_AbortStream, immediate) + calls `response.close()` (interrupts HTTP iter_bytes). Verified via sounddevice source: `stop()` waits for buffer drain, `abort()` stops immediately.

4. **OpenRouter support via env var.** Anthropic SDK reads `ANTHROPIC_BASE_URL` from environment (SDK line 100-101). Set `ANTHROPIC_BASE_URL=https://openrouter.ai/api` in `.env`. Zero code changes. OpenRouter adds 25-40ms overhead (verified via web search, not assumption).

5. **Haiku 4.5 NOT faster than Sonnet 4.6 for vision.** Tested via OpenRouter. Haiku took 4.7s (same as Sonnet). Haiku also generates more verbose responses, ignoring "one or two sentences" system prompt rule. Switched back to Sonnet 4.6.

6. **Memory recall reduced 3000→1500 chars.** Debug logs showed verbose responses correlated with large memory context. Clicky macOS sends ZERO persistent memory. Reducing to 1500 chars = last 5-6 interactions. Memory injection instruction changed to "use silently, don't summarize or reference it."

7. **System prompt reverted to Clicky's verbatim.** Added "don't narrate" rule conflicted with "reference specific things you see." Clicky's proven prompt works as-is — the verbosity was caused by memory context, not the prompt.

8. **Cartesia Sonic-3 over ElevenLabs.** Benchmarks: Cartesia 40ms TTFA vs ElevenLabs 75ms. Cartesia is faster. Voice: switched from "Brooke - Big Sister" to "Katie - Friendly Fixer" (Cartesia-recommended for voice agents).

9. **Phase 2 differentiator: multi-step guided workflows.** Timer-based auto-advance where Clicky watches the screen and gives next-step instructions without hotkey press. Nobody does this. Validated by danpeg/clicky proactive fork (79 stars in 3 days). Deferred to Phase 2.

**References:** Debug logs at `~/.clicky-windows/debug/`, AssemblyAI docs, sounddevice source, OpenRouter docs, Cartesia benchmarks, Clicky GitHub issues #26/#30/#38.

---

## 2026-04-19: Gemini 3 Flash Preview via OpenRouter — dual-SDK routing for BYOK model-agnosticism

**Context.** Step 7 orchestrator shipped 2026-04-13 in commit `8b3710c`. Manual testing + debug logs show Claude Sonnet 4.6 vision inference is 5-9s = 85-90% of total PTT latency (e.g. session 03:24:32: stop_recording=301ms, capture=228ms, Claude=8035ms, TTS=instant). Target was sub-2s end-to-end. Aaron (senior engineer, met at SUTD InspireCon 2026-04-18) explicit feedback: *"Gemini Flash is actually good enough."* He validated OpenRouter as the BYOK abstraction: users shouldn't be forced onto one provider (Clicky macOS is locked into ElevenLabs → top-3 upstream complaint per issues #22/#27/#32/#33).

**Decision.** Swap LLM from Claude Sonnet 4.6 → Gemini 3 Flash Preview via OpenRouter. Keep OpenRouter as the dual-SDK BYOK routing layer:
- `anthropic/claude-sonnet-4-6` → `AnthropicClient` (anthropic SDK, OpenRouter Anthropic-compat endpoint via `ANTHROPIC_BASE_URL`)
- `google/gemini-3-flash-preview` → `GeminiClient` (openai SDK, OpenRouter OpenAI-compat endpoint `https://openrouter.ai/api/v1`)

Router is `ai.create_ai_client(model_id, api_key)` — prefix-based dispatch. app.py reads `MODEL_ID` from .env, factory routes. Zero app.py threading change, zero STT/TTS change, zero overlay change.

**Alternatives considered:**
1. **Gemini Live API (WebSocket speech-to-speech).** ✓ Fastest claimed (~960ms voice-to-voice). ✗ Google-only, not proxiable through OpenRouter, violates BYOK — hard no.
2. **Stay on Claude, do Path A parallelism first.** ✓ No new dep. ✗ Complex orchestration (capture-at-press, prefix caching, speculative inference). Saves ~2-3s at best. Gemini swap alone saves ~3-5s at half the engineering cost.
3. **Grok / Cerebras.** ✗ Aaron mentioned Cerebras for fast inference but vision support is limited per Grok research. Not suitable for `[POINT:x,y]` coordinate extraction on screenshots.
4. **Direct Google Gemini SDK.** ✓ Native, no proxy. ✗ Locks us into Google, breaks the OpenRouter abstraction, requires re-work if we add more providers.

**Why this won:**
- Gemini 3 Flash Preview shows +57 points on UI navigation benchmarks vs Gemini 2.5 Pro (the critical metric for our `[POINT:x,y]` coordinate extraction).
- Vision TTFT: ~0.5-0.6s vs Claude Sonnet 4.6's ~1.0-1.8s. **50%+ reduction on the dominant pipeline stage.**
- Same price as Google direct — OpenRouter adds no markup on Google models.
- Preserves model-agnostic BYOK differentiator. Future: `grok/grok-4-fast`, `meta-llama/...`, etc. as subclass drops.
- Aaron's explicit validation at InspireCon 2026-04-18.

**Consequences:**
- New dependency: `openai>=1.60.0` (for OpenAI-compat endpoint via OpenRouter).
- New test classes `TestGeminiClient` (6 tests) + `TestCreateAIClient` (5 tests) + `test_default_ai_client_comes_from_factory` (1 test). 130/130 total tests green.
- `ai.py` grows ~150 LOC. Architecture still clean: abstract base + two concrete subclasses + factory.
- Users can swap MODEL_ID in .env and pick any OpenRouter model with the `google/` or `anthropic/` prefix. Zero code change.
- Phase 2 providers (Grok, Gemini, Llama, OpenAI) become subclass drops — the dual-SDK pattern generalizes.
- Step 2 "Path A" parallelism is a separate, additive win — tracked in ROADMAP.md Phase 1.5.

**References:**
- Aaron InspireCon transcript: `C:\Users\Abhis\Downloads\Aaron InspireCon Clicky feedback.txt` (00:00:01-00:02:20)
- Research: "Gemini 3 Flash vs Claude Sonnet 4.6 vision TTFT" (2026-04-19 Agent research pass)
- OpenRouter docs: https://openrouter.ai/google/gemini-3-flash-preview
- Debug logs: `~/.clicky-windows/debug/2026-04-13_*` show Claude 5-9s inference

---

## 2026-04-19 (evening): Gemini 2.5/3 Flash rejected as default — measurement-driven reversal

**Context.** Earlier 2026-04-19 decision above shipped GeminiClient + factory + dual-SDK routing — landed in 8 commits 2026-04-19 (`02196e7` → `3988a51`), 138/138 tests green. Set `.env` MODEL_ID to `google/gemini-3-flash-preview` for manual verification. Then tested against both Gemini models on fresh + stale screenshots, measured real latency from Step 7 orchestrator debug logs.

**Empirical results (measured, not estimated):**

| Metric | Claude Sonnet 4.6 via OpenRouter | Gemini 3 Flash Preview | Gemini 2.5 Flash |
|---|---|---|---|
| Total pipeline latency (debug log) | 5-9s baseline | N/A — tested via ai.py only | 4.7s (measured on "how do I make my repo public" 06:08:32 session) |
| Gemini/Claude streaming stage | 4-8s | ~3s | **3.8s** (debug log 2026-04-19_06-08-32) |
| Actual pipeline savings vs Claude | — | — | **~1.0-1.5s** (NOT the 50% research predicted) |
| Coordinate accuracy (pixel-precision for UI pointing) | ±5px Step 1 verified | **Broken — returns [0,1000] normalized, not pixel space** | **±~200px miss** on Settings tab test |
| Coordinate return rate | ~100% | — | ~67% (returned None on 1 of 3 test prompts) |

**Ground-truth coordinate miss, Gemini 2.5 Flash (from debug log 2026-04-19_06-08-32 `screenshot_with_marker.jpg`):**
- User asked: "how do I make my repo public" on GitHub
- Gemini said: "see that 'settings' tab up near the top right, between 'insights' and 'security and quality'? click on that"
- Gemini coordinate: `(721, 215)` labeled "settings tab"
- Ground truth: Settings tab with gear icon at ~`(950, 138)` in the 1280×800 image
- **Miss: ~230px horizontal, ~80px vertical.** Marker landed in empty space between Issues and Pull Requests tabs, nowhere near Settings. Completely unusable for precision pointing.

**Gemini 3 Flash Preview (from ai.py live gate):**
- Returned `(236, 971)` on a 1280×800 image — y=971 is 171 pixels out of bounds.
- Math check: `236/1000 * 1280 = 302`, `971/1000 * 800 = 777` — (302, 777) would land on start button area. **Gemini 3 is returning coords in Gemini's native [0, 1000] normalized space** regardless of our "use pixel dimensions as coordinate space" instruction. Known Gemini behavior. Would need post-processing (detect-and-normalize, or different prompt).

**Decision.** **Revert .env default to `anthropic/claude-sonnet-4-6`.** Keep GeminiClient + factory + all 138 tests — the code stays as opt-in alternative (commented in `.env` with measurement notes). Phase 2 settings UI will let users opt in if they prioritize latency over precision.

**Why the earlier decision (morning 2026-04-19) was wrong:**
1. **Relied on third-party benchmarks for TTFT** — the research cited Gemini 2.5 Flash at 0.5-0.6s vision TTFT. Real measurement via OpenRouter: ~2.8s TTFT. OpenRouter overhead + upstream routing eats half of Gemini's theoretical advantage.
2. **Trusted the +57 UI benchmark without empirical testing** — the +57 point UI navigation benchmark Google published is for GUI Agent-style tasks (probably bounding-box detection), NOT pixel-precise pointing via vision-tag regex. We tested our actual workload and Gemini misses by 200px routinely.
3. **Didn't test coordinate-space behavior before committing** — should have run `py -3.13 -m ai` as a gate BEFORE writing the plan. Would have caught Gemini 3's [0,1000] space issue immediately and Gemini 2.5's pointing imprecision on the second run.

**Alternatives NOW considered (since Gemini is rejected):**
1. **Stay on Claude, do Path A parallelism next (chosen for Step 2).** Capture at hotkey PRESS, prefix caching, fix STT cutoff, fix TTS feedback. Real user-visible latency wins WITHOUT sacrificing precision. Tracked in ROADMAP.md Phase 1.5 Step 2.
2. **Try Gemini 3 Flash with normalized-coord detection.** Add logic to GeminiClient: if max(x, y) < 1000 but either > image bound, rescale. Half-day experiment if we revisit later.
3. **Prompt engineering for Gemini pixel precision.** Add explicit examples showing pixel coords not normalized. Unclear whether Gemini's [0,1000] behavior can be overridden by prompting.
4. **Haiku 4.5 for speed.** Earlier tested (per 2026-04-13 DECISIONS entry decision 5): NOT faster than Sonnet for vision, also more verbose.

**Consequences:**
- Latency stays at 5-9s for Phase 1.5 Step 1 — the swap didn't deliver.
- All GeminiClient + factory infrastructure stays in the repo. NO code rollback. 138/138 tests still green. Nothing wasted from the Step 1 sprint — the abstraction is still correct and needed for future provider swaps (Grok, Gemini when they improve, Llama, etc.).
- Phase 1.5 Step 2 (Path A parallelism) becomes THE primary latency vector. Expected wins there are user-visible (capture-at-press 200-400ms, STT cutoff fix eliminates truncation rework, TTS-to-mic feedback elimination eliminates re-prompts).
- Future providers now plug in as subclass drops via `create_ai_client()` — so this sprint's code is still load-bearing even with Gemini not as default.
- Lesson logged: **test the ACTUAL workload (debug_capture.jpg + pointing prompts) before shipping model swaps.** Published benchmarks lie by omission — they don't test our exact task.

**References:**
- Debug logs: `~/.clicky-windows/debug/2026-04-19_06-08-32_chrome.exe/` — real PTT interaction with Gemini 2.5 Flash, shows coordinate miss
- ai.py live gate runs (2026-04-19 evening): Gemini 3 Flash Preview (236, 971) OOB; Gemini 2.5 Flash (183, 767) in-bounds once, (501, 811) OOB once, none-returned once; Claude Sonnet 4.6 (301, 731) clean
- Earlier morning entry (above) — stands as the architectural decision (dual-SDK routing), this entry supersedes only the default-MODEL_ID choice

---

## 2026-04-19 (late evening): Head-to-head Claude vs Gemini on identical PTT workload — final latency data

**Context.** Earlier 2026-04-19 evening entry reverted default from Gemini back to Claude based on coordinate-accuracy testing via `py -3.13 -m ai`. User wanted second data point: run the EXACT same PTT question ("how do I make my repo public") through the full orchestrator (`py -3.13 -m app`) with BOTH models on the SAME GitHub page, compare full-pipeline debug logs.

**Measurement (both runs are real PTT interactions on the same Chrome tab on the same GitHub repo, same user utterance):**

| Stage | Gemini 2.5 Flash (session 2026-04-19_06-08-32) | Claude Sonnet 4.6 (session 2026-04-19_06-18-30) |
|---|---|---|
| STT stop_recording finalization | 302ms | 376ms |
| Screen capture (hide overlay + mss.grab + resize) | 503ms | 238ms |
| Memory recall | 2ms | 2ms |
| **LLM streaming stage** | **3768ms** | **3655ms** |
| TTS dispatch | ~15ms | ~15ms |
| **Total pipeline** | **4669ms** | **4325ms (340ms FASTER)** |
| Coordinate returned | `(721, 215)` labeled "settings tab" | `(934, 184)` labeled "settings tab" |
| Coordinate accuracy (ground truth: Settings gear icon at ~(950, 138) in 1280×800 image) | **~230px horizontal miss, marker in empty space between Issues and Pull Requests tabs** | **Bullseye — marker directly on the Settings gear icon** |
| Response length | 242 chars | 123 chars |

**Decision.** Phase 1.5 Step 1 is CLOSED. Claude Sonnet 4.6 is the default. Gemini infrastructure (GeminiClient + factory + dual-SDK routing) stays — it was the right architectural bet even if this specific model is the wrong default. Gemini 2.5 Flash is available as opt-in via `MODEL_ID=google/gemini-2.5-flash` in `.env` for users who prioritize latency over precision.

**Why this is final (not re-opened):**
- Real-world OpenRouter latency variance (±400ms per run across network + model load + queue) completely swamps Gemini's theoretical TTFT advantage. Claude was 340ms FASTER on this single A/B — that's noise-level.
- Coordinate precision gap is NOT noise. Gemini's 230px miss on the Settings tab is a categorical failure for a pointing-at-UI-elements product.
- Option 3 (Gemini-specific prompt engineering with bounds rules + anti-normalized instruction + few-shot bottom-of-screen example) was planned but skipped — if Gemini has no latency edge, no prompt fix on accuracy is worth the effort vs Path A parallelism.

**Consequences:**
- `.env` MODEL_ID stays `anthropic/claude-sonnet-4-6`
- 8 commits (`02196e7`..`3988a51`) pushed to origin/main 2026-04-19 late evening — GeminiClient + factory + dual-SDK routing + 138/138 tests + full docs trail. Infrastructure is load-bearing for future provider drops (Grok, Llama, Gemini-when-fixed). NOTE: SHAs reflect post-rewrite history (history was filter-branch'd 2026-04-19 to strip `Co-Authored-By: Claude` trailers — see global `~/.claude/CLAUDE.md` git rule).
- Phase 1.5 Step 2 (Path A parallelism) becomes THE primary latency vector, with Claude preserved (no precision tax). Target: 5-9s → ~2s via capture-at-press, prefix caching, STT cutoff fix, TTS-to-mic feedback fix, memory reduction.
- Lesson logged: **head-to-head A/B on identical real workload beats isolated live-gate runs.** The ai.py-only tests showed Gemini might work in ideal conditions. The full-orchestrator A/B on the same workload shows Gemini loses on BOTH latency and accuracy. Always test the real pipeline, not isolated components.

**References:**
- Debug logs: `~/.clicky-windows/debug/2026-04-19_06-08-32_chrome.exe/` (Gemini) and `~/.clicky-windows/debug/2026-04-19_06-18-30_chrome.exe/` (Claude) — both have `screenshot_with_marker.jpg` showing the coordinate placement.
- Earlier evening entry supersedes: Gemini 2.5/3 rejected as default on ai.py-only testing
- This entry finalizes: head-to-head confirms the rejection with stronger data

---

## 2026-04-19 (late evening): Competitive landscape + "don't fork" strategic decision

**Context.** User asked brutally-honest question: "Why am I building from scratch instead of forking an existing Clicky clone that already supports Windows + Linux?" Research agent did exhaustive GitHub fork-tree + web search pass. Found 12+ Clicky clones shipped in the 12 days since Farza open-sourced macOS Clicky on 2026-04-07. Space is saturating fast. Strategic decision needed: fork one, or continue current Python/PyQt6 implementation.

**Clones found (12 significant, ranked by fork-worthiness):**

**Tier 1 — serious candidates:**
1. **`tornikegomareli/clicky-desktop`** — Rust + Raylib, 8 ⭐, MIT, Linux + Windows binaries shipped, CI, active (last commit 2026-04-11). **~90% feature parity with our Phase 1.** BUT `computer_use.rs` header reads *"Ported from ElementLocationDetector.swift:1-335"* — **the exact dead code our DECISIONS.md 2026-04-12 e3 research-pass caught.** Zero tests. No persistent memory.
2. **`tekram/clicky-windows`** — TypeScript/Electron/React, 26 ⭐, MIT, Windows only, actively developed (3 days ago). **Shipped installer (Squirrel), 3 STT providers (AssemblyAI + OpenAI Whisper + whisper.cpp local), 3 TTS providers (ElevenLabs + OpenAI + Windows SAPI), OpenRouter 300+ models, HIPAA mode (all-local processing).** THE Windows Clicky a non-tech user finds first today. Missing: persistent memory.
3. **`mo-tunn/OpenGuider`** — JS/Electron, 66 ⭐, Apache 2.0, Windows + macOS + Linux installers. Claude + OpenAI + Gemini + Groq + OpenRouter + Ollama. Structured task-planner (trili.ai-style). Missing: persistent memory.

**Tier 2 — Windows clones less mature:**
4-10: `shreshth-s/clicky-windows` (C#/WPF, 11 ⭐), `Arnie936/zippy-windows` (C#/WinForms "Zippy", 28 ⭐), `NReyes22/clicky-windows` (C#, 2 ⭐), `CONFUZ3/ClickyWindows` (C#, 4 ⭐), `jvaught01/flicky` (Electron, 7 ⭐), `annasba07/clicky-windows` (Rust incomplete, 0 ⭐), `JaySmith502/clicky-win` (Python+uv, 3 ⭐ — **closest Python cousin, has per-app static knowledge-base injection but NOT learned persistent memory**).

**Tier 3 — adjacent:**
11. `danpeg/clicky` — macOS proactive-tutor fork, 86 ⭐ (highest-star fork of any kind).
12. `rishabhsai/glance` — UIA structured screen library (Phase 2 reference).
13. `mediar-ai/terminator` — Rust UIA cross-platform library (Phase 2 accessibility upgrade).

**Decision: Do NOT fork. Continue current Python/PyQt6 implementation.**

**Alternatives considered:**
1. **Fork `tornikegomareli/clicky-desktop`** (Rust, Linux+Windows). ✓ Cross-platform, shipped binaries, feature-complete. ✗ **Requires full Rust rewrite** (zero Rust in current codebase), ✗ **inherits the ElementLocationDetector dead-code port** we caught and corrected, ✗ **zero tests** (we'd lose our 138-test safety net), ✗ **still needs persistent memory built from scratch**. Net cost: ~3-4 weeks of work to return to current state minus test coverage.
2. **Fork `tekram/clicky-windows`** (Electron, Windows). ✓ Shipped installer + BYOK UI + HIPAA + provider abstraction. ✗ **Electron adds 150MB install size** and degrades overlay performance (we care about a transparent click-through always-on-top overlay — Electron is wrong for this). ✗ **No persistent memory** (our differentiator unchanged). ✗ Rewriting architectural pieces in TypeScript is slower than our current Python velocity.
3. **Fork `mo-tunn/OpenGuider`** (Electron, 3 platforms). ✓ Most provider-agnostic, cross-platform. ✗ Electron drawbacks (as above). ✗ Different UX philosophy (structured planner vs conversational buddy — their ethos is trili.ai, not Clicky). ✗ No persistent memory.

**Why current path won:**
- Python/PyQt6 is ~30MB install vs Electron's 150MB. Better suited for an always-on overlay.
- Claude Code has stronger Python fluency than Rust — we iterate faster here.
- 138 passing tests + DECISIONS.md discipline + research-pass habit are engineering-rigor moats no fork would preserve.
- The `ElementLocationDetector.swift` dead-code gotcha caught in our 2026-04-12 e3 research pass would be INHERITED by the Rust fork (tornikegomareli ported it verbatim without noticing the dead-code status). We'd be downgrading.
- **Persistent per-app markdown memory is unclaimed territory.** All 12 clones either have no memory or have static user-curated docs (`JaySmith502/clicky-win`). Our Karpathy-style learned memory is the one "wow" frame nobody else can reproduce.

**Consequences + what MUST happen for this decision to hold:**
- Phase B section added to ROADMAP.md with explicit parity-gap closures (installer B1, tray B2, OpenRouter UI B4, HIPAA B5, Linux B6). These are EXISTENTIAL for competing with tekram who already has them.
- **Hard deadline sanity-check:** if a non-tech user googles "clicky windows" today, they find `tekram/clicky-windows` and `tornikegomareli/clicky-desktop` shipped. We must have `Clicky-Windows-Setup.exe` + tray + demo video (B1 + B2 + B3) shipped within ~2-3 weeks or we become invisible. Phase 1.5 Step 2 (Path A parallelism) is one week; B1-B3 is the week after. That's the ~3-week window.
- Engineering-rigor differentiators (tests, DECISIONS.md trail, research-pass discipline, Boris #5 reviews) are only valuable if we SHIP. Post-Phase-B, we pitch the "unexpected finding" B0 writeup as the public differentiator.
- Fork conversation reopens ONLY if:
  - Someone ships persistent memory in a competing clone (our moat falls). Probability moderate-high within 30 days given the clone velocity.
  - Python-specific wall hits in Phase B (install experience bad, threading contention, PyQt6 reliability on Win 11 24H2). Probability low — nothing in Phase 1 has hit this.

**References:**
- Research agent full findings (2026-04-19 late evening): 12 clones inspected via `gh api` forks list + line-level source reads on top 3 candidates.
- GitHub: https://github.com/tornikegomareli/clicky-desktop, https://github.com/tekram/clicky-windows, https://github.com/mo-tunn/OpenGuider
- Farza upstream Issue #26 (Windows, 18 comments) + #13 (Linux) + #59 (Linux, newer)
- ROADMAP.md "Competitive landscape snapshot (2026-04-19 research pass)" + Phase B section captures the actionable parity gaps.

---

<!-- Append new decisions below this line. NEVER delete old entries. Format: ## YYYY-MM-DD: Short title → Context → Decision → Alternatives → Why → Consequences → References -->
