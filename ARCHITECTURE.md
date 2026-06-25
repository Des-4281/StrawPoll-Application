# StrawPoll Voting App — Architecture & Build Guide

**Last updated:** 2026-06-25
**Maintainer:** This file is updated manually and by `python update_docs.py`. The post-commit hook writes to BUILD_LOG.md automatically on every commit. Run `update_docs.py` to synthesize BUILD_LOG into this file and check for discrepancies.

---

## What This Is

StrawPoll Voting App tracks how every US Senator votes on legislation and lets users ask natural-language questions about those records using AI. The longer-term goal is to compare each senator's actual voting record to polling data from their home state — surfacing gaps between how a senator votes and what their constituents say they want.

The app is backend-only right now (a REST API). A frontend (web or mobile) will connect to it later.

---

## Why These Technology Choices

| Decision | What We Chose | Why |
|---|---|---|
| Language | Python | Strong async support, best Anthropic SDK, fast to iterate |
| Web framework | FastAPI | Async-native, auto-generates API docs at `/docs`, Pydantic validation built in |
| Database | SQLite (via aiosqlite) | Zero setup, one file, good enough for < 5M rows; swap to PostgreSQL later |
| ORM | SQLAlchemy (async) | Industry standard, makes PostgreSQL migration a one-line change |
| AI model | Claude Opus 4.8 | Best reasoning quality for complex legislative questions |
| AI pattern | Agentic tool-use loop | Claude decides which DB queries to run, loops until it has a full answer |
| Vote data source | Senate.gov XML | Official source; theunitedstates.io doesn't publish 119th Congress yet |
| Politician data | @unitedstates/congress-legislators | Maintained by volunteer coders + Congress staff, free, accurate |
| Bill metadata | LegiScan API | Better coverage and search than Congress.gov for finding bills by keyword |
| Bill subject tags | Congress.gov subjects API | Official human-assigned subject tags, most authoritative source |
| Tag fallback | Claude | Used when Congress.gov has no subjects for a bill (newer bills, edge cases) |

---

## Repository Structure

```
StrawPoll Application/
├── main.py              — FastAPI app, API endpoints, AI agent loop
├── models.py            — SQLAlchemy database schema (all tables)
├── database.py          — DB engine setup, session factory, init_db()
├── ai_tools.py          — Tool definitions Claude can call + their implementations
├── services.py          — LegiScan API client (live bill lookups)
├── seed_db.py           — One-time data import script (politicians + votes)
├── tag_bills.py         — One-time tagging script (issue categories per bill)
├── seed_candidates.py   — Imports 2026 Senate candidates from FEC + extracts positions from campaign websites
├── summarize_bills.py   — On-demand bill text fetcher + Claude summarizer
├── update_docs.py       — On-demand ARCHITECTURE.md updater (reads BUILD_LOG, uses Claude)
├── README.md            — Start here: guided reading order, quick setup, file guide
├── PROBLEM.md           — Plain English: what problems this solves and why each feature exists
├── STORY.md             — Development journal: how it was built, step by step, and why
├── ARCHITECTURE.md      — Technical reference: schema, data sources, API, setup
├── NEXT_STEPS.md        — Personal action list: what to do next and in what order
├── BUILD_LOG.md         — Auto-written after every git commit by the post-commit hook
├── requirements.txt     — Python dependencies
├── .env                 — API keys (never commit this file)
├── .env.example         — Placeholder template for new developers
├── .gitignore           — Protects .env, .claude/, *.db, venv/
├── .git/hooks/post-commit — Shell hook that writes to BUILD_LOG.md on every commit
└── strawpoll.db         — SQLite database file (gitignored)
```

---

## Database Schema

### `politicians`
One row per member of Congress. Primary key is the **bioguide_id** (a stable ID assigned by Congress, e.g. `S001217`).

| Column | Type | Notes |
|---|---|---|
| bioguide_id | TEXT PK | Official Congress ID, stable across terms |
| name | TEXT | Full name |
| party | TEXT | R, D, I |
| state | TEXT | Two-letter state code |
| lis_member_id | TEXT | LIS ID — used to match Senate.gov XML votes to this row |

