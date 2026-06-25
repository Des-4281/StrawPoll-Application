# --- Imports ---
# FastAPI for the web server, Anthropic for the AI, SQLAlchemy for the database.
import os
import json
from contextlib import asynccontextmanager
from datetime import datetime

import anthropic
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from dotenv import load_dotenv

from database import get_db, init_db
from models import User, UserFavorite, ChatSession, FavoriteType
from ai_tools import TOOL_DEFINITIONS, execute_tool


# --- Environment & AI Client ---
# Loads the Anthropic API key and creates a single shared Claude client.
load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# --- App Startup ---
# Runs once when the server starts — creates any missing database tables.
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="AI Congressional Vote Tracker", lifespan=lifespan)


# --- Request Schemas ---
# Defines the shape of data the API expects to receive for each endpoint.
# FastAPI validates incoming requests against these automatically.

class CreateUserRequest(BaseModel):
    email: EmailStr


class ChatRequest(BaseModel):
    user_id: int
    session_id: int | None = None  # omit to start a new conversation
    message: str


class AddFavoriteRequest(BaseModel):
    user_id: int
    favorite_type: FavoriteType
    reference_id: str


# --- Internal Helpers ---
# Utility functions used by the AI loop — not exposed as API endpoints.

def _serialize_content(content) -> list[dict]:
    # Converts Claude's response objects into plain dicts so they can be stored as JSON.
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks = []
    for block in content:
        if isinstance(block, dict):
            blocks.append(block)
        elif hasattr(block, "model_dump"):
            blocks.append(block.model_dump())
        else:
            blocks.append({"type": "text", "text": str(block)})
    return blocks


def _extract_text(content) -> str:
    # Pulls the plain text out of Claude's response to send back to the user.
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block["text"]
        if hasattr(block, "type") and block.type == "text":
            return block.text
    return ""


# --- AI Agent Loop ---
# The core of the app. Sends the conversation to Claude, runs any tool calls
# it requests (database lookups, bill searches), feeds the results back,
# and repeats until Claude has a final answer to return to the user.
async def _run_agent_loop(messages: list[dict], db: AsyncSession) -> tuple[str, list[dict]]:
    while True:
        with claude.messages.stream(
            model="claude-opus-4-8",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            tools=TOOL_DEFINITIONS,
            messages=messages,
            system=(
                "You are an expert AI assistant for tracking US congressional voting records. "
                "You have access to a database of politician votes and can look up live bill details. "
                "Always use the available tools to find accurate, up-to-date information before answering. "
                "Be concise, cite bill numbers and vote positions when available."
            ),
        ) as stream:
            response = stream.get_final_message()

        messages.append({"role": "assistant", "content": _serialize_content(response.content)})

        if response.stop_reason == "end_turn":
            return _extract_text(response.content), messages

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                block_type = block.type if hasattr(block, "type") else block.get("type")
                if block_type == "tool_use":
                    tool_id = block.id if hasattr(block, "id") else block["id"]
                    tool_name = block.name if hasattr(block, "name") else block["name"]
                    tool_input = block.input if hasattr(block, "input") else block["input"]
                    result_str = await execute_tool(tool_name, tool_input, db)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result_str,
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop reason — return whatever text exists
            return _extract_text(response.content), messages


# --- API Endpoints ---
# The four URLs the app exposes. A frontend or mobile app calls these.

@app.post("/users", status_code=201)
async def create_user(body: CreateUserRequest, db: AsyncSession = Depends(get_db)):
    # Creates a new user account. Returns 409 if the email is already registered.
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(email=body.email)
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return {"id": user.id, "email": user.email, "created_at": user.created_at}


@app.post("/chat")
async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    # Sends a message to the AI and returns its response.
    # Loads prior conversation history if session_id is provided so the AI has context.
    user = await db.get(User, body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    session: ChatSession | None = None
    if body.session_id:
        session = await db.get(ChatSession, body.session_id)
        if not session or session.user_id != body.user_id:
            raise HTTPException(status_code=404, detail="Chat session not found")

    existing_messages: list[dict] = session.messages if session else []
    messages = list(existing_messages) + [{"role": "user", "content": body.message}]

    final_text, updated_messages = await _run_agent_loop(messages, db)

    # Saves the updated conversation back to the database.
    if session:
        session.messages = updated_messages
        session.updated_at = datetime.utcnow()
    else:
        title = body.message[:80] + ("…" if len(body.message) > 80 else "")
        session = ChatSession(
            user_id=body.user_id,
            session_title=title,
            messages=updated_messages,
        )
        db.add(session)
        await db.flush()
        await db.refresh(session)

    return {
        "session_id": session.id,
        "response": final_text,
    }


@app.post("/favorites", status_code=201)
async def add_favorite(body: AddFavoriteRequest, db: AsyncSession = Depends(get_db)):
    # Saves a politician or bill to a user's watchlist.
    user = await db.get(User, body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    favorite = UserFavorite(
        user_id=body.user_id,
        favorite_type=body.favorite_type,
        reference_id=body.reference_id,
    )
    db.add(favorite)
    await db.flush()
    await db.refresh(favorite)
    return {
        "id": favorite.id,
        "user_id": favorite.user_id,
        "favorite_type": favorite.favorite_type,
        "reference_id": favorite.reference_id,
    }


@app.get("/favorites/{user_id}")
async def get_favorites(user_id: int, db: AsyncSession = Depends(get_db)):
    # Returns all politicians and bills a user has saved to their watchlist.
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(
        select(UserFavorite).where(UserFavorite.user_id == user_id)
    )
    favorites = result.scalars().all()
    return [
        {
            "id": f.id,
            "favorite_type": f.favorite_type,
            "reference_id": f.reference_id,
        }
        for f in favorites
    ]
