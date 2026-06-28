# --- Imports ---
# SQLAlchemy column types and tools for defining database tables.
import enum
from datetime import datetime
from sqlalchemy import String, Integer, Float, ForeignKey, Enum, DateTime, func, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
# JSON works with SQLite (stored as TEXT) and PostgreSQL.
# Swap JSON → JSONB for PostgreSQL when you want indexed JSON queries:
#   from sqlalchemy.dialects.postgresql import JSONB
from database import Base


# --- Enums ---
# Fixed set of allowed values for the favorite_type column.
class FavoriteType(str, enum.Enum):
    politician = "politician"
    bill = "bill"


# --- Congressional Data Tables ---
# These tables are populated by seed_db.py using @unitedstates bulk data.
# They are read-only during normal app operation — the AI queries them but never writes to them.

class Politician(Base):
    __tablename__ = "politicians"

    bioguide_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    party: Mapped[str | None] = mapped_column(String(50))
    state: Mapped[str | None] = mapped_column(String(2))
    # LIS ID (Legislative Information System) — used to match Senate.gov vote XML.
    # Populated from legislators-current.json during seeding.
    lis_member_id: Mapped[str | None] = mapped_column(String(10), index=True)

    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="politician")
    district: Mapped["District | None"] = relationship(
        "District", back_populates="representative", foreign_keys="District.representative_bioguide_id"
    )


# ---------------------------------------------------------------------------
# District — House districts only (Senate members have no district row).
#
# Primary key is a canonical string like "TX-07" or "CA-AT" (at-large).
#
# POLLING / SENTIMENT DATA PHILOSOPHY
# ------------------------------------
# True district-level polling barely exists — most polling firms only survey
# competitive districts in the final ~60 days of an election cycle. What we
# store instead are reliable proxies for constituent sentiment that cover
# every district, every cycle:
#
#   cook_pvi / pvi_score
#     Cook Political Report Partisan Voter Index. Expressed as "R+5" or "D+8".
#     Measures how a district votes relative to the national average using the
#     last two presidential election results. The best single number for
#     "how partisan is this district." Updated after each presidential election.
#     - Source: Cook Political Report (paywalled, but GitHub aggregators exist)
#       e.g. github.com/jeffreymorganio/d3-country-bubble-chart has historical CSVs
#       or scrape the Wikipedia table "Cook Partisan Voter Index" annually.
#     - Why use it: lets you flag when a rep votes against their district's lean,
#       which is the most politically meaningful story to surface to users.
#
#   last_dem_pct / last_rep_pct / last_margin / last_election_year
#     Actual election results from the most recent House race in that district.
#     More granular than PVI — captures wave years, incumbency advantage, etc.
#     A rep who won 55-45 in a R+12 district is underperforming; one who won
#     60-40 in a D+2 district is outperforming. That gap is the story.
#     - Source: MIT Election Data and Science Lab (free, CC license)
#       dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/IG0UN2
#       Downloads as a CSV of all House results back to 1976.
#     - Also available from: Ballotpedia (per-district pages), OpenElections
#       (openelections.net — state-by-state certified results as CSVs).
#
# ---------------------------------------------------------------------------
# FUTURE DATA SOURCES — add columns + ingestion here as needed
# ---------------------------------------------------------------------------
#
#   FiveThirtyEight / ABC Partisan Lean
#     Similar to Cook PVI but uses a blend of presidential + Senate results
#     and demographic trends. More up-to-date between elections than Cook.
#     Published as a free CSV: github.com/fivethirtyeight/data (partisan-lean folder).
#     Add column: fte_partisan_lean Float (negative = D, positive = R)
#
#   Campaign Finance (OpenSecrets)
#     Total raised, top donor industries, PAC vs small-dollar split.
#     Useful for correlating voting record with donor interests.
#     API: opensecrets.org/api (free tier, 200 req/day)
#     Add columns: total_raised_usd Float, top_industry String, pac_pct Float
#
#   Census / ACS Demographics
#     Median income, education level, racial composition, urban/rural index.
#     Powerful for explaining WHY a district votes the way it does.
#     Source: Census Bureau API (free, no key for most endpoints)
#       api.census.gov/data/2022/acs/acs5
#     Add columns: median_income Int, pct_college_degree Float, pct_urban Float
#
#   Dave's Redistricting App
#     District shapefiles + demographic breakdowns after 2020 redistricting.
#     Good for map-based UI features.
#     davesredistricting.org — export GeoJSON per district.
#     Add column: geojson JSONB
#
#   State-level polling (for Senate comparison)
#     FiveThirtyEight polling averages, RealClearPolitics averages.
#     These are the best available for Senate sentiment comparison.
#     Neither provides a bulk data API — you'd scrape or build a scheduled
#     fetch job. Add to a separate StatePolling table rather than here.
#
# ---------------------------------------------------------------------------

