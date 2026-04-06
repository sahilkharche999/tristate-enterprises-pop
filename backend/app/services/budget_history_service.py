"""Durable HOA-scoped budget history service."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..generate_budget import infer_growth_factor_from_input
from ..generate_budget_pipeline import BudgetPipeline
from ..models.financial_document_extraction import (
    DocumentExtractionFailure,
    ExtractedFinancialStatement,
    ExtractedStatementLineItem,
)
from ..services.income_statement_parser import (
    parse_rows_with_sections,
    detect_columns,
    _parse_financial_float as _parse_financial_float_new,
    _match_section_header,
)
from ..services.financial_document_router import choose_financial_document_route
from ..services.financial_statement_validation import (
    has_blocking_validation_issues,
    validate_extracted_statement,
)
from ..services.normalized_statement_workbook import build_normalized_statement_workbook
from ..services.pdf_vlm_extractor import extract_pdf_statement
from ..services.statement_period_inference import (
    extract_pdf_statement_period_hint,
    infer_growth_factor_from_statement_period,
)
from ..models.budget_history import (
    BudgetDraftCompareBaselineOption,
    BudgetDraftCompareOptionsResponse,
    BudgetDraftCompareResponse,
    BudgetDraftReserveReviewRequest,
    BudgetDraftReserveReviewResponse,
    BudgetDraftChangeSummary,
    BudgetDraftCompareRow,
    BudgetReserveComponentRow,
    BudgetReserveReviewSummary,
    BudgetDraftSummary,
    BudgetDraftPayload,
    BudgetDraftSaveRequest,
    BudgetGenerateRequest,
    BudgetGenerateResponse,
    BudgetHistoryResponse,
    BudgetNoteRecord,
    BudgetNoteSaveRequest,
    BudgetNoteSaveResponse,
    BudgetTimelineEvent,
    BudgetUploadResponse,
    BudgetVersionCompareCard,
    BudgetVersionCompareResponse,
    BudgetVersionDetail,
    BudgetVersionMetadataUpdateRequest,
    BudgetVersionMetadataUpdateResponse,
    BudgetVersionReopenResponse,
    BudgetVersionSummary,
)
from ..services import macros_service
from ..ai_implementation.db.models import (
    BUDGET_DRAFT_ACTIVE,
    BUDGET_DRAFT_GENERATED,
    BUDGET_DRAFT_SUPERSEDED,
    BUDGET_VERSION_STAGE_FINAL,
    BUDGET_VERSION_STAGE_INTERIM,
    BudgetAuditEvent,
    BudgetDraft,
    BudgetNote,
    BudgetUpload,
    BudgetVersion,
    Property,
)

# Enriched workbook output column indices (fixed positions added by the pipeline)
# These are NOT input indices — they reference AK/AL columns written by IncomeStatementEnricher.
_COL_PROJECTION_INDEX = 37
_COL_PERCENT_CHANGE_INDEX = 38


def _now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _storage_root() -> Path:
    root = Path(settings.BUDGET_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _json_loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _write_atomic_bytes(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    temp_path.replace(destination)


def _relative_storage_path(*parts: object) -> str:
    return str(Path(*map(str, parts)))


def _budget_storage_path(relative_path: str) -> Path:
    return _storage_root() / relative_path


def _storage_file_available(relative_path: Optional[str]) -> bool:
    return bool(relative_path and _budget_storage_path(relative_path).exists())


def _write_temp_workbook(file_bytes: bytes, original_filename: str) -> str:
    suffix = Path(original_filename or "upload.xlsx").suffix or ".xlsx"
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=suffix, dir=settings.TEMP_DIR)
    os.close(fd)
    Path(path).write_bytes(file_bytes)
    return path


def _ensure_xlsx(temp_input_path: str) -> str:
    """Convert .xls or .pdf to .xlsx for pipeline processing. Returns the .xlsx path."""
    ext = Path(temp_input_path).suffix.lower()
    if ext == ".xls":
        from .income_statement_parser import _read_xls_rows
        from openpyxl import Workbook as _Workbook
        rows = _read_xls_rows(temp_input_path)
        wb = _Workbook()
        ws = wb.active
        ws.title = "Income Statement"
        for r, row in enumerate(rows, start=1):
            for c, val in enumerate(row, start=1):
                ws.cell(row=r, column=c, value=val)
        xlsx_path = temp_input_path.replace(".xls", ".xlsx")
        wb.save(xlsx_path)
        wb.close()
        return xlsx_path
    elif ext == ".pdf":
        from .income_statement_parser import _read_pdf_rows
        from openpyxl import Workbook as _Workbook
        rows = _read_pdf_rows(temp_input_path)
        wb = _Workbook()
        ws = wb.active
        ws.title = "Income Statement"
        for r, row in enumerate(rows, start=1):
            for c, val in enumerate(row, start=1):
                ws.cell(row=r, column=c, value=val)
        xlsx_path = temp_input_path.rsplit(".", 1)[0] + ".xlsx"
        wb.save(xlsx_path)
        wb.close()
        return xlsx_path
    return temp_input_path


def _draft_enriched_storage_key(hoa_id: int, draft_id: int) -> str:
    return _relative_storage_path("hoa", hoa_id, "drafts", draft_id, "enriched.xlsx")


def _extract_statement_month(fiscal_year_start_month: int, detected_months: int) -> int:
    return (fiscal_year_start_month + detected_months - 2) % 12 + 1


def _canonical_statement_from_line_items(
    line_items: list[dict[str, Any]],
    *,
    family: str,
) -> ExtractedFinancialStatement:
    canonical_items: list[ExtractedStatementLineItem] = []
    for item in line_items:
        raw_section = str(item.get("raw", {}).get("section") or item.get("section") or item.get("category") or "")
        account_code = item.get("account_code")
        canonical_items.append(
            ExtractedStatementLineItem(
                account_code_text=None if account_code is None else str(account_code),
                label=str(item.get("label") or item.get("name") or ""),
                section_label=raw_section or None,
                section_kind=str(item.get("category") or "") or None,
                ytd_actual=_parse_float(item.get("ytd_actual")) if item.get("ytd_actual") is not None else None,
                annual_budget=_parse_float(item.get("annual_budget")) if item.get("annual_budget") is not None else None,
            )
        )
    return ExtractedFinancialStatement(
        document_family=family,
        report_type="income_statement",
        line_items=canonical_items,
        confidence=1.0,
    )


def _build_review_required_response(
    session: Session,
    *,
    hoa_id: int,
    actor: dict[str, Any],
    upload: BudgetUpload,
    original_filename: str,
    reason: str,
    code: str,
    warnings: Optional[list[str]] = None,
) -> BudgetUploadResponse:
    upload.enrichment_status = "failed"
    review_event = _create_audit_event(
        session,
        hoa_id=hoa_id,
        actor=actor,
        event_type="enrichment_review_required",
        summary=f"Review required for {original_filename}",
        upload_id=upload.id,
        payload={"code": code, "reason": reason},
    )
    session.commit()
    return BudgetUploadResponse(
        upload_id=upload.id,
        draft=None,
        timeline_event=_serialize_timeline_event(review_event),
        warnings=warnings or [],
        review_required=True,
        review_reason=reason,
    )


def _extract_pdf_statement_sync(path: str) -> ExtractedFinancialStatement | DocumentExtractionFailure:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, extract_pdf_statement(path)).result()

    return asyncio.run(extract_pdf_statement(path))


def _parse_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {"-", "—"}:
            return 0.0
        text = text.replace(",", "").replace("$", "")
        try:
            return float(text)
        except ValueError:
            return 0.0
    return 0.0


def _normalize_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_compare_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_line_item_key(value: Any) -> Optional[str]:
    text = _normalize_optional_text(value)
    return text if text is not None else None


def _line_item_amount(item: Optional[dict[str, Any]]) -> float:
    if not item:
        return 0.0
    proposed_amount = item.get("proposed_amount")
    if proposed_amount is None:
        proposed_amount = item.get("proposedAmount")
    if proposed_amount is not None:
        return _parse_float(proposed_amount)

    annual_budget = _parse_float(item.get("annual_budget"))
    raw_percent_change = item.get("percent_change")
    if raw_percent_change is None:
        raw_percent_change = item.get("percentChange")
    percent_change = _parse_float(raw_percent_change)
    return annual_budget * (1 + (percent_change / 100.0))


def _line_item_category(item: Optional[dict[str, Any]]) -> str:
    if not item:
        return "operating"
    category = _normalize_optional_text(item.get("category"))
    if category:
        return category.lower()
    label = str(item.get("label") or "")
    account_code = item.get("account_code")
    section = str(item.get("raw", {}).get("section") or "")
    return _infer_category(label, _extract_account_code(str(account_code or label)), section)


def _line_item_label(item: Optional[dict[str, Any]]) -> str:
    if not item:
        return ""
    return _normalize_optional_text(item.get("label")) or _normalize_optional_text(item.get("name")) or ""


def _line_item_account_code_text(item: Optional[dict[str, Any]]) -> Optional[str]:
    if not item:
        return None
    account_code = item.get("account_code")
    if account_code is None:
        account_code = _extract_account_code(_line_item_label(item))
    return _normalize_optional_text(account_code)


def _line_item_match_tokens(item: Optional[dict[str, Any]]) -> list[str]:
    if not item:
        return []
    tokens: list[str] = []
    line_item_key = _normalize_line_item_key(item.get("line_item_key"))
    label = _normalize_compare_text(_line_item_label(item))
    account_code = _line_item_account_code_text(item)
    for token in (line_item_key, label, account_code):
        normalized = _normalize_compare_text(token)
        if normalized and normalized not in tokens:
            tokens.append(normalized)
    return tokens


def _build_line_item_index(line_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in line_items:
        for token in _line_item_match_tokens(item):
            index.setdefault(token, item)
    return index


def _find_matching_line_item(
    item: Optional[dict[str, Any]],
    *,
    index: dict[str, dict[str, Any]],
) -> Optional[dict[str, Any]]:
    for token in _line_item_match_tokens(item):
        match = index.get(token)
        if match is not None:
            return match
    return None


def _is_reserve_item(item: Optional[dict[str, Any]]) -> bool:
    if not item:
        return False
    cat = _line_item_category(item)
    return cat in ("reserve", "reserve_income", "reserve_expense")


def _reserve_group_for_item(item: Optional[dict[str, Any]]) -> Optional[str]:
    if not _is_reserve_item(item):
        return None
    explicit_group = _normalize_optional_text(
        item.get("reserve_group") if item else None
    ) or _normalize_optional_text(item.get("reserveGroup") if item else None)
    if explicit_group in {"component", "income", "transfer"}:
        return explicit_group
    normalized_label = _normalize_compare_text(_line_item_label(item))
    normalized_section = _normalize_compare_text(item.get("raw", {}).get("section") if item else "")
    if normalized_section == "reserve income" or "reserve income" in normalized_section:
        return "income"
    if "allocation to reserves" in normalized_section or normalized_section == "allocation to reserves":
        return "transfer"
    # Reserve expense items default to component
    if "reserve expense" in normalized_section:
        return "component"
    # Label-based fallbacks for pre-existing data
    if "reserve income" in normalized_label or "interest earned reserve" in normalized_label or "change in asset value" in normalized_label:
        return "income"
    if "allocation/transfer" in normalized_label or "transfer" in normalized_label:
        return "transfer"
    return "component"


def _is_reserve_overlay_excluded(label: str) -> bool:
    normalized_label = _normalize_compare_text(label)
    return (
        "reserve income" in normalized_label
        or "allocation/transfer" in normalized_label
        or "transfer" in normalized_label
    )


def _is_reserve_overlay_eligible(row: dict[str, Any]) -> bool:
    return (
        bool(row.get("is_reserve"))
        and str(row.get("reserve_group") or "") == "component"
        and _parse_float(row.get("baseline_amount")) > 0.0
    )


def _apply_reserve_inflation_overlay(
    rows: list[dict[str, Any]],
    *,
    reserve_inflation_rate: float,
) -> list[dict[str, Any]]:
    overlaid_rows: list[dict[str, Any]] = []
    for row in rows:
        updated_row = dict(row)
        eligible = _is_reserve_overlay_eligible(updated_row)
        baseline_amount = _parse_float(updated_row.get("baseline_amount"))
        updated_row["reserve_inflation_eligible"] = eligible
        updated_row["inflation_adjusted_baseline_amount"] = (
            baseline_amount * (1 + reserve_inflation_rate)
            if reserve_inflation_rate > 0.0 and eligible
            else baseline_amount
        )
        comparison_baseline_amount = _parse_float(updated_row["inflation_adjusted_baseline_amount"])
        current_amount = _parse_float(updated_row.get("current_amount"))
        updated_row["comparison_baseline_amount"] = comparison_baseline_amount
        updated_row["delta_amount"] = current_amount - comparison_baseline_amount
        updated_row["delta_percent"] = (
            None
            if comparison_baseline_amount == 0.0
            else ((current_amount - comparison_baseline_amount) / comparison_baseline_amount) * 100
        )
        updated_row["changed"] = abs(updated_row["delta_amount"]) > 1e-9
        overlaid_rows.append(updated_row)
    return overlaid_rows


def _extract_account_code(label: str) -> Optional[int]:
    head = (label or "").split("-", 1)[0].strip()
    return int(head) if head.isdigit() else None


def _infer_category(label: str, account_code: Optional[int], section: str) -> str:
    """Infer category from section state. Falls back to account code ranges.

    IMPORTANT: Does NOT check label keywords. Section position is authoritative.
    """
    normalized_section = (section or "").strip().lower()

    # Section state is the primary signal (from section state machine)
    if normalized_section.startswith("operating income") or normalized_section.startswith("income"):
        return "income"
    if normalized_section.startswith("operating expense") or normalized_section.startswith("operating"):
        return "operating"
    if normalized_section.startswith("reserve"):
        return "reserve"

    # Fallback to account code ranges when no section header matched
    if account_code is not None:
        if 40000 <= account_code <= 49999:
            return "income"
        if 50000 <= account_code <= 89999:
            return "operating"
        if account_code >= 90000:
            return "reserve"

    return "operating"


def _line_items_to_percent_changes(line_items: list[dict[str, Any]]) -> dict[str, float]:
    changes: dict[str, float] = {}
    for item in line_items:
        if item.get("read_only") or item.get("readOnly"):
            continue
        label = macros_service._normalize_label(str(item.get("label") or item.get("name") or ""))
        if not label:
            continue
        raw_percent_change = item.get("percent_change")
        if raw_percent_change is None:
            raw_percent_change = item.get("percentChange")
        if raw_percent_change is None:
            continue
        changes[label] = _parse_float(raw_percent_change) / 100.0
    return changes


def _table_to_line_items(table: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Returns (line_items, warnings)."""
    warnings: list[str] = []
    headers = [str(header or f"column_{index}") for index, header in enumerate(table.get("headers", []), start=1)]

    if "Label" not in headers:
        # Raw income statement layout — use section-aware parser
        rows = table.get("rows", [])
        col_indices = detect_columns([table.get("headers", [])] + rows[:9])
        detection_tier = col_indices.pop("_detection_tier", 1)
        if detection_tier == 3:
            warnings.append(
                "Heads up \u2014 the column layout in your file looks a bit different from what we "
                "usually see. The numbers might not line up perfectly. If anything looks off, try "
                "re-uploading the standard monthly income statement from your accounting software."
            )
        # Scan all header/early rows for enrichment column labels (written by pipeline at row 5)
        for scan_row in ([table.get("headers", [])] + rows[:9]):
            for ci, cell in enumerate(scan_row or []):
                cell_text = str(cell or "").strip().lower()
                if cell_text == "projection" and "projection" not in col_indices:
                    col_indices["projection"] = ci
                elif cell_text in ("% change", "percent change") and "percent_change" not in col_indices:
                    col_indices["percent_change"] = ci
        col_indices.setdefault("projection", _COL_PROJECTION_INDEX)
        col_indices.setdefault("percent_change", _COL_PERCENT_CHANGE_INDEX)
        return parse_rows_with_sections(rows, col_indices), warnings

    # Pre-processed table with "Label" header (from enriched/generated workbook)
    line_items: list[dict[str, Any]] = []
    for row in table.get("rows", []):
        item = {header: row[index] if index < len(row) else None for index, header in enumerate(headers)}
        label = item.get("Label") or item.get("Account Name") or item.get("Description") or f"Line Item {len(line_items) + 1}"
        account_code = item.get("Account Code") or item.get("Account")
        line_item_key = str(account_code or label)
        section = item.get("Section") or item.get("section") or ""
        category = _infer_category(str(label), _extract_account_code(str(account_code or label)), section)
        normalized = {
            "line_item_key": line_item_key,
            "account_code": account_code,
            "label": label,
            "category": category,
            "ytd_actual": item.get("YTD Actual"),
            "annual_budget": item.get("Annual Budget"),
            "projection": item.get("Projection"),
            "percent_change": item.get("% Change") or item.get("Percent Change"),
            "read_only": category == "reserve_expense",  # only reserve study expense items are read-only
            "raw": item,
        }
        line_items.append(normalized)
    return line_items, warnings


