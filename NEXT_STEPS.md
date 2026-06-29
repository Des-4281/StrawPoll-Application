# Next Steps — What You Need to Do

This is your personal action list. In order of priority.

---

## Right Now (Before Anything Else)

### 1. Install the SQLite Viewer extension
Open VS Code → Extensions (⌘⇧X) → search **SQLite Viewer** → install the one by **Florian Klampfer**.
Then right-click `strawpoll.db` → Open With → SQLite Viewer to browse your data.

### 2. ✅ FEC API key — Done
You have a real key in `.env` (`FEC_API_KEY`). All 273 2026 Senate filers are now in the DB.

**To update who is actually in the general election:** AKA Look up whos still running! 
The DB has everyone who raised money and filed with FEC — that includes primary losers.
After each state's primary, update the winners and losers manually:

```python
# Run this in a Python shell after primary results are in
import sqlite3, datetime
conn = sqlite3.connect('strawpoll.db')
c = conn.cursor()
now = datetime.datetime.now(datetime.timezone.utc).isoformat()

# Mark the general election nominees (replace with actual names)
winners = [
    ('Jon Ossoff', 'GA'),       # Example: GA Democratic nominee
    ('Brian Kemp', 'GA'),       # Example: GA Republican nominee
    # Add all 33 states' nominees here
]
for name, state in winners:
    c.execute("UPDATE candidates SET race_status='primary_winner', race_status_updated_at=? WHERE name=? AND state=?", (now, name, state))

# Mark primary losers
c.execute("""
    UPDATE candidates SET race_status='primary_loser', race_status_updated_at=?
    WHERE race_status='declared' AND state IN (
        'GA'  -- add states where primary is done
    ) AND name NOT IN (SELECT name FROM candidates WHERE race_status='primary_winner')
""", (now,))

conn.commit()
conn.close()
```

**33 states have 2026 Senate races** (Class 2 seats — last elected in 2020):
AK, AL, AR, CO, DE, GA, IA, ID, IL, KS, KY, LA, MA, ME, MI, MN, MS, MT, NC, NE, NH, NJ, NM, OK, OR, RI, SC, SD, TN, TX, VA, WV, WY

**To check race status weekly for withdrawal/suspension:**
```bash
python seed_candidates.py --check-status   # scans campaign websites for withdrawal language
```

### 3. Build a URL scraper for candidates (future task)
209 candidates have bad or missing URLs from FEC — all-caps domains, double-scheme URLs, stale links. Running `--refresh` would re-use the same bad FEC data, so it's not worth doing.

The real fix is a scraper that finds each candidate's actual current campaign website: search their name + state + "2026 senate campaign" and validate the result. Ask Claude Code: *"Build a script that finds and validates current campaign website URLs for candidates where `needs_update=True`, using web search and updating `website_url` in the DB."*

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

## Phase 3 — UI (After Phase 2)

### Bill page with copilot sidebar
Each bill gets a page that displays the full bill text fetched live from Congress.gov via `bill_text_url` (no storage on our end). A chat panel sits alongside the text so users can ask questions about what they're reading. The chat uses `ai_summary` as context for most questions; for deeper questions it can fetch the full text on demand.

- `bill_text_url` is already stored on the bills table — the UI just fetches it
- The sidebar chat reuses the existing `/chat` endpoint with the bill's `ai_summary` injected as system context
- Keep users in the app rather than linking out to Congress.gov — the integrated reading + chat experience is the product

---

## Phase 4 — Polling Data (After Phase 3)

You need state-level polling broken down by issue. The best sources:

- **YouGov MRP estimates** — They publish state-level opinion data by issue. Not a free API; you'd need to find published datasets or contact them.
- **Pew Research** — Has state-by-state breakdowns on many issues. Published as reports; you'd extract the data manually or find a dataset aggregator.
- **FiveThirtyEight polling averages** — They publish CSVs on GitHub for presidential approval and some issues. Free and bulk-downloadable.

When you're ready to tackle this, ask Claude Code: *"I need to build a StatePolling table and import mechanism for state-level polling data by issue category. Help me find the best free data source and build the import."*

---

## Phase 5 — Expanding Legislative Coverage

The current system tracks Senate floor votes on bills. These are real, binding actions — but they're only part of what senators do. Add these in order of value:

### Sponsored & co-sponsored bills that never got a floor vote
A senator co-sponsoring a bill is a public position even without a vote. If Senator X co-sponsored a healthcare bill that died in committee, that's trackable. Congress.gov API has full co-sponsorship records for every introduced bill.
- Add a `Cosponsorship` table: `senator_bioguide_id`, `bill_number`, `is_primary_sponsor`
- Source: `GET /bill/{congress}/{type}/{number}/cosponsors` on Congress.gov API
- Ask Claude Code: *"Add co-sponsorship data from Congress.gov to track senator positions on bills that never got a floor vote."*

### House-passed bills that came to the Senate
Bills passed by the House and sent to the Senate are in Congress.gov. Some die without a Senate vote. Tracking these shows what the Senate chose not to act on — which is itself a position.
- Source: Congress.gov API, filter by `latestAction` showing "Received in the Senate"
- Ask Claude Code: *"Seed House-passed bills that reached the Senate but never got a floor vote."*

