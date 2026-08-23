"""131 Missouri Street regression fixture — representative, not hardcoded product rules.

The governing document defers exception-pool dollars to an external DRE
proration schedule. Unit square footage and ownership percentages both exist.
The income statement combines electricity and gas. Levy's approved schedule
is the expected executable resolution of that external rule.
"""

from __future__ import annotations

from decimal import Decimal


MISSOURI_CCR_SUMMARY = (
    "Regular assessments are divided equally among all owners, except for "
    "insurance, gas and water, and reserves for roof, paint, and water heaters, "
    "which are divided according to the proration schedule in the DRE operating budget."
)

MISSOURI_DECLARED_EXCEPTION_METHOD = "custom_factor"
MISSOURI_DENOMINATOR_LABEL = "DRE operating budget proration schedule"

MISSOURI_UNITS: list[dict[str, str]] = [
    {"unit_number": "201", "ownership_percent": "14.5", "square_feet": "2202"},
    {"unit_number": "202", "ownership_percent": "8.6", "square_feet": "1308"},
    {"unit_number": "203", "ownership_percent": "10.1", "square_feet": "1526"},
    {"unit_number": "204", "ownership_percent": "17.2", "square_feet": "2599"},
    {"unit_number": "301", "ownership_percent": "9.7", "square_feet": "1465"},
    {"unit_number": "302", "ownership_percent": "9.7", "square_feet": "1462"},
    {"unit_number": "401", "ownership_percent": "10.3", "square_feet": "1560"},
    {"unit_number": "402", "ownership_percent": "9.6", "square_feet": "1457"},
    {"unit_number": "403", "ownership_percent": "10.3", "square_feet": "1557"},
]

MISSOURI_TOTAL_SQFT = Decimal("15136")
MISSOURI_TOTAL_OWNERSHIP_POINTS = Decimal("100.0")

MISSOURI_REQUIRED_CATEGORIES = (
    "insurance",
    "gas",
    "water",
    "roof reserve",
    "painting reserve",
    "water heater reserve",
)

MISSOURI_BUDGET_LINES: list[dict] = [
    {"label": "Insurance", "annual_amount": Decimal("8340"), "category": "operating"},
    {
        "label": "Electricity & Gas",
        "annual_amount": Decimal("16800"),
        "category": "operating",
    },
    {"label": "Water", "annual_amount": Decimal("9000"), "category": "operating"},
    {
        "label": "Reserve - Allocation/Transfer",
        "annual_amount": Decimal("31935"),
        "category": "operating",
    },
    {"label": "Management", "annual_amount": Decimal("18000"), "category": "operating"},
    {"label": "Repairs", "annual_amount": Decimal("20383"), "category": "operating"},
]

# Levy / DRE-schedule resolution of the CC&R exception list.
MISSOURI_LEVY_VARIABLE_SLICES: dict[str, Decimal] = {
    "insurance": Decimal("8340"),
    "gas": Decimal("5600"),
    "water": Decimal("9000"),
    "water heater reserve": Decimal("718"),
    "painting reserve": Decimal("7181"),
    "roof reserve": Decimal("1028"),
}

MISSOURI_LEVY_VARIABLE_ANNUAL = Decimal("31867")
MISSOURI_LEVY_EQUAL_ANNUAL = Decimal("72591")
MISSOURI_LEVY_HOA_ANNUAL = Decimal("104458")

# Electricity remains in the equal residual after the gas slice is split out.
MISSOURI_ELECTRICITY_EQUAL_SLICE = Decimal("11200")
MISSOURI_RESERVE_EQUAL_SLICE = Decimal("23008")

# Monthly totals from Levy (equal $672.14 + ownership-prorated variable).
MISSOURI_LEVY_MONTHLY_ASSESSMENTS: dict[str, Decimal] = {
    "201": Decimal("1057.20"),
    "202": Decimal("900.52"),
    "203": Decimal("940.35"),
    "204": Decimal("1128.90"),
    "301": Decimal("929.73"),
    "302": Decimal("929.73"),
    "401": Decimal("945.67"),
    "402": Decimal("927.08"),
    "403": Decimal("945.67"),
}


