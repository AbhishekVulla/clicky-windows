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

<!-- Append new decisions below this line. NEVER delete old entries. Format: ## YYYY-MM-DD: Short title → Context → Decision → Alternatives → Why → Consequences → References -->
