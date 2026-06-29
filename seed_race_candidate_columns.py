"""
seed_race_candidate_columns.py

Manages the candidates table from two sources:

HAPPY PATH — JSON input (primary source of truth)
  Reads a JSON file and upserts candidates into the DB.
  The JSON format is the standard interface — today it's a hand-curated file,
  tomorrow it can be the output of a web scraper. Same command, same format.

  python seed_race_candidate_columns.py --input candidates_2026.json
  python seed_race_candidate_columns.py --input candidates_2026.json --dry-run

FEC ENRICHMENT — supplementary only
  Queries the FEC API and enriches existing rows with fec_candidate_id and
  website_url. Does NOT insert new candidates or control who is in the table.
  Run this after the JSON seed to fill in FEC-specific fields.

  python seed_race_candidate_columns.py --fec
  python seed_race_candidate_columns.py --fec --dry-run
"""

import argparse   # lets us accept --input, --fec, and --dry-run flags
import json       # reads the candidate JSON file
import os         # reads the FEC API key from the environment
import sqlite3    # talks to the SQLite database directly
from pathlib import Path  # finds files relative to this script

from dotenv import load_dotenv

load_dotenv()

# Path to the database file — same folder as this script
DB_PATH = Path(__file__).parent / "strawpoll.db"

FEC_API_KEY = os.getenv("FEC_API_KEY", "")
FEC_BASE_URL = "https://api.open.fec.gov/v1"


# =============================================================================
# HAPPY PATH — JSON → DB
# =============================================================================

def seed_from_json(input_path: str, dry_run: bool = False):
    """
    Reads a JSON file and upserts candidates into the candidates table.
    Inserts anyone not already in the DB. Updates race columns for existing rows.
    JSON is the source of truth — FEC data is secondary.
    """
    data_file = Path(input_path)
    if not data_file.exists():
        print(f"Error: file not found — {input_path}")
        return

    with open(data_file) as f:
        races = json.load(f)

    print(f"Loaded {len(races)} races from {data_file.name}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    updated = 0
    inserted = 0

    for race in races:
        state        = race["state"]
        race_stage   = race["race_stage"]
        primary_date = race.get("primary_date")   # null if primary already passed
        is_special   = 1 if race.get("is_special") else 0

        for cand in race["candidates"]:
            name      = cand["name"]
            party     = cand["party"]
            incumbent = 1 if cand.get("incumbent") else 0

            # Check if this candidate already exists in the DB
            existing = c.execute(
                "SELECT id FROM candidates WHERE LOWER(name) = LOWER(?) AND state = ?",
                (name, state),
            ).fetchone()

            if existing:
                # Already in the DB — update their race columns
                print(f"  [UPDATE] {name} ({state}) → {race_stage}")
                if not dry_run:
                    c.execute(
                        """UPDATE candidates
                           SET race_stage = ?, primary_date = ?, is_special = ?, incumbent = ?
                           WHERE id = ?""",
                        (race_stage, primary_date, is_special, incumbent, existing["id"]),
                    )
                updated += 1
            else:
                # Not in the DB — insert them.
                # JSON is the source of truth, not FEC. FEC enriches later.
                print(f"  [INSERT] {name} ({party}, {state}) → {race_stage}")
                if not dry_run:
                    c.execute(
                        """INSERT INTO candidates
                               (name, party, state, election_year, office, incumbent,
                                race_stage, primary_date, is_special, needs_update, race_status,
                                positions)
                           VALUES (?, ?, ?, 2026, 'Senate', ?, ?, ?, ?, 1, 'declared', '{}')""",
                        (name, party, state, incumbent, race_stage, primary_date, is_special),
                    )
                inserted += 1

    if not dry_run:
        conn.commit()
    conn.close()

    label = "[DRY RUN] " if dry_run else ""
    print(f"\n{label}Done. Updated: {updated} | Inserted: {inserted}")


# =============================================================================
# FEC ENRICHMENT — fills in fec_candidate_id and website_url for existing rows
# =============================================================================

def enrich_from_fec(dry_run: bool = False):
    """
    Queries the FEC API for 2026 Senate candidates and enriches matching rows
    in the DB with fec_candidate_id and website_url.

    Does NOT insert new candidates. Does NOT overwrite race_stage or any columns
    set by the JSON seed. FEC is supplementary — it fills in FEC-specific fields only.
    """
    if not FEC_API_KEY:
        print("Error: FEC_API_KEY not set in .env")
        return

    import requests  # only needed for the FEC path

    print("Fetching 2026 Senate candidates from FEC API...")

    # Pull all 2026 Senate filers from FEC (paginated)
    fec_candidates = []
    page = 1
    while True:
        resp = requests.get(
            f"{FEC_BASE_URL}/candidates/",
            params={
                "api_key":       FEC_API_KEY,
                "election_year": 2026,
                "office":        "S",        # S = Senate
                "per_page":      100,
                "page":          page,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        fec_candidates.extend(data["results"])
        if page >= data["pagination"]["pages"]:
            break
        page += 1

    print(f"Found {len(fec_candidates)} FEC filers")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    enriched = 0
    skipped  = 0

    for fec_cand in fec_candidates:
        # FEC stores names as "LAST, FIRST" — normalize to "First Last"
        raw_name = fec_cand.get("name", "")
        if "," in raw_name:
            last, first = raw_name.split(",", 1)
            name = f"{first.strip().title()} {last.strip().title()}"
        else:
            name = raw_name.title()

        state          = fec_cand.get("state", "")
        fec_id         = fec_cand.get("candidate_id", "")
        website_url    = fec_cand.get("candidate_url", "") or None

        # Only update candidates already in the DB — FEC does not add new rows
        existing = c.execute(
            "SELECT id, fec_candidate_id, website_url FROM candidates WHERE LOWER(name) = LOWER(?) AND state = ?",
            (name, state),
        ).fetchone()

        if existing:
            print(f"  [ENRICH] {name} ({state}) — fec_id: {fec_id}")
            if not dry_run:
                c.execute(
                    """UPDATE candidates
                       SET fec_candidate_id = ?,
                           website_url = COALESCE(website_url, ?)
                       WHERE id = ?""",
                    # COALESCE keeps the existing website_url if one is already set
                    (fec_id, website_url, existing["id"]),
                )
            enriched += 1
        else:
            skipped += 1

    if not dry_run:
        conn.commit()
    conn.close()

    label = "[DRY RUN] " if dry_run else ""
    print(f"\n{label}Done. Enriched: {enriched} | No match: {skipped}")

# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed race columns into the candidates table")
    parser.add_argument("--input",   help="Path to a JSON data file (happy path)")
    parser.add_argument("--fec",     action="store_true", help="Enrich existing rows from the FEC API")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Preview without writing to DB")
    args = parser.parse_args()

    if args.input:
        seed_from_json(input_path=args.input, dry_run=args.dry_run)
    elif args.fec:
        enrich_from_fec(dry_run=args.dry_run)
    else:
        print("Specify --input <file.json> or --fec. Use --dry-run to preview.")
