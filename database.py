# --- Imports ---
# Tools needed to connect to the database and load environment variables.
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv


# --- Environment ---
# Reads the .env file so DATABASE_URL and other secrets are available.
load_dotenv()


# --- Database URL ---
# SQLite by default — just a local file, no server needed.
# To switch to PostgreSQL, change this to:
#   postgresql+asyncpg://user:password@localhost:5432/strawpoll
# and swap aiosqlite for asyncpg in requirements.txt.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./strawpoll.db")


# --- Engine ---
# The engine is the actual connection to the database file (or server).
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    # SQLite doesn't support pool_pre_ping — only needed for PostgreSQL
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


# --- Session Factory ---
# A session is a single conversation with the database — open one, run queries, close it.
# This factory creates new sessions on demand throughout the app.
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# --- Base Class ---
# All database table definitions in models.py inherit from this.
# SQLAlchemy uses it to track which tables exist.
class Base(DeclarativeBase):
    pass


# --- Session Dependency ---
# FastAPI calls this automatically for any endpoint that needs the database.
# Opens a session, hands it to the endpoint, then commits or rolls back when done.
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# --- Table Creation ---
# Creates all database tables on first run if they don't already exist.
# Called once when the server starts (see main.py lifespan).
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
