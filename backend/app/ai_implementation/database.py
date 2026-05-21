"""Database initialization and backward-compatible shims.

Schema source of truth: schema.sql (run on startup).
All new code should use db.session.get_session() instead of get_db().
"""
import contextlib
import logging
import sqlite3
from pathlib import Path
from typing import Iterable

from .config import settings
from .db.session import engine

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_PROPERTY_COLUMN_DEFINITIONS: dict[str, str] = {
    "hoa_code": "TEXT",
    "tax_id": "TEXT",
    "reserve_inflation_rate": "REAL DEFAULT 0",
    "fiscal_year_end_month": "INTEGER DEFAULT 12",
    "city": "TEXT",
    "portfolio_year": "INTEGER",
    "workflow_status": "TEXT DEFAULT 'Not Started'",
    # HOA-permanent facts (drifting-puzzling-grove refactor) — never change
    # year-over-year, so they live on the Property row, not the
    # per-package Disclosure Settings form.
    "state": "TEXT DEFAULT 'CA'",
    "entity_type": "TEXT",
    "incorporation_year": "INTEGER",
    # Multi-tenancy column reservation (Phase 5.8 of
    # dre-driven-assessment-engine). v1 hardcodes tenant_id=1; data-access
    # filters by tenant_id so a future RBAC rollout is purely
    # row-level-policy work. No semantic effect in v1.
    "tenant_id": "INTEGER NOT NULL DEFAULT 1",
    # Quick lookup for the currently-approved AssessmentSetup for this HOA
    # (Phase 4.2 of dre-driven-assessment-engine, task 106). The approval
    # service writes this column when promoting an extraction. Engine
    # callers prefer this over querying assessment_setups with
    # status='approved' so renames/migrations don't require re-querying.
    "default_assessment_setup_id": "INTEGER",
}

_BUDGET_UPLOAD_COLUMN_DEFINITIONS: dict[str, str] = {
    "document_role": "TEXT NOT NULL DEFAULT 'budget_source'",
}

_BUDGET_DRAFT_COLUMN_DEFINITIONS: dict[str, str] = {
    "enriched_storage_key": "TEXT",
    "reserve_study_upload_id": "INTEGER",
    "reserve_study_rows_json": "TEXT",
    "reserve_study_warnings_json": "TEXT",
    "reserve_study_status": "TEXT DEFAULT 'none'",
    "reserve_inflation_rate": "REAL DEFAULT 0",
    "reserve_inflation_note": "TEXT",
}

_BUDGET_VERSION_COLUMN_DEFINITIONS: dict[str, str] = {
    "reserve_inflation_rate": "REAL DEFAULT 0",
    "reserve_inflation_note": "TEXT",
}

_HOA_SETTINGS_COLUMN_DEFINITIONS: dict[str, str] = {
    # Added so the disclosure-package Notes section can render
    # "Reserve study … dated <date>". Auto-populated when the operator
    # uploads a reserve study; editable from the Disclosure Settings form.
    "reserve_study_date": "TEXT",
    # Priority-A disclosure inputs (drifting-puzzling-grove).
    "approved_monthly_assessment_per_unit": "REAL",
    "income_tax_provision_override": "REAL",
    "reserve_funding_source": "TEXT DEFAULT 'reserve_study_provision'",
    "reserve_funding_manual_amount": "REAL",
    "special_assessments_json": "TEXT DEFAULT '[]'",
    "additional_assessments_needed_json": "TEXT DEFAULT '[]'",
    "outstanding_loan_json": "TEXT",
    # Phase 1 boilerplate-gap fields (drifting-puzzling-grove).
    "letter_date": "TEXT",
    "letter_signed_by_title": "TEXT",
    "accountant_report_date": "TEXT",
    "reserve_funding_plan_date": "TEXT",
    "hoa_state": "TEXT DEFAULT 'CA'",
    "hoa_entity_type": "TEXT",
    "hoa_incorporation_year": "INTEGER",
    # 30-year reserve funding study (drifting-puzzling-grove rebuild).
    # assessment_increase_schedule_json: list of {start_year, end_year, rate}
    # rate is decimal (0.03 = 3%). Used by the cash-flow forecast to escalate
    # the replacement-fund per-unit monthly assessment year by year.
    #
    # DEPRECATED (Task #185 of dre-driven-assessment-engine): these two values
    # have moved to ``allocation_pools.escalation_schedule_json`` +
    # ``allocation_pools.starting_monthly_per_unit`` so the forecast is driven
    # by the per-HOA AssessmentSetup. The hoa_settings columns remain readable
    # as a fallback for HOAs that haven't migrated; the compile-side overlay
    # in ``disclosure_package/service.py`` prefers pool values when present.
    # Do NOT add new readers — go through the AssessmentSetup path instead.
    "assessment_increase_schedule_json": "TEXT DEFAULT '[]'",
    # Separate per-unit monthly assessment dedicated to the replacement fund
    # (distinct from the operations monthly assessment). Used as the base for
    # the cash-flow forecast's "Regular assessments" row. When NULL the
    # compiler falls back to (reserve_provision / units / 12).
    "replacement_fund_monthly_assessment_per_unit": "REAL",
    # board_deferrals_json: list of {year, amount} — operator-entered
    # deferrals of scheduled reserve expenditures by year (rare; usually []).
    "board_deferrals_json": "TEXT DEFAULT '[]'",
    # Multi-tenancy + optimistic-lock columns (Phase 5.8 + 5.9 of
    # dre-driven-assessment-engine).
    "tenant_id": "INTEGER NOT NULL DEFAULT 1",
    "version_int": "INTEGER NOT NULL DEFAULT 0",
}


