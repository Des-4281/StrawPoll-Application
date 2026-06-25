"""
Seed the database with @unitedstates bulk data + LegiScan bill metadata.

Sources:
  Legislators  : github.com/unitedstates/congress-legislators  (legislators-current.json)
  Votes        : theunitedstates.io  (per-vote JSON files)
  Bill metadata: LegiScan API  (title + brief description, no full text stored)

Usage:
  python seed_db.py                          # 119th Congress (current), all votes
  python seed_db.py --congress 119 118       # current + last Congress
  python seed_db.py --legislators-only       # just refresh member list
  python seed_db.py --max-votes 50           # quick test run (first 50 votes only)

LegiScan has a monthly request limit on free accounts (~30k/month).
The seeder caches bill lookups so each unique bill is fetched only once.
"""

import asyncio
import argparse
import logging
from typing import Any

import httpx
from sqlalchemy import select
# SQLite upsert — same API as the PostgreSQL version.
# When switching to PostgreSQL, change this to:
#   from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as pg_insert

from database import AsyncSessionLocal, init_db
from models import Bill, District, Politician, Vote
from services import legiscan_service

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

LEGISLATORS_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
VOTES_INDEX_URL = "https://theunitedstates.io/congress/{congress}/votes/{session}/index.json"
VOTE_DETAIL_URL = (
    "https://theunitedstates.io/congress/{congress}/votes/{session}/{vote_id}/data.json"
)

POSITION_MAP = {
    "Yea": "Yea",
    "Aye": "Yea",
    "Nay": "Nay",
    "No": "Nay",
    "Present": "Present",
    "Not Voting": "Not Voting",
}


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