### Confirmation votes (nominations)
Some of the most significant Senate votes — Supreme Court justices, cabinet members, federal judges, ambassadors. These are in Senate.gov roll call XML but use Presidential Nomination format (PN123-119) instead of bill numbers, so the current seeder skips them. High priority.
- Ask Claude Code: *"Add confirmation vote tracking — seed PN-format votes from Senate.gov and build a Nomination model with nominee name, position, and confirmation outcome."*

### Amendment votes
During bill consideration, senators vote on specific amendments. A senator might vote Yea on a final bill but Nay on a key amendment — revealing their position on a single provision. Already partially present in the DB (one "Amendment Agreed to" vote exists). Valuable for complex bills like the NDAA and BBB.
- Ask Claude Code: *"Expand amendment vote tracking — link amendment votes to their parent bill and surface them on senator record pages."*

### Treaty ratifications
Less frequent but constitutionally significant — the Senate votes to ratify international trade agreements, arms control treaties, and diplomatic agreements. Same format issue as nominations.
- Ask Claude Code: *"Add treaty ratification votes to the tracking system."*

### Senate committee votes
Some bills die in committee without ever reaching the full Senate floor. Committee votes are published by each Senate committee individually — no single API covers all of them. Complex to collect but valuable for showing where bills are killed before the public sees them.

### Interactive Committee Explorer
Each Senate committee has a defined jurisdiction, a membership roster, and a record of what bills they advanced or killed. This is a major transparency feature — most voters have no idea what committees their senators sit on or what power that gives them.

**What to build:**
- A `Committee` table: name, jurisdiction description, type (standing, select, joint)
- A `CommitteeMembership` table: senator + committee + role (chair, ranking member, member)
- A `CommitteeAction` table: bill + committee + action (referred, advanced, tabled, hearing held)
- UI: clickable committee cards explaining what each committee controls in plain English
- Drill down: who sits on it, what bills they've acted on, whether those bills survived to a floor vote

**Data sources:**
- Senate.gov publishes committee membership rosters (senate.gov/committees)
- Congress.gov API has committee referral data per bill (`/bill/{congress}/{type}/{number}/committees`)
- Ask Claude Code: *"Build the Committee explorer — seed committee membership from Senate.gov and link bills to committees via Congress.gov API."*

### Resolutions — visibility actions vs. real actions
Resolutions (S.Res, H.Res, S.Con.Res) are already in the DB but excluded from summarization. They matter because they reveal how senators signal positions without making binding law — symbolic statements, commemorations, procedural positioning.

When you add resolutions back:
- Tag them separately as "Symbolic" or "Procedural" in the UI so users understand a resolution vote is a visibility action, not a law being made
- Surface them on senator record pages with clear labeling: "This was a non-binding resolution"
- Use them to show the full picture of a senator's public positioning, not just their legislative record

---

## Things to Keep in Mind

- **`.env` is never committed** — your API keys are safe. If you ever need to set up the project on a new machine, copy from `.env.example` and re-enter your keys.
- **`strawpoll.db` is gitignored** — the database doesn't get pushed to GitHub. Anyone who clones the repo needs to run `seed_db.py` + `tag_bills.py` to rebuild it.
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

---

## Keeping the Data Fresh (Ongoing)

The 119th Congress is still active — new votes happen weekly. The pipeline needs to be re-run periodically to stay current.

**Full refresh order:**
```bash
python seed_db.py              # pulls new votes + bills from Senate.gov
python tag_bills.py            # tags any new bills
python summarize_bills.py      # summarizes new bills (skips already-done ones)
python describe_bills.py       # generates descriptions for new bills
```

**How to trigger it:**

- **Calendar reminder** — simplest. Set a weekly or bi-weekly reminder to run the above sequence. Takes ~10-30 min depending on how many new votes came in.
- **Cron job** — add to your Mac's crontab to run automatically (e.g. every Sunday night). Ask Claude Code: *"Set up a cron job to run the data refresh pipeline weekly."*
- **Smart trigger** — have `seed_db.py` write the count of new bills/votes it inserted, and only run the downstream scripts if the count > 0. Most efficient, slightly more work to build.

**When the 120th Congress starts (Jan 2027):**
Update the congress number in `seed_db.py` and re-run everything from scratch for the new session.

---

## End of Build Session: Sync the Docs

No post-commit hook runs anymore — git itself is the audit log. When you're done committing for the day, run this once to have Claude read the full git history and update ARCHITECTURE.md:

```bash
python update_docs.py
```

**What it does:**
- Reads every commit (hash, date, message, file stats) directly from `git log`
- Reads the current source files to catch anything the commit messages missed
- Rewrites ARCHITECTURE.md with all changes reflected and roadmap checkboxes updated
- Prints any discrepancies it finds between the docs and the actual code

**When to run it:**
- After a batch of related commits (feature complete, bug fixed, seed run finished)
- Before starting a new Claude Code session, so the docs are current
- Any time you want ARCHITECTURE.md to catch up

**Cost:** ~$0.50–1.00 per run in Claude API tokens. Run it intentionally — once per session, not after every commit.
