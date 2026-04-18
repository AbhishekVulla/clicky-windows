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

**Context.** Step 7 orchestrator shipped at `942a905`. Manual testing + debug logs show Claude Sonnet 4.6 vision inference is 5-9s = 85-90% of total PTT latency (e.g. session 03:24:32: stop_recording=301ms, capture=228ms, Claude=8035ms, TTS=instant). Target was sub-2s end-to-end. Aaron (senior engineer, met at SUTD InspireCon 2026-04-18) explicit feedback: *"Gemini Flash is actually good enough."* He validated OpenRouter as the BYOK abstraction: users shouldn't be forced onto one provider (Clicky macOS is locked into ElevenLabs → top-3 upstream complaint per issues #22/#27/#32/#33).

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

**Context.** Earlier 2026-04-19 decision above shipped GeminiClient + factory + dual-SDK routing. Landed at commits `6ee8f3f` through `3a76962`, 138/138 tests green. Set `.env` MODEL_ID to `google/gemini-3-flash-preview` for manual verification. Then tested against both Gemini models on fresh + stale screenshots, measured real latency from Step 7 orchestrator debug logs.

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

<!-- Append new decisions below this line. NEVER delete old entries. Format: ## YYYY-MM-DD: Short title → Context → Decision → Alternatives → Why → Consequences → References -->