def _extract_totals_from_preview(preview: Optional[dict[str, Any]]) -> tuple[float, float, float]:
    rows = preview.get("rows", []) if preview else []
    totals = {
        "total income": 0.0,
        "total expense": 0.0,
        "net operating income": 0.0,
    }
    for row in rows:
        if len(row) < 2:
            continue
        # Find the label in any of the first few cells (handles both old 12-month
        # layout where col A has labels, and new 8-column reference format where
        # col A is account code and col B is the label).
        key = None
        for cell in row[:3]:
            candidate = str(cell or "").strip().lower()
            if candidate in totals:
                key = candidate
                break
        if key is None:
            continue
        # Find the largest absolute numeric value in the row — this is the annual
        # total regardless of which column layout is used.
        best = 0.0
        for cell in row:
            try:
                value = float(cell or 0)
                if abs(value) > abs(best):
                    best = value
            except (TypeError, ValueError):
                continue
        totals[key] = best
    return totals["total income"], totals["total expense"], totals["net operating income"]


def _actor_name(actor: dict[str, Any]) -> str:
    return str(actor.get("name") or actor.get("email") or "Unknown User")


def _serialize_timeline_event(event: BudgetAuditEvent) -> BudgetTimelineEvent:
    return BudgetTimelineEvent(
        id=event.id,
        event_type=event.event_type,
        summary=event.summary,
        actor_name=event.actor_name,
        occurred_at=event.created_at,
        related_upload_id=event.upload_id,
        related_draft_id=event.draft_id,
        related_version_id=event.version_id,
        related_note_id=event.note_id,
        file_name=None,
        version_code=None,
        payload=_json_loads(event.payload_json, None),
    )


