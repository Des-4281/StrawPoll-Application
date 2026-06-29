"""
build_policy_taxonomy.py

Generates and refines entries in policy_taxonomy.json using Claude web_search
to discover the real range of political positions on any given issue, then
walks you through an interactive HITL session to approve, edit, or request
more alternatives before anything is written to the approved taxonomy.

─────────────────────────────────────────────────────────────────────────────
WHEN THIS RUNS
─────────────────────────────────────────────────────────────────────────────

  1. MANUALLY — seed the initial 22 categories before any candidate scraping.
     Run one category at a time, review each session, commit when satisfied.
     This is the recommended first-run workflow.

  2. AUTOMATICALLY — triggered by scrape_candidate_positions.py when Claude
     extracts a candidate position on an issue not yet in the approved taxonomy.
     The scraper pauses, this pipeline runs to HITL-approve the new category,
     then the scraper resumes and maps the candidate to the new entry.

─────────────────────────────────────────────────────────────────────────────
WHAT THIS FILE DOES
─────────────────────────────────────────────────────────────────────────────

  Step 1 — Find all available stated positions on the issue.
    Claude web_search with one open instruction: find every distinct stated
    position on this issue that exists in public political discourse. No
    source list, no political label, no nudge about where to look — any
    editorial direction about sources is itself a bias injection point.
    Claude surfaces whatever is out there. Each find must include the exact
    URL it came from (per-find, not per-run).

  Step 2 — Cluster into semantically distinct positions.
    Claude groups the discovered stances by what they actually propose, not
    by party label. N clusters emerges from the data — not a preset number.
    Two positions that say different things with the same party rhetoric are
    separate clusters. Two positions that say the same thing with different
    rhetoric are one cluster. All raw finds are preserved in full as
    raw_finds so the complete spectrum is visible before anything is filtered.

  Step 3 — Write one neutral "is for X" statement per cluster.
    Claude writes one plain-English sentence per cluster following the
    framing rules in policy_taxonomy.json:
      - "is for X" when the cluster represents a positive proposal
      - "does not support X" when the cluster is defined purely by opposition
        with no stated alternative
    Concrete and specific — no vague generalities like "is for a stronger economy."
    6th grade reading level. One sentence. The policy proposal, not the tribe.

    Each statement is paired with two fields:
      source       — URL of the most authoritative page in that cluster
      derived_from — the raw text the statement was synthesized from
    No source = not eligible for approval.

  Step 4 — Write raw finds to a local working file, then draft to taxonomy.
    Before the HITL session starts, raw_finds are written to a local working
    file: taxonomy_working/{issue}_raw.json (~750KB per category, ~16MB for
    all 22). This file is temporary — it exists only during the initial seeding
    run and can be deleted once all categories are approved.

    Why the working file:
      - Seeding 22 categories happens over multiple sessions, not one sitting.
        If the terminal crashes mid-HITL, the expensive discovery step doesn't
        need to re-run — the working file picks up where you left off.
      - The [m] more alternatives call reads raw_finds from the working file
        rather than re-sending the full payload through the API every time,
        which keeps token cost low for a long HITL session.

    The draft also goes to policy_taxonomy.json under _drafts:
      raw_finds: [{text, source}, ...]              — every find Claude surfaced
      positions: [{statement, source, derived_from}, ...]  — one per cluster
    Nothing goes to approved yet.

  Step 5 — HITL interactive review loop.
    Walk through each draft statement one at a time, showing raw source text
    and generated statement side by side:

      Statement 2 of 6:
      Raw:       "We need a government option to compete with private insurers..."
                 (source: https://...)
      Generated: "is for adding a public option alongside private insurance"
                 (source: https://...)

      [a] Accept    [e] Edit    [m] More alternatives    [s] Skip    [d] Done reviewing
      >

    [a] Accept — moves this {statement, source, derived_from} to approved list
    [e] Edit   — user types their version inline; source + derived_from preserved;
                 re-prompts to confirm
    [m] More   — reads raw_finds from working file, asks Claude for 3 alternative
                 phrasings of the same cluster; each with its own source +
                 derived_from; user picks one or edits; loops until resolved
    [s] Skip   — not added to approved list
    [d] Done   — ends session early with whatever is approved so far

    At end: show full approved list, ask "Save to taxonomy? [y/n]"
    On save: moves from _drafts["{issue}"] to approved["{issue}"].
    derived_from is kept in approved as a permanent audit trail.

─────────────────────────────────────────────────────────────────────────────
WORKING FILES (temporary, local only)
─────────────────────────────────────────────────────────────────────────────

  taxonomy_working/{issue}_raw.json — raw finds for one category (~750KB each)

  These files exist only during the initial seeding process. Once all 22
  categories are approved and in policy_taxonomy.json, delete the folder.
  They never go to the cloud — they are local session artifacts only.
  The folder is gitignored.

─────────────────────────────────────────────────────────────────────────────
STACK
─────────────────────────────────────────────────────────────────────────────

  - Python 3.11+
  - anthropic SDK — web_search server-side tool for discovering positions
  - Model: claude-opus-4-8 — needs reasoning to cluster nuanced policy stances
  - Env vars required: ANTHROPIC_API_KEY
  - Local working dir: taxonomy_working/ (gitignored, deleted after seeding)

─────────────────────────────────────────────────────────────────────────────
USAGE
─────────────────────────────────────────────────────────────────────────────

  python build_policy_taxonomy.py --issue "Healthcare"
  python build_policy_taxonomy.py --issue "AI Regulation"         # new issue
  python build_policy_taxonomy.py --issue "Healthcare" --refresh  # re-derive
  python build_policy_taxonomy.py --list   # show approved categories + counts

─────────────────────────────────────────────────────────────────────────────
TAXONOMY FILE
─────────────────────────────────────────────────────────────────────────────

  All reads and writes go to policy_taxonomy.json in the same directory.
  Structure:
    approved["{issue}"]  — live, reviewed positions used by the scraper
    _drafts["{issue}"]   — pending HITL review (not used by scraper)

  The scraper (scrape_candidate_positions.py) only reads from approved.
  This file reads from and writes to both sections.

─────────────────────────────────────────────────────────────────────────────
TODO — BUILD THIS FILE
─────────────────────────────────────────────────────────────────────────────
"""

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# MODEL = "claude-opus-4-8"
# TAXONOMY_PATH = Path(__file__).parent / "policy_taxonomy.json"
# WORKING_DIR   = Path(__file__).parent / "taxonomy_working"
# # taxonomy_working/ is gitignored — local session artifacts only, ~750KB per
# # issue category. Delete the folder once all 22 categories are approved.

