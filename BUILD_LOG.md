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

## [6c31baf] 2026-06-25 02:48 — David Solorio
**Hash:** 6c31baf92ddddf833047c1a56d09247a00762cb9
**Files:** BUILD_LOG.md,STORY.md,
**Lines:** +35 / -0

Update BUILD_LOG and STORY with recent documentation system changes
---

## [e68b032] 2026-06-25 02:55 — David Solorio
**Hash:** e68b0325d27a9f03e6496ca2ac94856b904a106c
**Files:** .env.example,ARCHITECTURE.md,BUILD_LOG.md,NEXT_STEPS.md,STORY.md,models.py,seed_candidates.py,
**Lines:** +590 / -2

Add 2026 Senate candidate tracking — FEC data + Claude position extraction

New table: candidates
- Stores every 2026 Senate candidate who has raised funds (309 D/R from FEC)
- Tracks name, state, party, incumbency, website URL, stated positions
- Links incumbents to their voting record via bioguide_id FK to politicians table

New script: seed_candidates.py
- Pulls candidate list from FEC API (official federal source, free JSON API)
- Fetches campaign website URL from FEC committee records
- Downloads each campaign website, strips HTML, sends to Claude Sonnet 4.6
- Claude extracts stated positions and maps to our 22 issue categories
- Saves neutral factual descriptions ("Supports X", "Opposes Y") — no spin
- Handles FEC rate limits with configurable delay (DEMO_KEY vs real key)
- Name formatter handles FEC "LAST, FIRST MIDDLE" format including initial-first quirk

Why FEC not Ballotpedia: Ballotpedia blocks all API and HTTP access (returns
202 empty). FEC is the official government source — free, structured, complete.

Requires free FEC API key from api.data.gov/signup — add as FEC_API_KEY in .env.
Without it DEMO_KEY works but is limited to 60 req/hour (too slow for full run).

Usage:
  python seed_candidates.py --dry-run      # preview
  python seed_candidates.py --state GA     # one state
  python seed_candidates.py                # all funded D/R candidates

Updated: ARCHITECTURE.md (candidates schema, new data source, setup step),
NEXT_STEPS.md (FEC key instructions), STORY.md (narrative for this step),
.env.example (FEC_API_KEY placeholder)
---

## [edfcf20] 2026-06-25 03:12 — David Solorio
**Hash:** edfcf20e2dae2404006e1b3e0905535b40b1823d
**Files:** ARCHITECTURE.md,NEXT_STEPS.md,STORY.md,models.py,seed_candidates.py,
**Lines:** +260 / -50

Add race status tracking and --check-status mode for 2026 Senate candidates

models.py — three new columns on the candidates table:
  - race_status: "declared", "suspended", "withdrawn", "primary_winner", "primary_loser"
  - race_status_updated_at: timestamp of last status check
  - fec_candidate_id: FEC's unique ID (e.g. "S8GA00180") stored for re-querying

seed_candidates.py:
  - Save block now stores fec_candidate_id and sets initial race_status from
    FEC's candidate_inactive flag (withdrawn if inactive, declared otherwise)
  - Website fetch checks for withdrawal/suspension language before extracting
    positions — skips position extraction for withdrawn candidates
  - New check_candidate_status() async function: re-checks FEC inactive flag
    and website keywords for every declared candidate, updates race_status
  - New _check_website_for_withdrawal() helper: keyword scan for phrases like
    "suspending my campaign", "dropped out", "no longer a candidate" — no AI
    needed, fast, free to run weekly
  - New --check-status CLI flag: runs status check mode instead of seed mode
  - --check-status --state GA: check one state only

DB migration: added three columns to existing candidates table via
  ALTER TABLE ADD COLUMN (SQLite supports this with default values)

ARCHITECTURE.md: updated candidates schema table with all new columns,
  added race_status values documentation, added Step 3 for seed_candidates,
  updated Phase 1 roadmap to mark candidate tracking complete

STORY.md: added Step 10 explaining the race status design, why keyword
  matching is used instead of Claude (cost), and how the two signals work

NEXT_STEPS.md: marked FEC key as done, added --check-status usage note,
  renumbered action items
---

## [90e6f83] 2026-06-25 03:23 — David Solorio
**Hash:** 90e6f838691ca62fc73b0552eddee0e1882b373c
**Files:** ARCHITECTURE.md,GOAL.md,NEXT_STEPS.md,STORY.md,models.py,seed_candidates.py,
**Lines:** +180 / -42

Fix candidate seed bugs, add needs_update flag, format GOAL.md

seed_candidates.py — two bug fixes:
  1. Website URL scheme bug: FEC stores URLs without "http://" (e.g. "HICKENLOOPER.COM").
     The check `if website.startswith("http")` silently discarded every URL from FEC.
     Fixed by prepending "https://" when no scheme is present.
  2. FEC candidate_inactive flag removed from withdrawal logic. Despite its name,
     the flag is an administrative field that's incorrectly True for many actively-running
     candidates including sitting senators (Tuberville, Daines, Tillis, Tina Smith).
     Withdrawal detection now relies only on website keyword scanning.
  - Added needs_update boolean set to True when no positions were extracted,
    False when at least one position category was found. Cleared automatically on re-seed.
  - Docstring on check_candidate_status() updated to reflect these changes.

models.py — added needs_update: Mapped[bool] field to Candidate table.

DB migration: reset 10 wrongly-flagged "withdrawn" candidates back to "declared".
  Added needs_update column via ALTER TABLE, flagged 242 candidates with no positions.

ARCHITECTURE.md:
  - Added needs_update column to schema table
  - Corrected race_status determination docs (removed FEC inactive flag reference)
  - Added "33 states have 2026 Senate races" note with full state list
  - Added note about manually updating primary_winner/primary_loser after primaries

STORY.md — added Step 11: documents both bugs found during the first full seed run,
  explains why the FEC inactive flag is unreliable, and why needs_update was added.

NEXT_STEPS.md — added manual SQL snippet for updating primary nominees after each
  state's primary. Listed all 33 states with 2026 Senate races.

GOAL.md — reformatted for readability (content unchanged).
---

## [a44561b] 2026-06-25 03:30 — David Solorio
**Hash:** a44561b (reworded from d659e83 via rebase)
**Files:** NEXT_STEPS.md, seed_candidates.py
**Lines:** +10 / -2

Fix FEC URL casing bug, normalize all-caps FEC URLs to lowercase

seed_candidates.py: lowercase URL before prepending https:// so FEC entries
like "HTTPS://WWW.SITE.COM" don't become "https://HTTPS://..." — also
normalizes all-caps domains like "HICKENLOOPER.COM" to lowercase

NEXT_STEPS.md: added step 3 — run --refresh once to pick up the URL
casing fix for candidates that got a bad URL on the first pass
---

## [be1e4c1] 2026-06-25 03:31 — David Solorio
**Hash:** be1e4c1 (reworded from 9eef0ee via rebase)
**Files:** BUILD_LOG.md, STORY.md, seed_output.log
**Lines:** +1615 / -0

Complete first candidate seed run — 273 saved, 64 with positions

First full run of seed_candidates.py with the website URL fix applied.
Results: 273 candidates saved, 125 with websites found in FEC records,
64 with issue positions extracted by Claude from campaign websites,
209 flagged needs_update=True for follow-up data collection.
---
