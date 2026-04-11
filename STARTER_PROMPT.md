# Starter Prompt for New Claude Code Session

Copy-paste this as your first message when you open this folder in Claude Code:

---

Read CLAUDE.md first — it has the full project context, competitor analysis, validated user demands, and technical architecture.

Then read these folders (READ ONLY, don't modify):
- C:\Users\Abhis\OneDrive\Documents\AI Wiki\wiki\ — my AI knowledge base. Start with wiki/index.md, then wiki/tools/clicky.md for the competitor analysis.
- C:\Users\Abhis\OneDrive\Documents\Maritime Project\Claude Code TIPS\ — AI tools and workflows I've collected. Skim the folder structure, read anything relevant to desktop apps, screen capture, overlays, or voice.
- C:\Users\Abhis\OneDrive\Documents\2nd Brain\wiki\ — my personal knowledge base. Read hot.md for current state, building0.md for Building 0 context (this project is a Building 0 project).

Then do your own research:
1. Clone and read https://github.com/farzaa/clicky (the original macOS app, ~5,200 LOC Swift)
2. Clone and read https://github.com/tekram/clicky-windows (existing Windows Electron port, 14 stars)
3. Clone and read https://github.com/danpeg/clicky (proactive tutor mode fork, 79 stars)
4. Read GitHub issues for validated user demands: https://github.com/farzaa/clicky/issues (21 open issues — Windows #1 request, persistent memory #2, proactive mode #3)

After research, write a PRD (PRD.md) covering:
- Problem statement (non-technical people learning software alone, no guidance)
- Target user (Windows users — 76% of desktop market, zero good options)
- What the product IS (screen-aware AI buddy with persistent memory, voice, pointing)
- What the product IS NOT (not a chatbot, not a screen recorder, not Claude Desktop Cowork)
- Core loop: hotkey → screenshot → Claude Vision → overlay pointer → voice response
- Phase 1 scope (Python prototype) with acceptance criteria
- Phase 2 scope (Tauri rewrite + persistent memory)
- What we're explicitly NOT building in Phase 1
- Competitor landscape summary (Clippi.us, GhostDesk, Screenpipe, tekram port, Precogni, Vercept acquisition)

Then start building Phase 1. Begin with capture.py (screen capture + cursor position) and verify it works before moving to the next component. Build component by component, verify each one works independently, then integrate.

---

## Skills to Install First

Run this before starting:
```
npx -y @anthropic-ai/superpower@latest init
```

This installs Superpowers (brainstorm → spec → plan → TDD → code review). One workflow, not five. Add more tools later if needed.

Firecrawl is already installed globally — use it for scraping GitHub pages and competitor websites.
