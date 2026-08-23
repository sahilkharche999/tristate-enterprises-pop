"""Bug 1 — CPA/accounting-firm address comes from hoa_settings, not a literal.

- The ``nl2br`` Jinja filter renders operator newlines as <br> and escapes
  markup (no injection).
- No standard template still hardcodes the CPA street address; all three
  reference ``hoa_settings.cpa_firm_address``.
- The seed/backfill logic gives existing rows the two-line default and is
  idempotent / preserves operator edits.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.disclosure_package.render import _build_env, _nl2br

_TEMPLATES = Path(__file__).resolve().parents[1] / (
    "app/disclosure_package/templates/standard"
)
_LEGACY_LITERAL = "100 Montgomery Street, Suite 715, San Francisco, California 94104"
_TWO_LINE = "100 Montgomery Street, Suite 715\nSan Francisco, California 94104"


def test_nl2br_renders_newlines_as_br():
    out = str(_nl2br("100 Montgomery Street, Suite 715\nSan Francisco, CA 94104"))
    assert "<br>" in out
    assert out.count("<br>") == 1


def test_nl2br_escapes_markup_no_injection():
    out = str(_nl2br("<script>alert(1)</script>\nLine 2"))
    # The angle brackets are escaped; only our own <br> survives as a tag.
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<br>" in out


def test_nl2br_none_is_empty():
    assert str(_nl2br(None)) == ""


def test_nl2br_filter_registered_on_env():
    env = _build_env()
    assert "nl2br" in env.filters
    rendered = env.from_string("{{ v | nl2br }}").render(v="A\nB")
    assert rendered == "A<br>B"


def test_no_page_hardcodes_cpa_address():
    """The address must come from hoa_settings on every page that shows it.

    add-full-document-editor moved the two compilation reports' bodies out of
    their templates and into operator-editable narrative baselines, so the
    address now reaches them as a ``cpa_firm_address`` value chip. The §5570
    form stays a template. Both spellings are checked here so neither location
    can quietly reintroduce a literal.
    """
    from app.services import narrative_content

    sources = {
        "pro_forma_disclosure_summary.html": (
            (_TEMPLATES / "pro_forma_disclosure_summary.html").read_text()
        ),
        "content/compilation_report.html": narrative_content.baseline_html(
            "compilation_report"
        ),
        "content/thirty_year_compilation.html": narrative_content.baseline_html(
            "thirty_year_compilation"
        ),
    }
    for name, text in sources.items():
        assert "100 Montgomery Street" not in text, f"{name} still hardcodes address"
        assert "cpa_firm_address" in text, f"{name} does not read the setting"


# --- backfill logic (mirrors _seed_tri_state_disclosure_defaults) ---

def _apply_backfill(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE hoa_settings SET cpa_firm_address = ? "
        "WHERE cpa_firm_address IS NULL OR cpa_firm_address = ''",
        (_TWO_LINE,),
    )
    conn.execute(
        "UPDATE hoa_settings SET cpa_firm_address = ? WHERE cpa_firm_address = ?",
        (_TWO_LINE, _LEGACY_LITERAL),
    )


def test_backfill_fills_blank_and_converts_legacy_and_preserves_edits():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE hoa_settings (id INTEGER PRIMARY KEY, cpa_firm_address TEXT)"
    )
    conn.executemany(
        "INSERT INTO hoa_settings (id, cpa_firm_address) VALUES (?, ?)",
        [
            (1, None),                       # never set -> gets two-line default
            (2, ""),                         # empty -> gets two-line default
            (3, _LEGACY_LITERAL),            # old single-line seed -> converted
            (4, "Custom CPA\n1 Main St"),    # operator edit -> preserved
        ],
    )
    _apply_backfill(conn)
    rows = dict(conn.execute("SELECT id, cpa_firm_address FROM hoa_settings").fetchall())
    assert rows[1] == _TWO_LINE
    assert rows[2] == _TWO_LINE
    assert rows[3] == _TWO_LINE
    assert rows[4] == "Custom CPA\n1 Main St"

    # Idempotent: a second pass changes nothing.
    before = dict(conn.execute("SELECT id, cpa_firm_address FROM hoa_settings").fetchall())
    _apply_backfill(conn)
    after = dict(conn.execute("SELECT id, cpa_firm_address FROM hoa_settings").fetchall())
    assert before == after
