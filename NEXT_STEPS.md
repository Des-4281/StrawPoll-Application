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

## Candidate Data Pipeline — Build These Next (In Order)

Three files exist with full descriptions and skeletons. Build in this order:

### A. `build_policy_taxonomy.py` + `policy_taxonomy.json` — build this first

**What it is:** The neutral position taxonomy is the foundation everything else
maps to. Without it, the position scraper has nothing to match against. Healthcare
is pre-seeded as a format reference. The other 21 categories need to be generated.

**First run — populate the empty categories one at a time:**
```bash
python build_policy_taxonomy.py --issue "Immigration"
python build_policy_taxonomy.py --issue "Economy"
# ... continue for each category
python build_policy_taxonomy.py --list   # see what's approved so far
```

**What happens each run:**
1. Claude web_search — one open instruction, no source list, no political label,
   no nudge. Claude finds every distinct stated position that exists in public
   political discourse on that issue. Any editorial direction about sources is a
   bias injection point, so we don't add one.
2. Positions are clustered by what they actually propose; N clusters comes from the data
3. Each cluster → one neutral statement + source URL + `derived_from` (the raw text)
4. Raw finds (~750KB) written to `taxonomy_working/{issue}_raw.json` before HITL starts
   — survives terminal crash, enables cheap [m] calls without re-fetching
5. HITL: each statement shown side by side with its raw source text
   → [a]ccept / [e]dit / [m]ore alternatives / [s]kip
6. On save → moves to `approved`; `derived_from` kept as permanent audit trail
7. Delete `taxonomy_working/` folder once all 22 categories are done

**Ongoing:** Also runs automatically when `scrape_candidate_positions.py` hits an
issue not yet in the taxonomy. The pipeline self-extends — no need to pre-seed every
possible edge case before starting candidate scraping.

---

### B. `scrape_current_senate_campaigns.py` — weekly/daily race status checker

**What it does:** Keeps the candidates table current on who is still in each
2026 Senate race, the race stage (pre-primary / post-primary / general), and
the next vote date. Runs weekly as a batch job.

**How it works (already documented in the file):**
1. Fetch all 2026 Senate filers from FEC → derive the set of states in play
2. For each state, build the FEC candidate spine (authoritative names, no hallucinations)
3. PASS A — Claude with open `web_search` tool: determine who is still in, stage, next vote
4. PASS B — Claude with `web_search` restricted to trusted domains (ballotpedia.org,
   state SoS, apnews.com, reuters.com, fec.gov): verify Pass A, return confidence score
5. Write results back to DB — update `race_stage`, `race_status`, `primary_date`, `is_special`

**Runtime prompts:** PROMPT_A (gather) and PROMPT_B (verify) are written verbatim in
the file — copy them as string constants, do not paraphrase.

**Flags to implement:** `--state GA` (single state), `--dry-run` (print, no DB writes)

**Regression test:** Dan Osborn (NE), Troy Bodnar (MT), Marcus Pinkins (MS) must
appear in output — these are the independents FEC may miss; if any disappear, the
independent path has broken.

**Do NOT bulk-scrape Ballotpedia** — only let the model access it via `web_search`.

---

### B. `scrape_candidate_positions.py` — HITL position extractor

**What it does:** For each candidate with `needs_update=True`, fetches their
campaign website and extracts stated positions mapped to our 22-category taxonomy.
Pauses for human review before writing anything to the DB. Run manually — never
automate this.

**How it works (already documented in the file):**
1. Claude `web_search` → find the correct Ballotpedia URL (name + state + "2026 Senate")
2. Claude `web_fetch` the Ballotpedia page → extract the campaign website URL
   - Fallback: `web_search` for "{name} {state} 2026 senate official website"
3. Claude `web_fetch` the campaign website → extract stated positions
   - Use `web_fetch` not httpx — most campaign sites are JS-rendered (React/Next.js);
     httpx gets an empty shell; `web_fetch` renders the page
   - Map only explicitly stated positions to the 22 categories — no inference
   - Returns: `{positions: {...}, general_platform: "...", confidence: 0.0-1.0}`
4. HITL review: print extracted positions → `[a]ccept / [s]kip / [e]dit`
5. On accept: write `positions`, `general_platform`, `ballotpedia_url`, `website_url`
   to DB; set `needs_update=False`

**Model:** `claude-opus-4-8` — position extraction needs reasoning depth.

**Flags to implement:** `--state GA`, `--name "Jon Ossoff" --state GA`, `--dry-run`

**TODO (after main path works):** DuckDuckGo + alternative model (Llama/Gemini) as
full fallback if Anthropic is unavailable. See fallback section in the file.

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

## Phase 6 — Infrastructure, Scale & Deployment

Everything below is post-product. Get the data pipeline and UI working first,
then tackle this phase. These are decisions, not just tasks — each one has a
right time to make it.