**lis_member_id** is the key that links vote XML data to the politicians table. The Senate.gov vote XML uses LIS IDs, not bioguide IDs. This mismatch is why we store both: bioguide_id is the stable cross-system ID, lis_member_id is the one Senate.gov actually uses in its XML.

### `bills`
One row per bill or procedural vote that was voted on. Primary key is a synthetic **bill_number** we construct.

| Column | Type | Notes |
|---|---|---|
| bill_number | TEXT PK | e.g. `S1234-119`, `PROC-On the Nomination PN373-119` |
| title | TEXT | Official bill title |
| summary | TEXT | Brief description (from LegiScan or Senate.gov) |
| status | TEXT | e.g. "Passed Senate (67-32)" |
| congress | INT | 119 = current Congress (2025–2026) |
| chamber | TEXT | Senate or House |
| tags | JSON | List of issue categories from our 22-category taxonomy |
| bill_type | TEXT | "Bill", "Joint Resolution", "Resolution", or "Procedural" |
| is_omnibus | BOOL | True if bill covers many unrelated topics (e.g. appropriations bills) |
| ai_summary | TEXT | Claude-extracted structured summary (~800 words), populated by summarize_bills.py |
| updated_at | DATETIME | Row last modified |

**bill_number format:** We construct this as `{PREFIX}{NUMBER}-{CONGRESS}`. Examples:
- `S5-119` = Senate bill 5, 119th Congress
- `SJRES82-119` = Senate Joint Resolution 82
- `PROC-On the Cloture Motion PN373-119` = procedural nomination vote

### `votes`
One row per senator-per-vote. Links politicians to bills.

| Column | Type | Notes |
|---|---|---|
| id | INT PK | Auto-increment |
| bioguide_id | TEXT FK | → politicians.bioguide_id |
| bill_number | TEXT FK | → bills.bill_number |
| position | TEXT | "Yea", "Nay", "Present", "Not Voting" |

Unique constraint on (bioguide_id, bill_number, position) prevents duplicates.

### `districts`
House districts only. Senators don't have district rows. Populated optionally from MIT Election Lab data.

| Column | Type | Notes |
|---|---|---|
| district_id | TEXT PK | e.g. "TX-07", "CA-AT" |
| state | TEXT | Two-letter code |
| cook_pvi | TEXT | e.g. "R+5", "D+8" — partisan lean index |
| pvi_score | FLOAT | Numeric: positive = R lean, negative = D lean |
| last_dem_pct | FLOAT | Dem % in most recent election |
| last_rep_pct | FLOAT | Rep % in most recent election |
| last_margin | FLOAT | rep_pct - dem_pct (positive = R won) |
| last_election_year | INT | Year of the result stored |

### `candidates`
One row per person running for office in an upcoming election. Populated by `seed_candidates.py`.

| Column | Type | Notes |
|---|---|---|
| id | INT PK | Auto-increment |
| name | TEXT | Full name (normalized from FEC "LAST, FIRST" format) |
| state | TEXT | Two-letter state code |
| party | TEXT | "Democratic Party", "Republican Party", etc. |
| election_year | INT | 2026 |
| office | TEXT | "Senate" (House added later) |
| incumbent | BOOL | True if they currently hold the seat |
| race_status | TEXT | "declared", "suspended", "withdrawn", "primary_winner", "primary_loser" |
| race_status_updated_at | DATETIME | When race_status was last checked |
| fec_candidate_id | TEXT | FEC candidate ID (e.g. `S8GA00180`) — used to re-query FEC for updates |
| bioguide_id | TEXT FK | → politicians.bioguide_id, if they're a sitting senator |
| website_url | TEXT | Campaign website URL (from FEC committee record) |
| ballotpedia_url | TEXT | Constructed Ballotpedia link for reference |
| positions | JSON | Stated positions mapped to our 22 categories: `{"Healthcare": "Supports..."}` |
| positions_source | TEXT | Where positions came from: "campaign website", "fec-no-website", etc. |
| positions_updated_at | DATETIME | When positions were last fetched |