def _serialize_draft(draft: BudgetDraft, upload: Optional[BudgetUpload] = None) -> BudgetDraftPayload:
    return BudgetDraftPayload(
        id=draft.id,
        status=draft.status,
        source_upload_id=draft.source_upload_id,
        reopened_from_version_id=draft.reopened_from_version_id,
        line_items=_json_loads(draft.line_items_json, []),
        global_note=draft.global_note,
        statement_month=draft.statement_month,
        growth_factor=draft.growth_factor,
        growth_factor_note=draft.growth_factor_note,
        reserve_inflation_rate=draft.reserve_inflation_rate,
        reserve_inflation_note=draft.reserve_inflation_note,
        updated_at=draft.updated_at,
        upload_filename=upload.original_filename if upload else None,
        enriched_file_available=_storage_file_available(draft.enriched_storage_key),
    )


def _serialize_draft_summary(
    draft: BudgetDraft,
    *,
    upload: Optional[BudgetUpload] = None,
    reopened_from_version: Optional[BudgetVersion] = None,
) -> BudgetDraftSummary:
    return BudgetDraftSummary(
        id=draft.id,
        status=draft.status,
        source_upload_id=draft.source_upload_id,
        source_upload_filename=upload.original_filename if upload else None,
        reopened_from_version_id=draft.reopened_from_version_id,
        reopened_from_version_code=reopened_from_version.version_code if reopened_from_version else None,
        reserve_inflation_rate=draft.reserve_inflation_rate,
        reserve_inflation_note=draft.reserve_inflation_note,
        updated_at=draft.updated_at,
        actor_name=draft.actor_name,
        enriched_file_available=_storage_file_available(draft.enriched_storage_key),
    )


def _serialize_note(note: BudgetNote) -> BudgetNoteRecord:
    return BudgetNoteRecord(
        id=note.id,
        note_scope=note.note_scope,
        line_item_key=note.line_item_key,
        title=note.title,
        body=note.body,
        created_at=note.created_at,
        created_by_name=note.created_by_name,
        upload_id=note.upload_id,
        draft_id=note.draft_id,
        version_id=note.version_id,
    )


def _serialize_version_summary(
    version: BudgetVersion,
    upload: Optional[BudgetUpload] = None,
) -> BudgetVersionSummary:
    return BudgetVersionSummary(
        id=version.id,
        version_number=version.version_number,
        version_code=version.version_code,
        stage=version.stage,
        label=version.label,
        summary_note=version.summary_note,
        total_income=version.total_income,
        total_expense=version.total_expense,
        net_operating_income=version.net_operating_income,
        growth_factor=version.growth_factor,
        growth_factor_note=version.growth_factor_note,
        reserve_inflation_rate=version.reserve_inflation_rate,
        reserve_inflation_note=version.reserve_inflation_note,
        statement_month=version.statement_month,
        created_at=version.created_at,
        created_by_name=version.created_by_name,
        source_draft_id=version.source_draft_id,
        output_storage_key=version.output_storage_key,
        source_upload_filename=upload.original_filename if upload else None,
    )


def _serialize_version_detail(
    version: BudgetVersion,
    upload: Optional[BudgetUpload] = None,
) -> BudgetVersionDetail:
    return BudgetVersionDetail(
        **_serialize_version_summary(version, upload).model_dump(),
        source_upload_id=version.source_upload_id,
        reopened_from_version_id=version.reopened_from_version_id,
        fiscal_year_start_month=version.fiscal_year_start_month,
        fiscal_year_end_month=version.fiscal_year_end_month,
        line_items=_json_loads(version.line_items_json, []),
        budget_preview=_json_loads(version.budget_preview_json, None),
    )


def _serialize_version_compare_card(
    version: BudgetVersion,
    upload: Optional[BudgetUpload],
) -> BudgetVersionCompareCard:
    return BudgetVersionCompareCard(
        id=version.id,
        version_code=version.version_code,
        stage=version.stage,
        label=version.label,
        summary_note=version.summary_note,
        created_at=version.created_at,
        created_by_name=version.created_by_name,
        source_upload_filename=upload.original_filename if upload else None,
        total_income=version.total_income,
        total_expense=version.total_expense,
        net_operating_income=version.net_operating_income,
        growth_factor=version.growth_factor,
        growth_factor_note=version.growth_factor_note,
        statement_month=version.statement_month,
        fiscal_year_start_month=version.fiscal_year_start_month,
        fiscal_year_end_month=version.fiscal_year_end_month,
    )


def _get_property(session: Session, hoa_id: int) -> Property:
    hoa = session.get(Property, hoa_id)
    if hoa is None:
        raise LookupError("HOA not found")
    return hoa


def _get_draft(session: Session, hoa_id: int, draft_id: int) -> BudgetDraft:
    draft = session.get(BudgetDraft, draft_id)
    if draft is None or draft.property_id != hoa_id:
        raise LookupError("Draft not found")
    return draft


def _get_editable_draft(session: Session, hoa_id: int, draft_id: int) -> BudgetDraft:
    draft = _get_draft(session, hoa_id, draft_id)
    if draft.status != BUDGET_DRAFT_ACTIVE:
        raise ValueError("Requested draft is no longer active")
    return draft


def _get_version(session: Session, hoa_id: int, version_id: int) -> BudgetVersion:
    version = session.get(BudgetVersion, version_id)
    if version is None or version.property_id != hoa_id:
        raise LookupError("Version not found")
    return version