async def fetch_json(client: httpx.AsyncClient, url: str) -> Any:
    try:
        r = await client.get(url, timeout=30.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        log.warning("HTTP %s — %s", e.response.status_code, url)
        return None
    except Exception as e:
        log.warning("Fetch failed (%s): %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Legislators
# ---------------------------------------------------------------------------

def _parse_legislator(raw: dict) -> dict | None:
    try:
        ids = raw["id"]
        bioguide_id = ids["bioguide"]
        name_obj = raw["name"]
        full_name = name_obj.get("official_full") or (
            f"{name_obj.get('first', '')} {name_obj.get('last', '')}".strip()
        )
        terms = raw.get("terms", [])
        if not terms:
            return None
        latest = terms[-1]
        return {
            "bioguide_id": bioguide_id,
            "name": full_name,
            "party": latest.get("party", "Unknown"),
            "state": latest.get("state", ""),
            "lis_member_id": ids.get("lis"),  # used to match Senate.gov vote XML
        }
    except KeyError:
        return None


async def seed_legislators(db) -> int:
    log.info("Fetching legislators-current.json …")
    async with httpx.AsyncClient() as client:
        raw_list = await fetch_json(client, LEGISLATORS_URL)

    if not raw_list:
        log.error("Could not fetch legislators data.")
        return 0

    records = [r for raw in raw_list if (r := _parse_legislator(raw))]
    log.info("Parsed %d legislators", len(records))

    stmt = pg_insert(Politician).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["bioguide_id"],
        set_={
            "name": stmt.excluded.name,
            "party": stmt.excluded.party,
            "state": stmt.excluded.state,
            "lis_member_id": stmt.excluded.lis_member_id,
        },
    )
    await db.execute(stmt)
    await db.commit()
    log.info("Upserted %d politicians.", len(records))
    return len(records)


# ---------------------------------------------------------------------------
# Bill + Vote seeding
# ---------------------------------------------------------------------------

def _build_bill_number(data: dict) -> tuple[str, str, str | int, int]:
    """
    Return (bill_key, bill_type_raw, bill_num_raw, congress) from a vote file.
    bill_key is our PK string, e.g. "S1234-119".
    """
    info = data.get("bill", {})
    bill_type = info.get("type", "").upper()
    bill_num = info.get("number", "")
    congress = info.get("congress", 0)

    if bill_type and bill_num:
        key = f"{bill_type}{bill_num}-{congress}" if congress else f"{bill_type}{bill_num}"
    else:
        key = data.get("vote_id", data.get("category", "unknown"))
        bill_type = ""
        bill_num = ""

    return key, bill_type, bill_num, congress


def _parse_vote_rows(data: dict, bill_number: str) -> list[dict]:
    rows = []
    for position_label, voters in data.get("votes", {}).items():
        canonical = POSITION_MAP.get(position_label, position_label)
        for voter in voters:
            bioguide_id = voter.get("id") if isinstance(voter, dict) else None
            if bioguide_id:
                rows.append({"bioguide_id": bioguide_id, "bill_number": bill_number, "position": canonical})
    return rows


CHAMBER_PREFIX = {"senate": "s", "house": "h", "both": ""}


async def _fetch_vote_index(
    client: httpx.AsyncClient, congress: int, session: int, chamber: str
) -> list[str]:
    url = VOTES_INDEX_URL.format(congress=congress, session=session)
    index = await fetch_json(client, url)
    if not index:
        return []
    prefix = CHAMBER_PREFIX.get(chamber, "")
    return [
        e["vote_id"]
        for e in index
        if "vote_id" in e and (not prefix or e["vote_id"].startswith(prefix))
    ]


# ---------------------------------------------------------------------------
# Senate.gov XML vote seeder — used for 119th Congress and newer.
# theunitedstates.io does not yet publish 119th Congress data, so we fall back
# to the official Senate.gov XML feed which updates in real time.
# ---------------------------------------------------------------------------

SENATE_GOV_INDEX_URL = (
    "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml"
)
SENATE_GOV_VOTE_URL = (
    "https://www.senate.gov/legislative/LIS/roll_call_votes"
    "/vote{congress}{session}/vote_{congress}_{session}_{vote_num:05d}.xml"
)


def _extract_bill_number_from_senate_gov(question_text: str, congress: int) -> str:
    """
    Pull a bill number out of the Senate.gov vote question text and normalize
    it to our PK format, e.g. 'S5-119'.  Returns a fallback string if no
    recognizable bill number is found.
    """
    import re
    # Match patterns like "S. 5", "H.R. 1234", "S.J.Res. 7", "H.Con.Res.45"
    pattern = r'\b(H\.R\.|S\.|H\.J\.Res\.|S\.J\.Res\.|H\.Con\.Res\.|S\.Con\.Res\.|H\.Res\.|S\.Res\.)\s*(\d+)'
    match = re.search(pattern, question_text, re.IGNORECASE)
    if match:
        prefix = match.group(1).replace('.', '').replace(' ', '').upper()
        number = match.group(2)
        return f"{prefix}{number}-{congress}"
    return f"PROC-{question_text[:30].strip()}-{congress}"


async def seed_votes_from_senate_gov(db, congress: int, max_votes: int) -> int:
    """
    Fetch Senate roll-call votes from the official Senate.gov XML feed.
    Used for congresses where theunitedstates.io data is not yet available (119+).
    Matches senators to our politicians table via their LIS member ID.
    """
    import xml.etree.ElementTree as ET

    # Build a lookup table: lis_member_id → bioguide_id
    result = await db.execute(
        select(Politician.bioguide_id, Politician.lis_member_id).where(
            Politician.lis_member_id.isnot(None)
        )
    )
    lis_to_bioguide = {row.lis_member_id: row.bioguide_id for row in result.all()}

    if not lis_to_bioguide:
        log.warning("No LIS member IDs in DB — seed legislators first.")
        return 0

    seen_bills: set[str] = set()
    total_vote_rows = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for session in (1, 2):
            # Fetch the vote index for this session
            index_url = SENATE_GOV_INDEX_URL.format(congress=congress, session=session)
            try:
                r = await client.get(index_url)
                r.raise_for_status()
                index_root = ET.fromstring(r.text)
            except Exception as e:
                log.info("No Senate.gov index for Congress %d session %d: %s", congress, session, e)
                continue

            vote_entries = index_root.findall(".//vote")
            if not vote_entries:
                log.info("No votes in index for Congress %d session %d", congress, session)
                continue

            log.info(
                "Congress %d session %d [Senate.gov] — %d votes, fetching up to %d",
                congress, session, len(vote_entries), max_votes,
            )

            fetched = 0
            for entry in vote_entries:
                if fetched >= max_votes:
                    break

                vote_num_str = (entry.findtext("vote_number") or "").strip()
                if not vote_num_str:
                    continue
                try:
                    vote_num = int(vote_num_str)
                except ValueError:
                    continue

                vote_url = SENATE_GOV_VOTE_URL.format(
                    congress=congress, session=session, vote_num=vote_num
                )
                try:
                    vr = await client.get(vote_url)
                    vr.raise_for_status()
                    vote_root = ET.fromstring(vr.text)
                except Exception as e:
                    log.warning("Could not fetch vote %d: %s", vote_num, e)
                    fetched += 1
                    continue

                question = vote_root.findtext("vote_question_text") or ""
                description = vote_root.findtext("vote_document_text") or ""
                result_text = vote_root.findtext("vote_result_text") or ""
                bill_number = _extract_bill_number_from_senate_gov(question, congress)

                # Upsert the Bill row
                if bill_number not in seen_bills:
                    bill_record = {
                        "bill_number": bill_number,
                        "title": (description or question)[:500],
                        "summary": None,
                        "status": result_text[:100],
                        "congress": congress,
                        "chamber": "Senate",
                    }

                    # Enrich with LegiScan title if we have a real bill number
                    if not bill_number.startswith("PROC-") and legiscan_service.api_key:
                        # Strip the congress suffix for the LegiScan search, e.g. "S5-119" → "S5"
                        raw_bill_num = bill_number.rsplit("-", 1)[0]
                        meta = await legiscan_service.search_bill_metadata(
                            client, bill_number_raw=raw_bill_num, congress=congress
                        )
                        if meta:
                            bill_record["title"] = meta.get("title") or bill_record["title"]
                            bill_record["summary"] = meta.get("summary")
                            bill_record["status"] = meta.get("status") or bill_record["status"]

                    b_stmt = pg_insert(Bill).values([bill_record])
                    b_stmt = b_stmt.on_conflict_do_update(
                        index_elements=["bill_number"],
                        set_={
                            "title": b_stmt.excluded.title,
                            "summary": b_stmt.excluded.summary,
                            "status": b_stmt.excluded.status,
                        },
                    )
                    await db.execute(b_stmt)
                    seen_bills.add(bill_number)

                # Upsert Vote rows
                rows = []
                for member in vote_root.findall(".//member"):
                    lis_id = (member.findtext("lis_member_id") or "").strip()
                    vote_cast = (member.findtext("vote_cast") or "").strip()
                    bioguide_id = lis_to_bioguide.get(lis_id)
                    if bioguide_id and vote_cast:
                        canonical = POSITION_MAP.get(vote_cast, vote_cast)
                        rows.append({
                            "bioguide_id": bioguide_id,
                            "bill_number": bill_number,
                            "position": canonical,
                        })

                if rows:
                    v_stmt = pg_insert(Vote).values(rows)
                    v_stmt = v_stmt.on_conflict_do_nothing()
                    await db.execute(v_stmt)
                    total_vote_rows += len(rows)

                fetched += 1

                if fetched % 50 == 0:
                    await db.commit()
                    log.info(
                        "  … %d votes fetched | %d unique bills | %d vote rows",
                        fetched, len(seen_bills), total_vote_rows,
                    )

            await db.commit()
            log.info(
                "Congress %d session %d done — %d vote rows total",
                congress, session, total_vote_rows,
            )

    return total_vote_rows


async def seed_votes(db, congress: int, max_votes: int, chamber: str = "senate") -> int:
    # 119th Congress and newer: theunitedstates.io data isn't published yet —
    # use the official Senate.gov XML feed instead (Senate only).
    if congress >= 119:
        if chamber == "house":
            log.warning(
                "House vote data via Senate.gov is not supported. "
                "Skipping House for Congress %d.", congress
            )
            return 0
        log.info("Congress %d: using Senate.gov XML feed (theunitedstates.io not yet available)", congress)
        return await seed_votes_from_senate_gov(db, congress=congress, max_votes=max_votes)

    result = await db.execute(select(Politician.bioguide_id))
    known_ids: set[str] = {row[0] for row in result.all()}
    if not known_ids:
        log.warning("No politicians in DB — run without --votes-only first.")
        return 0

    if not legiscan_service.api_key:
        log.warning(
            "LEGISCAN_API_KEY not set — bill titles/descriptions will not be fetched. "
            "Vote positions will still be seeded."
        )

    # In-process cache: bill_number → already upserted, skip LegiScan call
    seen_bills: set[str] = set()
    total_vote_rows = 0

    async with httpx.AsyncClient() as client:
        for session in (1, 2):
            vote_ids = await _fetch_vote_index(client, congress, session, chamber)
            if not vote_ids:
                log.info("No vote index for Congress %d session %d (%s)", congress, session, chamber)
                continue

            log.info(
                "Congress %d session %d [%s] — %d votes available, fetching up to %d",
                congress, session, chamber, len(vote_ids), max_votes,
            )

            fetched = 0
            for vote_id in vote_ids:
                if fetched >= max_votes:
                    break

                url = VOTE_DETAIL_URL.format(congress=congress, session=session, vote_id=vote_id)
                data = await fetch_json(client, url)
                if not data:
                    fetched += 1
                    continue

                bill_key, bill_type, bill_num, bill_congress = _build_bill_number(data)
                chamber = "House" if vote_id.startswith("h") else "Senate"

                # --- Upsert Bill row ---
                if bill_key not in seen_bills:
                    bill_record: dict = {
                        "bill_number": bill_key,
                        # The vote file's top-level "question" field is a short description
                        # e.g. "On Passage of the Bill" — use it as a fallback title
                        "title": (data.get("description") or data.get("question") or "")[:500],
                        "summary": None,
                        "status": None,
                        "congress": congress,
                        "chamber": chamber,
                    }

                    # Enrich from LegiScan if we have a real bill number
                    if bill_type and bill_num and legiscan_service.api_key:
                        meta = await legiscan_service.search_bill_metadata(
                            client,
                            bill_number_raw=f"{bill_type}{bill_num}",
                            congress=bill_congress or congress,
                        )
                        if meta:
                            bill_record["title"] = meta.get("title") or bill_record["title"]
                            bill_record["summary"] = meta.get("summary")
                            bill_record["status"] = meta.get("status")

                    stmt = pg_insert(Bill).values([bill_record])
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["bill_number"],
                        set_={
                            "title": stmt.excluded.title,
                            "summary": stmt.excluded.summary,
                            "status": stmt.excluded.status,
                        },
                    )
                    await db.execute(stmt)
                    seen_bills.add(bill_key)

                # --- Upsert Vote rows ---
                rows = [r for r in _parse_vote_rows(data, bill_key) if r["bioguide_id"] in known_ids]
                if rows:
                    vote_stmt = pg_insert(Vote).values(rows)
                    vote_stmt = vote_stmt.on_conflict_do_nothing()
                    await db.execute(vote_stmt)
                    total_vote_rows += len(rows)

                fetched += 1

                if fetched % 50 == 0:
                    await db.commit()
                    log.info(
                        "  … %d vote files processed | %d unique bills | %d vote rows",
                        fetched, len(seen_bills), total_vote_rows,
                    )

            await db.commit()
            log.info(
                "Congress %d session %d done — %d vote rows total",
                congress, session, total_vote_rows,
            )

    return total_vote_rows


