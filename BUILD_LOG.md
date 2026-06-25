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