def _get_upload(session: Session, upload_id: Optional[int]) -> Optional[BudgetUpload]:
    if upload_id is None:
        return None
    return session.get(BudgetUpload, upload_id)


def _replace_active_draft(session: Session, hoa_id: int, timestamp: str) -> None:
    active_drafts = session.scalars(
        select(BudgetDraft).where(
            BudgetDraft.property_id == hoa_id,
            BudgetDraft.status == BUDGET_DRAFT_ACTIVE,
        )
    ).all()
    for existing in active_drafts:
        existing.status = BUDGET_DRAFT_SUPERSEDED
        existing.updated_at = timestamp


def _create_audit_event(
    session: Session,
    *,
    hoa_id: int,
    actor: dict[str, Any],
    event_type: str,
    summary: str,
    upload_id: Optional[int] = None,
    draft_id: Optional[int] = None,
    version_id: Optional[int] = None,
    note_id: Optional[int] = None,
    payload: Optional[dict[str, Any]] = None,
) -> BudgetAuditEvent:
    event = BudgetAuditEvent(
        property_id=hoa_id,
        upload_id=upload_id,
        draft_id=draft_id,
        version_id=version_id,
        note_id=note_id,
        event_type=event_type,
        summary=summary,
        actor_user_id=actor["id"],
        actor_name=_actor_name(actor),
        payload_json=_json_dumps(payload) if payload is not None else None,
        created_at=_now_text(),
    )
    session.add(event)
    session.flush()
    return event


def _render_draft_snapshot_from_upload(
    upload: BudgetUpload,
    *,
    line_items: list[dict[str, Any]],
    growth_factor: Optional[float],
    growth_factor_note: Optional[str],
) -> tuple[bytes, dict[str, Any]]:
    if not upload.storage_key:
        raise LookupError("Source upload file not found")

    source_path = _budget_storage_path(upload.storage_key)
    if not source_path.exists():
        raise LookupError("Source upload file not found")

    temp_input_path = _write_temp_workbook(source_path.read_bytes(), upload.original_filename)
    temp_output_dir = Path(tempfile.mkdtemp(prefix="budget_draft_snapshot_"))
    try:
        temp_input_path = _ensure_xlsx(temp_input_path)

        macros_service.write_percent_changes_by_label(
            temp_input_path,
            "Income Statement",
            _line_items_to_percent_changes(line_items),
        )
        intermediate_path = str(temp_output_dir / "Income_Statement_Enriched.xlsx")
        output_path = str(temp_output_dir / "Budget_Pipeline.xlsx")
        pipeline = BudgetPipeline(
            input_path=temp_input_path,
            intermediate_path=intermediate_path,
            output_path=output_path,
            growth_factor=growth_factor,
            growth_factor_note=growth_factor_note,
            enrich_only=False,
        )
        pipeline.run()
        preview = macros_service.read_first_sheet_preview(output_path, settings.MAX_PREVIEW_ROWS)
        enriched_bytes = Path(intermediate_path).read_bytes()
        return enriched_bytes, preview
    finally:
        if os.path.exists(temp_input_path):
            os.remove(temp_input_path)
        shutil.rmtree(temp_output_dir, ignore_errors=True)


def _persist_draft_enriched_workbook(
    draft: BudgetDraft,
    *,
    enriched_bytes: bytes,
) -> str:
    storage_key = draft.enriched_storage_key or _draft_enriched_storage_key(draft.property_id, draft.id)
    _write_atomic_bytes(_budget_storage_path(storage_key), enriched_bytes)
    draft.enriched_storage_key = storage_key
    return storage_key


def _refresh_draft_snapshot_from_upload(
    session: Session,
    draft: BudgetDraft,
    upload: BudgetUpload,
) -> dict[str, Any]:
    line_items = _json_loads(draft.line_items_json, [])
    enriched_bytes, preview = _render_draft_snapshot_from_upload(
        upload,
        line_items=line_items,
        growth_factor=draft.growth_factor or upload.growth_factor,
        growth_factor_note=draft.growth_factor_note or upload.growth_factor_note,
    )
    _persist_draft_enriched_workbook(draft, enriched_bytes=enriched_bytes)
    draft.budget_preview_json = _json_dumps(preview)
    session.flush()
    return preview


def _ensure_draft_enriched_workbook(
    session: Session,
    draft: BudgetDraft,
) -> Path:
    if _storage_file_available(draft.enriched_storage_key):
        return _budget_storage_path(draft.enriched_storage_key or "")

    upload = _get_upload(session, draft.source_upload_id)
    if upload is None:
        raise LookupError("Enriched draft file unavailable")

    try:
        _refresh_draft_snapshot_from_upload(session, draft, upload)
    except LookupError as exc:
        raise LookupError("Enriched draft file unavailable") from exc
    except FileNotFoundError as exc:
        raise LookupError("Enriched draft file unavailable") from exc

    if not draft.enriched_storage_key:
        raise LookupError("Enriched draft file unavailable")
    return _budget_storage_path(draft.enriched_storage_key)