# Framing rules passed verbatim to Claude at generation time so the output
# stays consistent across sessions and across different issues
# FRAMING_RULES = """
# - Write each position as "is for X" when the stance is a positive proposal.
# - Write "does not support X" only when a cluster is defined purely by
#   opposition with no stated alternative — do not invent a positive characterization.
# - Never use "opposes" — too adversarial.
# - 6th grade reading level. One sentence. Hemingway word economy.
# - Be specific: "is for a 15% flat income tax" not "is for tax reform."
# - Describe the policy proposal, not the political tribe or the opponent.
# """


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — find all available stated positions on the issue via web_search
# ─────────────────────────────────────────────────────────────────────────────

# def discover_positions(issue: str) -> list[dict]:
#     # Claude web_search — one open instruction, no source list, no political
#     # label, no nudge about where to look. Claude surfaces whatever exists.
#     # Each find must include the exact URL it came from.
#     # Returns: list of {text: str, source: str} — one entry per raw find
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2+3 — cluster raw finds and write one neutral statement per cluster
# ─────────────────────────────────────────────────────────────────────────────

# def generate_statements(issue: str, raw_finds: list[dict]) -> list[dict]:
#     # Send raw_finds (each with text + source) to Claude with FRAMING_RULES
#     # Claude:
#     #   1. Groups into semantically distinct clusters (N emerges from data)
#     #   2. Writes one "is for X" or "does not support X" statement per cluster
#     #   3. Attaches the most authoritative source URL from that cluster
#     #   4. Records the specific raw text the statement was synthesized from
#     # Returns: list of {statement: str, source: str, derived_from: str}
#     # Any entry missing a source URL is flagged — cannot be promoted to approved
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — write raw finds to working file, then draft to taxonomy
# ─────────────────────────────────────────────────────────────────────────────

