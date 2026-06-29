"""
scrape_candidate_positions.py

HITL (human-in-the-loop) position extractor for 2026 Senate candidates.
Run manually — never automated. For each candidate, Claude fetches their
campaign website and maps their stated positions to the neutral taxonomy in
policy_taxonomy.json, then pauses for your review before writing to the DB.

─────────────────────────────────────────────────────────────────────────────
WHAT THIS FILE DOES
─────────────────────────────────────────────────────────────────────────────

  Step 1 — Find the candidate's Ballotpedia page.
    *** web_search — this is the ONE place web_search is used in this file ***
    Use Claude web_search to locate the correct Ballotpedia URL for this
    candidate (name + state + "2026 Senate"). NOT constructed programmatically
    — name collisions are common (e.g. Michael Collins) and must be verified.
    Store the confirmed URL in ballotpedia_url.

  Step 2 — Find the actual campaign website URL.
    *** web_fetch ***
    Claude web_fetch the Ballotpedia page and extract the official campaign
    website link from the infobox. Ballotpedia reliably maintains these.
    If Ballotpedia has no website listed: fall back to web_search for
    "{name} {state} 2026 senate official website" to find it directly.
    Store the confirmed URL in website_url.

  Step 3 — Extract raw stated positions from the campaign website.
    *** web_fetch ***
    Claude web_fetch the campaign website. web_fetch renders JavaScript —
    most campaign sites are React/Next.js and plain httpx gets an empty shell.
    Claude reads what is on the site and returns every stated policy position
    verbatim or as a close paraphrase, with no interpretation or inference.
    Output: a flat list of (issue_hint, raw_statement) pairs.
      e.g. [("healthcare", "I support expanding Medicaid to all working adults"),
            ("immigration", "I do not support amnesty")]
    This is raw material — not the final taxonomy-mapped output.

  Step 4 — Map raw positions to the approved taxonomy.
    Load policy_taxonomy.json → approved section.
    For each raw position, Claude finds the closest matching statement in
    the approved taxonomy for that issue category.

    FRAMING at mapping time (not extraction time):
      - If the raw position is a positive proposal → match to an "is for X" entry
      - If the raw position is purely a negative ("I do not support X") with no
        stated alternative → match to a "does not support X" entry if one exists,
        otherwise flag for taxonomy expansion
      - Never invent a positive framing for a purely negative statement

    If a candidate mentions an issue NOT in the approved taxonomy:
      → Pause this candidate
      → Trigger build_policy_taxonomy.py for the new issue (HITL session)
      → Once the new issue is approved and added to the taxonomy, resume mapping

  Step 5 — HITL review.
    Show the candidate's mapped positions one by one. For each:
      "{name} is for expanding Medicaid to all working adults."
      Source: "{raw statement from their website}"

      [a]ccept  [e]dit  [s]kip this position  [d]done reviewing candidate
    On [e]dit: user types their version inline, then re-prompts to confirm.
    At the end: show the full accepted set and ask to save to DB.

  Step 6 — Write to DB.
    On save: update positions, general_platform, ballotpedia_url, website_url,
    positions_source, positions_updated_at, needs_update=False.

─────────────────────────────────────────────────────────────────────────────
WHY web_fetch FOR ALMOST EVERYTHING
─────────────────────────────────────────────────────────────────────────────

  web_fetch runs in Anthropic's infrastructure and renders JavaScript before
  reading the page. This is essential because:
  - Most campaign websites in 2026 are JS-rendered (React, Next.js)
  - Plain httpx fetches the empty HTML shell before JS runs — you get nothing
  - seed_candidates.py used httpx and silently got blank pages on most sites
  - web_fetch solves this without any external dependency

  web_search is used ONLY in Step 1 (finding the Ballotpedia URL) and the
  Step 2 fallback (finding the campaign website directly). Everything else
  is web_fetch on a known URL.

─────────────────────────────────────────────────────────────────────────────
HOW POSITIONS ARE FRAMED (the two-rule system)
─────────────────────────────────────────────────────────────────────────────

  Rule 1 — "is for X" when the candidate states a positive policy proposal.
    "{Name} is for expanding Medicaid to all working adults."
    "{Name} is for a 15% flat income tax with no deductions."

  Rule 2 — "does not support X" when the candidate only states opposition
    with no stated alternative on their site.
    "{Name} does not support amnesty for undocumented immigrants."

  Never use "opposes" — adversarial framing, reads like an attack.
  Never invent a positive position when the candidate only stated a negative.

  These rules live in policy_taxonomy.json → _meta → framing_rules and are
  passed verbatim to Claude at mapping time.

─────────────────────────────────────────────────────────────────────────────
TAXONOMY INTEGRATION
─────────────────────────────────────────────────────────────────────────────

  This file reads from policy_taxonomy.json → approved.
  It never writes to the taxonomy — that is build_policy_taxonomy.py's job.

  When a new issue is detected during mapping:
    1. This script pauses and calls build_policy_taxonomy.py --issue "{new}"
    2. build_policy_taxonomy.py runs its HITL session to derive and approve
       the new category's position statements
    3. Once complete, this script resumes and maps the candidate
  This is the only time the two scripts interact.

─────────────────────────────────────────────────────────────────────────────
STACK
─────────────────────────────────────────────────────────────────────────────

  - Python 3.11+
  - anthropic SDK
      web_fetch — Steps 2, 3 (JS-rendered page rendering)
      web_search — Step 1 only (Ballotpedia URL discovery) + Step 2 fallback
  - Model: claude-opus-4-8 (reasoning depth needed for nuanced mapping)
  - Env vars required: ANTHROPIC_API_KEY

─────────────────────────────────────────────────────────────────────────────
USAGE
─────────────────────────────────────────────────────────────────────────────

  python scrape_candidate_positions.py                    # all candidates with needs_update=True
  python scrape_candidate_positions.py --state GA         # one state only
  python scrape_candidate_positions.py --name "Jon Ossoff" --state GA  # one candidate
  python scrape_candidate_positions.py --dry-run          # fetch and print, no DB writes

─────────────────────────────────────────────────────────────────────────────
NOTE ON SCHEDULING
─────────────────────────────────────────────────────────────────────────────

  This script is intentionally NOT automated. Positions shape how users
  perceive candidates — always run with HITL. Run it manually after a
  candidate updates their issues page, after a primary narrows the field
  to nominees, or when a candidate's ballotpedia_url or website_url is null.

─────────────────────────────────────────────────────────────────────────────
TODO — BUILD THIS FILE
─────────────────────────────────────────────────────────────────────────────
"""

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# MODEL = "claude-opus-4-8"
# DB_PATH = Path(__file__).parent / "strawpoll.db"
# TAXONOMY_PATH = Path(__file__).parent / "policy_taxonomy.json"


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — find the correct Ballotpedia URL  [web_search — the one exception]
# ─────────────────────────────────────────────────────────────────────────────

