# Clicky Windows — Architectural Decision Records

**Append-only.** Never delete entries. If a decision is reversed, add a NEW entry referencing the old one.

Format: `## YYYY-MM-DD: Short title` → Context → Decision → Alternatives considered → Consequences → References.

For **what and why** → [PRD.md](PRD.md)
For **where are we now** → [ROADMAP.md](ROADMAP.md)
For **how** → [CLAUDE.md](CLAUDE.md)

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

<!-- Append new decisions below this line. NEVER delete old entries. Format: ## YYYY-MM-DD: Short title → Context → Decision → Alternatives → Why → Consequences → References -->