**race_status values:**
- `declared` — actively running (FEC shows active, no withdrawal signals on website)
- `suspended` — campaign paused (website signals pause language; may resume)
- `withdrawn` — formally dropped out (FEC inactive flag or clear withdrawal language on site)
- `primary_winner` / `primary_loser` — set after primary results are certified

**How race_status is determined** (`seed_candidates.py --check-status`):
1. FEC `candidate_inactive` flag — most reliable signal a campaign formally ended
2. Keyword scan of campaign website for withdrawal/suspension language ("suspending my campaign", "no longer a candidate", etc.) — catches campaigns that stepped back without filing FEC termination paperwork

**Why this matters:** Incumbents have a voting record (in the `votes` table) AND stated positions (in `candidates.positions`). Challengers only have stated positions. The comparison between what incumbents *say* and how they *vote* is a key feature for Phase 2.

### `users`, `user_favorites`, `chat_sessions`
App user tables. Users sign up with email, can save favorite politicians/bills, and have chat history with the AI stored as a JSON message list per session.

---

## The 22 Issue Categories

All bills are tagged with one or more of these categories. Procedural votes (nominations, cloture motions) get no tags.

```
Economy & Taxes            Government Budget & Spending
Elections & Campaign Finance  Civil Rights
LGBTQ+ Rights              Healthcare
Military & Defense         Foreign Policy & International Affairs
Immigration                Border Security
Environmental Policy       Climate Policy
Gun Policy                 Criminal Justice
Policing & Law Enforcement Education
Social Safety Net          Housing
Drug Policy                Labor & Workers Rights
Technology & Privacy       US Territory Policy
```

**Omnibus bills** (like annual appropriations packages) get tagged with all applicable categories — no cap. The `is_omnibus` flag marks them so the scoring system can weight them differently.

---

## How Data Gets Into the Database

### Step 1: Seed politicians and votes (`seed_db.py`)

Run once (or re-run to update). Does three things:

1. **Fetches politician roster** from `unitedstates.github.io/congress-legislators/legislators-current.json` — maintained by the @unitedstates project, which is a collaboration between volunteer coders and congressional staff. Includes bioguide_id, name, party, state, and LIS member ID.

2. **Fetches votes** — auto-detects source based on Congress number:
   - **119th Congress (current):** Senate.gov XML feed at `www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml`. Downloads an index of all roll call votes, then fetches each one individually.
   - **118th Congress and older:** theunitedstates.io bulk data (JSON files organized by Congress/session).

3. **Matches senators to votes** using `lis_member_id`. Senate.gov XML uses LIS IDs, not bioguide IDs, so we build a lookup table `{lis_id: bioguide_id}` from the politician data before processing any votes.

4. **Bill numbers** are extracted from the vote's question text using regex. If no bill number is found (nomination votes, procedural motions), the bill_number is constructed as `PROC-{question_text}`.

```bash
python seed_db.py                    # 119th Congress Senate (default)
python seed_db.py --congress 118 119 # both congresses
python seed_db.py --chamber both     # House too (future)
```

### Step 2: Tag bills (`tag_bills.py`)

Run after seeding. Tags every bill with issue categories.

1. **Congress.gov subjects API** — returns the official `policyArea` and `legislativeSubjects` for each bill, assigned by human catalogers at the Library of Congress.
2. **Static mapping** — Congress.gov's ~40 policy areas and hundreds of subject terms are mapped to our 22 categories via lookup tables in `tag_bills.py`.
3. **Claude fallback** — If Congress.gov returns no subjects, Claude reads the bill title and summary and assigns categories.
4. **bill_type** detected from bill number prefix. **is_omnibus** set from title keywords or 6+ categories assigned.

```bash
python tag_bills.py           # tag untagged bills
python tag_bills.py --retag   # overwrite all tags
python tag_bills.py --dry-run # preview without saving
```

### Step 3: Seed 2026 candidates (`seed_candidates.py`)

Imports all 2026 Senate candidates from FEC, fetches their campaign websites, and uses Claude to extract stated positions.