def _iter_missing_property_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(properties)").fetchall()
    }
    for column_name, column_sql in _PROPERTY_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_property_columns() -> None:
    """Brownfield migration path for Phase 1 HOA settings columns."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_property_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing properties.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE properties ADD COLUMN {column_name} {column_sql}")

        raw_conn.execute(
            """
            UPDATE properties
               SET fiscal_year_end_month = CASE
                     WHEN fiscal_year_end_month IS NULL
                     THEN ((COALESCE(fiscal_year_start_month, 1) + 10) % 12) + 1
                     ELSE fiscal_year_end_month
                   END,
                   workflow_status = COALESCE(NULLIF(TRIM(workflow_status), ''), 'Not Started')
            """
        )
        raw_conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_properties_hoa_code ON properties(hoa_code)")
        raw_conn.commit()
    finally:
        raw_conn.close()


def _iter_missing_budget_draft_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(budget_drafts)").fetchall()
    }
    for column_name, column_sql in _BUDGET_DRAFT_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def _iter_missing_budget_upload_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(budget_uploads)").fetchall()
    }
    for column_name, column_sql in _BUDGET_UPLOAD_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_budget_upload_columns() -> None:
    """Brownfield migration path for upload metadata added after initial launch."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_budget_upload_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing budget_uploads.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE budget_uploads ADD COLUMN {column_name} {column_sql}")
        raw_conn.commit()
    finally:
        raw_conn.close()


def ensure_budget_draft_columns() -> None:
    """Brownfield migration path for draft artifact persistence columns."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_budget_draft_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing budget_drafts.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE budget_drafts ADD COLUMN {column_name} {column_sql}")
        raw_conn.commit()
    finally:
        raw_conn.close()


def _iter_missing_budget_version_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(budget_versions)").fetchall()
    }
    for column_name, column_sql in _BUDGET_VERSION_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_budget_version_columns() -> None:
    """Brownfield migration path for version reserve inflation columns."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_budget_version_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing budget_versions.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE budget_versions ADD COLUMN {column_name} {column_sql}")
        raw_conn.commit()
    finally:
        raw_conn.close()


def _iter_missing_hoa_settings_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(hoa_settings)").fetchall()
    }
    for column_name, column_sql in _HOA_SETTINGS_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_hoa_settings_columns() -> None:
    """Brownfield migration path for hoa_settings columns added post-launch."""
    raw_conn = engine.raw_connection()
    try:
        # Table may not yet exist on a freshly-created DB during the
        # init_db() executescript run order — schema.sql creates it
        # before this function is called, so PRAGMA will succeed.
        missing_columns = list(_iter_missing_hoa_settings_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing hoa_settings.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE hoa_settings ADD COLUMN {column_name} {column_sql}")
        raw_conn.commit()
    finally:
        raw_conn.close()


# Task #185 of dre-driven-assessment-engine: 30-year forecast inputs moved
# from hoa_settings to per-pool storage so AssessmentSetup is the source of
# truth for the cash-flow forecast.
_ALLOCATION_POOL_COLUMN_DEFINITIONS: dict[str, str] = {
    "escalation_schedule_json": "TEXT DEFAULT '[]'",
    "starting_monthly_per_unit": "REAL",
}


def _iter_missing_allocation_pool_columns(
    raw_conn: sqlite3.Connection,
) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(allocation_pools)").fetchall()
    }
    for column_name, column_sql in _ALLOCATION_POOL_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