# def save_working_file(issue: str, raw_finds: list[dict]):
#     # Write raw_finds to taxonomy_working/{issue}_raw.json
#     # Creates WORKING_DIR if it doesn't exist
#     # ~750KB per category — survives terminal crash, enables cheap [m] calls
#     ...

# def save_draft(issue: str, raw_finds: list[dict], positions: list[dict]):
#     # Call save_working_file() first
#     # Then load policy_taxonomy.json and write:
#     # taxonomy["_drafts"][issue] = {
#     #   derived_at: ...,
#     #   raw_finds:  [{text, source}, ...],
#     #   positions:  [{statement, source, derived_from}, ...]
#     # }
#     # Save taxonomy file — nothing goes to approved yet
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — HITL interactive review loop
# ─────────────────────────────────────────────────────────────────────────────

# def load_working_file(issue: str) -> list[dict]:
#     # Load raw_finds from taxonomy_working/{issue}_raw.json
#     # Used by request_alternatives() to avoid re-sending the full raw payload
#     ...

# def request_alternatives(entry: dict, issue: str) -> list[dict]:
#     # Called when user presses [m]
#     # Loads raw_finds from working file (cheap — reads local JSON, not API)
#     # Sends current {statement, source, derived_from} + relevant raw_finds
#     # to Claude, asks for 3 alternative phrasings of the same cluster
#     # Each alternative includes its own source + derived_from
#     # Returns: list of {statement, source, derived_from} alternatives
#     ...

# def review_session(issue: str) -> list[dict] | None:
#     # Load _drafts[issue] from taxonomy — has raw_finds and positions
#     # For each draft entry, show side by side:
#     #   Raw:       "{derived_from}" (source: url)
#     #   Generated: "{statement}"    (source: url)
#     # Prompt: [a]ccept [e]dit [m]ore alternatives [s]kip [d]done
#     #   [a] → add {statement, source, derived_from} to approved_list
#     #   [e] → collect inline statement edit; source + derived_from preserved
#     #   [m] → call request_alternatives(), show each with raw+generated side by side
#     #   [s] → skip
#     #   [d] → break early with approved_list so far
#     # At end: print full approved_list, ask "Save to taxonomy? [y/n]"
#     # On y: call promote_to_approved()
#     # Returns approved_list or None if user chose not to save
#     ...

# def promote_to_approved(issue: str, approved: list[dict]):
#     # Load policy_taxonomy.json
#     # approved[issue] = approved  (list of {statement, source, derived_from})
#     # derived_from is kept in approved as a permanent audit trail
#     # Remove issue from _drafts
#     # Save file
#     # Note: working file (taxonomy_working/{issue}_raw.json) can now be deleted
#     ...


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

# if __name__ == "__main__":
#     parser.add_argument("--issue", help="Issue category to generate or refresh")
#     parser.add_argument("--refresh", action="store_true",
#                         help="Re-derive an already-approved category (moves it back to draft first)")
#     parser.add_argument("--list", action="store_true",
#                         help="Show all approved categories and their position counts")
#
#     # --list: print approved categories + counts, exit
#     # --issue + --refresh: move existing approved entry back to _drafts, then run pipeline
#     # --issue (new): discover → generate → save_draft → review_session
#     # If working file already exists for this issue (resume after crash):
#     #   skip discover + generate, go straight to review_session