# def find_ballotpedia_url(name: str, state: str) -> str | None:
#     # web_search: "{name} {state} 2026 Senate site:ballotpedia.org"
#     # Verify it's the right person — name + state + 2026 Senate context
#     # Return confirmed URL or None if not found
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — extract campaign website URL from Ballotpedia  [web_fetch]
# ─────────────────────────────────────────────────────────────────────────────

# def find_campaign_website(ballotpedia_url: str, name: str, state: str) -> str | None:
#     # web_fetch the Ballotpedia page
#     # Extract "Campaign website" link from the infobox
#     # FALLBACK (web_search): if not listed, search "{name} {state} 2026 senate official website"
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — extract raw stated positions from the campaign website  [web_fetch]
# ─────────────────────────────────────────────────────────────────────────────

# def extract_raw_positions(website_url: str, name: str) -> list[dict]:
#     # web_fetch the campaign website (handles JS-rendered pages)
#     # Ask Claude: "List every policy position stated on this page.
#     #   For each, return {issue_hint: str, raw_statement: str}.
#     #   Only include explicitly stated positions. Do not infer."
#     # Returns list of {issue_hint, raw_statement} dicts
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — map raw positions to the approved taxonomy
# ─────────────────────────────────────────────────────────────────────────────

# def load_taxonomy() -> dict:
#     # Load policy_taxonomy.json → return approved section only
#     ...

# def map_to_taxonomy(raw_positions: list[dict], taxonomy: dict, name: str) -> dict:
#     # For each raw position:
#     #   1. Identify the taxonomy category (from issue_hint + Claude judgment)
#     #   2. If category NOT in taxonomy → call trigger_taxonomy_build(category)
#     #   3. Find the closest approved statement in taxonomy[category]
#     #   4. Apply framing rules:
#     #        positive proposal → "is for X" (match to approved "is for" entry)
#     #        purely negative   → "does not support X" (match or flag)
#     # Returns: {category: mapped_statement, ...}
#     ...

# def trigger_taxonomy_build(issue: str):
#     # Called when a new issue not in the taxonomy is encountered
#     # Pauses mapping, runs: python build_policy_taxonomy.py --issue "{issue}"
#     # Blocks until HITL session is complete and taxonomy is updated
#     # Resumes mapping with the newly approved category available
#     import subprocess
#     subprocess.run(["python", "build_policy_taxonomy.py", "--issue", issue], check=True)
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — HITL review loop
# ─────────────────────────────────────────────────────────────────────────────

# def review_mapped_positions(name: str, state: str, mapped: dict, raw: dict) -> dict | None:
#     # For each mapped position, show:
#     #   "{name} is for expanding Medicaid to all working adults."
#     #   Source: "{raw_statement from their website}"
#     #   [a]ccept  [e]dit  [s]kip this position  [d]done reviewing
#     # On [e]dit: collect inline replacement, re-prompt
#     # On [d]done: stop early with whatever is accepted so far
#     # At end: show full accepted set, ask "Save to DB? [y/n]"
#     # Returns accepted dict or None if user chose not to save
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — write approved positions to DB
# ─────────────────────────────────────────────────────────────────────────────

# def save_positions(candidate_id: int, positions: dict, general_platform: str,
#                    ballotpedia_url: str, website_url: str):
#     # UPDATE candidates SET positions=?, general_platform=?, ballotpedia_url=?,
#     #   website_url=?, positions_source='scrape_candidate_positions',
#     #   positions_updated_at=now(), needs_update=0
#     # WHERE id=?
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

# if __name__ == "__main__":
#     parser.add_argument("--state")
#     parser.add_argument("--name")
#     parser.add_argument("--dry-run", action="store_true")
#     # taxonomy = load_taxonomy()
#     # Query DB for candidates with needs_update=True (filtered by --state / --name)
#     # For each candidate:
#     #   find_ballotpedia_url → find_campaign_website → extract_raw_positions
#     #   → map_to_taxonomy (triggers taxonomy build if new issue found)
#     #   → review_mapped_positions → save_positions (if accepted and not dry-run)