```bash
python seed_candidates.py --dry-run        # preview without saving
python seed_candidates.py --state GA       # one state only (for testing)
python seed_candidates.py                  # all funded D/R candidates (~273)
python seed_candidates.py --refresh        # re-fetch positions for existing rows
python seed_candidates.py --check-status   # re-check who is still in the race
python seed_candidates.py --check-status --state GA  # check one state only
```

**What `--check-status` does:** For every candidate whose `race_status` is "declared", it:
1. Hits FEC's `/candidate/{id}/` endpoint and checks the `candidate_inactive` flag
2. Fetches the candidate's campaign website and scans for withdrawal/suspension language
3. Updates `race_status` and `race_status_updated_at` if a change is detected

Run this weekly during active campaign season to catch candidates who drop out.

### Step 4: Summarize bills (`summarize_bills.py`)

Optional but powerful. For each real bill (not procedural votes):

1. Calls Congress.gov text endpoint to get the full bill text URL
2. Downloads the actual text (plain text or HTML, strips tags)
3. Sends up to 40,000 characters to Claude Opus 4.8
4. Claude returns a structured ~800-word summary with sections: Plain English Summary, Key Provisions, Who It Affects, Fiscal Impact, Legal Basis, Political Context
5. Stored in `bills.ai_summary` — one Claude call per bill, cached forever

```bash
python summarize_bills.py               # all unsummarized bills
python summarize_bills.py --bill S5-119 # one specific bill
python summarize_bills.py --limit 20    # test run
```

---

## How Voting Scores Work (The Neutral Approach)

This is a key design question: how do you score votes without imposing a political viewpoint?

**The answer: describe what they voted FOR, not whether it was right or wrong.**

Instead of labeling votes "progressive" or "conservative," each bill gets a plain-English description of what a Yea vote meant in concrete policy terms. For example:

| Bill | Issue Tag | Yea vote meant... |
|---|---|---|
| S1234 | Healthcare | Funding $50B for Medicaid expansion |
| S567 | Immigration | Authorizing construction of 500 miles of border barrier |
| SJRES82 | Elections | Overturning the FEC's updated campaign finance rule |

This framing is factual and neutral. Whether funding Medicaid is good or bad is for the user to decide. The app just tells you: Senator X voted for it, here's what "it" actually does.

**How the scoring will work:**

For each senator, for each issue category:
- Find all bills tagged with that category they voted on
- For each bill, store a `yea_action` description (what a Yea vote did)
- Count: how many times did they vote to expand/fund vs. restrict/cut in that issue area?
- Present it as a factual record: "Voted for increased military spending 12 of 14 times" — not a score

**The resolution/nomination caveat:** Resolutions (SRES) often congratulate things or express the Senate's sense on an issue — they don't change law. These need to be weighted less or displayed separately so they don't distort the record. The `bill_type` field handles this.

**You're right that nobody "votes for inflation"** — votes are always FOR something specific (a bill, an amendment, a nominee). The scoring system surfaces what they actually supported in concrete terms, never reframes it as a position on an abstraction like inflation or crime.

---

## The AI Agent (How Chat Works)

**Endpoint:** `POST /chat`

The user sends a natural language question like "How did Senator Murkowski vote on climate bills?" The system:

1. Loads the user's conversation history from `chat_sessions` so the AI has context across messages.
2. Sends the conversation to Claude Opus 4.8 with a list of tools the AI can call.
3. Claude decides which tools to use and calls them in sequence or parallel.
4. Tool results are fed back to Claude. This loop repeats until Claude has enough information to answer.
5. Claude's final text response is returned to the user and the conversation is saved.

**Available tools (defined in `ai_tools.py`):**

| Tool | What it does |
|---|---|
| `search_politician_votes` | Queries the SQLite DB for a politician's votes, optionally filtered by bill tag or date range |
| `lookup_bill` | Calls LegiScan API to get live bill details (full title, status, sponsor, summary) |

**Why this pattern:** Claude with tool use is more reliable than prompt-engineering a SQL query directly. The AI handles ambiguous politician names, multi-step questions, and knowing when it needs more data.

---

## The Documentation System (How This File Stays Current)

Two separate mechanisms, intentionally split so you control cost:

### 1. Free auto-logging (every commit)
The `.git/hooks/post-commit` shell script fires automatically after every `git commit`. It appends a structured entry to `BUILD_LOG.md` containing the commit hash, timestamp, author, files changed, and commit message. Zero AI, zero tokens. This always runs — you never have to think about it.

### 2. On-demand synthesis (`update_docs.py`)
Run manually when you want this ARCHITECTURE.md to catch up to recent changes:

```bash
python update_docs.py
```

This sends `BUILD_LOG.md` + all source files to Claude, which rewrites ARCHITECTURE.md with updates applied and flags any discrepancies between what the docs say and what the code actually does. Costs tokens, so you run it intentionally — not after every commit.

**When to run it:** After a batch of related commits wraps up a feature. Not after every single commit.

---

## API Endpoints

All endpoints are documented interactively at `http://localhost:8001/docs` when the server is running.

| Method | Path | What it does |
|---|---|---|
| POST | `/users` | Create a user account (email only) |
| POST | `/chat` | Send a message, get AI response |
| POST | `/favorites` | Save a politician or bill to watchlist |
| GET | `/favorites/{user_id}` | Get a user's watchlist |

```bash
# Start the server
uvicorn main:app --reload --port 8001

# Create a user
curl -X POST http://localhost:8001/users \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'

# Ask the AI (use the user_id returned above)
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "message": "How did Susan Collins vote on healthcare bills?"}'
```

---

## Environment Setup

```bash
# 1. Clone the repo
git clone https://github.com/Des-4281/StrawPoll-Application
cd "StrawPoll Application"

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up API keys
cp .env.example .env
# Edit .env and fill in:
#   ANTHROPIC_API_KEY     — from console.anthropic.com
#   LEGISCAN_API_KEY      — from legiscan.com/legiscan-api
#   CONGRESS_GOV_API_KEY  — from api.congress.gov/sign-up

# 5. Seed the database (takes 5-15 minutes for 119th Congress)
python seed_db.py

# 6. Tag all bills (uses Congress.gov API, ~10 min)
python tag_bills.py

# 7. Seed 2026 Senate candidates (requires free FEC API key from api.data.gov/signup)
# Add FEC_API_KEY to .env first, then:
python seed_candidates.py --dry-run   # preview
python seed_candidates.py             # full import (~270 candidates, ~30 min)

# 8. Start the server
uvicorn main:app --reload --port 8001
```

---

## Viewing the Database

**Option 1 — VS Code extension (easiest, no install needed beyond VS Code):**
1. Open VS Code Extensions panel: ⌘⇧X (Mac) or Ctrl+Shift+X (Windows)
2. Search: `SQLite Viewer`
3. Install the one by **Florian Klampfer** (extension ID: `qwtel.sqlite-viewer`)
4. Right-click `strawpoll.db` in the VS Code file explorer → **Open With → SQLite Viewer**
5. Click any table name in the left panel to browse rows. Use the SQL tab to run queries.

**Option 2 — DB Browser for SQLite (full desktop app, free):**
Download from sqlitebrowser.org. Open `strawpoll.db`, browse tables, run SQL queries, export to CSV. Best option if you want to edit data directly.

**Option 3 — Command line:**
```bash
sqlite3 strawpoll.db
.tables                                          # list all tables
.schema bills                                    # show columns for bills table
SELECT COUNT(*) FROM votes;                      # 54,960 rows
SELECT * FROM bills WHERE bill_type = 'Bill' LIMIT 10;
SELECT name, state, party FROM politicians LIMIT 10;
SELECT bill_number, tags FROM bills WHERE tags != '[]' LIMIT 5;
.quit
```

---

## Current Data Stats (as of 2026-06-25)

| Table | Count | Notes |
|---|---|---|
| politicians | 537 | All current members of Congress (House + Senate) |
| bills | 532 | 119th Congress Senate roll call votes |
| votes | 54,960 | Individual senator position per vote |
| bills by type | 416 Procedural, 76 Joint Resolutions, 27 Bills, 13 Resolutions | |
| tagged (non-procedural) | 116 of 116 | All real bills have issue category tags |