---

### Decide: Server vs. Serverless vs. Hybrid

**Recommendation: Hybrid serverless.**

| Component | Approach | Why |
|---|---|---|
| FastAPI API | Serverless (Lambda + Mangum, or Cloud Run) | Scales to zero, pay per request, no server to manage |
| Database | Managed PostgreSQL (Supabase free tier → AWS RDS) | SQLite cannot be shared across serverless instances — must migrate |
| Scheduled scrapers | Lambda on a schedule | Fits under 15-min limit per-state; or Cloud Run Jobs for no time limit |
| HITL scrapers (positions, taxonomy) | Run locally → write to cloud DB | Developer tools, not production services |
| Static assets | CloudFront CDN | Free at low scale, fast globally |

**The one non-negotiable:** SQLite → PostgreSQL before any cloud deployment.
SQLAlchemy already supports it — connection string change plus one driver swap.
Supabase has a generous free tier and works as a drop-in managed PostgreSQL host.

---

### Security Hardening

- **API key rotation** — move all keys (Anthropic, FEC, Congress.gov) to AWS Secrets
  Manager. Never in environment variables on a deployed server.
- **Rate limiting** — add per-user rate limits to the `/chat` endpoint before going
  public. Without it, one user can exhaust your Anthropic API budget.
- **Input validation** — the chat endpoint takes free-text user input sent to Claude.
  Sanitize it and constrain Claude with a system prompt before deployment.
- **HTTPS** — handled automatically by API Gateway (Lambda) or Cloud Run.
- **Dependency audit** — run `pip audit` before deployment to catch known CVEs.

---

### Multiple Users + Auth

The `users` table and basic user model already exist. What's missing:

- **Authentication middleware** — JWT tokens or session cookies on every protected
  endpoint. AWS Cognito is the path-of-least-resistance (free up to 50K monthly
  active users). Alternatively Auth0 free tier.
- **SSO** — Cognito supports Google OAuth, Apple Sign-In, and SAML out of the box.
  Apple Sign-In is required for App Store submission if the app offers any other
  third-party login.
- **Per-user rate limiting** — Redis (AWS ElastiCache). Each user gets a token
  bucket; the chat endpoint checks it before hitting Claude.

---

### Reliability — Target Uptime

| SLA | Downtime/year | What it takes |
|---|---|---|
| 99.9% | 8.77 hours | Single-region, Multi-AZ RDS + Lambda. Reasonable v1 target. |
| 99.99% | 52.6 minutes | Multi-AZ + CloudFront + Route 53 health checks. Achievable with standard AWS setup. |
| 99.999% | 5.26 minutes | Active-passive multi-region failover. Significant but buildable. |
| 99.9999% | 31.5 seconds | Active-active multi-region, chaos engineering, full observability stack. Ambitious — doable, but honest timeline is 6–18 months of dedicated infrastructure work after the product is stable. |

**How AWS achieves high availability without much configuration:**
- **Multi-AZ RDS** — automatic DB failover if a zone goes down; ~30 second failover.
- **Lambda** — automatically spans multiple AZs; no configuration needed.
- **CloudFront** — serves cached responses even if the API is briefly unavailable.
- **Route 53 health checks** — reroutes traffic if an endpoint goes unhealthy.

**Steps toward 99.9999% (six nines) when you're ready:**
1. Multi-AZ RDS with read replicas (handles DB failures)
2. Active-passive multi-region (us-east-1 primary, us-west-2 warm standby)
3. Global Accelerator for DNS-level failover between regions
4. Active-active multi-region with DynamoDB Global Tables (hardest — requires
   rethinking the data layer entirely; DynamoDB not PostgreSQL)
5. Chaos engineering — intentionally kill components and verify auto-recovery
6. Full observability: CloudWatch alarms, X-Ray tracing, PagerDuty on-call
7. SLA audit with a third-party uptime monitor

Start at 99.9% and build toward it. Six nines is a valid goal — just not the
first goal. Each additional nine is roughly 10x harder than the last.

---

### Scale Hardening — Handling Many Concurrent Users

The chatbot is the bottleneck. Each `/chat` request calls Claude and can take
5–30 seconds. Under load, this blocks everything.

- **Redis queue (AWS ElastiCache)** — chat requests go into a queue, a worker
  processes them, response is pushed back when ready. Users see a "thinking..."
  state instead of a timeout. Standard pattern for AI apps under load.
- **Cheaper model for chat** — Claude Haiku 4.5 ($1/M input, $5/M output) vs
  Opus ($5/$25). For most conversational queries about senators and bills, Haiku
  is sufficient. Reserve Opus for offline summarization tasks.
- **Response caching** — common questions can be cached in Redis. Same question
  from two users = one Claude call.
- **Lambda auto-scaling** — happens automatically with no configuration. If you
  use ECS/Fargate instead, set min=1, max=N and AWS handles the rest.

