"""
Seed 2026 Senate candidates using the FEC API, then extract their stated
positions from their campaign websites using Claude.

Why FEC as the data source:
  - Official federal government source — every candidate who legally files is here
  - Free JSON API, no scraping, returns clean structured data
  - Has candidate name, state, party, incumbency status
  - Committee records include the campaign website URL
  - More reliable than any third-party aggregator

Why Claude for position extraction:
  - Campaign websites have wildly different layouts; Claude handles that naturally
  - We need positions mapped to our exact 22 categories — one Claude call does that
  - Claude flags when a position is inferred vs. explicitly stated
  - Neutral framing: "supports X" / "opposes Y" — no spin, no value judgment

Data flow per candidate:
  1. FEC /candidates → name, state, party, incumbency
  2. FEC /candidate/{id}/committees → campaign website URL
  3. httpx fetch of campaign website
  4. Claude: extract stated positions, map to 22 categories
  5. Save to candidates table

Rate limits:
  - DEMO_KEY: 60 req/hour (fine for testing, too slow for full 300+ candidate run)
  - Real key: 1000 req/hour — get one free at https://api.data.gov/signup/
  - Add FEC_API_KEY to your .env file

Usage:
  python seed_candidates.py                     # all funded D/R 2026 Senate candidates
  python seed_candidates.py --state GA          # one state only
  python seed_candidates.py --limit 10          # first 10 candidates (for testing)
  python seed_candidates.py --dry-run           # list candidates without fetching positions
  python seed_candidates.py --refresh           # re-fetch positions for existing rows
  python seed_candidates.py --all-parties       # include third-party candidates too
  python seed_candidates.py --check-status      # re-check who is still in the race
  python seed_candidates.py --check-status --state GA   # check one state only
"""

import asyncio
import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import anthropic
import httpx
from dotenv import load_dotenv
from sqlalchemy import select, text

from database import AsyncSessionLocal, engine, Base
from models import Candidate, Politician

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

FEC_API_KEY = os.getenv("FEC_API_KEY", "DEMO_KEY")
FEC_BASE = "https://api.open.fec.gov/v1"
claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

ISSUE_CATEGORIES = [
    "Economy & Taxes", "Government Budget & Spending", "Elections & Campaign Finance",
    "Civil Rights", "LGBTQ+ Rights", "Healthcare", "Military & Defense",
    "Foreign Policy & International Affairs", "Immigration", "Border Security",
    "Environmental Policy", "Climate Policy", "Gun Policy", "Criminal Justice",
    "Policing & Law Enforcement", "Education", "Social Safety Net", "Housing",
    "Drug Policy", "Labor & Workers Rights", "Technology & Privacy", "US Territory Policy",
]


# ---------------------------------------------------------------------------
# FEC API helpers
# ---------------------------------------------------------------------------

async def fetch_all_candidates(
    client: httpx.AsyncClient,
    filter_state: str | None = None,
    all_parties: bool = False,
    limit: int | None = None,
) -> list[dict]:
    """
    Page through the FEC candidates endpoint and return all 2026 Senate candidates
    who have raised funds (signals a serious campaign, filters out placeholder filings).
    """
    params = {
        "election_year": 2026,
        "office": "S",
        "has_raised_funds": True,
        "per_page": 100,
        "api_key": FEC_API_KEY,
        "sort": "name",
    }
    if not all_parties:
        params["party"] = ["DEM", "REP"]
    if filter_state:
        params["state"] = filter_state.upper()

    all_results = []
    page = 1

    while True:
        params["page"] = page
        try:
            r = await client.get(f"{FEC_BASE}/candidates/", params=params, timeout=20.0)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.error("FEC candidates fetch failed (page %d): %s", page, e)
            break

        results = data.get("results", [])
        all_results.extend(results)

        pagination = data.get("pagination", {})
        if page >= pagination.get("pages", 1):
            break
        page += 1

        if limit and len(all_results) >= limit:
            all_results = all_results[:limit]
            break

        # Respect FEC rate limits — small delay between pages
        await asyncio.sleep(0.5)

    return all_results