# ---------------------------------------------------------------------------
# Districts — House districts only
#
# WHY THIS EXISTS
# ---------------
# True district-level polling barely exists. What we store instead are
# reliable proxies for constituent sentiment that cover every district:
#
#   - Cook PVI: best single-number measure of partisan lean
#   - Election results: actual vote margins from the MIT Election Lab
#
# See models.py District class for the full data-source catalog and
# column-level comments on what to add next.
#
# IMPLEMENTED HERE
# ----------------
# MIT Election Data and Science Lab — free CC-licensed CSV of every
# House general election result back to 1976. We use it to populate
# last_dem_pct, last_rep_pct, last_margin, last_election_year, and
# to link the current representative's bioguide_id to their district.
#
# Download URL (update year as new data is released):
#   https://dataverse.harvard.edu/api/access/datafile/:persistentId
#   ?persistentId=doi:10.7910/DVN/IG0UN2/QQWN7H
# The file is named "1976-2022-house.tab" (tab-separated).
# Replace QQWN7H with the latest file ID from the Harvard Dataverse page.
#
# NOT YET IMPLEMENTED — add these when you have the data or an API key:
#
#   Cook PVI
#     Paywalled at cookpolitical.com. Free alternatives:
#       - Wikipedia "Cook Partisan Voter Index" table (scrapeable)
#       - github.com/jeffreymorganio/d3-country-bubble-chart (historical CSVs)
#     When you have it: call seed_cook_pvi(db, csv_path) below and populate
#     cook_pvi + pvi_score columns on District rows.
#
#   FiveThirtyEight Partisan Lean
#     Free CSV: github.com/fivethirtyeight/data/tree/master/partisan-lean
#     More current than Cook between elections. Negative = D, Positive = R.
#     When you have it: call seed_fte_lean(db, csv_path) and add a
#     fte_partisan_lean Float column to the District model.
#
#   OpenSecrets Campaign Finance
#     API: opensecrets.org/api (free tier, 200 req/day)
#     Endpoints: getCandSummary (total raised), getIndustries (top donors)
#     Best stored in a separate CampaignFinance table linked to politicians.
#
#   Census / ACS Demographics
#     api.census.gov/data/2022/acs/acs5 — free, no key required for most
#     endpoints. Variables: B19013_001E (median income), B15003 (education),
#     B01003_001E (population). Join on GEOID which maps to district FIPS codes.
#     Add columns to District: median_income, pct_college_degree, pct_urban.
#
# ---------------------------------------------------------------------------
# ADD YOUR OWN DATA SOURCES HERE
# ---------------------------------------------------------------------------
# Copy the pattern below to add a new district-level data source:
#
#   async def seed_<source_name>(db, <args>) -> int:
#       """
#       Brief description of the source and why it's useful.
#       Source URL / API docs: ...
#       How to get an API key (if needed): ...
#       """
#       records = []
#       # ... fetch and parse data ...
#       stmt = pg_insert(District).values(records)
#       stmt = stmt.on_conflict_do_update(
#           index_elements=["district_id"],
#           set_={<only the columns this source owns>},
#       )
#       await db.execute(stmt)
#       await db.commit()
#       return len(records)
#
# Each source should own only its own columns in the on_conflict_do_update
# so sources don't overwrite each other's data.
# ---------------------------------------------------------------------------