def missouri_extraction_payload() -> dict:
    """Governing-document extraction shape that triggered the silent sqft collapse."""
    return {
        "document_metadata": {
            "association_name": "131 Missouri Street Homeowners' Association",
        },
        "page_inventory": [],
        "assessment_setup": {
            "setup_type": "multi_pool_combination",
            "display_mode": "",
            "summary": MISSOURI_CCR_SUMMARY,
            "requires_dre_for_future_years": True,
            "confidence": 0.9,
            "source_pages": [6],
        },
        "unit_structure": {
            "unit_count": 9,
            "group_count": 0,
            "groups": [],
            "units": [
                {
                    "unit_number": row["unit_number"],
                    "square_feet": row["square_feet"],
                    "ownership_percent": row["ownership_percent"],
                }
                for row in MISSOURI_UNITS
            ],
        },
        "allocation_pools": [
            {
                "pool_key": "equal_base",
                "pool_name": "Equal Base Pool",
                "annual_amount": None,
                "allocation_method": "equal",
                "recipient_scope": "all_units",
                "denominator_label": "units",
                "denominator_value": None,
                "denominator_source": "unknown",
                "included_budget_lines": [],
                "excluded_budget_lines": [],
                "budget_line_derivation": "residual_default",
                "source_pages": [6],
                "confidence": 0.9,
            },
            {
                "pool_key": "variable_dre_exceptions",
                "pool_name": "Insurance, Utilities, and Specific Reserves Pool",
                "annual_amount": None,
                "allocation_method": MISSOURI_DECLARED_EXCEPTION_METHOD,
                "recipient_scope": "all_units",
                "denominator_label": MISSOURI_DENOMINATOR_LABEL,
                "denominator_value": None,
                "denominator_source": "unknown",
                "included_budget_lines": list(MISSOURI_REQUIRED_CATEGORIES),
                "excluded_budget_lines": [],
                "budget_line_derivation": "explicit_lines",
                "source_pages": [6],
                "confidence": 0.9,
            },
            {
                "pool_key": "parking_cost_center",
                "pool_name": "Parking Cost Center",
                "annual_amount": None,
                "allocation_method": "square_footage",
                "recipient_scope": "parking_users",
                "denominator_label": "parking square footage",
                "denominator_value": None,
                "denominator_source": "unknown",
                "included_budget_lines": [],
                "excluded_budget_lines": [],
                "budget_line_derivation": "explicit_lines",
                "pool_kind": "",
                "source_pages": [6],
                "confidence": 0.8,
            },
            {
                "pool_key": "structural_repair_sa",
                "pool_name": "Structural Area Major Repair Special Assessment",
                "annual_amount": None,
                "allocation_method": "square_footage",
                "recipient_scope": "all_units",
                "denominator_label": "unit square footage",
                "denominator_value": None,
                "denominator_source": "unknown",
                "included_budget_lines": [
                    "rebuilding or major repair of structural Common Area"
                ],
                "excluded_budget_lines": [],
                "budget_line_derivation": "explicit_lines",
                "pool_kind": "separately_billed_special_assessment",
                "source_pages": [6],
                "confidence": 0.9,
            },
        ],
        "formulas": [],
        "reserve_setup": None,
        "validation_checks": [],
        "human_review_questions": [],
        "recommended_saved_setup": None,
    }


def missouri_sqft_monthly_for_unit(unit_number: str) -> Decimal:
    """What the silent custom_factor→sqft collapse would charge this unit."""
    from decimal import ROUND_HALF_UP

    unit = next(u for u in MISSOURI_UNITS if u["unit_number"] == unit_number)
    sqft = Decimal(unit["square_feet"])
    equal_monthly = (MISSOURI_LEVY_EQUAL_ANNUAL / Decimal("12") / Decimal("9")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    variable_monthly = (
        MISSOURI_LEVY_VARIABLE_ANNUAL / Decimal("12") * sqft / MISSOURI_TOTAL_SQFT
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return equal_monthly + variable_monthly
