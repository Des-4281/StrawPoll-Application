# StrawPoll Build Log

Auto-generated log of every commit. Run `python update_docs.py` to synthesize
this log into ARCHITECTURE.md and check for discrepancies.

---


## [367fbfb] 2026-06-25 02:31 — David Solorio
**Hash:** 367fbfb6ee00be4f5020fcf2f1a863f7692c7611
**Files changed:** ARCHITECTURE.md,models.py,summarize_bills.py,update_docs.py,
**Lines:** +539 / -78

Add bill summarizer, auto-doc system, and neutral vote scoring design

- New summarize_bills.py: fetches full bill text from Congress.gov on demand,
  extracts structured ~800-word summary with Claude (plain English, provisions,
  fiscal impact, legal basis), caches in bills.ai_summary — one call per bill
- New update_docs.py: on-demand ARCHITECTURE.md updater — reads BUILD_LOG.md
  and source files, asks Claude to update docs and flag discrepancies
- New .git/hooks/post-commit: zero-token auto-logger, appends structured entry
  to BUILD_LOG.md after every commit (hash, files, message)
- Add ai_summary column to Bill model
- Rewrite ARCHITECTURE.md: adds bill summarizer docs, documentation system
  section, neutral vote scoring design (describe what senators voted FOR not
  labels), fix Phase 1 roadmap checkboxes to reflect completed work
---

## [8b24af9] 2026-06-25 02:32 — David Solorio
**Hash:** 8b24af90a831a5a591353b2c3c04ac01f20502bb
**Files changed:** models.py,summarize_bills.py,
**Lines:** +21 / -10

Expand bill summarizer to surface hidden provisions and earmarks

Add "Hidden or Overlooked Provisions" section to Claude's extraction prompt:
earmarks directed to specific districts, carve-outs for named companies or
industries, foreign aid to specific countries, agricultural subsidies, unrelated
riders, liability shields, and sunset clauses. Bump max_tokens 1200→2000 and
ai_summary column size 4000→8000 chars to accommodate richer output.
---

## [83ed9fe] 2026-06-25 02:37 — David Solorio
**Hash:** 83ed9fe3cacc7041b7644168fa1324f37adc72cc
**Files changed:** ARCHITECTURE.md,BUILD_LOG.md,PROBLEM.md,STORY.md,
**Lines:** +307 / -1

Add STORY.md, PROBLEM.md, and update post-commit hook

- PROBLEM.md: plain-English explanation of the 5 problems StrawPoll solves
  (vote invisibility, context gap, hidden fine print, polling vs. votes gap,
  fragmented data) and how each feature maps to solving them
- STORY.md: full development narrative — decisions made, problems hit,
  why each piece was built in the order it was, written for non-technical readers
- Update post-commit hook to append structured stubs to STORY.md on every
  commit in addition to BUILD_LOG.md
- Update ARCHITECTURE.md repo structure to list all three doc files
---

## [2742948] 2026-06-25 02:41 — David Solorio
**Hash:** 274294833cd6ea7e66cea30da37bb47c61d0ba8c
**Files:** ARCHITECTURE.md,NEXT_STEPS.md,README.md,update_docs.py,
**Lines:** +233 / -8

Add README, NEXT_STEPS, SQLite Viewer docs, and token-saving improvements

- README.md: guided start-here file linking PROBLEM → STORY → ARCHITECTURE → NEXT_STEPS
- NEXT_STEPS.md: personal action list — immediate tasks, Phase 2 build instructions,
  Phase 3 polling data sources, and prompts to use with Claude Code
- ARCHITECTURE.md: add full SQLite Viewer install steps (extension ID, right-click flow),
  add DB Browser option, add NEXT_STEPS and README to repo structure listing
- update_docs.py: switch from claude-opus-4-8 to claude-sonnet-4-6 (doc synthesis
  doesn't need Opus; Sonnet is ~5x cheaper and equally capable for this task)
- post-commit hook: skip STORY.md entries for typo/formatting/style/whitespace commits;
  keep refactoring and all feature/fix commits; BUILD_LOG still captures everything
---

## [69c22c7] 2026-06-25 02:42 — David Solorio
**Hash:** 69c22c7ccc2b36818ce56a68ba8a95273d3fa41e
**Files:** BUILD_LOG.md,STORY.md,
**Lines:** +72 / -0

Update post-commit hook to prompt for Claude doc update
---
   (doc update prompt skipped — non-interactive terminal)

## [5e9e5a4] 2026-06-25 02:43 — David Solorio
**Hash:** 5e9e5a45caf81bc5488b6b541fa73bd89223aa7f
**Files:** NEXT_STEPS.md,
**Lines:** +18 / -0

Simplify post-commit hook — remove interactive Claude prompt, add commit workflow to NEXT_STEPS
---
