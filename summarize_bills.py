"""
Fetch bill full text from Congress.gov and store an AI-extracted structured summary.

Why this approach instead of storing raw text:
- Raw bill XML is 10-200KB each; structured summaries are ~2KB
- The summary is more useful for the polling comparison use case than legal boilerplate
- Congress.gov hosts all bill text for free — no need to store it ourselves
- One Claude call per bill, result cached in bills.ai_summary forever

Usage:
  python summarize_bills.py               # summarize all bills without a summary yet
  python summarize_bills.py --bill S5-119 # summarize a specific bill
  python summarize_bills.py --limit 20    # process at most 20 bills (good for testing)
  python summarize_bills.py --dry-run     # print what would be fetched, don't save
"""

import asyncio
import argparse
import logging
import os
import re

import anthropic
import httpx
from dotenv import load_dotenv
from sqlalchemy import select, text

from database import AsyncSessionLocal
from models import Bill

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONGRESS_GOV_API_KEY = os.getenv("CONGRESS_GOV_API_KEY", "")
CONGRESS_GOV_BASE = "https://api.congress.gov/v3"

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))


# Maps our bill number prefix to Congress.gov's bill type codes
BILL_TYPE_CODES = {
    "HR": "hr", "S": "s",
    "HJRES": "hjres", "SJRES": "sjres",
    "HCONRES": "hconres", "SCONRES": "sconres",
    "HRES": "hres", "SRES": "sres",
}


def parse_bill_number(bill_number: str) -> tuple[str, str, int] | None:
    """Parse 'S1234-119' → ('s', '1234', 119). Returns None for procedural votes."""
    if bill_number.startswith("PROC"):
        return None
    match = re.match(r"^([A-Z]+)(\d+)-(\d+)$", bill_number.upper())
    if not match:
        return None
    prefix, num, congress = match.group(1), match.group(2), int(match.group(3))
    code = BILL_TYPE_CODES.get(prefix)
    return (code, num, congress) if code else None


async def fetch_bill_text_url(client: httpx.AsyncClient, bill_number: str) -> str | None:
    """
    Get the URL for the most recent plain-text version of a bill from Congress.gov.
    Congress.gov stores bills in multiple formats; we prefer plain text for Claude.
    Returns the URL string, or None if no text version is available.
    """
    parsed = parse_bill_number(bill_number)
    if not parsed:
        return None
    bill_type, num, congress = parsed

    url = (
        f"{CONGRESS_GOV_BASE}/bill/{congress}/{bill_type}/{num}/text"
        f"?api_key={CONGRESS_GOV_API_KEY}&format=json"
    )
    try:
        r = await client.get(url, timeout=15.0)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        # textVersions is a list of available formats; prefer TXT, fall back to HTML
        versions = data.get("textVersions", [])
        if not versions:
            return None
        # Get the most recent version
        latest = versions[0]
        formats = latest.get("formats", [])
        txt_url = next((f["url"] for f in formats if f.get("type") == "Formatted Text"), None)
        htm_url = next((f["url"] for f in formats if f.get("type") in ("HTML", "Formatted XML")), None)
        return txt_url or htm_url
    except Exception as e:
        log.warning("Text URL fetch failed for %s: %s", bill_number, e)
        return None


async def fetch_bill_text(client: httpx.AsyncClient, text_url: str) -> str | None:
    """Download the actual bill text from the URL returned by the API."""
    try:
        r = await client.get(text_url, timeout=30.0)
        r.raise_for_status()
        raw = r.text
        # Strip HTML tags if present (rough but good enough for Claude input)
        if "<html" in raw.lower():
            raw = re.sub(r"<[^>]+>", " ", raw)
            raw = re.sub(r"\s+", " ", raw).strip()
        # Trim to 40,000 chars — enough for Claude to understand any bill
        return raw[:40000]
    except Exception as e:
        log.warning("Text download failed for %s: %s", text_url, e)
        return None


