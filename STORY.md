# StrawPoll — How It Was Built and Why

This document tells the story of how StrawPoll was built: what problems each piece of code solves, what order decisions were made in, and why. It's meant to help anyone — technical or not — understand the project from the ground up.

New entries are appended automatically by the git post-commit hook after every commit. Run `python update_docs.py` to have Claude synthesize recent commit entries into proper narrative paragraphs.

See [PROBLEM.md](PROBLEM.md) for a plain-English explanation of what this app solves and why each feature exists.

---

## Development Journal

Each entry below describes a step taken to build the app, the problem it solved, and what decisions were made along the way.

---

### Step 1 — Deciding the Data Model (Before Any Code)

**The problem:** Before writing any code, we had to decide: what data do we actually need, and where does it come from?

The goal is to answer questions like "how did Senator X vote on immigration bills?" That requires three things:
1. A list of all senators (with stable IDs, not just names)
2. A list of every bill they voted on, with what the bill was about
3. A link between senators and bills: who voted Yea, who voted Nay

That became the core schema: `politicians` → `votes` → `bills`.

**Key decision:** Use SQLite, not PostgreSQL. SQLite is a single file on disk, needs zero setup, runs on any machine. For a dataset of ~100K rows it's more than fast enough. The tradeoff is it can't handle concurrent writes at scale — but that's a future problem, and swapping to PostgreSQL later is a one-line config change.

**Key decision:** Use Python + FastAPI + SQLAlchemy. Python has the best Anthropic SDK, FastAPI generates interactive API documentation automatically, and SQLAlchemy makes the PostgreSQL migration a one-line change.

---

### Step 2 — Finding the Data Sources (senator.gov vs. everywhere else)

**The problem:** Where do you actually get congressional voting records?

The obvious answer is Congress.gov. But Congress.gov's API doesn't give you individual senator votes — it gives you bill metadata and committee activity.

The actual vote records live on **Senate.gov** as XML files. But those XML files use a "LIS member ID" system — not the standard "bioguide ID" that every other congressional data source uses.

Separately, the **@unitedstates project** (a collaboration between volunteer coders and congressional staff) publishes a clean JSON file of every current and former member of Congress with their bioguide ID, name, party, state — *and* their LIS member ID.

So the data pipeline became:
1. Download the @unitedstates JSON → store all senators with both their bioguide ID and LIS ID
2. Download Senate.gov vote XML → match each vote to the right senator using LIS ID → store the vote linked to the senator's bioguide ID

**The 119th Congress problem:** The most common congressional data aggregator (theunitedstates.io) hadn't published 119th Congress data yet when we built this. We had to go directly to Senate.gov XML, which required building a custom parser.

---

### Step 3 — Building `seed_db.py` (The Data Import Script)

**The problem:** Getting all the data into the database in one automated pass.

`seed_db.py` does four things:

1. **Downloads the politician roster** from the @unitedstates GitHub Pages endpoint, stores every member of Congress in the `politicians` table with their bioguide ID, LIS member ID, name, party, and state.

