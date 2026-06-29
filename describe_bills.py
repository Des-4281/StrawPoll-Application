"""
Generate UI-facing bill descriptions and yea impact statements from ai_summary.

Requires summarize_bills.py to have run first — reads ai_summary from the DB
and derives two shorter outputs in a single Claude call:

  bill_description — 75-150 words, main points only, no riders, shown to users
  yea_impact       — 2-3 sentences describing what a Yea vote concretely did,
                     shown on senator record pages

Usage:
  python describe_bills.py               # process all bills with ai_summary but no description yet
  python describe_bills.py --bill S5-119 # process a specific bill
  python describe_bills.py --limit 10    # process at most 10 bills
  python describe_bills.py --redo        # reprocess bills that already have a description
  python describe_bills.py --dry-run     # print output without saving to DB
"""

import asyncio
import argparse
import json
import logging
import os

import anthropic
from dotenv import load_dotenv
from sqlalchemy import select, text

from database import AsyncSessionLocal
from models import Bill
from tag_bills import ISSUE_CATEGORIES

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

async def describe_with_claude(bill_number: str, title: str, ai_summary: str) -> tuple[str, str] | None:
    """
    Two sequential Claude calls to derive UI-facing content from ai_summary.
    Returns (bill_description, yea_impact) or None on failure.
    """
    # --- Call 1: bill_description ---
    try:
        desc_response = await asyncio.to_thread(
            claude.messages.create,
            model="claude-opus-4-8",
            max_tokens=32000,
            messages=[{
                "role": "user",
                "content": (
                    f"You are writing a plain-English description of a US congressional bill for a public transparency app.\n\n"
                    f"Bill: {bill_number}\n"
                    f"Title: {title or 'Unknown'}\n\n"
                    f"Here is the full analysis of this bill:\n{ai_summary}\n\n"
                    f"Write a 75-150 word description of what this bill does. Cover the main points. "
                    f"Do NOT mention riders, earmarks, or hidden provisions — those are surfaced separately. "
                    f"Write in plain English for a general audience. No bullet points, just prose."
                ),
            }],
        )
        bill_description = desc_response.content[0].text.strip()
    except Exception as e:
        log.warning("bill_description failed for %s: %s", bill_number, e)
        return None

    # --- Call 2: yea_impact ---
    try:
        impact_response = await asyncio.to_thread(
            claude.messages.create,
            model="claude-opus-4-8",
            max_tokens=32000,
            messages=[{
                "role": "user",
                "content": (
                    f"You are explaining a congressional vote to a constituent.\n\n"
                    f"Bill: {bill_number}\n"
                    f"Title: {title or 'Unknown'}\n\n"
                    f"Here is the full analysis of this bill:\n{ai_summary}\n\n"
                    f"List every concrete impact of a Yea vote on this bill as bullet points. "
                    f"Start with the primary impact points, then list all remaining impacts ordered by how broadly they apply — "
                    f"nationwide effects first, then industry-specific, then group-specific, then locality- or company-specific provisions last. "
                    f"Each bullet should be 1-3 sentences. Be specific — name dollar amounts, programs, agencies, and groups affected. "
                    f"Do not combine unrelated impacts into one bullet. Cover everything. "
                    f"Write at a 6th-grade reading level. Use short sentences and strong verbs. Cut any word that doesn't add meaning."
                ),
            }],
        )
        yea_impact = impact_response.content[0].text.strip()
    except Exception as e:
        log.warning("yea_impact failed for %s: %s", bill_number, e)
        return None

    return bill_description, yea_impact


