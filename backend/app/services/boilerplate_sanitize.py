"""Sanitization for operator-authored boilerplate slot HTML.

Every save-path write to a boilerplate slot (``hoa_boilerplate.merge_overrides``)
goes through here — it is the single place slot content crosses the trust
boundary from operator/editor input to stored data. The render path
(``disclosure_package.render``'s ``safe_html`` filter) trusts stored slot
values on the strength of that boundary and does not re-sanitize.

Uses ``nh3`` (Rust/Ammonia binding), not ``bleach`` — bleach shipped its
final release 2026-06-05 and is archived/unmaintained (built on dead
html5lib); nh3 is the maintained, ~20x faster, bleach-allowlist-compatible
successor.
"""
from __future__ import annotations

import re
from typing import Optional

import nh3

# Formatting the rich-text editor exposes (bold, lists, indentation) plus
# `span[data-var]` for variable-token chips. Nothing else survives —
# scripts, styles, links, images, and inline event/style attributes are
# stripped by omission from this allowlist.
_ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "ul", "ol", "li", "span"}
_ALLOWED_ATTRIBUTES = {
    "span": {"data-var"},
    "*": {"class"},  # editor-applied indent level, e.g. class="indent-1"
}

# Same tag-detection heuristic as render.py's `_safe_html` filter. A value
# with no HTML tags is plain text (a legacy pre-editor row, or a direct API
# write) — nh3.clean() would still HTML-escape a bare `&`/`<` in it (it
# treats any input as an HTML fragment), which corrupts the stored value on
# every subsequent save/render round-trip and would double-escape at render
# time (the `safe_html` filter escapes tag-free values itself). Bypassing
# nh3 entirely for tag-free input keeps it byte-for-byte as submitted.
_HAS_TAG_RE = re.compile(r"<[a-zA-Z!/]")


def sanitize_slot_html(raw: Optional[str]) -> Optional[str]:
    """Allowlist-clean one slot's HTML. ``None``/empty passes through as-is.

    Plain text (no tags at all) passes through byte-for-byte unchanged;
    formatting/escaping for it happens once, at render time.
    """
    if not raw:
        return raw
    if not _HAS_TAG_RE.search(raw):
        return raw
    return nh3.clean(
        raw,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        link_rel=None,
    )