2. **Auto-detects the right vote data source** based on the Congress number:
   - 119th Congress → Senate.gov XML (since theunitedstates.io doesn't have it yet)
   - 118th Congress and older → theunitedstates.io bulk JSON

3. **Builds a LIS-to-bioguide lookup table** so when we process Senate.gov XML (which uses LIS IDs), we can link each vote to the right senator row.

4. **Extracts bill numbers** from each vote's question text using pattern matching. If the vote doesn't reference a bill (nomination votes, procedural motions), it gets a synthetic `PROC-` prefix.

**An error we hit:** The @unitedstates legislators file moved from raw.githubusercontent.com to unitedstates.github.io. The old URL returned a 404. Fixed by updating the URL.

**Another error:** SQLAlchemy's `aiosqlite` driver wasn't installed — the project had been set up assuming PostgreSQL (asyncpg). We switched the full stack to SQLite/aiosqlite.

---

### Step 4 — Building the FastAPI App and AI Chat (`main.py`, `ai_tools.py`)

**The problem:** How does a user actually *ask* questions about the vote data?

The answer is an AI agent. Claude Opus 4.8 receives the user's natural language question and has access to two tools:
- `search_politician_votes` — queries the SQLite database for a senator's votes, filterable by issue tag or date range
- `lookup_bill` — calls LegiScan API to get live bill details

The AI decides which tools to call, calls them, gets the results, and loops until it has enough information to write a clear answer. This is called an "agentic tool-use loop."

**Why AI instead of just a search bar?** Because questions like "how did my senator vote on gun bills?" require interpretation — mapping "gun bills" to the right tag, looking up which senator represents the user's state, filtering votes, and synthesizing the results into a readable answer. That's hard to do with a traditional search. Claude handles all of it naturally.

**The session memory design:** Each conversation is stored as a JSON list of messages in the `chat_sessions` table. When the user sends a new message, the full history is loaded and sent to Claude — so Claude remembers what was said earlier in the conversation.

---

### Step 5 — Building the Bill Tagging System (`tag_bills.py`)

**The problem:** Right now we have 532 bills in the database, but no way to filter them by topic. To ask "how did Senator X vote on healthcare?" we need to know which bills are about healthcare.

The solution: tag every bill with one or more of 22 issue categories.

**Why 22 categories?** They were chosen to map directly to polling questions. You can poll a state on "what do you think about immigration policy?" but not on every specific bill. The 22 categories are broad enough to have meaningful polling data but specific enough to be distinct.

**Where the tags come from:** Congress.gov has a subjects API that returns human-assigned tags from Library of Congress catalogers. These are authoritative and free. When Congress.gov has no tags (newer bills), Claude reads the bill title and summary and assigns categories from our list.

**The static mapping problem:** Congress.gov uses their own 40-category taxonomy (e.g. "Health", "Armed Forces and National Security"). We built a lookup table that maps their categories to our 22. We also built a keyword-matching layer for their `legislativeSubjects` field.

**The omnibus bill problem:** Some bills (annual appropriations packages, continuing resolutions) cover dozens of unrelated policy areas. These get tagged with all applicable categories — no cap — and flagged with `is_omnibus = True` so the scoring system can weight them differently.

**The resolution problem:** Resolutions (SRES, HRES) often just express Congress's opinion on something — they don't change law. We track them separately with `bill_type` so they can be weighted less in scoring.

**Result:** 416 procedural votes (nominations, cloture motions) correctly get no tags. 116 real bills are all tagged.

---

### Step 6 — Adding Bill Text Summarization (`summarize_bills.py`)

**The problem:** Even with issue tags, users can't understand what they're looking at without knowing what a bill actually does. Full bill text is hundreds of pages of legal language.

**The approach:** Fetch the full bill text from Congress.gov on demand, send it to Claude, store a structured summary. One Claude call per bill, cached forever in `bills.ai_summary`.

The summary has six sections:
- **Plain English Summary** — what it does in 2-3 sentences
- **Key Provisions** — the 4-8 most important things the bill actually changes
- **Hidden or Overlooked Provisions** — earmarks to specific districts, carve-outs for named companies or industries, foreign aid to specific countries, riders (unrelated provisions attached to get the bill passed), liability shields, sunset clauses that water down the bill's apparent scope
- **Who It Affects** — who benefits and who bears the cost
- **Fiscal Impact** — using actual CBO numbers if cited
- **Political Context** — why it was controversial or bipartisan

**Why the "hidden provisions" section?** Legislation frequently contains provisions that have nothing to do with the bill's stated purpose. A highway bill might contain a subsidy for dairy farmers. A defense authorization might waive liability for a specific industry. A foreign aid bill might include a contract guarantee for a specific company. These provisions are how a lot of real political deal-making happens — and they're what most news coverage misses entirely.

---

### Step 7 — Building the Documentation System (`update_docs.py`, post-commit hook)

**The problem:** How do you keep documentation current without it being a chore?

**Two-part solution:**

**Part 1 — Zero-cost auto-logging:** A shell script in `.git/hooks/post-commit` runs automatically after every `git commit`. It appends a structured entry to `BUILD_LOG.md` — commit hash, timestamp, author, files changed, and the commit message. No AI, no tokens. This always runs.

**Part 2 — On-demand synthesis:** Running `python update_docs.py` sends `BUILD_LOG.md` and all source files to Claude, which rewrites `ARCHITECTURE.md` with updates applied and flags any discrepancies between what the docs say and what the code actually does.

**Why separate them?** If every commit triggered a Claude call, it would be slow (adds 30-60 seconds to every commit) and expensive (tokens for every commit). The split means logging is always free and instant, and synthesis happens when you actually want it — after a batch of related changes is done.

---

### Step 8 — The Neutral Scoring Design (Not Yet Built)

**The problem:** How do you score a senator's voting record without imposing a political viewpoint?

**The wrong approach:** Label votes "progressive" or "conservative." That's an opinion.

**The right approach:** Describe what they actually voted *for* in concrete, factual terms.

For every bill, Claude will classify what a Yea vote concretely did — stored as a `yea_action` field (not yet built). Examples:
- "Funded $50 billion for Medicaid expansion"
- "Authorized construction of 500 miles of border barrier"
- "Overturned the FEC's updated campaign contribution limits"

These are facts. Whether they're good or bad is for the user to decide. The app just surfaces the record.

For display, instead of a score, the app shows: "Senator X voted to increase defense spending in 12 of 14 votes on Military & Defense bills." That's a factual statement, not a political label.

---

*New entries are appended below automatically after each git commit. Run `python update_docs.py` to have Claude rewrite recent entries into proper narrative format.*

---

### [83ed9fe] 2026-06-25 02:37 — Add STORY.md, PROBLEM.md, and update post-commit hook
**Files:** ARCHITECTURE.md,BUILD_LOG.md,PROBLEM.md,STORY.md,

Add STORY.md, PROBLEM.md, and update post-commit hook

- PROBLEM.md: plain-English explanation of the 5 problems StrawPoll solves
  (vote invisibility, context gap, hidden fine print, polling vs. votes gap,
  fragmented data) and how each feature maps to solving them
- STORY.md: full development narrative — decisions made, problems hit,
  why each piece was built in the order it was, written for non-technical readers
- Update post-commit hook to append structured stubs to STORY.md on every
  commit in addition to BUILD_LOG.md
- Update ARCHITECTURE.md repo structure to list all three doc files

> *Run `python update_docs.py` to expand this into a narrative entry.*

---

### [2742948] 2026-06-25 02:41 — Add README, NEXT_STEPS, SQLite Viewer docs, and token-saving improvements
**Files:** ARCHITECTURE.md,NEXT_STEPS.md,README.md,update_docs.py,

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

> *Run `python update_docs.py` to expand this into a narrative entry.*

---

### [69c22c7] 2026-06-25 02:42 — Update post-commit hook to prompt for Claude doc update
**Files:** BUILD_LOG.md,STORY.md,

Update post-commit hook to prompt for Claude doc update

> *Run `python update_docs.py` to expand this into a narrative entry.*

---

### [5e9e5a4] 2026-06-25 02:43 — Simplify post-commit hook — remove interactive Claude prompt, add commit workflow to NEXT_STEPS
**Files:** NEXT_STEPS.md,

Simplify post-commit hook — remove interactive Claude prompt, add commit workflow to NEXT_STEPS

> *Run `python update_docs.py` to expand this into a narrative entry.*

---

### [6c31baf] 2026-06-25 02:48 — Update BUILD_LOG and STORY with recent documentation system changes
**Files:** BUILD_LOG.md,STORY.md,

Update BUILD_LOG and STORY with recent documentation system changes

> *Run `python update_docs.py` to expand this into a narrative entry.*

---

### Step 9 — Adding 2026 Senate Candidates (`seed_candidates.py`, `models.py`)

**The problem:** Voting records tell you what sitting senators have done. But what about the people challenging them? And when an election is coming up, users need to compare candidates — not just incumbents' records, but challengers' stated positions too.

This required a new table (`candidates`) and a new data pipeline.

**Why FEC as the data source:**
The first instinct was to use Ballotpedia — it's a popular nonpartisan source for election data. But Ballotpedia blocks all API access (their MediaWiki API returns a 202 with an empty body). Their HTML pages are also blocked.

Instead we went to the FEC (Federal Election Commission) — the official US government source for candidate filings. Every candidate who legally files for federal office appears there. The API is free, returns clean JSON, and is not throttled for registered users. Getting an API key takes 30 seconds at api.data.gov/signup.

**What FEC gives us:**
- Candidate name, state, party, incumbency status (incumbent/challenger/open seat)
- Campaign committee records, which include the campaign website URL

**What FEC doesn't give us:**
Policy positions — that's not FEC's job. For positions, we fetch the candidate's campaign website and use Claude Sonnet 4.6 to extract their stated positions and map them to our 22 issue categories. The same neutral framing applies: "Supports X" and "Opposes Y" — factual descriptions, not political labels.

**The incumbent link:**
If a candidate is a sitting senator (incumbent), we link their `candidates` row to their `politicians` row using `bioguide_id`. This means for incumbents, you have both their stated campaign positions AND their actual voting record. Comparing those two things — what they say vs. what they vote — is a feature we'll build in Phase 2.

**The DEMO_KEY problem:**
FEC's DEMO_KEY allows 60 requests per hour — too slow for 273 candidates. The script detects which key you're using and increases the delay accordingly, but a real FEC key (free, instant) is needed for a full run.

**The FEC name format problem:**
FEC stores names as "LAST, FIRST MIDDLE" in all caps. Some entries put a middle initial before the actual first name (e.g. "OSSOFF, T. JON" instead of "OSSOFF, JON T"). We built a name formatter that detects and skips single-character initials.

---

### Step 11 — Fixing Two Seed Bugs and Simplifying Race Status Logic

**Bug 1 — Website URLs were silently discarded:**
When we ran the first full seed of 273 candidates, every single one came back "Website: not found" — even John Hickenlooper, a sitting senator with an active campaign. The FEC committee endpoint stores website URLs without the `http://` scheme: just `"HICKENLOOPER.COM"`. Our code checked `if website.startswith("http")` which silently rejected every URL in the FEC system. Fixed by prepending `https://` when the URL has no scheme.

**Bug 2 — FEC's `candidate_inactive` flag is not a withdrawal signal:**
We assumed `candidate_inactive = True` meant a candidate dropped out. After the first run, 10 candidates were marked as "withdrawn" — but they included Tommy Tuberville, Steve Daines, Thom Tillis, and Tina Smith, all of whom are actively running in 2026. The FEC flag appears to be an administrative field that doesn't reliably indicate campaign termination. It may flag candidates whose older FEC committee records were closed or whose committee designation changed. The flag was removed entirely from the withdrawal logic. Status is now determined only by explicit keyword scanning of campaign websites.

**Added `needs_update` flag:**
Many candidates (especially minor primary candidates) have no campaign website in FEC records, so we can't extract their positions. The `needs_update` boolean column flags these rows so they can be prioritized for manual data entry or follow-up collection. When positions are successfully extracted, the flag is cleared automatically by the seed script.

**33 states, ~2 candidates each:**
The FEC data has 273 filers because it includes everyone who raised any money and filed for the race — including primary losers, candidates who dropped out early, and minor candidates. The real universe is 33 states with Senate races in 2026 (Class 2 seats, last elected 2020), with 2 general election candidates per state once primaries are done. After each state's primary, nominees are manually marked `primary_winner` and losers as `primary_loser` using the SQL in NEXT_STEPS.md.

---

### Step 10 — Tracking Who's Still in the Race (`race_status`, `fec_candidate_id`, `--check-status`)

**The problem:** After we seed candidates, the race doesn't stand still. People drop out, suspend their campaigns, lose primaries. If we don't track this, the app will show users candidates who are no longer running — which is confusing and potentially misleading.

We needed a way to:
1. Store the current status of each candidate's campaign
2. Update it automatically without requiring manual research

**What we added to the `candidates` table:**
- `race_status` — a string field: "declared", "suspended", "withdrawn", "primary_winner", or "primary_loser"
- `race_status_updated_at` — timestamp of the last status check
- `fec_candidate_id` — the FEC's unique ID for this candidate (e.g. "S8GA00180"), stored so we can re-query FEC directly for updates

The first two were a schema migration on an existing table using `ALTER TABLE ADD COLUMN`, since SQLite supports adding columns with a default value.

**How status is determined:**
Two signals, checked in order:

1. **FEC `candidate_inactive` flag** — when a candidate formally files to terminate their campaign with the FEC, this flag flips to true. It's the most reliable signal that a campaign is legally over.

2. **Website keyword scan** — campaigns often stop operating before they file termination paperwork. We scan the campaign website for phrases like "suspending my campaign", "no longer a candidate", "dropped out" — and flag these as withdrawn or suspended accordingly. This catches real-world campaign endings that FEC data misses by weeks or months.

**The `--check-status` mode:**
Running `python seed_candidates.py --check-status` re-checks every declared candidate. It's fast (FEC API + one HTTP fetch per candidate) and costs nothing in AI tokens — the keyword scan doesn't need Claude.

**The design choice on keyword matching vs. Claude:**
Claude would be more accurate at detecting nuanced language (e.g. "I have decided the time is not right for my campaign"). But running Claude for 273 candidates on a weekly basis would cost $5-10 per run in API tokens. A keyword list handles 90% of cases for free, and the FEC flag catches the rest with official certainty. Claude is reserved for extraction tasks (positions), not for pattern matching on known phrases.

---

### [e68b032] 2026-06-25 02:55 — Add 2026 Senate candidate tracking — FEC data + Claude position extraction
**Files:** .env.example,ARCHITECTURE.md,BUILD_LOG.md,NEXT_STEPS.md,STORY.md,models.py,seed_candidates.py,

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

> *Run `python update_docs.py` to expand this into a narrative entry.*

---

### [edfcf20] 2026-06-25 03:12 — Add race status tracking and --check-status mode for 2026 Senate candidates
**Files:** ARCHITECTURE.md,NEXT_STEPS.md,STORY.md,models.py,seed_candidates.py,

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

> *Run `python update_docs.py` to expand this into a narrative entry.*

---

### [90e6f83] 2026-06-25 03:23 — Fix candidate seed bugs, add needs_update flag, format GOAL.md
**Files:** ARCHITECTURE.md,GOAL.md,NEXT_STEPS.md,STORY.md,models.py,seed_candidates.py,

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

> *Run `python update_docs.py` to expand this into a narrative entry.*

---

### [d659e83] 2026-06-25 03:30 — Fix FEC URL casing bug and add --refresh reminder to NEXT_STEPS
**Files:** NEXT_STEPS.md,seed_candidates.py,

Fix FEC URL casing bug and add --refresh reminder to NEXT_STEPS

seed_candidates.py: lowercase URL before prepending https:// so FEC entries
like "HTTPS://WWW.SITE.COM" don't become "https://HTTPS://..." — also
normalizes all-caps domains like "HICKENLOOPER.COM" to lowercase

NEXT_STEPS.md: added step 3 — run --refresh once to pick up the URL
casing fix for candidates that got a bad URL on the first pass

> *Run `python update_docs.py` to expand this into a narrative entry.*

---

### [9eef0ee] 2026-06-25 03:31 — Fix FEC URL casing bug and add --refresh reminder to NEXT_STEPS -- FInished First round of Candidate Seeding
**Files:** BUILD_LOG.md,COMMIT STEPS ,STORY.md,seed_output.log,

Fix FEC URL casing bug and add --refresh reminder to NEXT_STEPS -- FInished First round of Candidate Seeding

seed_candidates.py: lowercase URL before prepending https:// so FEC entries
like "HTTPS://WWW.SITE.COM" don't become "https://HTTPS://..." — also
normalizes all-caps domains for candidate urls like "HICKENLOOPER.COM" to lowercase

NEXT_STEPS.md: added step 3 — run --refresh once to pick up the URL
casing fix for candidates that got a bad URL on the first pass

> *Run `python update_docs.py` to expand this into a narrative entry.*

---

### [d79c1db] 2026-06-25 04:00 — Correct BUILD_LOG entries after commit rebase — update stale hashes and messages
**Files:** BUILD_LOG.md,STORY.md,

Correct BUILD_LOG entries after commit rebase — update stale hashes and messages

> *Run `python update_docs.py` to expand this into a narrative entry.*

---
