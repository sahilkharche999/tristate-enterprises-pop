"""Read budget-source files with the live Gemini pipeline and print what it found.

usage (from backend/, with GEMINI_API_KEY / GEMINI_MODEL set):
    python run_budget_source_corpus.py <file or folder> [...]

For each .xlsx/.xls/.pdf: the kind Gemini decided (income statement / final
budget / not a budget source), the pages used, row count, annual and YTD
totals, totals mismatches, and the disclosure facts found. Read-only: nothing
is written to the database or storage.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.models.financial_document_extraction import DocumentExtractionFailure
from app.services.budget_history_service import _extract_excel_source
from app.services.financial_statement_validation import validate_extracted_statement
from app.services.pdf_vlm_extractor import extract_pdf_statement

SUFFIXES = {".xlsx", ".xlsm", ".xls", ".pdf"}


async def read_one(path: Path) -> str:
    started = time.time()
    if path.suffix.lower() == ".pdf":
        result = await extract_pdf_statement(str(path), filename=path.name)
    else:
        result = await _extract_excel_source(str(path), original_filename=path.name)
    seconds = time.time() - started
    if isinstance(result, DocumentExtractionFailure):
        facts = (result.details or {}).get("disclosure_facts")
        return f"{path.name}: {result.code} in {seconds:.0f}s - {result.message}" + (f"\n   facts: {facts}" if facts else "")
    meta = result.extraction_metadata or {}
    mismatches = [i["message"] for i in validate_extracted_statement(result) if i["code"] == "subtotal_mismatch"]
    facts = {key: (value or {}).get("printed") if isinstance(value, dict) else value
             for key, value in (meta.get("disclosure_facts") or {}).items() if value}
    lines = [
        f"{path.name}: {result.report_type} in {seconds:.0f}s, {len(result.line_items)} rows, "
        f"annual {sum(i.annual_budget or 0 for i in result.line_items):,.2f}, "
        f"ytd {sum(i.ytd_actual or 0 for i in result.line_items):,.2f}, period {result.statement_period!r}",
    ]
    if meta.get("pages_note"):
        lines.append(f"   {meta['pages_note']}")
    lines += [f"   totals: {message}" for message in mismatches]
    if facts:
        lines.append(f"   facts: {facts}")
    return "\n".join(lines)


async def main(arguments: list[str]) -> None:
    files = []
    for argument in arguments:
        path = Path(argument)
        files += sorted(p for p in path.iterdir() if p.suffix.lower() in SUFFIXES) if path.is_dir() else [path]
    for path in files:
        try:
            print(await read_one(path), flush=True)
        except Exception as exc:  # keep going through the corpus
            print(f"{path.name}: ERROR {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1:]))