---

## Roadmap

### Phase 1 — Data Foundation
- [x] Politician roster (House + Senate, 119th Congress)
- [x] Senate roll call votes (119th Congress, all 532 votes)
- [x] Bill tagging system (22 issue categories, all 116 real bills tagged)
- [x] AI chat endpoint (natural language vote queries)
- [x] Bill text summarizer (summarize_bills.py — on-demand, Claude extracts structured summary)
- [x] Auto-documentation system (BUILD_LOG.md hook + update_docs.py)
- [x] 2026 Senate candidate tracking (FEC data + Claude position extraction + race status)
- [ ] Seed 118th Congress data for historical comparison

### Phase 2 — Scoring & Analysis
- [ ] **`yea_action` field on bills** — one sentence describing what a Yea vote concretely did (e.g. "funded $50B for Medicaid expansion"). Claude classifies this from the bill summary. Neutral, factual, no political framing.
- [ ] **Voting record display per senator per issue** — for each category, list all bills they voted on, what each Yea/Nay meant, and a plain-English summary of their record (e.g. "Voted to increase defense spending in 12 of 14 votes on Military & Defense bills")
- [ ] **Score weighting** — weight Bills more than Resolutions (Resolutions don't change law), weight omnibus bills appropriately given they cover many topics

### Phase 3 — Polling Integration
- [ ] **State-level polling data** — approval ratings / issue polling by state. Best sources: FiveThirtyEight averages, Pew Research state-level data, YouGov MRP state estimates.
- [ ] **Polling vs. voting gap** — for each senator × each issue: compare their voting record to their state's polling sentiment on that issue. A large gap is the politically interesting signal.
- [ ] **StatePolling table** — state, issue_category, polling_pct, poll_date, source

### Phase 4 — API Expansion
- [ ] `GET /senators/{state}` — all senators for a state with their voting record
- [ ] `GET /senator/{bioguide_id}/record` — full issue-by-issue voting record
- [ ] `GET /compare/{state}` — senator voting vs. state polling on same issues
- [ ] `GET /bills` — paginated bill list with filtering by tag, type, congress

### Phase 5 — Frontend
- [ ] Web app (React/Next.js or similar)
- [ ] State map — click a state, see senators + their records
- [ ] Senator profile — voting record by issue with key votes highlighted
- [ ] AI chat interface
- [ ] Bill explorer — browse by issue tag

### Phase 6 — Scale
- [ ] Migrate SQLite → PostgreSQL (one-line change in DATABASE_URL)
- [ ] Add House votes (excluded now because House districts don't map cleanly to state polling)
- [ ] Historical data: 117th, 116th Congress for trend analysis
- [ ] Nightly data refresh (new votes auto-pulled from Senate.gov)

---

## Data Sources Reference

| Source | What it provides | API key needed | Notes |
|---|---|---|---|
| `unitedstates.github.io/congress-legislators` | Politician roster | No | Volunteer + congressional staff maintained |
| `www.senate.gov` XML feeds | 119th Congress roll call votes | No | Official source |
| `theunitedstates.io` | 118th Congress and older bulk vote data | No | Hasn't published 119th yet |
| LegiScan API | Bill search, details, status, sponsors | Yes (free) | Better search than Congress.gov |
| Congress.gov API | Bill subject tags, full bill text | Yes (free) | Official; subject tags are human-assigned |
| FEC API (api.open.fec.gov) | 2026 Senate candidates: name, state, party, incumbency, committee website URL | Yes (free, instant at api.data.gov/signup) | Official federal source; 309 funded D/R candidates |
| MIT Election Lab | House election results by district (1976–present) | No | Harvard Dataverse, CC license |
| Cook Political Report | Cook PVI (district partisan lean) | Paywalled | GitHub aggregators exist |

---

## Security Notes

- `.env` is gitignored — never commit API keys
- `.claude/` is gitignored — Claude's local project memory
- `strawpoll.db` is gitignored — contains real user data
- All user emails are stored in plaintext — add bcrypt hashing before any real launch
- The API has no authentication yet — add JWT tokens before any public deployment
