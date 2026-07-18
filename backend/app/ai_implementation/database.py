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
from ..assessment_mode import (
    ASSESSMENT_MODE_FIXED,
    ASSESSMENT_MODE_VARIABLE,
)

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
    "assessment_mode": "TEXT NOT NULL DEFAULT 'variable'",
}

_BUDGET_UPLOAD_COLUMN_DEFINITIONS: dict[str, str] = {
    "document_role": "TEXT NOT NULL DEFAULT 'budget_source'",
    "source_mode": "TEXT NOT NULL DEFAULT 'income_statement'",
    "assessment_mode": "TEXT NOT NULL DEFAULT 'variable'",
}

_BUDGET_DRAFT_COLUMN_DEFINITIONS: dict[str, str] = {
    "enriched_storage_key": "TEXT",
    "reserve_study_upload_id": "INTEGER",
    "reserve_study_rows_json": "TEXT",
    "reserve_study_warnings_json": "TEXT",
    "reserve_study_status": "TEXT DEFAULT 'none'",
    "reserve_inflation_rate": "REAL DEFAULT 0",
    "reserve_inflation_note": "TEXT",
    "version_int": "INTEGER NOT NULL DEFAULT 0",
    "source_mode": "TEXT NOT NULL DEFAULT 'income_statement'",
    "assessment_mode": "TEXT NOT NULL DEFAULT 'variable'",
}

_BUDGET_VERSION_COLUMN_DEFINITIONS: dict[str, str] = {
    "reserve_inflation_rate": "REAL DEFAULT 0",
    "reserve_inflation_note": "TEXT",
    "source_mode": "TEXT NOT NULL DEFAULT 'income_statement'",
    "assessment_mode": "TEXT NOT NULL DEFAULT 'variable'",
}

_ANNUAL_PACKAGE_COLUMN_DEFINITIONS: dict[str, str] = {
    "assessment_mode": "TEXT NOT NULL DEFAULT 'variable'",
    # C2/C1 (fix-critical-disclosure-integrity): fifth snapshot column —
    # {assessment_matrix, hoa_metadata, hoa_settings_overrides,
    #  assessment_revenue_annual} frozen at finalize so a finalized render
    # needs no live reads beyond the snapshot columns themselves.
    "compile_context_snapshot_json": "TEXT",
}

_DISCLOSURE_JOB_COLUMN_DEFINITIONS: dict[str, str] = {
    # C1: links a render job to the annual package it targets so the
    # compile can take the frozen-snapshot branch for finalized packages.
    # NULL = ad-hoc live render (pre-change behavior).
    "annual_package_id": "INTEGER REFERENCES annual_packages(id)",
}

_HOA_SETTINGS_COLUMN_DEFINITIONS: dict[str, str] = {
    # Added so the disclosure-package Notes section can render
    # "Reserve study … dated <date>". Auto-populated when the operator
    # uploads a reserve study; editable from the Disclosure Settings form.
    "reserve_study_date": "TEXT",
    # Priority-A disclosure inputs (drifting-puzzling-grove).
    "approved_monthly_assessment_per_unit": "REAL",
    "financial_packet_archetype": "TEXT DEFAULT 'dual-fund'",
    "reserve_interest_income_override": "REAL",
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
    # Per-HOA disclosure-package letterhead logo (fix-disclosure-layout-toc-
    # special-assessment). Nullable — HOAs without one render the existing
    # default inline SVG mark. Stores a relative filename under
    # BUDGET_STORAGE_ROOT/hoa-logos/, not an absolute path (mirrors how
    # budget/DRE uploads are stored — see budget_storage_service.py).
    "logo_filename": "TEXT",
    # Per-HOA package language overrides + workbench reference PDF
    # (hoa-boilerplate-workbench).
    "boilerplate_overrides_json": "TEXT",
    "boilerplate_reference_filename": "TEXT",
}


