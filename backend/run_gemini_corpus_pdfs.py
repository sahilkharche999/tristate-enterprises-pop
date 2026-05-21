"""Test Gemini PDF extraction against the Example Income Statements corpus."""
import asyncio
import sys
import os
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("GEMINI_API_KEY", "")

from app.services.pdf_vlm_extractor import extract_pdf_statement
from app.models.financial_document_extraction import (
    ExtractedFinancialStatement,
    DocumentExtractionFailure,
)

PDF_DIR = Path(__file__).parent.parent / "Example Income Statements"


async def test_one_pdf(path: str) -> dict:
    name = Path(path).stem
    start = time.time()
    try:
        result = await extract_pdf_statement(path)
        elapsed = time.time() - start

        if isinstance(result, DocumentExtractionFailure):
            return {
                "name": name,
                "status": "FAILURE",
                "code": result.code,
                "message": result.message,
                "elapsed": elapsed,
            }

        sections = Counter(
            item.section_kind or "unknown" for item in result.line_items
        )
        return {
            "name": name,
            "status": "OK",
            "items": len(result.line_items),
            "sections": dict(sections),
            "confidence": result.confidence,
            "period": result.statement_period,
            "elapsed": elapsed,
        }
    except Exception as e:
        elapsed = time.time() - start
        return {"name": name, "status": "ERROR", "error": str(e), "elapsed": elapsed}


async def main():
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {PDF_DIR}")
        return

    print(f"Testing {len(pdfs)} PDFs with Gemini ({os.environ.get('GEMINI_MODEL', 'gemini-3.1-flash-lite-preview')})")
    print(f"API Key: ...{os.environ.get('GEMINI_API_KEY', '')[-8:]}")
    print("=" * 70)

    results = []
    for i, pdf in enumerate(pdfs):
        print(f"\n[{i+1}/{len(pdfs)}] {pdf.name}...", flush=True)
        r = await test_one_pdf(str(pdf))
        results.append(r)

        if r["status"] == "OK":
            print(f"  OK: {r['items']} items, confidence={r['confidence']:.2f}, {r['elapsed']:.1f}s")
            print(f"  Sections: {r['sections']}")
            if r.get("period"):
                print(f"  Period: {r['period']}")
        elif r["status"] == "FAILURE":
            print(f"  FAILURE [{r['code']}]: {r['message']} ({r['elapsed']:.1f}s)")
        else:
            print(f"  ERROR: {r['error']} ({r['elapsed']:.1f}s)")

        # Rate limit buffer between PDFs
        if i < len(pdfs) - 1:
            print("  Waiting 5s for rate limits...", flush=True)
            await asyncio.sleep(5)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    ok = [r for r in results if r["status"] == "OK"]
    fail = [r for r in results if r["status"] != "OK"]
    print(f"Success: {len(ok)}/{len(results)}")
    if ok:
        total_items = sum(r["items"] for r in ok)
        avg_conf = sum(r["confidence"] for r in ok) / len(ok)
        avg_time = sum(r["elapsed"] for r in ok) / len(ok)
        print(f"Total items extracted: {total_items}")
        print(f"Avg confidence: {avg_conf:.2f}")
        print(f"Avg extraction time: {avg_time:.1f}s")
    if fail:
        print(f"\nFailed ({len(fail)}):")
        for r in fail:
            reason = r.get("message") or r.get("error") or r.get("code")
            print(f"  - {r['name']}: {reason}")


if __name__ == "__main__":
    asyncio.run(main())