async def extract_secondary_tags(
    bill_number: str,
    title: str,
    ai_summary: str,
    existing_tags: list[str],
) -> list[str]:
    categories_str = "\n".join(f"- {c}" for c in ISSUE_CATEGORIES)
    existing_str = ", ".join(existing_tags) if existing_tags else "none"

    try:
        response = await asyncio.to_thread(
            claude.messages.create,
            model="claude-opus-4-8",
            max_tokens=32000,
            messages=[{
                "role": "user",
                "content": (
                    f"You are tagging a US congressional bill with secondary issue categories.\n\n"
                    f"Bill: {bill_number}\n"
                    f"Title: {title or 'Unknown'}\n"
                    f"Primary tag already assigned: {existing_str}\n\n"
                    f"Here is the full analysis of this bill:\n{ai_summary}\n\n"
                    f"Identify any SECONDARY issue categories meaningfully present in this bill — "
                    f"meaning a specific provision directly changes law, funding, or policy in that area. "
                    f"Do NOT include categories already listed as primary tags.\n\n"
                    f"Hard rules:\n"
                    f"- 'Government Budget & Spending': only if the bill directly appropriates or allocates funds. "
                    f"Authorization bills (like the NDAA) do NOT qualify.\n"
                    f"- 'Civil Rights': only if the bill changes the legal rights of a defined class of people "
                    f"(e.g. due process, equal protection, right to counsel). Internal policy changes do not qualify.\n"
                    f"- 'Immigration': include if the bill changes immigration law OR the legal rights of non-citizens.\n\n"
                    f"Choose ONLY from this exact list:\n{categories_str}\n\n"
                    f"Return a JSON array of category names. Return [] if none apply. No explanation, just the JSON array."
                ),
            }],
        )
        raw = response.content[0].text.strip()
        secondary = json.loads(raw)
        return [t for t in secondary if t in ISSUE_CATEGORIES and t not in existing_tags]
    except Exception as e:
        log.warning("Secondary tag extraction failed for %s: %s", bill_number, e)
        return []


async def describe_all(
    specific_bill: str | None = None,
    limit: int | None = None,
    redo: bool = False,
    dry_run: bool = False,
) -> None:
            # Add columns if missing (safe to run repeatedly)
    async with AsyncSessionLocal() as db:
        for col in ["ai_summary TEXT", "bill_description TEXT", "yea_impact TEXT"]:
            try:
                await db.execute(text(f"ALTER TABLE bills ADD COLUMN {col}"))
                await db.commit()
            except Exception:
                pass  # already exists

        if specific_bill:
            result = await db.execute(
                select(Bill).where(Bill.bill_number == specific_bill)
            )
        else:
            stmt = (
                select(Bill)
                .where(Bill.ai_summary.is_not(None))
            )
            if not redo:
                stmt = stmt.where(Bill.bill_description.is_(None))
            if limit:
                stmt = stmt.limit(limit)
            result = await db.execute(stmt)
        bills = result.scalars().all()

    if not bills:
        log.info("No bills to process. Run summarize_bills.py first, or use --redo to reprocess.")
        return

    log.info("Bills to describe: %d", len(bills))
    success = 0
    skipped = 0

    for i, bill in enumerate(bills):
        log.info("[%d/%d] %s — %s", i + 1, len(bills), bill.bill_number, (bill.title or "")[:60])

        if dry_run:
            log.info("  → [dry-run] Would describe %s", bill.bill_number)
            continue

        result = await describe_with_claude(
            bill.bill_number,
            bill.title or "",
            bill.ai_summary,
        )

        if not result:
            skipped += 1
            continue

        bill_description, yea_impact = result

        existing_tags = bill.tags or []
        secondary_tags = await extract_secondary_tags(
            bill.bill_number,
            bill.title or "",
            bill.ai_summary,
            existing_tags,
        )
        updated_tags = existing_tags + secondary_tags

        async with AsyncSessionLocal() as db:
            b = await db.get(Bill, bill.bill_number)
            if b:
                b.bill_description = bill_description
                b.yea_impact = yea_impact
                b.tags = updated_tags
                await db.commit()

        log.info(
            "  → Saved bill_description (%d chars) + yea_impact (%d chars) + tags %s",
            len(bill_description), len(yea_impact), updated_tags,
        )
        success += 1

    log.info("Done. Described: %d | Skipped: %d", success, skipped)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate bill descriptions and yea impact from ai_summary")
    parser.add_argument("--bill", help="Process a specific bill number (e.g. S5-119)")
    parser.add_argument("--limit", type=int, help="Max number of bills to process")
    parser.add_argument("--redo", action="store_true", help="Reprocess bills that already have a description")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Print without saving to DB")
    args = parser.parse_args()

    asyncio.run(describe_all(
        specific_bill=args.bill,
        limit=args.limit,
        redo=args.redo,
        dry_run=args.dry_run,
    ))
