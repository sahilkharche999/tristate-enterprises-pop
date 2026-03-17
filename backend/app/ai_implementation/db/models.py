"""SQLAlchemy ORM models — mirrors schema.sql (which is the source of truth).

These models are for querying/inserting only. Tables are created by schema.sql.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, Text, Float, ForeignKey, text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── Shared constants ─────────────────────────────────────────────────────────

DECIDED_STATUSES = ["accepted", "modified"]
MAX_PCT_CHANGE = 0.30

FEATURE_COLUMNS = [
    "account_level_1", "account_level_2", "account_level_3",
    "adjusted_pct_diff", "adjusted_coverage_ratio",
    "seasonality_index", "normalized_annual_budget",
    "is_income", "is_reserve", "is_admin",
]

_CREATED_AT_DEFAULT = text("datetime('now')")


# ── Models ───────────────────────────────────────────────────────────────────

class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False, unique=True)
    units = Column(Integer)
    fiscal_year_start_month = Column(Integer, default=1)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    runs = relationship("SuggestionRun", back_populates="property", lazy="raise")
    cases = relationship("FeedbackCase", back_populates="property", lazy="raise")


class SuggestionRun(Base):
    __tablename__ = "suggestion_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    source = Column(Text, nullable=False)
    total_annual_budget = Column(Float)
    total_ytd_actuals = Column(Float)
    pct_year_elapsed = Column(Float)
    statement_month = Column(Integer)
    fiscal_year = Column(Integer)
    growth_factor = Column(Float)
    executive_summary = Column(Text)
    coherence_score = Column(Text)
    total_budget_impact = Column(Text)
    flagged_items_json = Column(Text)
    latency_ms = Column(Integer)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    property = relationship("Property", back_populates="runs", lazy="raise")
    cases = relationship("FeedbackCase", back_populates="run", lazy="raise")


class FeedbackCase(Base):
    __tablename__ = "feedback_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("suggestion_runs.id"), nullable=False)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    account_code = Column(Integer, nullable=False)
    account_name = Column(Text, nullable=False)
    label = Column(Text, nullable=False)
    category = Column(Text)
    account_level_1 = Column(Integer)
    account_level_2 = Column(Integer)
    account_level_3 = Column(Integer)
    is_income = Column(Integer, default=0)
    is_reserve = Column(Integer, default=0)
    is_admin = Column(Integer, default=0)
    annual_budget = Column(Float, nullable=False)
    ytd_actual = Column(Float, nullable=False)
    projection = Column(Float)
    pct_diff = Column(Float)
    coverage_ratio = Column(Float)
    adjusted_pct_diff = Column(Float)
    adjusted_coverage_ratio = Column(Float)
    seasonality_index = Column(Float)
    normalized_annual_budget = Column(Float)
    cbr_anchor_pct = Column(Float)
    cbr_similarity = Column(Float)
    ml_baseline_pct = Column(Float)
    ai_suggested_pct_change = Column(Float)
    ai_reason = Column(Text)
    ai_confidence = Column(Float)
    revised_by_pass2 = Column(Integer, default=0)
    user_decision = Column(Text, default="pending")
    user_final_pct_change = Column(Float)
    user_note = Column(Text)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    run = relationship("SuggestionRun", back_populates="cases", lazy="raise")
    property = relationship("Property", back_populates="cases", lazy="raise")


class SOPRule(Base):
    __tablename__ = "sop_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_text = Column(Text, nullable=False)
    active = Column(Integer, default=1)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, nullable=False, unique=True)
    name = Column(Text, nullable=False)
    hashed_password = Column(Text, nullable=False)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)