MIT_ELECTION_LAB_URL = (
    # Harvard Dataverse direct download for the 1976-2022 House results CSV.
    # Check dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/IG0UN2
    # for the latest file ID when a new election cycle is added.
    "https://dataverse.harvard.edu/api/access/datafile/:persistentId"
    "?persistentId=doi:10.7910/DVN/IG0UN2/QQWN7H"
)


def _parse_pvi(pvi_str: str) -> float | None:
    """
    Convert a Cook PVI string to a signed float.
    "R+5" → +5.0, "D+8" → -8.0, "EVEN" → 0.0
    Returns None if the string can't be parsed.
    """
    if not pvi_str:
        return None
    s = pvi_str.strip().upper()
    if s in ("EVEN", "0"):
        return 0.0
    try:
        if s.startswith("R+"):
            return float(s[2:])
        if s.startswith("D+"):
            return -float(s[2:])
    except ValueError:
        pass
    return None


def _district_id(state: str, district_num: int | str) -> str:
    """Build canonical district ID, e.g. 'TX-07' or 'AK-AT'."""
    num = str(district_num).zfill(2)
    if num == "00":
        num = "AT"
    return f"{state.upper()}-{num}"


async def seed_districts_from_mit_election_lab(db) -> int:
    """
    Populate the districts table with election results from the MIT Election
    Data and Science Lab. This gives us last_dem_pct, last_rep_pct,
    last_margin, last_election_year, and links each district to its current
    representative via bioguide_id from the politicians table.

    We take only the most recent election result per district (max year).
    """
    import csv
    import io

    log.info("Fetching MIT Election Lab House results …")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            r = await client.get(MIT_ELECTION_LAB_URL)
            r.raise_for_status()
            raw_text = r.text
        except Exception as e:
            log.error("Could not fetch MIT Election Lab data: %s", e)
            log.error(
                "Download manually from: "
                "dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/IG0UN2"
                " and pass the file path to seed_districts_from_csv(db, path) instead."
            )
            return 0

    return await _ingest_mit_csv(db, raw_text)