_DRE_EXTRACTION_RUNS_COLUMN_DEFINITIONS: dict[str, str] = {
    # Added by dre-extraction-structured-output: pairs with prompt_sha256
    # so audit can detect wire-schema drift independently of prompt drift.
    "wire_schema_sha256": "TEXT NOT NULL DEFAULT ''",
    # Added by v2.0 hardening: capture what Gemini actually resolved the
    # model alias to, why generation stopped, and how many tokens were
    # consumed. Lets audit answer "which model produced this run?" when
    # the alias has rotated since.
    "model_version_resolved": "TEXT NOT NULL DEFAULT ''",
    "finish_reason": "TEXT NOT NULL DEFAULT ''",
    "output_tokens_used": "INTEGER NOT NULL DEFAULT 0",
}


def _iter_missing_dre_extraction_runs_columns(
    raw_conn: sqlite3.Connection,
) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(dre_extraction_runs)").fetchall()
    }
    for column_name, column_sql in _DRE_EXTRACTION_RUNS_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_dre_extraction_runs_columns() -> None:
    """Brownfield migration path for dre_extraction_runs columns added post-launch."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_dre_extraction_runs_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info(
                "Adding missing dre_extraction_runs.%s column", column_name
            )
            raw_conn.execute(
                f"ALTER TABLE dre_extraction_runs ADD COLUMN {column_name} {column_sql}"
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


def ensure_allocation_pool_columns() -> None:
    """Brownfield migration path for allocation_pools columns added post-launch."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_allocation_pool_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing allocation_pools.%s column", column_name)
            raw_conn.execute(
                f"ALTER TABLE allocation_pools ADD COLUMN {column_name} {column_sql}"
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


def _seed_tri_state_disclosure_defaults() -> None:
    """One-time seed for Tri-State Enterprises-managed HOAs.

    The user's whole portfolio is managed by Tri-State (with Levy, Erlanger &
    Company as CPA, SMA Reserves of San Jose as reserve expert). These values
    are the same for every HOA in the portfolio, so we backfill blank
    hoa_settings columns with the standard Tri-State boilerplate. Operators
    can override per-HOA via the Disclosure Settings form.

    Old Mill specifically also gets its HOA-permanent facts (state,
    entity_type, incorporation_year) backfilled on the Property row.
    """
    raw_conn = engine.raw_connection()
    try:
        # Tri-State portfolio defaults — written to hoa_settings rows where
        # the column is currently NULL. Existing operator-typed values are
        # preserved (COALESCE keeps non-null values intact).
        text_defaults = {
            "management_company": "Tri-State Enterprises, Inc.",
            "management_company_address": "2133 Leghorn Street, Mountain View, CA 94043",
            "management_company_phone": "650.210.0085",
            "management_company_fax": "650.210.0086",
            "management_company_web": "www.3state.net",
            "cpa_firm_name": "Levy, Erlanger & Company LLP",
            "cpa_firm_address": "100 Montgomery Street, Suite 715, San Francisco, California 94104",
            "reserve_study_expert_name": "SMA Reserves of San Jose",
            "letter_signed_by_title": "Tri-State Enterprises, Inc.",
        }
        for col, val in text_defaults.items():
            raw_conn.execute(
                f"UPDATE hoa_settings SET {col} = ? WHERE {col} IS NULL OR {col} = ''",
                (val,),
            )
        # Numeric rate defaults — only seed when row is at the "unset"
        # sentinel (NULL or 0.0). Treating 0.0 as "unset" here is intentional:
        # the compiler's run_render_job now honors a literal 0, so explicit
        # 0% rates set via the form are preserved on subsequent restarts
        # only if the operator types a non-zero value first. (Acceptable
        # trade-off for the boilerplate-default UX.)
        raw_conn.execute(
            "UPDATE hoa_settings SET replacement_cost_increase_rate = 0.03 "
            "WHERE replacement_cost_increase_rate IS NULL OR replacement_cost_increase_rate = 0"
        )
        raw_conn.execute(
            "UPDATE hoa_settings SET interest_rate_after_tax = 0.018 "
            "WHERE interest_rate_after_tax IS NULL OR interest_rate_after_tax = 0"
        )

        # HOA-permanent facts on the Property row (state defaults to CA via
        # the column definition; entity_type / incorporation_year are
        # per-HOA). Only Old Mill is seeded with specific values today;
        # other HOAs default to CA and operator-set entity/incorporation.
        raw_conn.execute(
            """
            UPDATE properties
               SET entity_type = COALESCE(entity_type, 'non-profit mutual benefit corporation'),
                   incorporation_year = COALESCE(incorporation_year, 1973)
             WHERE LOWER(name) LIKE '%old mill%'
            """
        )
        # Backfill state='CA' on any row where it's null (column default
        # only applies to new rows).
        raw_conn.execute(
            "UPDATE properties SET state = 'CA' WHERE state IS NULL OR state = ''"
        )

        # Old Mill 30-year reserve funding study seed
        # (drifting-puzzling-grove rebuild). Only backfill when the columns
        # are at their "unset" sentinel — never overwrite operator edits.
        old_mill_schedule = (
            '[{"start_year": 2026, "end_year": 2035, "rate": 0.03}, '
            '{"start_year": 2036, "end_year": 2045, "rate": 0.03}, '
            '{"start_year": 2046, "end_year": 2055, "rate": 0.00}]'
        )
        raw_conn.execute(
            """
            UPDATE hoa_settings
               SET assessment_increase_schedule_json = ?
             WHERE (assessment_increase_schedule_json IS NULL
                    OR assessment_increase_schedule_json = ''
                    OR assessment_increase_schedule_json = '[]')
               AND property_id IN (
                   SELECT id FROM properties WHERE LOWER(name) LIKE '%old mill%'
               )
            """,
            (old_mill_schedule,),
        )
        raw_conn.execute(
            """
            UPDATE hoa_settings
               SET replacement_fund_monthly_assessment_per_unit = 200.98
             WHERE replacement_fund_monthly_assessment_per_unit IS NULL
               AND property_id IN (
                   SELECT id FROM properties WHERE LOWER(name) LIKE '%old mill%'
               )
            """
        )
        raw_conn.commit()
    finally:
        raw_conn.close()


def _seed_old_mill_assessment_setup() -> None:
    """Seed Old Mill's assessment_setup + equal-costs pool.

    Old Mill is the regression baseline: a fixed-pattern HOA with 279 units
    paying $605/mo each via a single equal-allocation pool. The $605 value
    is NOT seeded as a permanent property of the HOA — operators set the
    annual approved revenue per package via the Disclosure Settings form
    (drifting-puzzling-grove rebuild) — but having the assessment_setup +
    pool rows in place lets the engine produce per-recipient results
    against the existing budget data on day one.

    Idempotent: skips when an approved AssessmentSetup already exists for
    the property. Never overwrites operator-edited setups.
    """
    raw_conn = engine.raw_connection()
    try:
        property_row = raw_conn.execute(
            "SELECT id, units FROM properties WHERE LOWER(name) LIKE '%old mill%' LIMIT 1"
        ).fetchone()
        if property_row is None:
            return
        property_id, units = property_row[0], property_row[1]
        if not units:
            units = 279  # regression baseline

        existing = raw_conn.execute(
            "SELECT id FROM assessment_setups WHERE property_id = ? AND status = 'approved' LIMIT 1",
            (property_id,),
        ).fetchone()
        if existing is not None:
            return

        cur = raw_conn.execute(
            """
            INSERT INTO assessment_setups
                (property_id, setup_type, display_mode, status, approved_at)
            VALUES
                (?, 'fixed', 'fixed', 'approved', datetime('now'))
            """,
            (property_id,),
        )
        setup_id = cur.lastrowid

        raw_conn.execute(
            """
            INSERT INTO allocation_pools
                (assessment_setup_id, pool_key, pool_name, allocation_method,
                 recipient_scope, denominator_source, variable_flag, display_order)
            VALUES
                (?, 'equal_costs', 'Equal Costs', 'equal',
                 'all_units', 'calculated', 0, 1)
            """,
            (setup_id,),
        )
        raw_conn.commit()
        logger.info("Seeded Old Mill assessment_setup id=%s (units=%s)", setup_id, units)
    finally:
        raw_conn.close()


def init_db() -> None:
    """Execute schema.sql to create tables. Safe to call on existing DB."""
    logger.info("Initializing database from schema.sql...")
    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(_SCHEMA_PATH.read_text())
        raw_conn.commit()
        logger.info("Database schema initialized.")
    finally:
        raw_conn.close()
    ensure_property_columns()
    ensure_budget_upload_columns()
    ensure_budget_draft_columns()
    ensure_budget_version_columns()
    ensure_hoa_settings_columns()
    ensure_allocation_pool_columns()
    ensure_dre_extraction_runs_columns()
    _seed_tri_state_disclosure_defaults()
    _seed_old_mill_assessment_setup()


# ── Backward-compatible shims (used by seed script) ──

def get_db() -> sqlite3.Connection:
    """DEPRECATED: Returns raw sqlite3 connection for seed script compatibility.
    New code should use db.get_session via Depends() instead."""
    db_path = Path(settings.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextlib.contextmanager
def write_lock():
    """DEPRECATED: No-op. SQLAlchemy session handles transactions."""
    yield
