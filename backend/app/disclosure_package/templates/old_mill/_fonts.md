# Old Mill Disclosure Package — Font Pinning (CONTEXT D-10)

## Bundled fonts (Docker image)

Installed by `apt-get install fonts-liberation fonts-dejavu fonts-noto-core`
in `backend/Dockerfile` (plan 11-01).

- **Liberation Serif** (metric-compatible with Times New Roman) — body text
- **Liberation Sans** (metric-compatible with Arial / Helvetica) — headings
- **DejaVu** — fallback for missing glyphs
- **Noto Core** — fallback for Unicode coverage

## Why pinning matters

WeasyPrint silently substitutes missing fonts (RESEARCH Pitfall 1).
Substitution shifts line wrapping by 1-2 pixels — enough to push a row
to the next page. Downstream page numbers in the TOC + appendix anchors
break silently. Raster diff fails on every page after the drift point.

Phase 11 makes the substitution path impossible by:

1. Pinning a closed set of bundled families in `_shared.css`.
2. Forbidding remote `@font-face` fetches via `_deny_url_fetcher`
   (T-11-03 mitigation in `render.py`).
3. Failing the docker image build at health-check time if any of the
   families are missing (Dockerfile + plan 11-01 verify).

## CSS contract

`_shared.css` references these families by name only:

```css
font-family: "Liberation Serif", Times, serif;
font-family: "Liberation Sans", Helvetica, Arial, sans-serif;
```

There are NO `@font-face` URL declarations. WeasyPrint loads via
fontconfig from the local image. The fallbacks (Times / Helvetica) are
defensive — if WeasyPrint is run outside the pinned image (e.g. local
dev) the renderer falls back to system Times metrics, which are very
close to Liberation Serif metrics by design.

## Verification

After every Dockerfile change:

```bash
docker run --rm <image> fc-list | grep -iE "(liberation|dejavu|noto)"
```

Must show all four families. If any is missing, the rendering CSS
falls back to a substituted font and parity tests will fail.

## Why no @font-face URL?

`_deny_url_fetcher` rejects every non-`file:` URL. If a future maintainer
adds an `@font-face { src: url('https://fonts.example/...') }` rule,
WeasyPrint will call the fetcher, which will raise
`RemoteFetchDenied` and abort the render. This is the desired blast
radius — the bug surfaces at render time, not silently in production.