async def summarize_with_claude(bill_number: str, title: str, text: str) -> str | None:
    """
    Extract a structured summary from the full bill text using Claude.
    Returns a ~800-word structured summary, or None on failure.
    """
    prompt = f"""You are reading the full text of a US congressional bill and extracting a structured summary for a political transparency app. Your job is to surface both the headline purpose AND the fine print that voters typically miss.

Bill: {bill_number}
Title: {title or "Unknown"}

Full text (may be truncated):
{text}

Write a structured summary with these exact sections. Be specific — name the actual programs, dollar amounts, agencies, companies, districts, countries, and legal authorities in the bill. Do not generalize or paraphrase vaguely.

## Plain English Summary
2-3 sentences: what does this bill do and why was it introduced?

## Key Provisions
Bullet list of the 4-8 most important things the bill actually does or changes in law. Lead with the main purpose, but include secondary provisions too.

## Hidden or Overlooked Provisions
This is critical: list any provisions buried in the bill that are not obvious from the title or main purpose. Include:
- Earmarks or funding directed to specific states, congressional districts, cities, or localities
- Benefits, contracts, or regulatory carve-outs for specific named companies or industries
- Foreign aid, loan guarantees, or advantages granted to specific countries or foreign entities
- Subsidy programs for specific agricultural products, sectors, or business types (e.g. dairy, ethanol, oil)
- Riders — provisions unrelated to the bill's stated topic that were attached to get it passed
- Liability shields or legal protections granted to specific industries or entities
- Sunset clauses, delayed implementation dates, or phase-in provisions that reduce the bill's apparent scope
If none found, write "None identified."

## Who It Affects
Which Americans, industries, companies, or groups benefit — and who bears the cost or is negatively impacted.

## Fiscal Impact
Estimated cost or savings. Use CBO score numbers if referenced. Break out any specific dollar amounts named in the bill for specific recipients.

## Legal Basis
What existing laws this amends, what agencies are given new authority, any constitutional or legal questions raised.

## Political Context
One sentence on why this was controversial, who opposed it, or why it was bipartisan.
"""

    try:
        response = claude.messages.create(
            model="claude-opus-4-8",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        log.warning("Claude summarization failed for %s: %s", bill_number, e)
        return None


async def run_migration():
    """Add ai_summary column to bills table if it doesn't exist yet."""
    async with AsyncSessionLocal() as db:
        try:
            await db.execute(text("ALTER TABLE bills ADD COLUMN ai_summary TEXT"))
            await db.commit()
            log.info("Added column: ai_summary")
        except Exception:
            pass  # already exists


async def summarize_all(
    specific_bill: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> None:
    await run_migration()

    async with AsyncSessionLocal() as db:
        if specific_bill:
            result = await db.execute(
                select(Bill).where(Bill.bill_number == specific_bill)
            )
        else:
            stmt = (
                select(Bill)
                .where(Bill.bill_type != "Procedural")
                .where(Bill.ai_summary.is_(None))
            )
            if limit:
                stmt = stmt.limit(limit)
            result = await db.execute(stmt)
        bills = result.scalars().all()

    if not bills:
        log.info("No bills need summarizing. Use --bill or check if ai_summary is already populated.")
        return

    log.info("Bills to summarize: %d", len(bills))
    success = 0
    skipped = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, bill in enumerate(bills):
            log.info("[%d/%d] %s — %s", i + 1, len(bills), bill.bill_number, (bill.title or "")[:60])

            # Step 1: Get the URL for the bill text
            text_url = await fetch_bill_text_url(client, bill.bill_number)
            if not text_url:
                log.info("  → No text available on Congress.gov, skipping")
                skipped += 1
                continue

            # Step 2: Download the actual text
            bill_text = await fetch_bill_text(client, text_url)
            if not bill_text:
                log.info("  → Text download failed, skipping")
                skipped += 1
                continue

            log.info("  → Fetched %d chars, sending to Claude...", len(bill_text))

            if dry_run:
                log.info("  → [dry-run] Would summarize %s", bill.bill_number)
                continue

            # Step 3: Extract structured summary with Claude
            summary = await summarize_with_claude(
                bill.bill_number,
                bill.title or "",
                bill_text,
            )

            if not summary:
                skipped += 1
                continue

            # Step 4: Save to DB
            async with AsyncSessionLocal() as db:
                b = await db.get(Bill, bill.bill_number)
                if b:
                    b.ai_summary = summary
                    await db.commit()

            log.info("  → Saved (%d chars)", len(summary))
            success += 1

    log.info("Done. Summarized: %d | Skipped (no text): %d", success, skipped)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Summarize bill text using Claude")
    parser.add_argument("--bill", help="Summarize a specific bill number (e.g. S5-119)")
    parser.add_argument("--limit", type=int, help="Max number of bills to process")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = parser.parse_args()

    asyncio.run(summarize_all(
        specific_bill=args.bill,
        limit=args.limit,
        dry_run=args.dry_run,
    ))
