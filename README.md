# StrawPoll Voting App

A tool for tracking how US Senators vote — and eventually, comparing those votes to what people in their state actually want.

---

## Start Here

**New to this project? Read in this order:**

1. **[PROBLEM.md](PROBLEM.md)** — What problem does this solve, in plain English. Start here before anything else.
2. **[STORY.md](STORY.md)** — How it was built, step by step. Decisions made, problems hit, and why things work the way they do.
3. **[ARCHITECTURE.md](ARCHITECTURE.md)** — The technical reference. Database schema, data sources, API endpoints, setup instructions.
4. **[NEXT_STEPS.md](NEXT_STEPS.md)** — What needs to happen next and in what order.

---

## What It Does Right Now

- Stores every Senate vote from the 119th Congress (54,960 individual votes)
- Categorizes every bill into one of 22 issue areas (healthcare, immigration, climate, etc.)
- Lets users ask natural language questions via an AI chat interface
- Summarizes bill text — including hidden provisions, earmarks, and fine print
- Logs every code change automatically

**What it doesn't do yet:** Compare voting records to state polling data. That's Phase 3.

---

## Quick Setup

```bash
# Clone and set up
git clone https://github.com/Des-4281/StrawPoll-Application
cd "StrawPoll Application"
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Copy API keys template
cp .env.example .env
# Edit .env — add your Anthropic, LegiScan, and Congress.gov keys

# Build the database
python seed_db.py      # imports all Senate votes (~10-15 min)
python tag_bills.py    # tags bills by issue category

# Start the server
uvicorn main:app --reload --port 8001
# API docs: http://localhost:8001/docs
```

Full setup guide with all options: [ARCHITECTURE.md — Environment Setup](ARCHITECTURE.md#environment-setup)

---

## Viewing the Database

Install the **SQLite Viewer** extension in VS Code:
- Open VS Code → Extensions (⌘⇧X) → search `SQLite Viewer` → install the one by **Florian Klampfer**
- Right-click `strawpoll.db` in the file explorer → Open With → SQLite Viewer

Or download **DB Browser for SQLite** (free desktop app): [sqlitebrowser.org](https://sqlitebrowser.org)

---

## File Guide

| File | What it is |
|---|---|
| `main.py` | The web server and AI chat endpoint |
| `models.py` | Database table definitions |
| `seed_db.py` | Imports politicians and votes from Senate.gov |
| `tag_bills.py` | Tags bills with issue categories |
| `summarize_bills.py` | Fetches bill text and extracts plain-English summary |
| `update_docs.py` | Updates ARCHITECTURE.md using Claude (run manually) |
| `BUILD_LOG.md` | Auto-written after every git commit |

---

## Current Status

**Phase 1 (Data Foundation) — Complete**
- 537 politicians, 532 bills, 54,960 votes in the database
- All 116 real bills tagged with issue categories
- AI chat working

**Phase 2 (Scoring) — Next**
Build per-senator voting records by issue category, described factually and without political framing.

**Phase 3 (Polling Comparison) — Planned**
Compare senator voting records to state polling data on the same issues.