async def fetch_candidate_website(
    client: httpx.AsyncClient, candidate_id: str
) -> str | None:
    """
    Fetch the candidate's campaign committees and return their website URL.
    Tries all committee types (not just principal) since many campaigns don't
    set the designation field correctly when they file.
    Retries once on 429 with a longer delay.
    """
    for attempt in range(2):
        try:
            r = await client.get(
                f"{FEC_BASE}/candidate/{candidate_id}/committees/",
                params={"per_page": 10, "api_key": FEC_API_KEY},
                timeout=15.0,
            )
            if r.status_code == 429:
                wait = 12 if attempt == 0 else 30
                log.info("  FEC rate limit hit — waiting %ds...", wait)
                await asyncio.sleep(wait)
                continue
            r.raise_for_status()
            for committee in r.json().get("results", []):
                website = committee.get("website")
                if website:
                    website = website.strip()
                    if not website.startswith("http"):
                        website = "https://" + website
                    return website.rstrip("/")
            return None
        except Exception as e:
            log.debug("Committee fetch failed for %s: %s", candidate_id, e)
            return None
    return None


async def fetch_website_text(client: httpx.AsyncClient, url: str) -> str | None:
    """
    Fetch a candidate's campaign website and return clean text.
    Strips HTML tags and trims to 15,000 chars — enough for position extraction
    without blowing the token budget.
    """
    try:
        r = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research bot)"},
            timeout=15.0,
            follow_redirects=True,
        )
        if r.status_code >= 400:
            return None

        raw = r.text
        # Strip HTML tags
        raw = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
        raw = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()
        return raw[:15000]
    except Exception as e:
        log.debug("Website fetch failed for %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Claude position extraction
# ---------------------------------------------------------------------------

def extract_positions_from_website(
    website_text: str, name: str, state: str, party: str
) -> tuple[dict, str]:
    """
    Ask Claude to extract a candidate's stated positions from their campaign website
    and map them to our 22 issue categories.

    Returns: (positions_dict, source_description)
    positions_dict: {"Healthcare": "Supports universal coverage", ...}
    Only includes categories with clearly stated positions — no guessing.
    """
    categories_str = "\n".join(f"- {c}" for c in ISSUE_CATEGORIES)

    prompt = f"""You are reading the campaign website of {name}, a {party} candidate for US Senate in {state} in 2026.

Extract their STATED policy positions and map each to one of our 22 issue categories.

STRICT RULES:
- Only include a category if the candidate explicitly states a position on it
- Use neutral factual language: "Supports X", "Opposes Y", "Calls for Z"
- Do NOT infer positions from their party affiliation
- Do NOT include vague statements like "supports working families" — only specific policy positions
- One sentence per category

Our 22 categories (use these exact names):
{categories_str}

Campaign website text:
{website_text}

Return a JSON object:
{{
  "positions": {{
    "Healthcare": "Supports allowing Medicare to negotiate drug prices",
    "Economy & Taxes": "Opposes any increase in the corporate tax rate",
    ...
  }},
  "source_note": "Positions drawn from Issues page of campaign website"
}}

Only include categories with clear stated positions. Return ONLY the JSON object.
"""
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {}, "no positions found"
        data = json.loads(match.group())
        positions = {k: v for k, v in data.get("positions", {}).items() if k in ISSUE_CATEGORIES}
        source_note = data.get("source_note", "campaign website")
        return positions, source_note
    except Exception as e:
        log.warning("Position extraction failed for %s: %s", name, e)
        return {}, "extraction error"


# ---------------------------------------------------------------------------
# Main seed pass
# ---------------------------------------------------------------------------

async def seed_candidates(
    filter_state: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    refresh: bool = False,
    all_parties: bool = False,
) -> None:
    # Ensure candidates table exists
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("candidates table ready")

    async with httpx.AsyncClient(timeout=20.0) as client:

        # Step 1: Get all 2026 Senate candidates from FEC
        log.info("Fetching 2026 Senate candidates from FEC...")
        candidates = await fetch_all_candidates(client, filter_state, all_parties, limit)
        log.info("Found %d candidates to process", len(candidates))

        if dry_run:
            log.info("\n--- DRY RUN: candidates that would be processed ---")
            for c in candidates:
                name = _format_name(c["name"])
                log.info("  %s | %s | %s | %s",
                         c["state"], name, c.get("party_full", ""), c.get("incumbent_challenge_full", ""))
            log.info("---")
            log.info("Run without --dry-run to fetch positions and save to DB.")
            return

        saved = 0
        skipped = 0
        no_website = 0
        no_positions = 0

        for i, fec_data in enumerate(candidates):
            candidate_id = fec_data["candidate_id"]
            raw_name = fec_data["name"]
            name = _format_name(raw_name)
            state = fec_data["state"]
            party_full = fec_data.get("party_full", "").title()
            incumbent = fec_data.get("incumbent_challenge", "") == "I"
            # Start all candidates as "declared" — the FEC candidate_inactive flag is NOT
            # a withdrawal signal. It's an administrative flag that's set for many active
            # candidates including sitting senators who are running. Status is determined
            # instead by the website keyword scan below.
            race_status = "declared"

            log.info("[%d/%d] %s — %s (%s)%s",
                     i + 1, len(candidates), state, name, party_full,
                     " INCUMBENT" if incumbent else "")

            async with AsyncSessionLocal() as db:
                existing = await db.execute(
                    select(Candidate).where(
                        Candidate.name == name,
                        Candidate.state == state,
                        Candidate.election_year == 2026,
                    )
                )
                existing_row = existing.scalar_one_or_none()

            if existing_row and not refresh:
                log.info("  Already in DB, skipping")
                skipped += 1
                continue

            delay = 1.5 if FEC_API_KEY == "DEMO_KEY" else 0.3
            await asyncio.sleep(delay)
            website_url = await fetch_candidate_website(client, candidate_id)
            log.info("  Website: %s", website_url or "not found")

            positions = {}
            positions_source = "fec-no-website"

            if website_url:
                website_text = await fetch_website_text(client, website_url)
                if website_text:
                    # Check for withdrawal language before extracting positions
                    withdrawal_status = _check_website_for_withdrawal(website_text)
                    if withdrawal_status:
                        race_status = withdrawal_status
                        log.info("  Status update from website: %s", race_status)

                    if race_status == "declared":
                        positions, positions_source = extract_positions_from_website(
                            website_text, name, state, party_full
                        )
                        log.info("  Positions found: %d categories", len(positions))
                else:
                    log.info("  Website unreachable")
                    no_website += 1
                    positions_source = "website-unreachable"
            else:
                no_website += 1

            if not positions:
                no_positions += 1

            bioguide_id = None
            if incumbent:
                async with AsyncSessionLocal() as db:
                    last_name = name.split()[-1]
                    result = await db.execute(
                        select(Politician).where(
                            Politician.state == state,
                            Politician.name.ilike(f"%{last_name}%"),
                        )
                    )
                    matches = result.scalars().all()
                    if len(matches) == 1:
                        bioguide_id = matches[0].bioguide_id
                        log.info("  Linked to voting record: %s", bioguide_id)

            data_complete = bool(positions)  # True when we have at least some positions
            async with AsyncSessionLocal() as db:
                if existing_row:
                    row = await db.get(Candidate, existing_row.id)
                    row.party = party_full
                    row.incumbent = incumbent
                    row.fec_candidate_id = candidate_id
                    row.race_status = race_status
                    row.race_status_updated_at = datetime.now(timezone.utc)
                    row.website_url = website_url
                    row.needs_update = not data_complete
                    row.positions = positions
                    row.positions_source = positions_source
                    row.positions_updated_at = datetime.now(timezone.utc)
                    if bioguide_id:
                        row.bioguide_id = bioguide_id
                else:
                    row = Candidate(
                        name=name,
                        state=state,
                        party=party_full,
                        election_year=2026,
                        office="Senate",
                        incumbent=incumbent,
                        needs_update=not data_complete,
                        fec_candidate_id=candidate_id,
                        race_status=race_status,
                        race_status_updated_at=datetime.now(timezone.utc),
                        bioguide_id=bioguide_id,
                        website_url=website_url,
                        ballotpedia_url=f"https://ballotpedia.org/{name.replace(' ', '_')}",
                        positions=positions,
                        positions_source=positions_source,
                        positions_updated_at=datetime.now(timezone.utc),
                    )
                    db.add(row)
                await db.commit()

            saved += 1

    log.info(
        "\nDone. Saved: %d | Skipped (existing): %d | No website: %d | No positions: %d",
        saved, skipped, no_website, no_positions,
    )


async def check_candidate_status(filter_state: str | None = None) -> None:
    """
    Re-check race status for all declared candidates by scanning their campaign
    websites for withdrawal or suspension language. Updates race_status and
    race_status_updated_at for any candidate where the status changes.

    Note: FEC's candidate_inactive flag is NOT used — it's an administrative flag
    that's incorrectly set for many active candidates including sitting senators.

    Run this periodically (weekly during active campaign season) to keep status current.
    After primaries, manually set race_status = 'primary_winner' or 'primary_loser'
    for the nominees in each state — see NEXT_STEPS.md.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        stmt = select(Candidate).where(
            Candidate.election_year == 2026,
            Candidate.race_status == "declared",
        )
        if filter_state:
            stmt = stmt.where(Candidate.state == filter_state.upper())
        result = await db.execute(stmt)
        candidates = result.scalars().all()

    log.info("Checking status for %d declared candidates...", len(candidates))
    updated = 0

    async with httpx.AsyncClient(timeout=15.0) as client:
        for i, cand in enumerate(candidates):
            log.info("[%d/%d] %s — %s", i + 1, len(candidates), cand.state, cand.name)

            new_status = None

            # Check website for withdrawal language.
            # Note: FEC's candidate_inactive flag is NOT a reliable withdrawal signal —
            # it's an administrative flag that's set for many active candidates including
            # sitting senators who are still running. We rely on website keyword scanning only.
            if cand.website_url:
                website_text = await fetch_website_text(client, cand.website_url)
                if website_text:
                    new_status = _check_website_for_withdrawal(website_text)
                    if new_status:
                        log.info("  Website signals: %s", new_status)

            if new_status and new_status != cand.race_status:
                async with AsyncSessionLocal() as db:
                    row = await db.get(Candidate, cand.id)
                    row.race_status = new_status
                    row.race_status_updated_at = datetime.now(timezone.utc)
                    await db.commit()
                log.info("  Updated: %s → %s", cand.race_status, new_status)
                updated += 1

    log.info("Status check done. Updated: %d", updated)


def _check_website_for_withdrawal(text: str) -> str | None:
    """
    Quick keyword scan of a campaign website for withdrawal or suspension language.
    Returns "withdrawn", "suspended", or None if the candidate appears still active.
    No Claude call needed — keyword matching is fast and accurate for this signal.
    """
    text_lower = text.lower()
    withdrawn_signals = [
        "suspended my campaign", "suspending my campaign",
        "ended my campaign", "ending my campaign",
        "withdraw from the race", "withdrawing from the race",
        "no longer a candidate", "dropped out",
        "have decided not to run",
    ]
    suspended_signals = [
        "pausing my campaign", "paused my campaign",
        "suspending operations", "on hold",
    ]
    for signal in withdrawn_signals:
        if signal in text_lower:
            return "withdrawn"
    for signal in suspended_signals:
        if signal in text_lower:
            return "suspended"
    return None


def _format_name(fec_name: str) -> str:
    """
    Convert FEC 'LAST, FIRST MIDDLE' format to 'First Last'.
    FEC sometimes puts a middle initial first (e.g. 'OSSOFF, T. JON') —
    in that case skip the initial and use the actual first name.
    """
    parts = fec_name.split(",", 1)
    if len(parts) == 2:
        last = parts[0].strip().title()
        first_parts = parts[1].strip().title().split()
        # If first token is just an initial (1-2 chars, possibly with dot), skip it
        first = ""
        for part in first_parts:
            clean = part.rstrip(".")
            if len(clean) > 1:
                first = part.rstrip(".")
                break
        if not first and first_parts:
            first = first_parts[0]
        return f"{first} {last}".strip()
    return fec_name.title()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed 2026 Senate candidates from FEC + extract positions from campaign websites"
    )
    parser.add_argument("--state", help="Only process one state (e.g. GA)")
    parser.add_argument("--limit", type=int, help="Max candidates to process (for testing)")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="List candidates without fetching or saving anything")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-fetch positions for candidates already in DB")
    parser.add_argument("--all-parties", action="store_true", dest="all_parties",
                        help="Include third-party candidates (Libertarian, Green, etc.)")
    parser.add_argument("--check-status", action="store_true", dest="check_status",
                        help="Re-check FEC inactive flag + website for withdrawal/suspension language")
    args = parser.parse_args()

    if args.check_status:
        asyncio.run(check_candidate_status(filter_state=args.state))
    else:
        asyncio.run(seed_candidates(
            filter_state=args.state,
            limit=args.limit,
            dry_run=args.dry_run,
            refresh=args.refresh,
            all_parties=args.all_parties,
        ))
