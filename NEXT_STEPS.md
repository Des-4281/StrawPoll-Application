# Next Steps — What You Need to Do

This is your personal action list. In order of priority.

---

## Every Time You Commit

The post-commit hook runs automatically and logs everything to BUILD_LOG.md and STORY.md for free. After it runs, you'll see this in your terminal:

```
→ Logged commit abc1234 to BUILD_LOG.md
→ STORY.md updated
  (Optional: run 'python update_docs.py' to update docs with Claude)
```

**If you want to update the docs (optional, costs ~$0.30 in tokens):**
```bash
python update_docs.py
```
Run this after a batch of related commits — not after every single one. It rewrites ARCHITECTURE.md and STORY.md to reflect everything in BUILD_LOG.md, and flags anything inconsistent between the docs and the code.

---

## Right Now (Before Anything Else)

### 1. Install the SQLite Viewer extension
Open VS Code → Extensions (⌘⇧X) → search **SQLite Viewer** → install the one by **Florian Klampfer**.
Then right-click `strawpoll.db` → Open With → SQLite Viewer to browse your data.

### 2. ✅ FEC API key — Done
You have a real key in `.env` (`FEC_API_KEY`). The full candidate seed is running now.

**To check race status weekly:**
```bash
python seed_candidates.py --check-status   # re-checks who is still in the race
```
This checks FEC's inactive flag and scans campaign websites for withdrawal language. Run it weekly during campaign season to keep `race_status` current.

### 3. Push the latest commits to GitHub
```bash
git push
```

### 5. Test the chat endpoint with a real question
The server needs to be running first:
```bash
source venv/bin/activate
uvicorn main:app --reload --port 8001
```
Then in a new terminal, create a user and ask a question:
```bash
# Create a user
curl -X POST http://localhost:8001/users \
  -H "Content-Type: application/json" \
  -d '{"email": "test@test.com"}'

# Ask the AI (replace user_id with the id returned above)
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "message": "How did Bernie Sanders vote on military defense bills?"}'
```

### 6. Try a bill summary to verify it works
```bash
python summarize_bills.py --limit 3
```
This will fetch 3 bills from Congress.gov, extract a plain-English summary including hidden provisions, and save it to the database. Takes about 2-3 minutes.

---

## This Week

### 7. Decide: run full bill summarization now or later?
Running `python summarize_bills.py` on all 116 real bills will:
- Make ~116 calls to Congress.gov (free, but some bills may have no text yet)
- Make ~116 Claude API calls (each costs about $0.01-0.03 at Opus 4.8 rates)
- Take roughly 20-40 minutes
- Store a structured plain-English summary for every bill, including hidden provisions

You don't have to do this now — summaries are generated on demand. But doing it in one batch means the AI chat can immediately reference bill summaries without any delay.

### 8. Run `python update_docs.py` after your next batch of changes
Once you've made a few more commits, run this to have Claude synthesize BUILD_LOG.md into updated narrative entries in STORY.md and ARCHITECTURE.md.
```bash
python update_docs.py
```

---

## Phase 2 — The Scoring System (Build This Next)

This is the most important next feature. Without it, you have a database of votes but no way to answer "how aligned is this senator with their constituents?"

### What needs to be built:

**Step A — Add `yea_action` to bills table**
For each real bill, store a one-sentence description of what a Yea vote concretely did. Example: `"funded $50B for Medicaid expansion"`. Claude generates this from the bill title/summary. This is the neutral, factual description of what senators voted FOR.

Ask Claude Code: *"Build a script called score_bills.py that adds a yea_action column to the bills table and uses Claude to generate a one-sentence factual description of what a Yea vote on each bill actually did in concrete policy terms."*

**Step B — Build the senator record view**
For each senator × each issue category: list all the bills they voted on in that category, what their vote meant in concrete terms, and a plain-English summary of their record.

Ask Claude Code: *"Add a GET /senator/{bioguide_id}/record endpoint that returns a senator's voting record broken down by issue category, showing what each vote concretely did and a plain-English summary per category."*

**Step C — Add a score weighting system**
Bills count more than Resolutions. Omnibus bills need special handling. Ask Claude Code to add this once Step B is working.

---

## Phase 3 — Polling Data (After Phase 2)

You need state-level polling broken down by issue. The best sources:

- **YouGov MRP estimates** — They publish state-level opinion data by issue. Not a free API; you'd need to find published datasets or contact them.
- **Pew Research** — Has state-by-state breakdowns on many issues. Published as reports; you'd extract the data manually or find a dataset aggregator.
- **FiveThirtyEight polling averages** — They publish CSVs on GitHub for presidential approval and some issues. Free and bulk-downloadable.

When you're ready to tackle this, ask Claude Code: *"I need to build a StatePolling table and import mechanism for state-level polling data by issue category. Help me find the best free data source and build the import."*

---

## Things to Keep in Mind

- **`.env` is never committed** — your API keys are safe. If you ever need to set up the project on a new machine, copy from `.env.example` and re-enter your keys.
- **`strawpoll.db` is gitignored** — the database doesn't get pushed to GitHub. Anyone who clones the repo needs to run `seed_db.py` + `tag_bills.py` to rebuild it.
- **The post-commit hook lives in `.git/hooks/`** — this folder is NOT pushed to GitHub. If someone else clones the repo they won't have the hook. You'd need to re-run the hook setup or add a setup script.
- **Congress.gov API rate limit** — free tier is ~1,000 requests/day. `tag_bills.py` used about 500. `summarize_bills.py` uses ~116. You're fine.
- **Tokens for update_docs.py** — each run costs roughly $0.50-1.00 in Claude API tokens. Run it intentionally, not constantly.

---

## When to Ask Claude Code for Help

You can continue this exact conversation and Claude Code will have full context. Or start a new session — Claude Code will re-read the project files to rebuild context.

Good prompts to use:
- *"Build the yea_action classification system for bills"*
- *"Add the senator voting record endpoint"*
- *"Help me find and import state-level polling data"*
- *"I want to add [new feature] — where does it fit in the architecture?"*
