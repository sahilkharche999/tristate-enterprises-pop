#!/usr/bin/env bash
# Extracts the 24 static appendix PDFs from the Old Mill 2026 golden disclosure
# package into backend/app/disclosure_package/appendices/old_mill/.
#
# Run once on the host before `docker compose up --build`. The extracted files
# are then COPYed into the backend image at build time and consumed by
# disclosure_package.compiler at runtime.
#
# This is the human-supervised step that Phase 11 plan 11-05 Task 2 leaves
# gated on Tri-State legal/CCRs review — the page-range mapping is recorded in
# backend/app/disclosure_package/appendices/old_mill/MANIFEST.md.
#
# Usage:
#   ./scripts/extract-old-mill-appendices.sh
#   GOLDEN_PDF="path/to/Old Mill 2026 budget disclosure.pdf" ./scripts/extract-old-mill-appendices.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GOLDEN_PDF="${GOLDEN_PDF:-${REPO_ROOT}/2026/Old Mill 2026 budget disclosure.pdf}"
APPDIR="${REPO_ROOT}/backend/app/disclosure_package/appendices/old_mill"

if ! command -v qpdf >/dev/null 2>&1; then
  echo "ERROR: qpdf not found on PATH." >&2
  echo "  macOS:  brew install qpdf" >&2
  echo "  Ubuntu: sudo apt-get install qpdf" >&2
  exit 1
fi

if [ ! -f "$GOLDEN_PDF" ]; then
  echo "ERROR: golden PDF not found at:" >&2
  echo "  $GOLDEN_PDF" >&2
  echo "Set GOLDEN_PDF=/path/to/file or place it at 2026/Old Mill 2026 budget disclosure.pdf." >&2
  exit 1
fi

mkdir -p "$APPDIR"

# (filename, source_pages, expected_count)
ENTRIES=(
  "thirty_year_plan_extra.pdf|32-45|14"
  "insurance_certificate.pdf|46-48|3"
  "annual_policy_statement_cover.pdf|49|1"
  "adr_disclosure.pdf|50-55|6"
  "collection_policy.pdf|56-58|3"
  "enforcement_fine_policy.pdf|59-62|4"
  "hard_surface_flooring.pdf|63-64|2"
  "window_patio_door.pdf|65-66|2"
  "garage_door_guidelines.pdf|67-68|2"
  "satellite_dish.pdf|69-73|5"
  "appendix_pages_74_87.pdf|74-87|14"
  "rules_restrictions.pdf|88-91|4"
  "pool_rules.pdf|92|1"
  "parking_rules.pdf|93|1"
  "water_intrusion.pdf|94-96|3"
  "clubhouse_rentals.pdf|97-98|2"
  "open_house_policy.pdf|99|1"
  "move_in_out.pdf|100|1"
  "quiet_hours.pdf|101|1"
  "storage_container.pdf|102|1"
  "emergency_shutoff.pdf|103-104|2"
  "open_forum_resolution.pdf|105-107|3"
  "electronic_consent_form.pdf|108|1"
  "signoff.pdf|109|1"
)

echo "Extracting 24 Old Mill appendices from:"
echo "  $GOLDEN_PDF"
echo "Into:"
echo "  $APPDIR"
echo

failures=0
for entry in "${ENTRIES[@]}"; do
  IFS='|' read -r filename pages expected <<<"$entry"
  out="$APPDIR/$filename"
  printf "  %-40s pages %-7s " "$filename" "$pages"
  qpdf "$GOLDEN_PDF" --pages "$GOLDEN_PDF" "$pages" -- --empty "$out"
  actual=$(qpdf --show-npages "$out")
  if [ "$actual" != "$expected" ]; then
    printf "[FAIL: got %s pages, expected %s]\n" "$actual" "$expected"
    failures=$((failures + 1))
  else
    printf "[ok %s pages]\n" "$actual"
  fi
done

echo
if [ "$failures" -gt 0 ]; then
  echo "ERROR: $failures appendix file(s) had wrong page count. Check the source PDF and the page ranges in MANIFEST.md." >&2
  exit 1
fi

echo "Validating PDF structure with qpdf --check..."
for f in "$APPDIR"/*.pdf; do
  if ! qpdf --check "$f" >/dev/null 2>&1; then
    echo "  FAIL: $(basename "$f")" >&2
    failures=$((failures + 1))
  fi
done

if [ "$failures" -gt 0 ]; then
  echo "ERROR: $failures appendix file(s) failed structural validation." >&2
  exit 1
fi

total=$(ls -1 "$APPDIR"/*.pdf | wc -l | tr -d ' ')
echo "Done. $total appendix files extracted and validated."
echo "Next: docker compose up --build"
