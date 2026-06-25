# --- Imports ---
# Database query tools and the LegiScan service for looking up live bill data.
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from models import Politician, Vote
from services import legiscan_service


# --- Tool Definitions ---
# These are the descriptions Claude reads to decide when and how to use each tool.
# Written as JSON schemas — Claude sees these like a menu of capabilities it can call on.
TOOL_DEFINITIONS = [
    {
        "name": "search_politician_votes",
        "description": (
            "Search the congressional vote database to find how a specific US politician voted on bills. "
            "Use this when a user asks about a politician's voting record, their stance on legislation, "
            "or their voting history on a topic or bill number. "
            "Returns a list of matching votes with the bill number and the politician's position (Yea/Nay/Present)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "politician_name": {
                    "type": "string",
                    "description": (
                        "The full or partial name of the US politician "
                        "(e.g., 'Nancy Pelosi', 'Sanders', 'Joe Biden')"
                    ),
                },
                "topic_or_bill": {
                    "type": "string",
                    "description": (
                        "A bill number (e.g., 'HR1234', 'S.2345', 'HJRes44') or a keyword to search "
                        "against bill numbers (e.g., 'HR', 'S'). For topic-based searches, use the "
                        "lookup_bill tool first to get the bill number."
                    ),
                },
            },
            "required": ["politician_name", "topic_or_bill"],
        },
    },
    {
        "name": "lookup_bill",
        "description": (
            "Look up live details for a US congressional bill using the LegiScan API. "
            "Returns the bill's title, summary, current legislative status, last action, and a URL "
            "to the full bill text. Use this when a user asks what a bill does, its current status, "
            "who sponsored it, or wants a plain-language summary of legislation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_number": {
                    "type": "string",
                    "description": (
                        "The LegiScan bill ID (numeric) or bill number to look up "
                        "(e.g., 'HR1234', '2345678')"
                    ),
                },
            },
            "required": ["bill_number"],
        },
    },
]


# --- Tool Functions ---
# The actual Python code that runs when Claude decides to use a tool.
# Each function queries the local database or calls an external API and returns a result.

async def search_politician_votes(
    politician_name: str,
    topic_or_bill: str,
    db: AsyncSession,
) -> dict:
    # Searches the local votes table for a politician by name and filters by bill number keyword.
    stmt = (
        select(
            Politician.name,
            Politician.party,
            Politician.state,
            Vote.bill_number,
            Vote.position,
        )
        .join(Vote, Politician.bioguide_id == Vote.bioguide_id)
        .where(func.lower(Politician.name).contains(politician_name.lower()))
        .where(func.lower(Vote.bill_number).contains(topic_or_bill.lower()))
        .limit(50)
    )

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return {
            "found": False,
            "message": f"No votes found for '{politician_name}' matching '{topic_or_bill}'.",
        }

    return {
        "found": True,
        "count": len(rows),
        "votes": [
            {
                "politician": row.name,
                "party": row.party,
                "state": row.state,
                "bill_number": row.bill_number,
                "position": row.position,
            }
            for row in rows
        ],
    }


async def lookup_bill(bill_number: str) -> dict:
    # Calls LegiScan to get the live title, summary, and status for a bill.
    return await legiscan_service.get_bill_details(bill_number)


# --- Tool Dispatcher ---
# When Claude says "call tool X with these inputs," this function routes that
# request to the right Python function and returns the result as a JSON string.
async def execute_tool(name: str, tool_input: dict, db: AsyncSession) -> str:
    if name == "search_politician_votes":
        result = await search_politician_votes(
            politician_name=tool_input["politician_name"],
            topic_or_bill=tool_input["topic_or_bill"],
            db=db,
        )
    elif name == "lookup_bill":
        result = await lookup_bill(bill_number=tool_input["bill_number"])
    else:
        result = {"error": f"Unknown tool: {name}"}

    return json.dumps(result)