async def seed_districts_from_csv(db, file_path: str) -> int:
    """
    Ingest the MIT Election Lab CSV from a local file.
    Use this if the direct download fails (the Harvard Dataverse URL changes
    when new election cycles are added).

      python seed_db.py --districts-csv ./1976-2022-house.tab
    """
    import io
    log.info("Reading MIT Election Lab CSV from %s …", file_path)
    with open(file_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
    return await _ingest_mit_csv(db, raw_text)


async def _ingest_mit_csv(db, raw_text: str) -> int:
    import csv
    import io

    # The file is tab-separated. Columns include:
    #   year, state_po, district, candidate, party, candidatevotes, totalvotes, ...
    reader = csv.DictReader(io.StringIO(raw_text), delimiter="\t")

    # Aggregate by district: track best Democrat + Republican result per year
    # Structure: {district_id: {year: {dem_pct, rep_pct}}}
    by_district: dict[str, dict] = {}

    for row in reader:
        try:
            state = row.get("state_po", "").strip().upper()
            district_num = int(row.get("district", 0))
            year = int(row.get("year", 0))
            party = (row.get("party", "") or "").strip().upper()
            candidate_votes = int(row.get("candidatevotes", 0) or 0)
            total_votes = int(row.get("totalvotes", 1) or 1)
        except (ValueError, TypeError):
            continue

        if not state or year < 2010:
            continue

        district_id = _district_id(state, district_num)
        pct = round(candidate_votes / total_votes * 100, 2) if total_votes else 0.0

        entry = by_district.setdefault(district_id, {})
        year_entry = entry.setdefault(year, {"dem_pct": 0.0, "rep_pct": 0.0, "state": state, "district_num": district_num})

        if "DEMOCRAT" in party or party == "DEM":
            year_entry["dem_pct"] = max(year_entry["dem_pct"], pct)
        elif "REPUBLICAN" in party or party == "REP":
            year_entry["rep_pct"] = max(year_entry["rep_pct"], pct)

    if not by_district:
        log.warning("No district data parsed — check CSV format.")
        return 0

    # Build one record per district using the most recent year's results
    records = []
    for district_id, years in by_district.items():
        latest_year = max(years.keys())
        yr = years[latest_year]
        dem = yr["dem_pct"]
        rep = yr["rep_pct"]
        margin = round(rep - dem, 2)
        records.append({
            "district_id": district_id,
            "state": yr["state"],
            "district_number": yr["district_num"] if yr["district_num"] != 0 else None,
            "last_dem_pct": dem,
            "last_rep_pct": rep,
            "last_margin": margin,
            "last_election_year": latest_year,
            # cook_pvi, pvi_score, representative_bioguide_id left null —
            # populated by separate seed functions when that data is available.
        })

    log.info("Upserting %d districts …", len(records))
    stmt = pg_insert(District).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["district_id"],
        set_={
            "last_dem_pct": stmt.excluded.last_dem_pct,
            "last_rep_pct": stmt.excluded.last_rep_pct,
            "last_margin": stmt.excluded.last_margin,
            "last_election_year": stmt.excluded.last_election_year,
        },
    )
    await db.execute(stmt)
    await db.commit()
    log.info("Districts seeded: %d", len(records))
    return len(records)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    await init_db()

    async with AsyncSessionLocal() as db:
        if not args.votes_only:
            n = await seed_legislators(db)
            log.info("Legislators seeded: %d", n)

        if not args.legislators_only:
            for congress in args.congress:
                log.info("=== Seeding Congress %d [%s] ===", congress, args.chamber)
                n = await seed_votes(db, congress=congress, max_votes=args.max_votes, chamber=args.chamber)
                log.info("Vote rows inserted for %dth Congress: %d", congress, n)

        if args.districts:
            if args.districts_csv:
                n = await seed_districts_from_csv(db, args.districts_csv)
            else:
                n = await seed_districts_from_mit_election_lab(db)
            log.info("Districts seeded: %d", n)

    log.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed DB from @unitedstates + LegiScan")
    parser.add_argument(
        "--congress",
        type=int,
        nargs="+",
        default=[119],
        help="Congress number(s) to fetch (default: 119 = current). Example: --congress 119 118",
    )
    parser.add_argument(
        "--max-votes",
        type=int,
        default=9999,
        dest="max_votes",
        help="Max vote files per session per Congress (default: 9999 = all). "
             "Set lower (e.g. 50) only for quick test runs.",
    )
    parser.add_argument(
        "--legislators-only",
        action="store_true",
        dest="legislators_only",
        help="Only refresh the legislators table",
    )
    parser.add_argument(
        "--votes-only",
        action="store_true",
        dest="votes_only",
        help="Only seed votes (legislators must already be in DB)",
    )
    parser.add_argument(
        "--chamber",
        choices=["senate", "house", "both"],
        default="senate",
        help="Which chamber's votes to seed (default: senate)",
    )
    parser.add_argument(
        "--districts",
        action="store_true",
        help="Seed House district election results from MIT Election Lab",
    )
    parser.add_argument(
        "--districts-csv",
        dest="districts_csv",
        default=None,
        metavar="FILE",
        help=(
            "Path to a local MIT Election Lab .tab file instead of downloading it. "
            "Use this if the Harvard Dataverse URL has changed. "
            "Download from: dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/IG0UN2"
        ),
    )
    args = parser.parse_args()
    asyncio.run(main(args))
