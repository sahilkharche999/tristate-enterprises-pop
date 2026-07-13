"""WeasyPrint + Jinja2 HTML→PDF renderer for disclosure-package pages.

Per CONTEXT D-08, WeasyPrint is the single rendering engine for all
generated pages. Per D-10 (font pinning) and T-11-03 (no SSRF via
remote URL fetches), the url_fetcher is locked to a deny-all that
allows ONLY local file:// references inside the templates directory.

Public surface:
    render_template(*, template_name, context, templates_subdir="standard") -> bytes
        Render a single Jinja template to PDF bytes.

    render_package(*, spec, computed, templates_subdir="standard") -> dict[str, bytes]
        Render every GeneratedPage in spec.entries.

    RemoteFetchDenied (RuntimeError subclass)
        Raised when a template attempts a non-local resource fetch.
"""
from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup, escape

logger = logging.getLogger(__name__)


def _nl2br(value: Any) -> Markup:
    """Escape a value, then turn newlines into <br>, returning safe markup.

    Escaping BEFORE introducing the ``<br>`` tags preserves the environment's
    autoescape guarantee — operator-entered text (e.g. a multi-line CPA firm
    address from HOA settings) cannot inject markup, but its line breaks are
    honored in the rendered PDF.
    """
    if value is None:
        return Markup("")
    escaped = escape(value)
    return Markup(escaped.replace("\n", Markup("<br>")))

TEMPLATES_DIR = Path(__file__).parent / "templates"


class RemoteFetchDenied(RuntimeError):
    """Raised when a template attempts a non-local resource fetch (T-11-03)."""


def _deny_url_fetcher(url: str, timeout: int = 10, ssl_context=None) -> dict:
    """url_fetcher that REJECTS all network requests (T-11-03 mitigation).

    WeasyPrint calls this for every external resource (img src, @font-face src,
    css @import). Phase 11 templates use only locally-bundled fonts and no
    remote images. Anything else is treated as a tampering attempt.

    Permits ``file:`` URLs that resolve inside the templates directory,
    rejecting any path that contains ``..`` segments (path-traversal guard,
    T-11-05), and ``data:`` URIs (task 2.3 per-HOA logo: inline base64
    image data carries no network/file access at all, so it can't be an
    SSRF vector — T-11-03 only concerns fetching an external resource).
    Returns the standard WeasyPrint dict shape via
    ``weasyprint.urls.default_url_fetcher`` for both permitted cases.
    """
    if url.startswith("file:"):
        if ".." in url:
            raise RemoteFetchDenied(
                f"Path traversal blocked in template URL: {url}"
            )
        from weasyprint.urls import default_url_fetcher

        return default_url_fetcher(url, timeout=timeout, ssl_context=ssl_context)

    if url.startswith("data:"):
        from weasyprint.urls import default_url_fetcher

        return default_url_fetcher(url, timeout=timeout, ssl_context=ssl_context)

    raise RemoteFetchDenied(
        f"Remote URL fetch blocked (T-11-03 mitigation): {url}. "
        "Templates must only reference locally bundled fonts/images."
    )


def _build_env(templates_subdir: str = "standard") -> Environment:
    """Construct the Jinja2 Environment used for template rendering.

    Hardening policies:
      * ``select_autoescape(["html", "xml"])`` — every ``{{ }}`` HTML-escapes
        by default. This is the T-11-03 (template injection) mitigation:
        HOA legal names, line-item labels, and reserve-component descriptions
        all flow through this filter.
      * ``StrictUndefined`` — fail loud on missing context variables (D-19
        spirit). Avoids silently rendering an empty string when a key is
        absent or misspelled in the caller's context dict.
      * ``trim_blocks`` / ``lstrip_blocks`` — keeps generated HTML readable
        for downstream debugging without affecting rendered output.
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR / templates_subdir)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["nl2br"] = _nl2br
    return env


def render_template(
    *,
    template_name: str,
    context: dict[str, Any],
    templates_subdir: str = "standard",
) -> bytes:
    """Render a single template to PDF bytes via WeasyPrint.

    Args:
        template_name: filename relative to ``templates/{subdir}/`` (e.g.
            ``"cover_letter.html"``).
        context: Jinja2 context — typically includes ``static_data``,
            ``fiscal_year``, ``spec``, and the ``computed`` dict produced by
            ``formulas.py``.
        templates_subdir: per-HOA subdir under ``templates/``.

    Returns:
        PDF bytes (one PDF per template; merge happens in plan 11-05).

    Raises:
        RemoteFetchDenied: if any template attempts an external fetch.
    """
    from weasyprint import HTML

    env = _build_env(templates_subdir)
    template = env.get_template(template_name)
    html_str = template.render(**context)

    base_url = str(TEMPLATES_DIR / templates_subdir)
    html_doc = HTML(
        string=html_str,
        base_url=base_url,
        url_fetcher=_deny_url_fetcher,
    )
    buf = BytesIO()
    html_doc.write_pdf(target=buf)
    return buf.getvalue()


def render_package(
    *,
    spec,  # PackageSpec
    computed: dict[str, Any],
    templates_subdir: str = "standard",
) -> dict[str, bytes]:
    """Render every GeneratedPage entry in ``spec.entries``.

    Returns a ``{template_name: pdf_bytes}`` dict. Static appendices
    (``StaticAppendix`` entries) are NOT rendered here — they are merged
    in plan 11-05.

    The caller must supply ``computed`` already populated with everything
    each template references; ``StrictUndefined`` will raise loudly if a
    key is missing.
    """
    from .schemas import GeneratedPage

    out: dict[str, bytes] = {}
    for entry in spec.entries:
        if isinstance(entry, GeneratedPage):
            ctx = {
                "spec": spec,
                "static_data": spec.static_data,
                "fiscal_year": spec.fiscal_year,
                # Default so TOC/page-reference templates can safely call
                # ``toc_page_numbers.get(...)`` even outside compiler.py's
                # two-pass render (e.g. standalone/test renders). Real
                # values are computed and injected by compile_package.
                "toc_page_numbers": {},
                # Same reasoning: appendix TOC rows are computed by
                # compile_package's single-source-of-truth appendix
                # resolution; standalone/test renders see an empty list.
                "appendix_toc_entries": [],
                # Default so _base.html's logo conditional doesn't raise
                # StrictUndefined outside compile_package. None -> default mark.
                "hoa_logo_data_uri": None,
                **computed,
            }
            out[entry.template] = render_template(
                template_name=entry.template,
                context=ctx,
                templates_subdir=templates_subdir,
            )
    return out
