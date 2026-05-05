from app.models.financial_document_extraction import DocumentExtractionFailure, ExtractedFinancialStatement
from app.services.financial_document_router import choose_financial_document_route


def test_success_pdf_corpus_files_route_to_vlm_path():
    for filename in ("2238 Market.pdf", "Crestview.pdf", "Pacifica Mariners.pdf"):
        route = choose_financial_document_route(filename, "application/pdf")
        assert route.path == "pdf_vlm"
        assert route.family == "pdf_visual_document"


def test_failure_pdf_corpus_files_still_route_to_vlm_path_for_review():
    for filename in ("Oak Grove Manor.pdf", "Vantage.pdf", "Cummings Park.pdf"):
        route = choose_financial_document_route(filename, "application/pdf")
        assert route.path == "pdf_vlm"
        assert route.requires_review_on_low_confidence is True


def test_variant_excel_filename_routes_to_deterministic_variant_family():
    route = choose_financial_document_route(
        "other-company-shifted-header-income.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert route.path == "excel_deterministic"
    assert route.family == "variant_excel_workbook"
