from __future__ import annotations

from typing import Literal, Optional


AssessmentMode = Literal["fixed", "variable"]
PackageImpact = Literal["none", "recheck_required", "regeneration_required"]

ASSESSMENT_MODE_FIXED: AssessmentMode = "fixed"
ASSESSMENT_MODE_VARIABLE: AssessmentMode = "variable"


def normalize_assessment_mode(value: Optional[str]) -> AssessmentMode:
    normalized = str(value or ASSESSMENT_MODE_VARIABLE).strip().lower()
    if normalized == ASSESSMENT_MODE_FIXED:
        return ASSESSMENT_MODE_FIXED
    return ASSESSMENT_MODE_VARIABLE


def package_impact_for_mode_drift(
    *,
    status: str,
    package_assessment_mode: Optional[str],
    live_assessment_mode: Optional[str],
    is_latest_for_fiscal_year: bool,
) -> tuple[PackageImpact, Optional[str]]:
    package_mode = normalize_assessment_mode(package_assessment_mode)
    live_mode = normalize_assessment_mode(live_assessment_mode)
    if not is_latest_for_fiscal_year or package_mode == live_mode:
        return "none", None
    if status == "finalized":
        return (
            "regeneration_required",
            "Assessment mode changed after finalization. Create a regeneration draft to publish the new mode.",
        )
    return (
        "recheck_required",
        "Assessment mode changed while this package is still live. Recheck the package before approval or finalization.",
    )