class District(Base):
    __tablename__ = "districts"

    # e.g. "TX-07", "CA-AT" (at-large), "AK-01"
    district_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    district_number: Mapped[int | None] = mapped_column(Integer)  # None = at-large

    # Current representative (null if seat is vacant or between terms)
    representative_bioguide_id: Mapped[str | None] = mapped_column(
        String(10), ForeignKey("politicians.bioguide_id"), nullable=True, index=True
    )

    # Cook Partisan Voter Index — e.g. "R+5", "D+8", "EVEN"
    cook_pvi: Mapped[str | None] = mapped_column(String(10))
    # Numeric version: positive = Republican lean, negative = Democrat lean
    # Derived from cook_pvi on insert. Useful for sorting and range queries.
    pvi_score: Mapped[float | None] = mapped_column(Float)

    # Most recent House general election results for this district
    last_dem_pct: Mapped[float | None] = mapped_column(Float)
    last_rep_pct: Mapped[float | None] = mapped_column(Float)
    # Positive = R won, negative = D won (rep_pct - dem_pct)
    last_margin: Mapped[float | None] = mapped_column(Float)
    last_election_year: Mapped[int | None] = mapped_column(Integer)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    representative: Mapped["Politician | None"] = relationship(
        "Politician", back_populates="district", foreign_keys=[representative_bioguide_id]
    )


class Bill(Base):
    __tablename__ = "bills"

    # bill_number is our PK, e.g. "S1234-119"
    bill_number: Mapped[str] = mapped_column(String(50), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[str | None] = mapped_column(String(100))
    congress: Mapped[int | None] = mapped_column(Integer, index=True)
    chamber: Mapped[str | None] = mapped_column(String(10))  # House / Senate

    # --- Tagging fields (populated by tag_bills.py) ---
    # List of issue categories from the 22-category taxonomy.
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # "Bill", "Joint Resolution", "Resolution", or "Procedural"
    bill_type: Mapped[str | None] = mapped_column(String(20))
    # True if this bill covers many unrelated policy areas (e.g. appropriations omnibus).
    is_omnibus: Mapped[bool] = mapped_column(default=False, nullable=False)

    # AI-extracted structured summary (populated on-demand by summarize_bill.py).
    # Stores ~800 words covering key provisions, who it affects, fiscal impact, legal basis.
    # Fetched once from Congress.gov full text, then cached here — never fetched again.
    ai_summary: Mapped[str | None] = mapped_column(String(8000))

    # UI-facing plain-English description of the bill (75-150 words).
    # Main points only — no riders or hidden provisions. Generated by summarize_bills.py.
    bill_description: Mapped[str | None] = mapped_column(String(2000))

    # 2-3 sentence description of what a Yea vote concretely accomplished.
    # Derived from ai_summary by summarize_bills.py. Used on senator record pages.
    yea_impact: Mapped[str | None] = mapped_column(String(500))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="bill")


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("bioguide_id", "bill_number", "position", name="uq_vote"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bioguide_id: Mapped[str] = mapped_column(
        String(10), ForeignKey("politicians.bioguide_id"), nullable=False, index=True
    )
    bill_number: Mapped[str] = mapped_column(
        String(50), ForeignKey("bills.bill_number"), nullable=False, index=True
    )
    position: Mapped[str] = mapped_column(String(20), nullable=False)  # Yea/Nay/Present

    politician: Mapped["Politician"] = relationship("Politician", back_populates="votes")
    bill: Mapped["Bill"] = relationship("Bill", back_populates="votes")


# --- Candidate Tables ---
# Candidates running for office in upcoming elections.
# Populated by seed_candidates.py using the FEC API (official federal source).

class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    party: Mapped[str | None] = mapped_column(String(50))
    election_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    office: Mapped[str] = mapped_column(String(20), nullable=False)  # "Senate" / "House"
    incumbent: Mapped[bool] = mapped_column(default=False, nullable=False)

    # True when this candidate has no positions extracted yet — needs a website or manual entry
    needs_update: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Race status — updated by seed_candidates.py --check-status
    # "declared"  — actively running (FEC shows active, no withdrawal signals)
    # "suspended" — campaign paused (language on website or FEC inactive flag)
    # "withdrawn" — formally dropped out (FEC inactive + website confirms)
    # "primary_winner" / "primary_loser" — set after primary results
    race_status: Mapped[str] = mapped_column(String(30), default="declared", nullable=False, index=True)
    race_status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # FEC candidate ID — e.g. "S8GA00180" — lets us re-query FEC for updates
    fec_candidate_id: Mapped[str | None] = mapped_column(String(20), index=True)

    # If they're a sitting senator, link to their voting record
    bioguide_id: Mapped[str | None] = mapped_column(
        String(10), ForeignKey("politicians.bioguide_id"), nullable=True, index=True
    )

    website_url: Mapped[str | None] = mapped_column(String(500))
    ballotpedia_url: Mapped[str | None] = mapped_column(String(500))

    # Stated positions mapped to our 22-category taxonomy.
    # Format: {"Healthcare": "Supports expanding Medicaid", "Immigration": "Opposes amnesty"}
    positions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    positions_source: Mapped[str | None] = mapped_column(String(100))
    positions_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    politician: Mapped["Politician | None"] = relationship(
        "Politician", foreign_keys=[bioguide_id]
    )


# --- User & App Tables ---
# These tables are written to by the app as users sign up, chat, and save favorites.

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    favorites: Mapped[list["UserFavorite"]] = relationship("UserFavorite", back_populates="user")
    chat_sessions: Mapped[list["ChatSession"]] = relationship("ChatSession", back_populates="user")


class UserFavorite(Base):
    __tablename__ = "user_favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    favorite_type: Mapped[FavoriteType] = mapped_column(Enum(FavoriteType), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="favorites")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    session_title: Mapped[str | None] = mapped_column(String(255))
    # Stores the full back-and-forth conversation as a JSON list so the AI has memory across messages.
    messages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship("User", back_populates="chat_sessions")
