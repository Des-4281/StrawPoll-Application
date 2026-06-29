"""
scrape_current_senate_campaigns.py

Weekly batch job — keeps the candidates table current on who is still in
each 2026 U.S. Senate race, what stage the race is in, and when the next
vote is scheduled.

─────────────────────────────────────────────────────────────────────────────
WHAT THIS FILE DOES
─────────────────────────────────────────────────────────────────────────────

  Step 1 — Identify which states have a 2026 Senate race.
    Query FEC for all 2026 Senate filers, derive the set of states that have
    at least one filer. Florida and Ohio are treated as specials within the
    same 2026 cycle. Any new special election that FEC hasn't grouped yet is
    caught by a Claude web_search cross-check (PROMPT_CONFIRM, below).

  Step 2 — Build the candidate spine for each state from FEC.
    Pull each candidate's name, party, FEC candidate ID, and
    incumbent/challenger/open status. This is the authoritative roster of
    who filed. FEC over-includes (it has primary losers, people who raised
    $5 and quit) — resolving that is Claude's job in Step 3.

  Step 3 — Resolve the volatile layer with Claude (two passes per state).
    PASS A — gather (open web_search):
      Claude receives the FEC spine and searches current news, state SoS
      sites, and Ballotpedia to determine: race stage, next vote date, and
      exactly who is still in. It filters the FEC list and flags any
      ballot-qualified independents FEC may have missed or oddly labeled.

    PASS B — verify (web_search restricted to trusted domains):
      Claude re-checks Pass A against: ballotpedia.org, the state's
      Secretary of State site, apnews.com, reuters.com, fec.gov.
      Returns a confidence score and a disagreements array. Any mismatch
      or low confidence trips `needs_review = True` on that race.

  Step 4 — Write results back to the candidates table.
    For each candidate still in the race: update race_stage, race_status,
    primary_date, is_special. Mark candidates who lost or withdrew as
    race_status = 'primary_loser' or 'withdrawn'. Never delete rows.

─────────────────────────────────────────────────────────────────────────────
WHY TWO PASSES?
─────────────────────────────────────────────────────────────────────────────

  The open pass gathers the full picture from wherever it is online.
  The verify pass is restricted to trusted structural sources that cover
  EVERY race, not just competitive ones — so a safe-seat candidate with
  zero national press coverage doesn't disappear from the output.

  Agreement between the two passes IS the accuracy signal. Disagreement is
  surfaced as `needs_review`, not silently resolved.

─────────────────────────────────────────────────────────────────────────────
KNOWN LIMITATION — INDEPENDENTS
─────────────────────────────────────────────────────────────────────────────

  Independents and minor-party candidates skip primaries and petition onto
  the general ballot later. FEC may not have them yet, or may label them
  under an unexpected committee name. PROMPT_A explicitly searches for them
  and adds them. Regression test: Dan Osborn (NE), Troy Bodnar (MT), and
  Marcus Pinkins (MS) must appear in output — if any goes missing, the
  independent path has broken.

─────────────────────────────────────────────────────────────────────────────
STACK
─────────────────────────────────────────────────────────────────────────────

  - Python 3.11+
  - anthropic SDK (Messages API with server-side web_search tool)
  - FEC open API  (https://api.open.fec.gov/v1/)
  - Model: claude-sonnet-4-6 (configurable constant — swap to claude-opus-4-8
    for higher accuracy at higher cost)
  - Env vars required: ANTHROPIC_API_KEY, FEC_API_KEY

─────────────────────────────────────────────────────────────────────────────
USAGE
─────────────────────────────────────────────────────────────────────────────

  python scrape_current_senate_campaigns.py              # all 35 states
  python scrape_current_senate_campaigns.py --state GA   # one state only
  python scrape_current_senate_campaigns.py --dry-run    # show what would change, write nothing

─────────────────────────────────────────────────────────────────────────────
SCHEDULING
─────────────────────────────────────────────────────────────────────────────

  Weekly is the default cadence — Sunday evening works well; most primary
  results have been certified by then. ~35 states × 2 Claude calls = ~70
  calls per run. At claude-sonnet-4-6 rates this is cheap.

  Daily is also supported and makes sense during election season (the two
  weeks around a primary, or any time a candidate withdrawal is expected).
  Use --state to narrow to just the active races rather than running all 35.

  Run manually for now; add to crontab once stable. The same script handles
  both cadences — just schedule it more frequently when the cycle demands it.

─────────────────────────────────────────────────────────────────────────────
DO NOT BULK-SCRAPE BALLOTPEDIA
─────────────────────────────────────────────────────────────────────────────

  Ballotpedia's terms restrict bulk scraping. This script NEVER calls
  Ballotpedia URLs directly. It is only ever read by the model through the
  web_search tool (Anthropic's infrastructure handles the fetch). FEC is
  the only feed this script pulls programmatically.

─────────────────────────────────────────────────────────────────────────────
RUNTIME PROMPTS — copy these verbatim into the code as string constants
─────────────────────────────────────────────────────────────────────────────

PROMPT_A (open web_search — gather + reconcile against FEC spine):

  You are an elections data resolver for one 2026 U.S. Senate race. You are
  given the FEC-filed candidate list for this race (the authoritative roster
  of who filed). Use web_search to determine the CURRENT state of the race,
  then return ONLY a JSON object (no prose, no code fences).

  Use the FEC list as your source of candidate identities. Do not invent
  names not on the FEC list, with ONE exception: ballot-qualified independent
  or minor-party candidates who petition directly onto the general ballot may
  be missing or oddly labeled in FEC data — search for these explicitly and
  add them.

  Decide which candidates are STILL IN, by phase:
  - Pre-primary (primary not yet held): keep the full declared field.
  - Runoff pending: keep only the two who advanced.
  - Post-primary / general (nominees set): keep one nominee per party.
  In every phase also keep independents/minor-party on the general ballot.
  DROP anyone who lost a concluded primary or withdrew.

  Return EXACTLY:
  {
    "state": "...", "seat_type": "regular|special",
    "stage": "pre_primary|runoff_pending|post_primary|general",
    "stage_headline": "...",
    "next_vote": {"type": "primary|runoff|general", "date": "YYYY-MM-DD",
                  "counting": false},
    "candidates": [
      {"name": "...", "party": "R|D|Ind|Libertarian|...",
       "incumbent": false, "status": "nominee|declared|runoff|write-in",
       "fec_id": "<id or null if petition independent>"}
    ],
    "sources": ["url", "..."],
    "as_of": "YYYY-MM-DD",
    "notes": "optional caveats"
  }
  Output the JSON object and nothing else.

  (Append to the user message: "FEC-filed candidates for {state} 2026 Senate:
  <json list of {name, party, fec_id, incumbent}>")


PROMPT_B (trusted-domain web_search — verify):

  You are verifying a draft result for one 2026 U.S. Senate race against
  trusted sources only. You are given the draft JSON from a first pass. Use
  web_search (restricted to trusted domains) to confirm: the stage, the next
  vote, and the still-in candidate list.

  Return ONLY:
  {
    "agrees": true|false,
    "confidence": 0.0-1.0,
    "disagreements": [
      {"field": "...", "draft": "...", "trusted": "...", "source": "url"}
    ],
    "corrected_candidates": [ ...same shape, ONLY if confident a correction
                               is needed; otherwise omit ],
    "sources": ["url", "..."]
  }
  Do NOT remove a candidate solely because trusted news didn't mention them
  — safe-seat candidates are often uncovered; lower confidence instead.
  Output the JSON object and nothing else.


TRUSTED_DOMAINS (for PROMPT_B's allowed_domains):
  ballotpedia.org, apnews.com, reuters.com, fec.gov
  + the relevant state's Secretary of State / Board of Elections domain

─────────────────────────────────────────────────────────────────────────────
TODO — BUILD THIS FILE
─────────────────────────────────────────────────────────────────────────────
"""

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS  (set these before building)
# ─────────────────────────────────────────────────────────────────────────────

