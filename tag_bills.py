"""
Tag all bills in the database with issue categories, bill type, and omnibus flag.

Strategy (in order of preference):
  1. Congress.gov subjects API  — official human-assigned subject tags, most accurate
  2. Claude fallback            — used when Congress.gov has no tags or bill is too new

Usage:
  python tag_bills.py               # tag all untagged bills
  python tag_bills.py --retag       # retag everything (overwrites existing tags)
  python tag_bills.py --dry-run     # print what would be tagged, don't save

Get a free Congress.gov API key at https://api.congress.gov/sign-up/
and add it to your .env as CONGRESS_GOV_API_KEY.
"""

import asyncio
import argparse
import json
import logging
import os
import re

import anthropic
import httpx
from dotenv import load_dotenv
from sqlalchemy import select, text

from database import AsyncSessionLocal, init_db
from models import Bill

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONGRESS_GOV_API_KEY = os.getenv("CONGRESS_GOV_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CONGRESS_GOV_BASE = "https://api.congress.gov/v3"

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# The 22 issue categories — the controlled vocabulary for all tagging.
# ---------------------------------------------------------------------------

ISSUE_CATEGORIES = [
    "Economy & Taxes",
    "Government Budget & Spending",
    "Elections & Campaign Finance",
    "Civil Rights",
    "LGBTQ+ Rights",
    "Healthcare",
    "Military & Defense",
    "Foreign Policy & International Affairs",
    "Immigration",
    "Border Security",
    "Environmental Policy",
    "Climate Policy",
    "Gun Policy",
    "Criminal Justice",
    "Policing & Law Enforcement",
    "Education",
    "Social Safety Net",
    "Housing",
    "Drug Policy",
    "Labor & Workers Rights",
    "Technology & Privacy",
    "US Territory Policy",
]


# ---------------------------------------------------------------------------
# Congress.gov policy area → our issue categories
# One policy area can map to multiple categories.
# ---------------------------------------------------------------------------

POLICY_AREA_MAP: dict[str, list[str]] = {
    "Agriculture and Food": ["Economy & Taxes"],
    "Animals": ["Environmental Policy"],
    "Armed Forces and National Security": ["Military & Defense"],
    "Arts, Culture, Religion": [],
    "Civil Rights and Liberties, Minority Issues": ["Civil Rights"],
    "Commerce": ["Economy & Taxes"],
    "Congress": ["Elections & Campaign Finance"],
    "Crime and Law Enforcement": ["Criminal Justice", "Policing & Law Enforcement"],
    "Economics and Public Finance": ["Economy & Taxes", "Government Budget & Spending"],
    "Education": ["Education"],
    "Emergency Management": ["Government Budget & Spending"],
    "Energy": ["Environmental Policy", "Climate Policy"],
    "Environmental Protection": ["Environmental Policy"],
    "Families": ["Social Safety Net"],
    "Finance and Financial Sector": ["Economy & Taxes"],
    "Foreign Trade and International Finance": ["Foreign Policy & International Affairs", "Economy & Taxes"],
    "Geography": [],
    "Government Operations and Politics": ["Elections & Campaign Finance"],
    "Health": ["Healthcare"],
    "Housing and Community Development": ["Housing"],
    "Immigration": ["Immigration"],
    "International Affairs": ["Foreign Policy & International Affairs"],
    "Labor and Employment": ["Labor & Workers Rights"],
    "Law": ["Criminal Justice"],
    "Native Americans": ["Civil Rights"],
    "Public Lands and Natural Resources": ["Environmental Policy"],
    "Science, Technology, Communications": ["Technology & Privacy"],
    "Social Sciences and History": [],
    "Social Welfare": ["Social Safety Net"],
    "Sports and Recreation": [],
    "Taxation": ["Economy & Taxes"],
    "Transportation and Public Works": ["Government Budget & Spending"],
    "Water Resources Development": ["Environmental Policy"],
}


# ---------------------------------------------------------------------------
# Congress.gov legislative subject keywords → our issue categories.
# Checked as case-insensitive substrings against each subject name.
# More specific phrases are listed first so they take priority.
# ---------------------------------------------------------------------------

SUBJECT_KEYWORD_MAP: list[tuple[str, list[str]]] = [
    # Climate & Environment
    ("climate change", ["Climate Policy"]),
    ("greenhouse gas", ["Climate Policy"]),
    ("global warming", ["Climate Policy"]),
    ("clean energy", ["Climate Policy", "Environmental Policy"]),
    ("renewable energy", ["Climate Policy", "Environmental Policy"]),
    ("carbon", ["Climate Policy"]),
    ("air quality", ["Environmental Policy"]),
    ("pollution", ["Environmental Policy"]),
    ("endangered species", ["Environmental Policy"]),
    ("wildlife", ["Environmental Policy"]),
    ("national park", ["Environmental Policy"]),
    ("environmental", ["Environmental Policy"]),
    ("water resources", ["Environmental Policy"]),
    ("public lands", ["Environmental Policy"]),

    # LGBTQ+
    ("lgbtq", ["LGBTQ+ Rights"]),
    ("transgender", ["LGBTQ+ Rights"]),
    ("same-sex", ["LGBTQ+ Rights"]),
    ("sexual orientation", ["LGBTQ+ Rights"]),
    ("gender identity", ["LGBTQ+ Rights"]),

    # Civil Rights
    ("voting rights", ["Civil Rights"]),
    ("racial discrimination", ["Civil Rights"]),
    ("civil rights", ["Civil Rights"]),
    ("women's rights", ["Civil Rights"]),
    ("disability", ["Civil Rights"]),
    ("native american", ["Civil Rights"]),
    ("tribal", ["Civil Rights", "US Territory Policy"]),

    # US Territories
    ("puerto rico", ["US Territory Policy"]),
    ("guam", ["US Territory Policy"]),
    ("virgin islands", ["US Territory Policy"]),
    ("american samoa", ["US Territory Policy"]),
    ("northern mariana", ["US Territory Policy"]),
    ("territories", ["US Territory Policy"]),

    # Policing
    ("police", ["Policing & Law Enforcement"]),
    ("law enforcement officer", ["Policing & Law Enforcement"]),
    ("qualified immunity", ["Policing & Law Enforcement"]),
    ("use of force", ["Policing & Law Enforcement"]),

    # Criminal Justice
    ("sentencing", ["Criminal Justice"]),
    ("incarceration", ["Criminal Justice"]),
    ("prison", ["Criminal Justice"]),
    ("criminal justice", ["Criminal Justice"]),
    ("recidivism", ["Criminal Justice"]),

    # Immigration & Border
    ("border security", ["Border Security"]),
    ("border patrol", ["Border Security"]),
    ("customs and border", ["Border Security"]),
    ("deportation", ["Immigration"]),
    ("daca", ["Immigration"]),
    ("asylum", ["Immigration"]),
    ("refugee", ["Immigration"]),
    ("visa", ["Immigration"]),
    ("immigration", ["Immigration"]),

    # Drug Policy
    ("marijuana", ["Drug Policy"]),
    ("cannabis", ["Drug Policy"]),
    ("opioid", ["Drug Policy"]),
    ("substance abuse", ["Drug Policy"]),
    ("drug trafficking", ["Drug Policy"]),
    ("controlled substance", ["Drug Policy"]),

    # Gun Policy
    ("firearm", ["Gun Policy"]),
    ("gun control", ["Gun Policy"]),
    ("second amendment", ["Gun Policy"]),
    ("background check", ["Gun Policy"]),
    ("assault weapon", ["Gun Policy"]),

    # Healthcare
    ("medicare", ["Healthcare"]),
    ("medicaid", ["Healthcare"]),
    ("health care", ["Healthcare"]),
    ("prescription drug", ["Healthcare"]),
    ("mental health", ["Healthcare"]),
    ("affordable care", ["Healthcare"]),
    ("public health", ["Healthcare"]),

    # Social Safety Net
    ("social security", ["Social Safety Net"]),
    ("snap benefits", ["Social Safety Net"]),
    ("food assistance", ["Social Safety Net"]),
    ("welfare", ["Social Safety Net"]),
    ("poverty", ["Social Safety Net"]),
    ("unemployment", ["Social Safety Net", "Labor & Workers Rights"]),

    # Housing
    ("affordable housing", ["Housing"]),
    ("homelessness", ["Housing"]),
    ("rental assistance", ["Housing"]),
    ("mortgage", ["Housing"]),
    ("public housing", ["Housing"]),

    # Labor
    ("minimum wage", ["Labor & Workers Rights"]),
    ("labor union", ["Labor & Workers Rights"]),
    ("collective bargaining", ["Labor & Workers Rights"]),
    ("worker", ["Labor & Workers Rights"]),
    ("wage", ["Labor & Workers Rights"]),
    ("workplace", ["Labor & Workers Rights"]),

    # Elections
    ("campaign finance", ["Elections & Campaign Finance"]),
    ("election", ["Elections & Campaign Finance"]),
    ("gerrymandering", ["Elections & Campaign Finance"]),
    ("voter", ["Elections & Campaign Finance"]),

    # Technology & Privacy
    ("privacy", ["Technology & Privacy"]),
    ("cybersecurity", ["Technology & Privacy"]),
    ("artificial intelligence", ["Technology & Privacy"]),
    ("internet", ["Technology & Privacy"]),
    ("data protection", ["Technology & Privacy"]),
    ("surveillance", ["Technology & Privacy"]),
    ("social media", ["Technology & Privacy"]),

    # Military & Defense
    ("armed forces", ["Military & Defense"]),
    ("military", ["Military & Defense"]),
    ("national defense", ["Military & Defense"]),
    ("veterans", ["Military & Defense"]),
    ("nato", ["Military & Defense", "Foreign Policy & International Affairs"]),
    ("pentagon", ["Military & Defense"]),

    # Foreign Policy
    ("sanctions", ["Foreign Policy & International Affairs"]),
    ("foreign aid", ["Foreign Policy & International Affairs"]),
    ("diplomatic", ["Foreign Policy & International Affairs"]),
    ("united nations", ["Foreign Policy & International Affairs"]),
    ("international", ["Foreign Policy & International Affairs"]),
    ("treaty", ["Foreign Policy & International Affairs"]),

    # Budget & Spending
    ("appropriations", ["Government Budget & Spending"]),
    ("federal budget", ["Government Budget & Spending"]),
    ("debt ceiling", ["Government Budget & Spending"]),
    ("government spending", ["Government Budget & Spending"]),
    ("deficit", ["Government Budget & Spending", "Economy & Taxes"]),

    # Economy & Taxes
    ("tax", ["Economy & Taxes"]),
    ("tariff", ["Economy & Taxes", "Foreign Policy & International Affairs"]),
    ("trade agreement", ["Economy & Taxes", "Foreign Policy & International Affairs"]),
    ("inflation", ["Economy & Taxes"]),
    ("banking", ["Economy & Taxes"]),
    ("financial regulation", ["Economy & Taxes"]),

    # Education
    ("education", ["Education"]),
    ("school", ["Education"]),
    ("student loan", ["Education"]),
    ("higher education", ["Education"]),
    ("early childhood", ["Education"]),
]


# ---------------------------------------------------------------------------
# Bill type detection from bill number prefix
# ---------------------------------------------------------------------------

def detect_bill_type(bill_number: str) -> str:
    """Determine bill_type from the bill number prefix."""
    prefix = bill_number.split("-")[0].upper()
    if prefix.startswith("PROC"):
        return "Procedural"
    if re.match(r"^(HJRES|SJRES)", prefix):
        return "Joint Resolution"
    if re.match(r"^(HRES|SRES|HCONRES|SCONRES)", prefix):
        return "Resolution"
    return "Bill"


def detect_is_omnibus(title: str, tags: list[str]) -> bool:
    """Flag a bill as omnibus if its title suggests it or it spans many categories."""
    if not title:
        return False
    title_lower = title.lower()
    omnibus_signals = [
        "consolidated appropriations",
        "omnibus",
        "continuing resolution",
        "full-year continuing",
        "minibus",
    ]
    if any(s in title_lower for s in omnibus_signals):
        return True
    # Also flag if Claude/Congress.gov assigned 6+ categories
    return len(tags) >= 6


# ---------------------------------------------------------------------------
# Congress.gov API — parse bill number and fetch official subject tags
# ---------------------------------------------------------------------------

BILL_TYPE_TO_CONGRESS_GOV = {
    "HR": "hr", "S": "s",
    "HJRES": "hjres", "SJRES": "sjres",
    "HCONRES": "hconres", "SCONRES": "sconres",
    "HRES": "hres", "SRES": "sres",
}


def parse_bill_number(bill_number: str) -> tuple[str, str, int] | None:
    """
    Parse our PK format into (bill_type_code, bill_num, congress).
    Returns None if the bill number is procedural or unrecognizable.
    e.g. "S5-119" → ("s", "5", 119)
         "SJRES82-119" → ("sjres", "82", 119)
    """
    if bill_number.startswith("PROC"):
        return None
    match = re.match(r"^([A-Z]+)(\d+)-(\d+)$", bill_number.upper())
    if not match:
        return None
    prefix, num, congress = match.group(1), match.group(2), int(match.group(3))
    code = BILL_TYPE_TO_CONGRESS_GOV.get(prefix)
    if not code:
        return None
    return code, num, congress


async def fetch_congress_gov_subjects(
    client: httpx.AsyncClient, bill_number: str
) -> tuple[str | None, list[str]]:
    """
    Fetch policyArea and legislativeSubjects from Congress.gov.
    Returns (policy_area, [subject_name, ...]) or (None, []) on failure.
    """
    parsed = parse_bill_number(bill_number)
    if not parsed or not CONGRESS_GOV_API_KEY:
        return None, []

    bill_type_code, bill_num, congress = parsed
    url = (
        f"{CONGRESS_GOV_BASE}/bill/{congress}/{bill_type_code}/{bill_num}/subjects"
        f"?api_key={CONGRESS_GOV_API_KEY}&format=json"
    )

    try:
        r = await client.get(url, timeout=15.0)
        if r.status_code == 404:
            return None, []
        r.raise_for_status()
        data = r.json()
        subjects_data = data.get("subjects", {})
        policy_area = (subjects_data.get("policyArea") or {}).get("name")
        legislative = [
            s["name"]
            for s in subjects_data.get("legislativeSubjects", [])
            if isinstance(s, dict) and s.get("name")
        ]
        return policy_area, legislative
    except Exception as e:
        log.warning("Congress.gov failed for %s: %s", bill_number, e)
        return None, []


def map_subjects_to_categories(
    policy_area: str | None, legislative_subjects: list[str]
) -> list[str]:
    """
    Convert Congress.gov subjects to our 22-category taxonomy.
    Deduplicates and preserves order of first appearance.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(cats: list[str]):
        for c in cats:
            if c not in seen:
                seen.add(c)
                found.append(c)

    # Policy area first (highest-level signal)
    if policy_area:
        add(POLICY_AREA_MAP.get(policy_area, []))

    # Legislative subjects via keyword matching
    for subject in legislative_subjects:
        subject_lower = subject.lower()
        for keyword, cats in SUBJECT_KEYWORD_MAP:
            if keyword in subject_lower:
                add(cats)
                break  # one keyword match per subject is enough

    return found


# ---------------------------------------------------------------------------
# Claude fallback — used when Congress.gov returns nothing
# ---------------------------------------------------------------------------

async def tag_with_claude(bill_number: str, title: str, summary: str) -> list[str]:
    """
    Ask Claude to assign issue categories from our 22-category list.
    Used only when Congress.gov has no data for a bill.
    """
    categories_str = "\n".join(f"- {c}" for c in ISSUE_CATEGORIES)
    prompt = (
        f"You are tagging a US congressional bill with issue categories.\n\n"
        f"Bill: {bill_number}\n"
        f"Title: {title or 'Unknown'}\n"
        f"Summary: {summary or 'No summary available'}\n\n"
        f"Assign ALL relevant categories from this exact list (use the exact names):\n"
        f"{categories_str}\n\n"
        f"Return ONLY a JSON array of category names. Example: [\"Healthcare\", \"Economy & Taxes\"]\n"
        f"If the bill is purely ceremonial (naming a post office, congratulating a sports team), return []."
    )

    try:
        response = claude.messages.create(
            model="claude-opus-4-8",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text.strip()
        # Extract JSON array from response
        match = re.search(r"\[.*?\]", raw_text, re.DOTALL)
        if not match:
            return []
        raw = json.loads(match.group())
        # Validate against our list
        return [c for c in raw if c in ISSUE_CATEGORIES]
    except Exception as e:
        log.warning("Claude tagging failed for %s: %s", bill_number, e)
        return []


# ---------------------------------------------------------------------------
# Main tagging pass
# ---------------------------------------------------------------------------

async def tag_all_bills(retag: bool = False, dry_run: bool = False) -> None:
    # SQLite ALTER TABLE — add columns if missing (safe to run repeatedly)
    async with AsyncSessionLocal() as db:
        for col, definition in [
            ("tags",       "TEXT NOT NULL DEFAULT '[]'"),
            ("bill_type",  "TEXT"),
            ("is_omnibus", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                await db.execute(text(f"ALTER TABLE bills ADD COLUMN {col} {definition}"))
                await db.commit()
                log.info("Added column: %s", col)
            except Exception:
                pass  # column already exists

    async with AsyncSessionLocal() as db:
        stmt = select(Bill)
        if not retag:
            # Only tag bills that haven't been tagged yet
            stmt = stmt.where(Bill.bill_type.is_(None))
        result = await db.execute(stmt)
        bills = result.scalars().all()

    log.info("Bills to tag: %d", len(bills))
    if not bills:
        log.info("Nothing to tag. Use --retag to overwrite existing tags.")
        return

    congress_gov_hits = 0
    claude_hits = 0
    skipped = 0

    async with httpx.AsyncClient(timeout=15.0) as client:
        for i, bill in enumerate(bills):
            bill_type = detect_bill_type(bill.bill_number)

            # Procedural votes get no issue tags
            if bill_type == "Procedural":
                tags = []
                skipped += 1
            else:
                # Try Congress.gov first
                policy_area, legislative_subjects = await fetch_congress_gov_subjects(
                    client, bill.bill_number
                )
                tags = map_subjects_to_categories(policy_area, legislative_subjects)

                if tags:
                    congress_gov_hits += 1
                else:
                    # Fall back to Claude
                    tags = await tag_with_claude(
                        bill.bill_number,
                        bill.title or "",
                        bill.summary or "",
                    )
                    if tags:
                        claude_hits += 1

            is_omnibus = detect_is_omnibus(bill.title or "", tags)

            log.info(
                "[%d/%d] %s → type=%s omnibus=%s tags=%s",
                i + 1, len(bills), bill.bill_number, bill_type, is_omnibus, tags,
            )

            if not dry_run:
                async with AsyncSessionLocal() as db:
                    b = await db.get(Bill, bill.bill_number)
                    if b:
                        b.tags = tags
                        b.bill_type = bill_type
                        b.is_omnibus = is_omnibus
                        await db.commit()

    log.info(
        "Done. Congress.gov: %d | Claude: %d | Procedural/skipped: %d",
        congress_gov_hits, claude_hits, skipped,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tag bills with issue categories")
    parser.add_argument(
        "--retag",
        action="store_true",
        help="Retag all bills, overwriting existing tags",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Print tags without saving to the database",
    )
    args = parser.parse_args()
    asyncio.run(tag_all_bills(retag=args.retag, dry_run=args.dry_run))
