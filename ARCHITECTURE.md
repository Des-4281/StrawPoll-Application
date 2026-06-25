# StrawPoll Voting App — Architecture & Build Guide

**Last updated:** 2026-06-25
**Maintainer:** Update this document whenever a new file is added, a data source changes, a schema column is added, or the roadmap progresses.

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
├── main.py          — FastAPI app, API endpoints, AI agent loop
├── models.py        — SQLAlchemy database schema (all tables)
├── database.py      — DB engine setup, session factory, init_db()
├── ai_tools.py      — Tool definitions Claude can call + their implementations
├── services.py      — LegiScan API client (live bill lookups)
├── seed_db.py       — One-time data import script (politicians + votes)
├── tag_bills.py     — One-time tagging script (issue categories per bill)
├── requirements.txt — Python dependencies
├── .env             — API keys (never commit this file)
├── .env.example     — Placeholder template for new developers
├── .gitignore       — Protects .env, .claude/, *.db, venv/
└── strawpoll.db     — SQLite database file (gitignored)
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

**lis_member_id** is the key that links vote XML data to the politicians table. The Senate.gov vote XML uses LIS IDs, not bioguide IDs.

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
# Seed only 119th Congress Senate votes (default)
python seed_db.py

# Seed both 118th and 119th
python seed_db.py --congress 118 119

# Seed House too (after building House vote support)
python seed_db.py --chamber both
```

### Step 2: Tag bills (`tag_bills.py`)

Run after seeding. Tags every bill with issue categories.

1. **Congress.gov subjects API** (`api.congress.gov/v3/bill/{congress}/{type}/{number}/subjects`) — returns the official `policyArea` and `legislativeSubjects` for each bill, assigned by human catalogers.

2. **Static mapping** — Congress.gov's ~40 policy areas and hundreds of subject terms are mapped to our 22 categories via two lookup tables in `tag_bills.py`: `POLICY_AREA_MAP` and `SUBJECT_KEYWORD_MAP`.

3. **Claude fallback** — If Congress.gov returns no subjects (newer bills, edge cases), Claude reads the bill title and summary and assigns categories from our list.

4. **bill_type** is detected from the bill number prefix: `S`/`HR` → Bill, `SJRES`/`HJRES` → Joint Resolution, `SRES`/`HRES`/`SCONRES`/`HCONRES` → Resolution, `PROC-` → Procedural.

5. **is_omnibus** is set if the title contains keywords like "Omnibus", "Consolidated Appropriations", "Continuing Resolution" or if 6+ categories are assigned.

```bash
python tag_bills.py           # tag untagged bills
python tag_bills.py --retag   # overwrite all tags
python tag_bills.py --dry-run # preview without saving
```

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

**Why this pattern:** Claude with tool use is more reliable than prompt-engineering a SQL query directly. The AI can handle ambiguous politician names, multi-step questions, and knowing when it needs more data before answering.

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

# 7. Start the server
uvicorn main:app --reload --port 8001
```

---

## Viewing the Database

**DB Browser for SQLite** (recommended): Free GUI app at sqlitebrowser.org. Open `strawpoll.db`, browse tables, run SQL queries, export to CSV.

**VS Code extension**: Install "SQLite Viewer" by Florian Klampfer. Right-click `strawpoll.db` → Open With → SQLite Viewer. No extra app needed.

**Command line:**
```bash
sqlite3 strawpoll.db
.tables                                  # list all tables
SELECT COUNT(*) FROM votes;              # 54,960 vote records
SELECT COUNT(*) FROM bills;             # 532 bills/procedural votes
SELECT * FROM bills WHERE bill_type = 'Bill' LIMIT 10;
SELECT name, state, party FROM politicians WHERE chamber = 'Senate';
```

---

## Bill Text — Recommended Approach

**Don't store raw bill text.** Store an AI-extracted structured summary instead.

Why: The raw XML text of a congressional bill is 10-200KB each. You don't need the legal boilerplate — you need the policy substance. Claude can read the full text once and extract a 800-word structured summary covering:
- What the bill does in plain English
- Who it affects (agencies, industries, individuals)
- Estimated fiscal impact
- Key legal authorities it invokes
- Provisions that would change existing law

**Implementation plan (not built yet):**
1. Add `ai_summary TEXT` column to the `bills` table
2. On demand (when a user asks about a bill), fetch full text from Congress.gov XML endpoint: `api.congress.gov/v3/bill/{congress}/{type}/{num}/text`
3. Pass to Claude, store the structured summary in `ai_summary`
4. Subsequent questions about the same bill use the cached summary — one Claude call per bill ever