def _table_exists(raw_conn: sqlite3.Connection, table_name: str) -> bool:
    return raw_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _table_has_column(
    raw_conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> bool:
    if not _table_exists(raw_conn, table_name):
        return False
    return any(
        str(row[1]) == column_name
        for row in raw_conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    )


def _legacy_property_assessment_mode(
    raw_conn: sqlite3.Connection,
    *,
    property_id: int,
) -> str:
    if _table_exists(raw_conn, "assessment_setups"):
        has_approved_setup = raw_conn.execute(
            """
            SELECT 1
              FROM assessment_setups
             WHERE property_id = ?
               AND status = 'approved'
             LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        if has_approved_setup is not None:
            return ASSESSMENT_MODE_VARIABLE
    if _table_exists(raw_conn, "hoa_settings"):
        fixed_hint = raw_conn.execute(
            """
            SELECT approved_monthly_assessment_per_unit
              FROM hoa_settings
             WHERE property_id = ?
             LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        if fixed_hint is not None and fixed_hint[0] not in (None, "", 0, 0.0):
            return ASSESSMENT_MODE_FIXED
    if _table_exists(raw_conn, "annual_packages"):
        fixed_package_hint = raw_conn.execute(
            """
            SELECT 1
              FROM annual_packages
             WHERE property_id = ?
               AND approved_assessment_revenue_annual IS NOT NULL
             LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        if fixed_package_hint is not None:
            return ASSESSMENT_MODE_FIXED
    return ASSESSMENT_MODE_VARIABLE


def _backfill_property_assessment_mode(raw_conn: sqlite3.Connection) -> None:
    _backfill_property_assessment_mode_with_force(raw_conn, force_overwrite=False)


def _backfill_property_assessment_mode_with_force(
    raw_conn: sqlite3.Connection,
    *,
    force_overwrite: bool,
) -> None:
    if not _table_exists(raw_conn, "properties"):
        return
    rows = raw_conn.execute("SELECT id FROM properties").fetchall()
    for row in rows:
        property_id = int(row[0])
        mode = _legacy_property_assessment_mode(raw_conn, property_id=property_id)
        if force_overwrite:
            raw_conn.execute(
                "UPDATE properties SET assessment_mode = ? WHERE id = ?",
                (mode, property_id),
            )
        else:
            raw_conn.execute(
                """
                UPDATE properties
                   SET assessment_mode = ?
                 WHERE id = ?
                   AND (assessment_mode IS NULL OR TRIM(assessment_mode) = '')
                """,
                (mode, property_id),
            )
    raw_conn.execute(
        """
        UPDATE properties
           SET assessment_mode = ?
         WHERE assessment_mode IS NULL OR TRIM(assessment_mode) = ''
        """,
        (ASSESSMENT_MODE_VARIABLE,),
    )


def _backfill_budget_assessment_mode(
    raw_conn: sqlite3.Connection,
    *,
    table_name: str,
    id_column: str,
    force_overwrite: bool = False,
) -> None:
    if not _table_exists(raw_conn, table_name):
        return
    if not _table_exists(raw_conn, "properties"):
        raw_conn.execute(
            f"""
            UPDATE {table_name}
               SET assessment_mode = ?
             WHERE assessment_mode IS NULL OR TRIM(assessment_mode) = ''
            """,
            (ASSESSMENT_MODE_VARIABLE,),
        )
        return
    property_has_assessment_mode = _table_has_column(
        raw_conn,
        "properties",
        "assessment_mode",
    )
    rows = raw_conn.execute(
        f"SELECT {id_column}, property_id FROM {table_name}"
    ).fetchall()
    for row_id, property_id in rows:
        property_mode_row = (
            raw_conn.execute(
                "SELECT assessment_mode FROM properties WHERE id = ?",
                (property_id,),
            ).fetchone()
            if property_has_assessment_mode
            else None
        )
        mode = (
            str(property_mode_row[0]).strip().lower()
            if property_mode_row and property_mode_row[0]
            else _legacy_property_assessment_mode(raw_conn, property_id=int(property_id))
        )
        if force_overwrite:
            raw_conn.execute(
                f"UPDATE {table_name} SET assessment_mode = ? WHERE {id_column} = ?",
                (mode, row_id),
            )
        else:
            raw_conn.execute(
                f"""
                UPDATE {table_name}
                   SET assessment_mode = ?
                 WHERE {id_column} = ?
                   AND (assessment_mode IS NULL OR TRIM(assessment_mode) = '')
                """,
                (mode, row_id),
            )
    raw_conn.execute(
        f"""
        UPDATE {table_name}
           SET assessment_mode = ?
         WHERE assessment_mode IS NULL OR TRIM(assessment_mode) = ''
        """,
        (ASSESSMENT_MODE_VARIABLE,),
    )


def _iter_missing_property_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    if not _table_exists(raw_conn, "properties"):
        return
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
        force_assessment_mode_backfill = any(
            column_name == "assessment_mode" for column_name, _ in missing_columns
        )
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
        if _table_exists(raw_conn, "properties"):
            _backfill_property_assessment_mode_with_force(
                raw_conn,
                force_overwrite=force_assessment_mode_backfill,
            )
        raw_conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_properties_hoa_code ON properties(hoa_code)")
        raw_conn.commit()
    finally:
        raw_conn.close()


def _iter_missing_budget_draft_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    if not _table_exists(raw_conn, "budget_drafts"):
        return
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(budget_drafts)").fetchall()
    }
    for column_name, column_sql in _BUDGET_DRAFT_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def _iter_missing_budget_upload_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    if not _table_exists(raw_conn, "budget_uploads"):
        return
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
        force_assessment_mode_backfill = any(
            column_name == "assessment_mode" for column_name, _ in missing_columns
        )
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing budget_uploads.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE budget_uploads ADD COLUMN {column_name} {column_sql}")
        if _table_exists(raw_conn, "budget_uploads"):
            raw_conn.execute(
                """
                UPDATE budget_uploads
                   SET source_mode = 'income_statement'
                 WHERE source_mode IS NULL OR TRIM(source_mode) = ''
                """
            )
            _backfill_budget_assessment_mode(
                raw_conn,
                table_name="budget_uploads",
                id_column="id",
                force_overwrite=force_assessment_mode_backfill,
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


def ensure_budget_draft_columns() -> None:
    """Brownfield migration path for draft artifact persistence columns."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_budget_draft_columns(raw_conn))
        force_assessment_mode_backfill = any(
            column_name == "assessment_mode" for column_name, _ in missing_columns
        )
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing budget_drafts.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE budget_drafts ADD COLUMN {column_name} {column_sql}")
        if _table_exists(raw_conn, "budget_drafts"):
            raw_conn.execute(
                """
                UPDATE budget_drafts
                   SET source_mode = 'income_statement'
                 WHERE source_mode IS NULL OR TRIM(source_mode) = ''
                """
            )
            _backfill_budget_assessment_mode(
                raw_conn,
                table_name="budget_drafts",
                id_column="id",
                force_overwrite=force_assessment_mode_backfill,
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


def _iter_missing_budget_version_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    if not _table_exists(raw_conn, "budget_versions"):
        return
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
        force_assessment_mode_backfill = any(
            column_name == "assessment_mode" for column_name, _ in missing_columns
        )
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing budget_versions.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE budget_versions ADD COLUMN {column_name} {column_sql}")
        if _table_exists(raw_conn, "budget_versions"):
            raw_conn.execute(
                """
                UPDATE budget_versions
                   SET source_mode = 'income_statement'
                 WHERE source_mode IS NULL OR TRIM(source_mode) = ''
                """
            )
            _backfill_budget_assessment_mode(
                raw_conn,
                table_name="budget_versions",
                id_column="id",
                force_overwrite=force_assessment_mode_backfill,
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


def _iter_missing_annual_package_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    if not _table_exists(raw_conn, "annual_packages"):
        return
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(annual_packages)").fetchall()
    }
    for column_name, column_sql in _ANNUAL_PACKAGE_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_annual_package_columns() -> None:
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_annual_package_columns(raw_conn))
        force_assessment_mode_backfill = any(
            column_name == "assessment_mode" for column_name, _ in missing_columns
        )
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing annual_packages.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE annual_packages ADD COLUMN {column_name} {column_sql}")
        if _table_exists(raw_conn, "annual_packages"):
            _backfill_budget_assessment_mode(
                raw_conn,
                table_name="annual_packages",
                id_column="id",
                force_overwrite=force_assessment_mode_backfill,
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


def ensure_disclosure_job_columns() -> None:
    """Brownfield migration for disclosure_package_jobs columns (C1)."""
    raw_conn = engine.raw_connection()
    try:
        if not _table_exists(raw_conn, "disclosure_package_jobs"):
            return
        existing = {
            row[1]
            for row in raw_conn.execute(
                "PRAGMA table_info(disclosure_package_jobs)"
            ).fetchall()
        }
        for column_name, column_sql in _DISCLOSURE_JOB_COLUMN_DEFINITIONS.items():
            if column_name not in existing:
                logger.info(
                    "Adding missing disclosure_package_jobs.%s column", column_name
                )
                raw_conn.execute(
                    f"ALTER TABLE disclosure_package_jobs "
                    f"ADD COLUMN {column_name} {column_sql}"
                )
        raw_conn.commit()
    finally:
        raw_conn.close()


def _iter_missing_hoa_settings_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    if not _table_exists(raw_conn, "hoa_settings"):
        return
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
    "budget_line_derivation": "TEXT NOT NULL DEFAULT 'unknown'",
    "residual_after_pool_keys_json": "TEXT NOT NULL DEFAULT '[]'",
    "residual_exclusions_json": "TEXT NOT NULL DEFAULT '[]'",
    "derivation_evidence_json": "TEXT",
    "escalation_schedule_json": "TEXT DEFAULT '[]'",
    "starting_monthly_per_unit": "REAL",
    "denominator_label": "TEXT",
    # Added by add-variable-special-assessments: structural classifier so a
    # special-assessment pool is recognized by a field, never by its
    # AI-generated pool_name. NULL = ordinary pool. The one recognized value is
    # 'separately_billed_special_assessment' (see _pool_is_visible hidden-kinds).
    "pool_kind": "TEXT",
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
    "job_status": "TEXT NOT NULL DEFAULT 'completed'",
    "error_message": "TEXT NOT NULL DEFAULT ''",
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
    # Added by ccr-assessment-extraction: discriminates DRE vs governing-doc
    # (CC&R) extraction runs so each path keeps its own listing and hashes.
    "document_type": "TEXT NOT NULL DEFAULT 'dre'",
    # Operator-entered per-unit allocation factors for CC&R runs where the
    # legal text defines the method but not the per-unit quantities.
    # JSON: {"unit_number": {"square_feet": N, "ownership_percent": X}, ...}
    "operator_unit_factors_json": "TEXT NOT NULL DEFAULT '{}'",
}


_DRE_DOCUMENTS_COLUMN_DEFINITIONS: dict[str, str] = {
    # Added by ccr-assessment-extraction: 'dre' | 'ccr'; existing rows default to 'dre'.
    "document_type": "TEXT NOT NULL DEFAULT 'dre'",
}


def _iter_missing_dre_documents_columns(
    raw_conn: sqlite3.Connection,
) -> Iterable[tuple[str, str]]:
    if not _table_exists(raw_conn, "dre_documents"):
        return
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(dre_documents)").fetchall()
    }
    for column_name, column_sql in _DRE_DOCUMENTS_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_dre_documents_columns() -> None:
    """Brownfield migration for dre_documents columns added post-launch."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_dre_documents_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing dre_documents.%s column", column_name)
            raw_conn.execute(
                f"ALTER TABLE dre_documents ADD COLUMN {column_name} {column_sql}"
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


_ASSESSMENT_BUDGET_MAPPING_RULE_COLUMN_DEFINITIONS: dict[str, str] = {
    "source_parent_category": "TEXT",
    "assessment_type": "TEXT NOT NULL DEFAULT 'unknown_needs_review'",
    "review_required": "INTEGER NOT NULL DEFAULT 0",
    "review_reason": "TEXT NOT NULL DEFAULT ''",
    "source_evidence_text": "TEXT NOT NULL DEFAULT ''",
}

_BUDGET_LINE_POOL_MAPPING_COLUMN_DEFINITIONS: dict[str, str] = {
    "source_rule_id": "INTEGER",
    "mapping_source": "TEXT NOT NULL DEFAULT 'operator'",
    "match_method": "TEXT",
    "approval_status": "TEXT NOT NULL DEFAULT 'approved'",
    "review_state": "TEXT NOT NULL DEFAULT 'ready'",
    "budget_line_amount": "NUMERIC",
    "approved_by": "TEXT",
    "approved_at": "TEXT",
    "active": "INTEGER NOT NULL DEFAULT 1",
}


def _iter_missing_budget_line_pool_mapping_columns(
    raw_conn: sqlite3.Connection,
) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(budget_line_pool_mappings)").fetchall()
    }
    for column_name, column_sql in _BUDGET_LINE_POOL_MAPPING_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_budget_line_pool_mapping_columns() -> None:
    """Brownfield migration path for generated mapping provenance columns."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_budget_line_pool_mapping_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing budget_line_pool_mappings.%s column", column_name)
            raw_conn.execute(
                f"ALTER TABLE budget_line_pool_mappings ADD COLUMN {column_name} {column_sql}"
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


_BUDGET_LINE_MERGE_COLUMN_DEFINITIONS: dict[str, str] = {
    "tenant_id": "INTEGER NOT NULL DEFAULT 1",
    "property_id": "INTEGER",
    "primary_account_code": "TEXT",
    "primary_label": "TEXT",
    "primary_normalized_label": "TEXT",
    "secondary_account_code": "TEXT",
    "secondary_label": "TEXT",
    "secondary_normalized_label": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "decision_source": "TEXT NOT NULL DEFAULT 'manual'",
    "actor": "TEXT NOT NULL DEFAULT 'system'",
    "created_at": "TEXT NOT NULL DEFAULT (datetime('now'))",
    "disabled_at": "TEXT",
    "updated_at": "TEXT NOT NULL DEFAULT (datetime('now'))",
}

_BUDGET_LINE_MERGE_APPLICATION_COLUMN_DEFINITIONS: dict[str, str] = {
    "tenant_id": "INTEGER NOT NULL DEFAULT 1",
    "merge_id": "INTEGER",
    "property_id": "INTEGER",
    "budget_draft_id": "INTEGER",
    "assessment_setup_id": "INTEGER",
    "source": "TEXT NOT NULL DEFAULT 'manual'",
    "status": "TEXT NOT NULL DEFAULT 'applied'",
    "match_strategy": "TEXT",
    "before_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
    "after_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
    "side_effect_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
    "actor": "TEXT NOT NULL DEFAULT 'system'",
    "created_at": "TEXT NOT NULL DEFAULT (datetime('now'))",
    "unmerged_at": "TEXT",
    "finalized_at": "TEXT",
}


def _iter_missing_columns(
    raw_conn: sqlite3.Connection,
    table_name: str,
    column_definitions: dict[str, str],
) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, column_sql in column_definitions.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_budget_line_merges_columns() -> None:
    """Create or repair the durable GL merge-rule table."""
    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS budget_line_merges (
                id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id                  INTEGER NOT NULL DEFAULT 1,
                property_id                INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
                primary_account_code       TEXT,
                primary_label              TEXT NOT NULL,
                primary_normalized_label   TEXT NOT NULL,
                secondary_account_code     TEXT,
                secondary_label            TEXT NOT NULL,
                secondary_normalized_label TEXT NOT NULL,
                status                     TEXT NOT NULL DEFAULT 'active'
                                           CHECK(status IN ('active', 'disabled')),
                decision_source            TEXT NOT NULL
                                           CHECK(decision_source IN ('manual', 'gemini_suggestion', 'system')),
                actor                      TEXT NOT NULL,
                created_at                 TEXT NOT NULL DEFAULT (datetime('now')),
                disabled_at                TEXT,
                updated_at                 TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_line_merges_active_rule
                ON budget_line_merges(
                    tenant_id, property_id,
                    COALESCE(primary_account_code, ''), primary_normalized_label,
                    COALESCE(secondary_account_code, ''), secondary_normalized_label
                )
                WHERE status = 'active';

            CREATE INDEX IF NOT EXISTS idx_budget_line_merges_property_status
                ON budget_line_merges(tenant_id, property_id, status);
            """
        )
        for column_name, column_sql in _iter_missing_columns(
            raw_conn,
            "budget_line_merges",
            _BUDGET_LINE_MERGE_COLUMN_DEFINITIONS,
        ):
            logger.info("Adding missing budget_line_merges.%s column", column_name)
            raw_conn.execute(
                f"ALTER TABLE budget_line_merges ADD COLUMN {column_name} {column_sql}"
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


def ensure_budget_line_merge_applications_columns() -> None:
    """Create or repair per-draft GL merge application storage."""
    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS budget_line_merge_applications (
                id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id                  INTEGER NOT NULL DEFAULT 1,
                merge_id                   INTEGER NOT NULL REFERENCES budget_line_merges(id) ON DELETE CASCADE,
                property_id                INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
                budget_draft_id            INTEGER NOT NULL REFERENCES budget_drafts(id) ON DELETE CASCADE,
                assessment_setup_id        INTEGER REFERENCES assessment_setups(id) ON DELETE SET NULL,
                source                     TEXT NOT NULL
                                           CHECK(source IN ('manual', 'gemini_suggestion', 'auto_applied')),
                status                     TEXT NOT NULL DEFAULT 'applied'
                                           CHECK(status IN ('applied', 'unmerged', 'finalized')),
                match_strategy             TEXT,
                before_snapshot_json       TEXT NOT NULL,
                after_snapshot_json        TEXT NOT NULL,
                side_effect_snapshot_json  TEXT NOT NULL,
                actor                      TEXT NOT NULL,
                created_at                 TEXT NOT NULL DEFAULT (datetime('now')),
                unmerged_at                TEXT,
                finalized_at               TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_line_merge_applications_applied
                ON budget_line_merge_applications(tenant_id, budget_draft_id, merge_id)
                WHERE status = 'applied';

            CREATE INDEX IF NOT EXISTS idx_budget_line_merge_applications_property_status
                ON budget_line_merge_applications(tenant_id, property_id, status);

            CREATE INDEX IF NOT EXISTS idx_budget_line_merge_applications_draft_status
                ON budget_line_merge_applications(tenant_id, budget_draft_id, status);
            """
        )
        for column_name, column_sql in _iter_missing_columns(
            raw_conn,
            "budget_line_merge_applications",
            _BUDGET_LINE_MERGE_APPLICATION_COLUMN_DEFINITIONS,
        ):
            logger.info(
                "Adding missing budget_line_merge_applications.%s column",
                column_name,
            )
            raw_conn.execute(
                f"ALTER TABLE budget_line_merge_applications ADD COLUMN {column_name} {column_sql}"
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


def _iter_missing_dre_extraction_runs_columns(
    raw_conn: sqlite3.Connection,
) -> Iterable[tuple[str, str]]:
    if not _table_exists(raw_conn, "dre_extraction_runs"):
        return
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(dre_extraction_runs)").fetchall()
    }
    for column_name, column_sql in _DRE_EXTRACTION_RUNS_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


_SUGGESTION_RUN_COLUMN_DEFINITIONS: dict[str, str] = {
    "projected_deficit": "REAL",
    "recommended_assessment_increase_pct": "REAL",
    "assessment_recommendation_note": "TEXT",
}


def _iter_missing_suggestion_run_columns(
    raw_conn: sqlite3.Connection,
) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(suggestion_runs)").fetchall()
    }
    for column_name, column_sql in _SUGGESTION_RUN_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_suggestion_run_columns() -> None:
    """Brownfield migration path for suggestion_runs columns added post-launch."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_suggestion_run_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing suggestion_runs.%s column", column_name)
            raw_conn.execute(
                f"ALTER TABLE suggestion_runs ADD COLUMN {column_name} {column_sql}"
            )
        raw_conn.commit()
    finally:
        raw_conn.close()


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


def _iter_missing_assessment_budget_mapping_rule_columns(
    raw_conn: sqlite3.Connection,
) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute(
            "PRAGMA table_info(assessment_budget_mapping_rules)"
        ).fetchall()
    }
    for column_name, column_sql in (
        _ASSESSMENT_BUDGET_MAPPING_RULE_COLUMN_DEFINITIONS.items()
    ):
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_assessment_budget_mapping_rule_columns() -> None:
    """Brownfield migration path for DRE mapping evidence rule columns."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(
            _iter_missing_assessment_budget_mapping_rule_columns(raw_conn)
        )
        for column_name, column_sql in missing_columns:
            logger.info(
                "Adding missing assessment_budget_mapping_rules.%s column",
                column_name,
            )
            raw_conn.execute(
                f"ALTER TABLE assessment_budget_mapping_rules ADD COLUMN {column_name} {column_sql}"
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


def rebuild_aupa_source_check(conn) -> bool:
    """Widen the ``source`` CHECK on ``assessment_unit_pool_allocations`` to
    accept ``'equal_split_placeholder'`` (C7). Returns True when a rebuild
    ran, False when the table was already new-shape (or absent).

    SQLite cannot ALTER a CHECK constraint, so pre-change DBs get the
    standard table rebuild: create the new-shape table, copy rows, drop the
    old table, rename, recreate the index. Detection is by inspecting the
    stored DDL in ``sqlite_master`` — run-twice safe (the rebuilt table's
    DDL contains the new value, so subsequent runs no-op).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='assessment_unit_pool_allocations'"
    ).fetchone()
    if row is None or "equal_split_placeholder" in (row[0] or ""):
        return False
    logger.info(
        "Rebuilding assessment_unit_pool_allocations to widen the "
        "source CHECK (adding 'equal_split_placeholder')"
    )
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute(
            """
            CREATE TABLE assessment_unit_pool_allocations_new (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                assessment_unit_id       INTEGER NOT NULL
                                         REFERENCES assessment_units(id) ON DELETE CASCADE,
                assessment_setup_id      INTEGER NOT NULL
                                         REFERENCES assessment_setups(id) ON DELETE CASCADE,
                pool_key                 TEXT NOT NULL,
                pool_id                  INTEGER REFERENCES allocation_pools(id) ON DELETE SET NULL,
                specified_monthly_amount NUMERIC NOT NULL,
                source                   TEXT NOT NULL DEFAULT 'dre'
                                         CHECK (source IN ('dre','manual','equal_split_placeholder')),
                source_page              INTEGER,
                notes                    TEXT,
                UNIQUE (assessment_unit_id, pool_key)
            )
            """
        )
        conn.execute(
            "INSERT INTO assessment_unit_pool_allocations_new "
            "(id, assessment_unit_id, assessment_setup_id, pool_key, "
            " pool_id, specified_monthly_amount, source, source_page, notes) "
            "SELECT id, assessment_unit_id, assessment_setup_id, pool_key, "
            "       pool_id, specified_monthly_amount, source, source_page, notes "
            "FROM assessment_unit_pool_allocations"
        )
        conn.execute("DROP TABLE assessment_unit_pool_allocations")
        conn.execute(
            "ALTER TABLE assessment_unit_pool_allocations_new "
            "RENAME TO assessment_unit_pool_allocations"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assessment_unit_pool_alloc_setup "
            "ON assessment_unit_pool_allocations(assessment_setup_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return True


def ensure_assessment_unit_pool_allocations_source_check() -> None:
    """Brownfield entry point for :func:`rebuild_aupa_source_check`."""
    raw_conn = engine.raw_connection()
    try:
        rebuild_aupa_source_check(raw_conn)
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
            # Two-line form: the disclosure templates now render this via the
            # ``nl2br`` filter, so the street and city/state/zip land on their
            # own lines like the old hardcoded letterhead block.
            "cpa_firm_address": "100 Montgomery Street, Suite 715\nSan Francisco, California 94104",
            "reserve_study_expert_name": "SMA Reserves of San Jose",
            "letter_signed_by_title": "Tri-State Enterprises, Inc.",
        }
        for col, val in text_defaults.items():
            raw_conn.execute(
                f"UPDATE hoa_settings SET {col} = ? WHERE {col} IS NULL OR {col} = ''",
                (val,),
            )
        # One-time backfill: rows seeded earlier hold the CPA address as a
        # single comma-joined line. Now that templates read
        # ``hoa_settings.cpa_firm_address`` (instead of a hardcoded two-line
        # block), rewrite that exact legacy literal to the two-line form so
        # existing HOAs keep the letterhead layout. Idempotent — matches only
        # the old string, so it is a no-op once converted or if an operator has
        # since edited the value.
        raw_conn.execute(
            "UPDATE hoa_settings SET cpa_firm_address = ? WHERE cpa_firm_address = ?",
            (
                "100 Montgomery Street, Suite 715\nSan Francisco, California 94104",
                "100 Montgomery Street, Suite 715, San Francisco, California 94104",
            ),
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
    ensure_budget_upload_columns()
    ensure_budget_draft_columns()
    ensure_budget_version_columns()
    ensure_dre_extraction_runs_columns()
    ensure_dre_documents_columns()
    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(_SCHEMA_PATH.read_text())
        raw_conn.commit()
        logger.info("Database schema initialized.")
    finally:
        raw_conn.close()
    ensure_suggestion_run_columns()
    ensure_property_columns()
    ensure_hoa_settings_columns()
    ensure_annual_package_columns()
    ensure_allocation_pool_columns()
    ensure_assessment_budget_mapping_rule_columns()
    ensure_budget_line_pool_mapping_columns()
    ensure_budget_line_merges_columns()
    ensure_budget_line_merge_applications_columns()
    ensure_assessment_unit_pool_allocations_source_check()
    ensure_disclosure_job_columns()
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