---

### AWS Migration Plan (do in order)

Each step is independently deployable — don't do them all at once:

1. **PostgreSQL migration** — provision Supabase (free) or RDS, run schema migration,
   update connection string, test locally against cloud DB first.
2. **Containerize** — add a `Dockerfile`, test locally with Docker.
3. **Deploy API** — Lambda + Mangum (simplest) or Cloud Run (no time limits).
   Set up API Gateway in front of Lambda.
4. **Add Cognito auth** — wire up `/users` to Cognito tokens.
5. **CloudFront** — CDN in front of the API.
6. **ElastiCache Redis** — add when the chat queue becomes necessary.
7. **Multi-AZ RDS** — enable when the DB becomes business-critical.
8. **Multi-region** — when targeting 99.999%+.

---

### Scheduled Runtime Actions (still to set up)

| Script | Cadence | Trigger |
|---|---|---|
| `scrape_current_senate_campaigns.py` | Weekly (daily during primary season) | Lambda + EventBridge schedule |
| `seed_db.py` → `tag_bills.py` → `summarize_bills.py` → `describe_bills.py` | Weekly | Lambda + EventBridge schedule |
| `seed_race_candidate_columns.py --fec` | Monthly | Lambda + EventBridge schedule |
| `scrape_candidate_positions.py` | Manual HITL only | Run locally |
| `build_policy_taxonomy.py` | Manual HITL only | Run locally |

Ask Claude Code: *"Set up EventBridge schedules for the weekly data refresh
pipeline and the campaign status scraper on AWS Lambda."*

---

### QA Testing & Load/DDoS Simulation

**QA Testing:**
- **Integration tests** — test every API endpoint against a real (test) database,
  not mocks. Catches the class of bugs where the code works but the DB query doesn't.
- **End-to-end tests** — simulate a real user session: create user → ask chat question
  → verify senator record loads → verify candidate positions render correctly.
  Playwright (the browser automation tool, not Anthropic's) is the standard tool.
- **Data integrity tests** — after each seed run, verify row counts, check for nulls
  in required fields, confirm no duplicate candidates or bills. Run these as a
  post-seed assertion script, not just manual spot-checks.
- **Regression tests** — the independent candidates (Dan Osborn NE, Troy Bodnar MT,
  Marcus Pinkins MS) must always appear in campaign scraper output. These are already
  noted in `scrape_current_senate_campaigns.py` as regression markers.

**Load Testing & DDoS Simulation:**
- **Locust** — open-source Python load testing tool. Define user behavior scripts
  (hit `/chat`, browse senator records, load candidate pages) and simulate hundreds
  of concurrent users. Tells you exactly where the app breaks under load before
  real users find it.
- **AWS WAF (Web Application Firewall)** — attach to API Gateway to block DDoS,
  SQL injection, and known bot patterns automatically. Has a free tier for basic
  rules; managed rule groups cost extra.
- **AWS Shield Standard** — automatically included with API Gateway and CloudFront
  at no extra cost. Protects against common network/transport layer DDoS attacks.
- **AWS Shield Advanced** — paid ($3K/month), but includes 24/7 DDoS response team
  and cost protection if a DDoS attack spikes your AWS bill. Consider when the app
  has real traffic and political relevance (election season = higher target profile).
- **Rate limiting simulation** — before launch, test what happens when a single IP
  sends 1,000 requests/minute to `/chat`. Verify the rate limiter blocks it cleanly
  without taking down the whole API.

Ask Claude Code when ready: *"Set up Locust load tests for the core API endpoints
and add AWS WAF rules to the API Gateway."*

---

### Code Hardening & Refactoring (before AWS migration)

- **Alembic migrations** — replace raw `ALTER TABLE` with Alembic so schema
  changes are tracked, versioned, and reversible. Required for production.
- **Error handling** — currently most endpoints will 500 on unexpected input.
  Add proper try/catch with meaningful HTTP status codes.
- **Test coverage** — integration tests for `/chat` and senator record endpoints
  at minimum before deployment.
- **Structured logging** — JSON logs (not print statements) so AWS CloudWatch
  can parse and alert on errors automatically.

---

### Apple App Store

Three paths, in order of effort:

1. **PWA (Progressive Web App)** — installable via Safari on iOS with no App Store
   review, zero extra development. "Add to Home Screen" works today. Limited native
   features but ships immediately. Good interim step.
2. **React Native** — shares logic with the web frontend, native feel,
   App Store approved. Requires a React Native developer or learning it.
3. **Swift native** — best performance, most work. Only if iOS-first is the goal.

Apple Sign-In is mandatory if the app offers any other third-party login.
App Store review takes 1–7 days. The developer account ($99/year) takes time
to activate — start it early.

**Recommended path:** Make the web app a PWA first (hours of work), then
build React Native once the product is stable.

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