Total storage for all bills: ~10MB. You don't need a TB drive for this.

**If you ever want semantic search** (find bills similar to a topic): add a `embedding BLOB` column and store the 1536-float vector from an embedding model. That's ~6KB per bill, or ~5MB for all bills — still tiny.

---

## Current Data Stats (as of 2026-06-25)

| Table | Count | Notes |
|---|---|---|
| politicians | 537 | All current members of Congress (House + Senate) |
| bills | 532 | 119th Congress Senate roll call votes |
| votes | 54,960 | Individual senator position per vote |
| bills (by type) | 416 Procedural, 76 Joint Resolutions, 27 Bills, 13 Resolutions | Procedural = nomination/cloture votes |

---

## Roadmap

### Phase 1 — Data Foundation (current)
- [x] Politician roster (House + Senate, 119th Congress)
- [x] Senate roll call votes (119th Congress, all 532 votes)
- [x] Bill tagging system (22 issue categories)
- [x] AI chat endpoint (natural language vote queries)
- [ ] **Run `python tag_bills.py`** (without --dry-run) to save tags to DB
- [ ] Seed 118th Congress data for historical comparison

### Phase 2 — Scoring & Analysis
- [ ] **Voting record score per senator per issue** — for each of the 22 categories, calculate what % of a senator's votes aligned with the progressive/conservative position. This is the core metric.
- [ ] **Vote direction classification** — for each bill+issue, determine which vote position (Yea/Nay) is the "progressive" position. Requires human curation or Claude classification.
- [ ] **AI bill summary** — fetch Congress.gov bill text, extract structured summary with Claude, store in `bills.ai_summary`

### Phase 3 — Polling Integration
- [ ] **State-level polling data** — find and import approval ratings / issue polling by state. Best sources: FiveThirtyEight averages, Pew Research state-level data, YouGov MRP state estimates.
- [ ] **Polling vs. voting gap** — for each senator × each issue: compare their voting score to their state's polling sentiment on that issue. A large gap is the most politically interesting signal.
- [ ] **StatePolling table** — add to schema: state, issue_category, polling_pct, poll_date, source

### Phase 4 — API Expansion
- [ ] `GET /senators/{state}` — all senators for a state with their voting scores
- [ ] `GET /senator/{bioguide_id}/scores` — issue-by-issue voting record scores
- [ ] `GET /compare/{state}` — senator voting scores vs. state polling on same issues
- [ ] `GET /bills` — paginated bill list with filtering by tag, type, congress

### Phase 5 — Frontend
- [ ] Web app (React/Next.js or similar)
- [ ] State map view — click a state, see senators + their alignment scores
- [ ] Senator profile page — voting record broken down by issue with key votes highlighted
- [ ] AI chat interface — conversational access to all data
- [ ] Bill explorer — browse and search bills by issue tag

### Phase 6 — Scale
- [ ] Migrate SQLite → PostgreSQL (one-line change in DATABASE_URL)
- [ ] Add House votes (currently excluded because House districts don't map cleanly to state polling)
- [ ] Historical data: 117th, 116th Congress for trend analysis
- [ ] Scheduled data refresh (nightly cron to pull new votes from Senate.gov)

---

## Data Sources Reference

| Source | What it provides | API key needed | Notes |
|---|---|---|---|
| `unitedstates.github.io/congress-legislators` | Politician roster (name, party, state, IDs) | No | Updated by volunteer+congressional staff |
| `www.senate.gov` XML feeds | 119th Congress roll call votes (official) | No | `vote_menu_{congress}_{session}.xml` index |
| `theunitedstates.io` | 118th Congress and older bulk vote data | No | Hasn't published 119th yet |
| LegiScan API | Bill search, details, status, sponsors | Yes (free) | Better search than Congress.gov |
| Congress.gov API | Bill subject tags, full bill text | Yes (free) | Official; subject tags are human-assigned |
| MIT Election Lab | House election results by district (1976–present) | No | Harvard Dataverse, CC license |
| Cook Political Report | Cook PVI (district partisan lean) | Paywalled | GitHub aggregators exist |

---

## Security Notes

- `.env` is gitignored — never commit API keys
- `.claude/` is gitignored — Claude's local project memory
- `strawpoll.db` is gitignored — contains real user data
- All user emails are stored in plaintext — add bcrypt hashing before any real launch
- The API has no authentication yet — add JWT tokens before any public deployment
