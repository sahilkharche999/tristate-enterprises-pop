"""Centralized routing for uploaded financial documents.

Both budget-history upload and macro-style budget generation should route through
this module so Excel/PDF handling cannot silently diverge.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..models.financial_document_extraction import FinancialDocumentRoute

_EXCEL_EXTENSIONS = {".xls", ".xlsx", ".xlsm"}
_PDF_EXTENSIONS = {".pdf"}
_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/tiff"}


def _normalize_filename(filename: str) -> str:
    return (filename or "").strip().lower()


def _infer_excel_family(filename: str) -> str:
    normalized = _normalize_filename(filename)
    variant_markers = ("shifted", "merged", "variant", "vendor", "unfamiliar")
    if any(marker in normalized for marker in variant_markers):
        return "variant_excel_workbook"
    return "known_clean_excel_workbook"


def choose_financial_document_route(
    filename: str,
    content_type: Optional[str] = None,
) -> FinancialDocumentRoute:
    """Choose the primary extraction path for an uploaded financial document.

    Excel stays deterministic-first. PDFs and visual/scanned documents route to
    the VLM-first path. Ambiguous file types prefer the safe visual-document
    path rather than forcing a deterministic spreadsheet parser.
    """

    ext = Path(filename or "").suffix.lower()
    normalized_content_type = (content_type or "").strip().lower()

    if ext in _EXCEL_EXTENSIONS:
        family = _infer_excel_family(filename)
        reason = (
            "Known clean Excel stays deterministic"
            if family == "known_clean_excel_workbook"
            else "Variant Excel stays deterministic-first with shared normalization and confidence gates"
        )
        return FinancialDocumentRoute(
            path="excel_deterministic",
            family=family,
            reason=reason,
            is_supported=True,
            requires_review_on_low_confidence=True,
        )

    if ext in _PDF_EXTENSIONS or "pdf" in normalized_content_type:
        return FinancialDocumentRoute(
            path="pdf_vlm",
            family="pdf_visual_document",
            reason="PDF-family inputs route through the VLM-first visual extraction path",
            is_supported=True,
            requires_review_on_low_confidence=True,
        )

    if normalized_content_type in _IMAGE_CONTENT_TYPES:
        return FinancialDocumentRoute(
            path="pdf_vlm",
            family="scanned_visual_document",
            reason="Image/scanned uploads require the visual extraction path",
            is_supported=True,
            requires_review_on_low_confidence=True,
        )

    return FinancialDocumentRoute(
        path="pdf_vlm",
        family="unknown_visual_document",
        reason="Ambiguous file type prefers the safe visual-document review path",
        is_supported=False,
        requires_review_on_low_confidence=True,
    )