# MODEL = "claude-sonnet-4-6"     # fast + cheap — good for 35-state weekly runs
# MODEL = "claude-opus-4-8"       # higher accuracy — use when results look off
# FEC_BASE_URL = "https://api.open.fec.gov/v1"
# DB_PATH = Path(__file__).parent / "strawpoll.db"
# TRUSTED_DOMAINS = ["ballotpedia.org", "apnews.com", "reuters.com", "fec.gov"]
# REGRESSION_INDEPENDENTS = [("Dan Osborn", "NE"), ("Troy Bodnar", "MT"), ("Marcus Pinkins", "MS")]


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — fetch states that have a 2026 Senate race from FEC
# ─────────────────────────────────────────────────────────────────────────────

# def fetch_fec_states() -> set[str]:
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — fetch the FEC candidate spine for one state
# ─────────────────────────────────────────────────────────────────────────────

# def fetch_fec_spine(state: str) -> list[dict]:
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3A — Claude gather pass (open web_search)
# ─────────────────────────────────────────────────────────────────────────────

# def gather_race(state: str, fec_spine: list[dict]) -> dict:
#     # Call Claude with web_search tool + PROMPT_A
#     # Returns the JSON result for one race
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3B — Claude verify pass (trusted-domain web_search)
# ─────────────────────────────────────────────────────────────────────────────

# def verify_race(state: str, draft: dict) -> dict:
#     # Call Claude with web_search tool restricted to TRUSTED_DOMAINS + PROMPT_B
#     # Returns confidence score + disagreements
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — write results back to the candidates table
# ─────────────────────────────────────────────────────────────────────────────

# def apply_race_result(state: str, gather: dict, verify: dict, dry_run: bool):
#     # UPDATE race_stage, race_status, primary_date, is_special for each candidate
#     # Mark dropped candidates as 'primary_loser' or 'withdrawn'
#     # Never DELETE rows
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# REGRESSION CHECK — independents must appear
# ─────────────────────────────────────────────────────────────────────────────

# def check_regression(all_results: list[dict]):
#     # Verify Dan Osborn (NE), Troy Bodnar (MT), Marcus Pinkins (MS) appear
#     # Log a warning if any are missing
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

# if __name__ == "__main__":
#     parser.add_argument("--state")
#     parser.add_argument("--dry-run", action="store_true")
#     # loop: fetch_fec_states → for each state: fetch_fec_spine → gather_race → verify_race → apply_race_result
#     # check_regression at the end
