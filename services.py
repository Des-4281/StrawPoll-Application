# --- Imports ---
# httpx makes async HTTP requests to external APIs (LegiScan).
import os
import httpx
from dotenv import load_dotenv


# --- Environment ---
# Loads the LegiScan API key from .env so it's never hardcoded.
load_dotenv()

LEGISCAN_API_KEY = os.getenv("LEGISCAN_API_KEY", "")
LEGISCAN_BASE_URL = "https://api.legiscan.com/"


# --- LegiScan Service ---
# All communication with the LegiScan API lives here.
# Two public methods:
#   get_bill_details  — called at runtime when a user asks about a specific bill
#   search_bill_metadata — called during seeding to fetch titles for bills in bulk
class LegiScanService:
    def __init__(self):
        self.api_key = LEGISCAN_API_KEY
        self.base_url = LEGISCAN_BASE_URL

    # --- Internal Helper ---
    # Shared request logic — attaches the API key and returns None on any failure
    # so callers don't need to handle HTTP errors individually.
    async def _get(self, client: httpx.AsyncClient, params: dict) -> dict | None:
        if not self.api_key:
            return None
        try:
            r = await client.get(self.base_url, params={"key": self.api_key, **params}, timeout=10.0)
            r.raise_for_status()
            data = r.json()
            return data if data.get("status") == "OK" else None
        except Exception:
            return None

    # --- Live Bill Lookup ---
    # Called by the AI when a user asks about a specific bill during a chat.
    # Returns full details including title, summary, status, and a link to the text.
    async def get_bill_details(self, bill_number: str) -> dict:
        if not self.api_key:
            return {"error": "LEGISCAN_API_KEY is not configured"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                data = await self._get(client, {"op": "getBill", "id": bill_number})

            if not data:
                return {"error": f"Bill '{bill_number}' not found on LegiScan"}

            bill = data.get("bill", {})
            return {
                "bill_number": bill.get("bill_number", ""),
                "title": bill.get("title", ""),
                "summary": bill.get("description", ""),
                "status": bill.get("status_desc", ""),
                "full_text_url": bill.get("url", ""),
                "last_action": bill.get("last_action", ""),
                "last_action_date": bill.get("last_action_date", ""),
            }

        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {str(e)}"}
        except httpx.RequestError as e:
            return {"error": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}

    # --- Bulk Bill Search (used during seeding only) ---
    # Searches LegiScan by bill number to get the human-readable title and description.
    # Tries an exact match first, then falls back to the best available US federal result.
    async def search_bill_metadata(
        self,
        client: httpx.AsyncClient,
        bill_number_raw: str,
        congress: int,
    ) -> dict | None:
        query = f"{bill_number_raw} congress:{congress}"
        data = await self._get(client, {"op": "search", "state": "US", "query": query})
        if not data:
            # Fallback: simpler query without congress filter
            data = await self._get(client, {"op": "search", "state": "US", "query": bill_number_raw})
        if not data:
            return None

        results = data.get("searchresult", {})
        for key, item in results.items():
            if key == "summary" or not isinstance(item, dict):
                continue
            # Accept the first US federal result whose bill_number matches
            if (
                item.get("state", "").upper() == "US"
                and item.get("bill_number", "").upper() == bill_number_raw.upper()
            ):
                return {
                    "legiscan_id": item.get("bill_id"),
                    "title": item.get("title", "")[:500],
                    "summary": item.get("description", "")[:2000],
                    "status": item.get("last_action", "")[:100],
                }

        # No exact match — take the first US result as a best effort
        for key, item in results.items():
            if key == "summary" or not isinstance(item, dict):
                continue
            if item.get("state", "").upper() == "US":
                return {
                    "legiscan_id": item.get("bill_id"),
                    "title": item.get("title", "")[:500],
                    "summary": item.get("description", "")[:2000],
                    "status": item.get("last_action", "")[:100],
                }

        return None


# --- Singleton ---
# One shared instance used everywhere in the app — avoids re-reading the API key repeatedly.
legiscan_service = LegiScanService()