def create_upload(
    session: Session,
    *,
    hoa_id: int,
    actor: dict[str, Any],
    original_filename: str,
    content_type: Optional[str],
    file_bytes: bytes,
) -> BudgetUploadResponse:
    hoa = _get_property(session, hoa_id)
    timestamp = _now_text()
    sha256 = hashlib.sha256(file_bytes).hexdigest()

    upload = BudgetUpload(
        property_id=hoa_id,
        original_filename=original_filename,
        storage_key="pending",
        content_type=content_type,
        byte_size=len(file_bytes),
        sha256=sha256,
        enrichment_status="failed",
        uploaded_by_user_id=actor["id"],
        uploaded_by_name=_actor_name(actor),
        created_at=timestamp,
    )
    session.add(upload)
    session.flush()

    _ext = Path(original_filename).suffix.lower() or ".xlsx"
    upload.storage_key = _relative_storage_path("hoa", hoa_id, "uploads", upload.id, f"source{_ext}")
    _write_atomic_bytes(_budget_storage_path(upload.storage_key), file_bytes)

    upload_received_event = _create_audit_event(
        session,
        hoa_id=hoa_id,
        actor=actor,
        event_type="upload_received",
        summary=f"Uploaded {original_filename}",
        upload_id=upload.id,
        payload={"filename": original_filename, "sha256": sha256},
    )

    route = choose_financial_document_route(original_filename, content_type)
    temp_input_path = _write_temp_workbook(file_bytes, original_filename)
    cleanup_paths = {temp_input_path}
    temp_output_dir = Path(tempfile.mkdtemp(prefix="budget_history_"))
    try:
        statement_period_hint: str | None = None
        if route.path == "pdf_vlm":
            pdf_source_path = temp_input_path
            pdf_result = _extract_pdf_statement_sync(temp_input_path)
            if isinstance(pdf_result, DocumentExtractionFailure):
                return _build_review_required_response(
                    session,
                    hoa_id=hoa_id,
                    actor=actor,
                    upload=upload,
                    original_filename=original_filename,
                    reason=pdf_result.message,
                    code=pdf_result.code,
                    warnings=[pdf_result.message],
                )

            statement_period_hint = pdf_result.statement_period or extract_pdf_statement_period_hint(pdf_source_path)
            normalized_path = build_normalized_statement_workbook(pdf_result)
            cleanup_paths.add(normalized_path)
            temp_input_path = normalized_path
        else:
            normalized_path = _ensure_xlsx(temp_input_path)
            cleanup_paths.add(normalized_path)
            temp_input_path = normalized_path

        growth_factor = None
        if statement_period_hint:
            inferred = infer_growth_factor_from_statement_period(
                statement_period_hint,
                hoa.fiscal_year_start_month or 1,
            )
            if inferred is not None:
                growth_factor, detected_months, source = inferred

        if growth_factor is None:
            growth_factor, detected_months, source = infer_growth_factor_from_input(
                temp_input_path,
                fiscal_year_start_month=hoa.fiscal_year_start_month or 1,
            )
        growth_factor_note = f"auto annualization 12/{detected_months} from {source}"
        statement_month = _extract_statement_month(hoa.fiscal_year_start_month or 1, detected_months)
        intermediate_path = str(temp_output_dir / "Income_Statement_Enriched.xlsx")
        output_path = str(temp_output_dir / "Budget_Pipeline.xlsx")
        pipeline = BudgetPipeline(
            input_path=temp_input_path,
            intermediate_path=intermediate_path,
            output_path=output_path,
            growth_factor=growth_factor,
            growth_factor_note=growth_factor_note,
            enrich_only=False,
        )
        pipeline.run()
        enriched = macros_service.read_sheet_as_table(intermediate_path, "Income Statement")
        preview = macros_service.read_first_sheet_preview(output_path, settings.MAX_PREVIEW_ROWS)
        line_items, parse_warnings = _table_to_line_items(enriched)
        canonical_statement = _canonical_statement_from_line_items(
            line_items,
            family=route.family or ("pdf_visual_document" if route.path == "pdf_vlm" else "known_clean_excel_workbook"),
        )
        canonical_issues = validate_extracted_statement(canonical_statement)
        if has_blocking_validation_issues(canonical_issues):
            review_reason = "Extraction requires review because the parsed statement failed confidence checks."
            warnings = [issue["message"] for issue in canonical_issues]
            return _build_review_required_response(
                session,
                hoa_id=hoa_id,
                actor=actor,
                upload=upload,
                original_filename=original_filename,
                reason=review_reason,
                code="validation_failed",
                warnings=parse_warnings + warnings,
            )

        _replace_active_draft(session, hoa_id, timestamp)
        draft = BudgetDraft(
            property_id=hoa_id,
            source_upload_id=upload.id,
            reopened_from_version_id=None,
            status=BUDGET_DRAFT_ACTIVE,
            line_items_json=_json_dumps(line_items),
            global_note=None,
            statement_month=statement_month,
            growth_factor=growth_factor,
            growth_factor_note=growth_factor_note,
            reserve_inflation_rate=hoa.reserve_inflation_rate or 0.0,
            reserve_inflation_note=None,
            budget_preview_json=_json_dumps(preview),
            created_by_user_id=actor["id"],
            updated_by_user_id=actor["id"],
            actor_name=_actor_name(actor),
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(draft)
        session.flush()
        _persist_draft_enriched_workbook(
            draft,
            enriched_bytes=Path(intermediate_path).read_bytes(),
        )

        upload.enrichment_status = "completed"
        upload.line_items_json = _json_dumps(line_items)
        upload.budget_preview_json = _json_dumps(preview)
        upload.statement_month = statement_month
        upload.growth_factor = growth_factor
        upload.growth_factor_note = growth_factor_note

        _create_audit_event(
            session,
            hoa_id=hoa_id,
            actor=actor,
            event_type="enrichment_completed",
            summary=f"Enriched upload for {hoa.name}",
            upload_id=upload.id,
            draft_id=draft.id,
            payload={"statement_month": statement_month, "growth_factor": growth_factor},
        )
        session.commit()
        return BudgetUploadResponse(
            upload_id=upload.id,
            draft=_serialize_draft(draft, upload),
            timeline_event=_serialize_timeline_event(upload_received_event),
            warnings=parse_warnings,
        )
    except Exception as exc:
        upload.enrichment_status = "failed"
        _create_audit_event(
            session,
            hoa_id=hoa_id,
            actor=actor,
            event_type="enrichment_failed",
            summary=f"Enrichment failed for {original_filename}",
            upload_id=upload.id,
            payload={"error": str(exc)},
        )
        session.commit()
        raise
    finally:
        for cleanup_path in cleanup_paths:
            if cleanup_path and os.path.exists(cleanup_path):
                os.remove(cleanup_path)
        shutil.rmtree(temp_output_dir, ignore_errors=True)


def get_active_draft(session: Session, hoa_id: int) -> BudgetDraftPayload:
    draft = session.scalars(
        select(BudgetDraft).where(
            BudgetDraft.property_id == hoa_id,
            BudgetDraft.status == BUDGET_DRAFT_ACTIVE,
        ).order_by(BudgetDraft.updated_at.desc())
    ).first()
    if draft is None:
        raise LookupError("Active draft not found")
    return _serialize_draft(draft, _get_upload(session, draft.source_upload_id))


def get_requested_draft(session: Session, hoa_id: int, draft_id: int) -> BudgetDraftPayload:
    draft = _get_editable_draft(session, hoa_id, draft_id)
    return _serialize_draft(draft, _get_upload(session, draft.source_upload_id))


def get_draft_compare_options(
    session: Session,
    *,
    hoa_id: int,
) -> BudgetDraftCompareOptionsResponse:
    hoa = _get_property(session, hoa_id)
    draft = _get_editable_draft(
        session,
        hoa_id,
        session.scalars(
            select(BudgetDraft.id).where(
                BudgetDraft.property_id == hoa_id,
                BudgetDraft.status == BUDGET_DRAFT_ACTIVE,
            ).order_by(BudgetDraft.updated_at.desc(), BudgetDraft.id.desc())
        ).first() or 0,
    )
    versions = session.scalars(
        select(BudgetVersion)
        .where(BudgetVersion.property_id == hoa_id)
        .order_by(BudgetVersion.created_at.desc(), BudgetVersion.id.desc())
    ).all()

    recommended_version = next(
        (version for version in versions if version.stage == BUDGET_VERSION_STAGE_FINAL),
        None,
    )
    recommended_reason = "Latest Final" if recommended_version is not None else None
    if recommended_version is None and versions:
        recommended_version = versions[0]
        recommended_reason = "Latest generated (no Final version yet)"

    recommended_version_id = recommended_version.id if recommended_version else None
    return BudgetDraftCompareOptionsResponse(
        draft_id=draft.id,
        recommended_baseline_version_id=recommended_version_id,
        recommended_baseline_reason=recommended_reason,
        baseline_versions=[
            BudgetDraftCompareBaselineOption(
                id=version.id,
                version_code=version.version_code,
                stage=version.stage,
                label=version.label,
                created_at=version.created_at,
                summary_note=version.summary_note,
                is_recommended=version.id == recommended_version_id,
            )
            for version in versions
        ],
        reserve_inflation_rate=hoa.reserve_inflation_rate or 0.0,
        reserve_inflation_note=None,
    )


def save_draft(
    session: Session,
    *,
    hoa_id: int,
    actor: dict[str, Any],
    payload: BudgetDraftSaveRequest,
) -> tuple[BudgetDraftPayload, Optional[BudgetTimelineEvent]]:
    hoa = _get_property(session, hoa_id)
    draft = _get_editable_draft(session, hoa_id, payload.draft_id)
    upload = _get_upload(session, draft.source_upload_id)
    if upload is None:
        raise LookupError("Source upload not found")
    existing_snapshot = {
        "line_items": _json_loads(draft.line_items_json, []),
        "global_note": draft.global_note,
        "statement_month": draft.statement_month,
        "growth_factor": draft.growth_factor,
        "growth_factor_note": draft.growth_factor_note,
    }
    incoming_snapshot = {
        "line_items": payload.line_items,
        "global_note": payload.global_note,
        "statement_month": payload.statement_month,
        "growth_factor": payload.growth_factor,
        "growth_factor_note": payload.growth_factor_note,
    }

    draft.line_items_json = _json_dumps(payload.line_items)
    draft.global_note = payload.global_note
    draft.statement_month = payload.statement_month
    draft.growth_factor = payload.growth_factor
    draft.growth_factor_note = payload.growth_factor_note
    # Use explicitly provided inflation rate, or fall back to HOA setting
    draft.reserve_inflation_rate = (
        payload.reserve_inflation_rate
        if payload.reserve_inflation_rate is not None
        else (hoa.reserve_inflation_rate or 0.0)
    )
    draft.reserve_inflation_note = payload.reserve_inflation_note
    draft.updated_by_user_id = actor["id"]
    draft.actor_name = _actor_name(actor)
    draft.updated_at = _now_text()
    _refresh_draft_snapshot_from_upload(session, draft, upload)
    session.flush()

    timeline_event = None
    if existing_snapshot != incoming_snapshot:
        event = _create_audit_event(
            session,
            hoa_id=hoa_id,
            actor=actor,
            event_type="manual_overrides_saved",
            summary="Saved meaningful draft overrides",
            upload_id=draft.source_upload_id,
            draft_id=draft.id,
            payload={"line_item_count": len(payload.line_items)},
        )
        timeline_event = _serialize_timeline_event(event)
    session.commit()
    return _serialize_draft(draft, upload), timeline_event


def _latest_line_item_notes(
    session: Session,
    *,
    hoa_id: int,
    draft_id: int,
) -> dict[str, BudgetNote]:
    notes = session.scalars(
        select(BudgetNote)
        .where(
            BudgetNote.property_id == hoa_id,
            BudgetNote.draft_id == draft_id,
            BudgetNote.line_item_key.is_not(None),
        )
        .order_by(BudgetNote.created_at.desc(), BudgetNote.id.desc())
    ).all()
    latest_by_key: dict[str, BudgetNote] = {}
    for note in notes:
        normalized_key = _normalize_line_item_key(note.line_item_key)
        if normalized_key and normalized_key not in latest_by_key:
            latest_by_key[normalized_key] = note
    return latest_by_key


def _build_draft_compare_summary(rows: list[dict[str, Any]]) -> BudgetDraftChangeSummary:
    baseline_amount = round(sum(_parse_float(row.get("baseline_amount")) for row in rows), 2)
    current_amount = round(sum(_parse_float(row.get("current_amount")) for row in rows), 2)
    delta_amount = round(sum(_parse_float(row.get("delta_amount")) for row in rows), 2)
    delta_percent = None if baseline_amount == 0 else (delta_amount / baseline_amount) * 100
    return BudgetDraftChangeSummary(
        baseline_amount=baseline_amount,
        current_amount=current_amount,
        delta_amount=delta_amount,
        delta_percent=delta_percent,
    )


def _build_reserve_review_summary(rows: list[dict[str, Any]]) -> BudgetReserveReviewSummary:
    baseline_amount = round(sum(_parse_float(row.get("baseline_amount")) for row in rows), 2)
    inflation_adjusted_amount = round(
        sum(_parse_float(row.get("inflation_adjusted_baseline_amount")) for row in rows),
        2,
    )
    impact_amount = round(inflation_adjusted_amount - baseline_amount, 2)
    return BudgetReserveReviewSummary(
        baseline_amount=baseline_amount,
        inflation_adjusted_amount=inflation_adjusted_amount,
        impact_amount=impact_amount,
        eligible_component_count=len(rows),
    )


def _build_draft_reserve_review_rows(
    line_items: list[dict[str, Any]],
    *,
    reserve_inflation_rate: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in line_items:
        if _reserve_group_for_item(item) != "component":
            continue
        baseline_amount = _line_item_amount(item)
        inflation_adjusted_amount = (
            baseline_amount * (1 + reserve_inflation_rate)
            if reserve_inflation_rate > 0.0
            else baseline_amount
        )
        rows.append(
            {
                "line_item_key": _normalize_line_item_key(item.get("line_item_key"))
                or _line_item_account_code_text(item)
                or _line_item_label(item),
                "label": _line_item_label(item),
                "baseline_amount": round(baseline_amount, 2),
                "inflation_adjusted_baseline_amount": round(inflation_adjusted_amount, 2),
            }
        )

    rows.sort(
        key=lambda row: (
            -(
                _parse_float(row.get("inflation_adjusted_baseline_amount"))
                - _parse_float(row.get("baseline_amount"))
            ),
            _normalize_compare_text(row.get("label")),
        )
    )
    return rows


def compare_draft_to_version(
    session: Session,
    *,
    hoa_id: int,
    draft_id: int,
    baseline_version_id: int,
    changed_only: bool,
) -> BudgetDraftCompareResponse:
    draft = _get_editable_draft(session, hoa_id, draft_id)
    baseline_version = _get_version(session, hoa_id, baseline_version_id)
    if baseline_version_id == draft.id:
        raise ValueError("Baseline version must be distinct from the active draft")

    draft_line_items = _json_loads(draft.line_items_json, [])
    baseline_line_items = _json_loads(baseline_version.line_items_json, [])
    baseline_index = _build_line_item_index(baseline_line_items)
    draft_index = _build_line_item_index(draft_line_items)
    note_by_key = _latest_line_item_notes(session, hoa_id=hoa_id, draft_id=draft.id)

    ordered_pairs: list[tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]] = []
    seen_baseline_ids: set[int] = set()

    for draft_item in draft_line_items:
        baseline_item = _find_matching_line_item(draft_item, index=baseline_index)
        if baseline_item is not None:
            seen_baseline_ids.add(id(baseline_item))
        ordered_pairs.append((draft_item, baseline_item))

    for baseline_item in baseline_line_items:
        if id(baseline_item) in seen_baseline_ids:
            continue
        ordered_pairs.append((_find_matching_line_item(baseline_item, index=draft_index), baseline_item))

    raw_rows: list[dict[str, Any]] = []
    for draft_item, baseline_item in ordered_pairs:
        item = draft_item or baseline_item
        if item is None:
            continue
        label = _line_item_label(item)
        baseline_amount = _line_item_amount(baseline_item)
        current_amount = _line_item_amount(draft_item)
        delta_amount = current_amount - baseline_amount
        line_item_key = (
            _normalize_line_item_key(item.get("line_item_key"))
            or _line_item_account_code_text(item)
            or label
        )
        note = note_by_key.get(line_item_key)
        raw_rows.append(
            {
                "line_item_key": line_item_key,
                "label": label,
                "category": _line_item_category(item),
                "reserve_group": _reserve_group_for_item(item),
                "is_reserve": _is_reserve_item(item),
                "reserve_inflation_eligible": False,
                "baseline_amount": baseline_amount,
                "inflation_adjusted_baseline_amount": baseline_amount,
                "current_amount": current_amount,
                "delta_amount": delta_amount,
                "delta_percent": None if baseline_amount == 0 else (delta_amount / baseline_amount) * 100,
                "changed": abs(delta_amount) > 1e-9,
                "note_title": note.title if note else None,
                "note_body": note.body if note else None,
            }
        )

    draft_compare_all_rows = [row for row in raw_rows if not row.get("is_reserve")]
    draft_compare_rows = (
        [row for row in draft_compare_all_rows if row["changed"]]
        if changed_only
        else draft_compare_all_rows
    )

    return BudgetDraftCompareResponse(
        draft_id=draft.id,
        baseline_version_id=baseline_version.id,
        changed_only=changed_only,
        draft_compare_summary=_build_draft_compare_summary(draft_compare_all_rows),
        draft_compare_rows=[BudgetDraftCompareRow(**row) for row in draft_compare_rows],
        reserve_review_summary=_build_reserve_review_summary([]),
        reserve_component_rows=[],
    )


def review_draft_reserves(
    session: Session,
    *,
    hoa_id: int,
    draft_id: int,
    payload: BudgetDraftReserveReviewRequest,
) -> BudgetDraftReserveReviewResponse:
    draft = _get_editable_draft(session, hoa_id, draft_id)
    working_line_items = payload.line_items or _json_loads(draft.line_items_json, [])
    reserve_inflation_rate = _parse_float(payload.reserve_inflation_rate)
    reserve_component_rows = _build_draft_reserve_review_rows(
        working_line_items,
        reserve_inflation_rate=reserve_inflation_rate,
    )
    return BudgetDraftReserveReviewResponse(
        draft_id=draft.id,
        reserve_inflation_rate=reserve_inflation_rate,
        reserve_review_summary=_build_reserve_review_summary(reserve_component_rows),
        reserve_component_rows=[
            BudgetReserveComponentRow(
                line_item_key=str(row["line_item_key"]),
                label=str(row["label"]),
                baseline_amount=round(_parse_float(row.get("baseline_amount")), 2),
                inflation_adjusted_amount=round(
                    _parse_float(row.get("inflation_adjusted_baseline_amount")),
                    2,
                ),
                impact_amount=round(
                    _parse_float(row.get("inflation_adjusted_baseline_amount"))
                    - _parse_float(row.get("baseline_amount")),
                    2,
                ),
            )
            for row in reserve_component_rows
        ],
    )


def save_note(
    session: Session,
    *,
    hoa_id: int,
    actor: dict[str, Any],
    payload: BudgetNoteSaveRequest,
) -> BudgetNoteSaveResponse:
    draft = _get_editable_draft(session, hoa_id, payload.draft_id)
    version = _get_version(session, hoa_id, payload.version_id) if payload.version_id else None
    note = BudgetNote(
        property_id=hoa_id,
        upload_id=draft.source_upload_id,
        draft_id=draft.id,
        version_id=version.id if version else None,
        note_scope=payload.note_scope,
        line_item_key=payload.line_item_key,
        title=payload.title,
        body=payload.body,
        created_by_user_id=actor["id"],
        created_by_name=_actor_name(actor),
        actor_name=_actor_name(actor),
        created_at=_now_text(),
    )
    session.add(note)
    session.flush()
    event = _create_audit_event(
        session,
        hoa_id=hoa_id,
        actor=actor,
        event_type="note_saved",
        summary=f"Saved note '{payload.title}'",
        upload_id=note.upload_id,
        draft_id=note.draft_id,
        version_id=note.version_id,
        note_id=note.id,
        payload={"note_scope": payload.note_scope, "line_item_key": payload.line_item_key},
    )
    session.commit()
    return BudgetNoteSaveResponse(
        note=_serialize_note(note),
        timeline_event=_serialize_timeline_event(event),
    )


def create_budget_version(
    session: Session,
    *,
    hoa_id: int,
    actor: dict[str, Any],
    payload: BudgetGenerateRequest,
) -> BudgetGenerateResponse:
    hoa = _get_property(session, hoa_id)
    draft = _get_editable_draft(session, hoa_id, payload.draft_id)
    upload = _get_upload(session, draft.source_upload_id)
    if upload is None:
        raise LookupError("Source upload not found")

    timestamp = _now_text()
    line_items = payload.line_items or _json_loads(draft.line_items_json, [])
    global_note = payload.global_note if payload.global_note is not None else draft.global_note

    source_path = _budget_storage_path(upload.storage_key)
    temp_input_path = _write_temp_workbook(source_path.read_bytes(), upload.original_filename)
    temp_output_dir = Path(tempfile.mkdtemp(prefix="budget_version_"))
    try:
        temp_input_path = _ensure_xlsx(temp_input_path)

        macros_service.write_percent_changes_by_label(
            temp_input_path,
            "Income Statement",
            _line_items_to_percent_changes(line_items),
        )
        intermediate_path = str(temp_output_dir / "Income_Statement_Enriched.xlsx")
        output_path = str(temp_output_dir / "Budget_Pipeline.xlsx")
        pipeline = BudgetPipeline(
            input_path=temp_input_path,
            intermediate_path=intermediate_path,
            output_path=output_path,
            growth_factor=draft.growth_factor or upload.growth_factor,
            growth_factor_note=draft.growth_factor_note or upload.growth_factor_note,
            enrich_only=False,
            hoa_name=hoa.name or '',
        )
        pipeline.run()
        enriched_bytes = Path(intermediate_path).read_bytes()
        preview = macros_service.read_first_sheet_preview(output_path, settings.MAX_PREVIEW_ROWS)
        total_income, total_expense, net_operating_income = _extract_totals_from_preview(preview)

        next_version_number = (
            session.scalar(
                select(func.coalesce(func.max(BudgetVersion.version_number), 0)).where(
                    BudgetVersion.property_id == hoa_id
                )
            )
            or 0
        ) + 1
        version = BudgetVersion(
            property_id=hoa_id,
            source_upload_id=upload.id,
            source_draft_id=draft.id,
            reopened_from_version_id=draft.reopened_from_version_id,
            storage_key=None,
            output_storage_key=None,
            version_number=next_version_number,
            version_code=f"V{next_version_number}",
            stage=BUDGET_VERSION_STAGE_INTERIM,
            label=None,
            summary_note=global_note,
            line_items_json=_json_dumps(line_items),
            budget_preview_json=_json_dumps(preview),
            total_income=total_income,
            total_expense=total_expense,
            net_operating_income=net_operating_income,
            growth_factor=draft.growth_factor or upload.growth_factor,
            growth_factor_note=draft.growth_factor_note or upload.growth_factor_note,
            reserve_inflation_rate=draft.reserve_inflation_rate or 0.0,
            reserve_inflation_note=draft.reserve_inflation_note,
            statement_month=draft.statement_month or upload.statement_month,
            fiscal_year_start_month=hoa.fiscal_year_start_month or 1,
            fiscal_year_end_month=hoa.fiscal_year_end_month or 12,
            created_by_user_id=actor["id"],
            created_by_name=_actor_name(actor),
            actor_name=_actor_name(actor),
            created_at=timestamp,
        )
        session.add(version)
        session.flush()

        output_storage_key = _relative_storage_path("hoa", hoa_id, "versions", version.id, "budget.xlsx")
        _write_atomic_bytes(_budget_storage_path(output_storage_key), Path(output_path).read_bytes())
        version.output_storage_key = output_storage_key

        draft.line_items_json = _json_dumps(line_items)
        draft.global_note = global_note
        draft.status = BUDGET_DRAFT_GENERATED
        _persist_draft_enriched_workbook(draft, enriched_bytes=enriched_bytes)
        draft.updated_by_user_id = actor["id"]
        draft.updated_at = timestamp
        draft.budget_preview_json = _json_dumps(preview)
        session.flush()

        event = _create_audit_event(
            session,
            hoa_id=hoa_id,
            actor=actor,
            event_type="budget_generated",
            summary=f"Generated {version.version_code}",
            upload_id=upload.id,
            draft_id=draft.id,
            version_id=version.id,
            payload={"stage": version.stage, "version_code": version.version_code},
        )
        session.commit()
        return BudgetGenerateResponse(
            draft=_serialize_draft(draft, upload),
            version=_serialize_version_detail(version, upload),
            timeline_event=_serialize_timeline_event(event),
        )
    finally:
        if os.path.exists(temp_input_path):
            os.remove(temp_input_path)
        shutil.rmtree(temp_output_dir, ignore_errors=True)


def get_history(session: Session, hoa_id: int) -> BudgetHistoryResponse:
    _get_property(session, hoa_id)
    active_draft = session.scalars(
        select(BudgetDraft).where(
            BudgetDraft.property_id == hoa_id,
            BudgetDraft.status == BUDGET_DRAFT_ACTIVE,
        ).order_by(BudgetDraft.updated_at.desc())
    ).first()
    drafts = session.scalars(
        select(BudgetDraft).where(BudgetDraft.property_id == hoa_id).order_by(BudgetDraft.updated_at.desc(), BudgetDraft.id.desc())
    ).all()
    notes = session.scalars(
        select(BudgetNote).where(BudgetNote.property_id == hoa_id).order_by(BudgetNote.created_at.desc(), BudgetNote.id.desc())
    ).all()
    versions = session.scalars(
        select(BudgetVersion).where(BudgetVersion.property_id == hoa_id).order_by(BudgetVersion.created_at.desc(), BudgetVersion.id.desc())
    ).all()
    timeline = session.scalars(
        select(BudgetAuditEvent).where(BudgetAuditEvent.property_id == hoa_id).order_by(BudgetAuditEvent.created_at.desc(), BudgetAuditEvent.id.desc())
    ).all()
    upload = _get_upload(session, active_draft.source_upload_id) if active_draft else None
    return BudgetHistoryResponse(
        active_draft=_serialize_draft(active_draft, upload) if active_draft else None,
        drafts=[
            _serialize_draft_summary(
                draft,
                upload=_get_upload(session, draft.source_upload_id),
                reopened_from_version=session.get(BudgetVersion, draft.reopened_from_version_id)
                if draft.reopened_from_version_id
                else None,
            )
            for draft in drafts
        ],
        timeline=[_serialize_timeline_event(event) for event in timeline],
        versions=[
            _serialize_version_summary(version, _get_upload(session, version.source_upload_id))
            for version in versions
        ],
        notes=[_serialize_note(note) for note in notes],
    )


def get_version_detail(session: Session, hoa_id: int, version_id: int) -> BudgetVersionDetail:
    version = _get_version(session, hoa_id, version_id)
    return _serialize_version_detail(version, _get_upload(session, version.source_upload_id))


def compare_versions(
    session: Session,
    *,
    hoa_id: int,
    left_version_id: int,
    right_version_id: int,
) -> BudgetVersionCompareResponse:
    if left_version_id == right_version_id:
        raise ValueError("Compare requires two distinct version ids")

    left_version = session.get(BudgetVersion, left_version_id)
    right_version = session.get(BudgetVersion, right_version_id)
    if left_version is None or right_version is None:
        raise LookupError("Version not found")
    if left_version.property_id != hoa_id or right_version.property_id != hoa_id:
        raise ValueError("Compared versions must belong to the selected HOA")

    return BudgetVersionCompareResponse(
        versions=[
            _serialize_version_compare_card(left_version, _get_upload(session, left_version.source_upload_id)),
            _serialize_version_compare_card(right_version, _get_upload(session, right_version.source_upload_id)),
        ]
    )


def reopen_version_as_draft(
    session: Session,
    *,
    hoa_id: int,
    actor: dict[str, Any],
    version_id: int,
) -> BudgetVersionReopenResponse:
    hoa = _get_property(session, hoa_id)
    version = _get_version(session, hoa_id, version_id)
    upload = _get_upload(session, version.source_upload_id)
    if upload is None:
        raise LookupError("Source upload not found")
    timestamp = _now_text()
    previous_active_draft = session.scalars(
        select(BudgetDraft).where(
            BudgetDraft.property_id == hoa_id,
            BudgetDraft.status == BUDGET_DRAFT_ACTIVE,
        ).order_by(BudgetDraft.updated_at.desc())
    ).first()
    previous_active_draft_id = previous_active_draft.id if previous_active_draft else None
    _replace_active_draft(session, hoa_id, timestamp)

    draft = BudgetDraft(
        property_id=hoa_id,
        source_upload_id=version.source_upload_id,
        reopened_from_version_id=version.id,
        status=BUDGET_DRAFT_ACTIVE,
        line_items_json=version.line_items_json,
        global_note=version.summary_note,
        statement_month=version.statement_month,
        growth_factor=version.growth_factor,
        growth_factor_note=version.growth_factor_note,
        reserve_inflation_rate=version.reserve_inflation_rate or 0.0,
        reserve_inflation_note=version.reserve_inflation_note,
        budget_preview_json=version.budget_preview_json,
        created_by_user_id=actor["id"],
        updated_by_user_id=actor["id"],
        actor_name=_actor_name(actor),
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(draft)
    session.flush()
    _refresh_draft_snapshot_from_upload(session, draft, upload)

    event = _create_audit_event(
        session,
        hoa_id=hoa_id,
        actor=actor,
        event_type="version_reopened",
        summary=f"Reopened {version.version_code} as a new draft",
        upload_id=version.source_upload_id,
        draft_id=draft.id,
        version_id=version.id,
        payload={
            "source_version_id": version.id,
            "source_version_code": version.version_code,
            "old_draft_id": previous_active_draft_id,
            "new_draft_id": draft.id,
        },
    )
    session.commit()
    return BudgetVersionReopenResponse(
        draft=_serialize_draft(draft, upload),
        timeline_event=_serialize_timeline_event(event),
    )


def update_version_metadata(
    session: Session,
    *,
    hoa_id: int,
    actor: dict[str, Any],
    version_id: int,
    payload: BudgetVersionMetadataUpdateRequest,
) -> BudgetVersionMetadataUpdateResponse:
    version = _get_version(session, hoa_id, version_id)
    timeline_events: list[BudgetTimelineEvent] = []
    changes: dict[str, Any] = {}
    current_stage = version.stage

    if payload.stage is not None and payload.stage != version.stage:
        version.stage = payload.stage
        changes["stage"] = payload.stage
    if payload.label != version.label:
        version.label = payload.label
        changes["label"] = payload.label
    if payload.summary_note != version.summary_note:
        version.summary_note = payload.summary_note
        changes["summary_note"] = payload.summary_note

    if changes:
        metadata_event = _create_audit_event(
            session,
            hoa_id=hoa_id,
            actor=actor,
            event_type="version_metadata_updated",
            summary=f"Updated metadata for {version.version_code}",
            upload_id=version.source_upload_id,
            version_id=version.id,
            payload={"version_code": version.version_code, "changes": changes},
        )
        timeline_events.append(_serialize_timeline_event(metadata_event))

    if current_stage != BUDGET_VERSION_STAGE_FINAL and version.stage == BUDGET_VERSION_STAGE_FINAL:
        final_event = _create_audit_event(
            session,
            hoa_id=hoa_id,
            actor=actor,
            event_type="version_marked_final",
            summary=f"Marked {version.version_code} as Final",
            upload_id=version.source_upload_id,
            version_id=version.id,
            payload={"version_code": version.version_code},
        )
        timeline_events.append(_serialize_timeline_event(final_event))

    session.commit()
    return BudgetVersionMetadataUpdateResponse(
        version=_serialize_version_detail(version, _get_upload(session, version.source_upload_id)),
        timeline_events=timeline_events,
    )


def record_version_download(
    session: Session,
    *,
    hoa_id: int,
    actor: dict[str, Any],
    version_id: int,
) -> tuple[Path, str]:
    version = _get_version(session, hoa_id, version_id)
    if not version.output_storage_key:
        raise LookupError("Version file not found")

    file_path = _budget_storage_path(version.output_storage_key)
    if not file_path.exists():
        raise LookupError("Version file not found")

    _create_audit_event(
        session,
        hoa_id=hoa_id,
        actor=actor,
        event_type="file_downloaded",
        summary=f"Downloaded workbook for {version.version_code}",
        upload_id=version.source_upload_id,
        version_id=version.id,
        payload={"version_code": version.version_code, "storage_key": version.output_storage_key},
    )
    session.commit()
    return file_path, f"{version.version_code}-budget.xlsx"


def record_draft_enriched_download(
    session: Session,
    *,
    hoa_id: int,
    actor: dict[str, Any],
    draft_id: int,
) -> tuple[Path, str]:
    draft = _get_draft(session, hoa_id, draft_id)
    file_path = _ensure_draft_enriched_workbook(session, draft)

    _create_audit_event(
        session,
        hoa_id=hoa_id,
        actor=actor,
        event_type="draft_enriched_downloaded",
        summary=f"Downloaded enriched workbook for Draft {draft.id}",
        upload_id=draft.source_upload_id,
        draft_id=draft.id,
        version_id=draft.reopened_from_version_id,
        payload={"draft_id": draft.id, "status": draft.status},
    )
    session.commit()
    return file_path, f"draft-{draft.id}-enriched.xlsx"


def delete_active_draft(
    session: Session,
    hoa_id: int,
    actor: dict[str, Any],
) -> dict[str, int]:
    draft = session.scalars(
        select(BudgetDraft).where(
            BudgetDraft.property_id == hoa_id,
            BudgetDraft.status == BUDGET_DRAFT_ACTIVE,
        ).order_by(BudgetDraft.updated_at.desc())
    ).first()
    if draft is None:
        raise LookupError("No active draft")

    has_version = session.scalar(
        select(BudgetVersion.id).where(
            BudgetVersion.source_draft_id == draft.id,
        ).limit(1)
    )
    if has_version is not None:
        raise ValueError("cannot_delete_draft_with_versions")

    draft_id = draft.id
    source_upload_id = draft.source_upload_id
    enriched_key = draft.enriched_storage_key

    _create_audit_event(
        session,
        hoa_id=hoa_id,
        actor=actor,
        event_type="draft_deleted",
        summary=f"Deleted active Draft {draft_id}",
        upload_id=source_upload_id,
        payload={"draft_id": draft_id, "source_upload_id": source_upload_id},
    )

    session.delete(draft)
    session.commit()

    if enriched_key:
        enriched_path = _budget_storage_path(enriched_key)
        if enriched_path.exists():
            enriched_path.unlink(missing_ok=True)

    return {"deleted_draft_id": draft_id}
