"""Transient preview overlay from draft resolutions and line slices."""

from __future__ import annotations

import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from .schemas import CanonicalAllocationMethod, FactorSnapshot
from .service import list_current_resolutions, list_slices


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_preview_overlay(
    connection: sqlite3.Connection,
    *,
    assessment_setup_id: int,
    units: list[dict[str, Any]],
    pool_annuals: dict[str, Decimal],
) -> dict[str, Any]:
    """Build a non-mutating runtime overlay. Never writes the approved setup."""
    resolutions = list_current_resolutions(
        connection, assessment_setup_id=assessment_setup_id
    )
    slices = list_slices(connection, assessment_setup_id=assessment_setup_id)
    overlay_pools: list[dict[str, Any]] = []
    recipient_totals: dict[str, Decimal] = {
        str(u.get("unit_number")): Decimal("0") for u in units
    }
    computable = True
    for rec in resolutions:
        method = rec.resolved_method
        if method is None and rec.declared_method in {
            "equal", "square_footage", "ownership_percentage", "specified_value",
        }:
            method = rec.declared_method  # type: ignore[assignment]
        annual = pool_annuals.get(rec.pool_key, Decimal("0"))
        for sl in slices:
            if sl.pool_key == rec.pool_key:
                annual += sl.slice_annual_amount
        row = {
            "pool_key": rec.pool_key,
            "declared_method": rec.declared_method,
            "resolved_method": method,
            "status": rec.status,
            "annual_amount": str(annual),
            "final": rec.status == "approved",
        }
        if method is None:
            computable = rec.status != "draft"
            row["computable"] = False
            overlay_pools.append(row)
            continue
        shares = _allocate(
            method=method,
            annual=annual,
            units=units,
            snapshot=rec.factor_snapshot,
        )
        row["computable"] = True
        row["monthly_by_unit"] = {k: str(v) for k, v in shares.items()}
        for unit, amount in shares.items():
            recipient_totals[unit] = recipient_totals.get(unit, Decimal("0")) + amount
        overlay_pools.append(row)
    return {
        "is_final": False,
        "preview_label": "Non-final allocation preview",
        "computable": computable,
        "pools": overlay_pools,
        "monthly_by_unit": {k: str(v) for k, v in recipient_totals.items()},
        "slices": [sl.model_dump(mode="json") for sl in slices],
    }


def _allocate(
    *,
    method: CanonicalAllocationMethod,
    annual: Decimal,
    units: list[dict[str, Any]],
    snapshot: FactorSnapshot,
) -> dict[str, Decimal]:
    if not units or annual == 0:
        return {str(u.get("unit_number")): Decimal("0.00") for u in units}
    monthly = annual / Decimal("12")
    if method == "equal":
        each = _money(monthly / Decimal(len(units)))
        return {str(u.get("unit_number")): each for u in units}
    if method == "specified_value":
        return {
            unit: _money(Decimal(str(val)))
            for unit, val in snapshot.recipients.items()
        }
    out: dict[str, Decimal] = {}
    if method == "ownership_percentage":
        for unit in units:
            key = str(unit.get("unit_number"))
            raw = snapshot.recipients.get(key)
            if raw is None:
                raw = Decimal(str(unit.get("ownership_percent") or "0"))
            if raw > Decimal("1"):
                raw = raw / Decimal("100")
            out[key] = _money(monthly * raw)
        return out
    # square_footage
    denom = snapshot.denominator_value
    if denom is None:
        denom = Decimal("0")
        for unit in units:
            denom += Decimal(str(unit.get("square_feet") or "0"))
    for unit in units:
        key = str(unit.get("unit_number"))
        sqft = snapshot.recipients.get(key)
        if sqft is None:
            sqft = Decimal(str(unit.get("square_feet") or "0"))
        share = (monthly * sqft / denom) if denom else Decimal("0")
        out[key] = _money(share)
    return out


def candidate_factors_from_units(units: list[dict[str, Any]]) -> dict[str, Any]:
    ownership: dict[str, str] = {}
    sqft: dict[str, str] = {}
    for unit in units:
        key = str(unit.get("unit_number"))
        if unit.get("ownership_percent") not in (None, ""):
            ownership[key] = str(unit["ownership_percent"])
        if unit.get("square_feet") not in (None, ""):
            sqft[key] = str(unit["square_feet"])
    return {
        "ownership_percentage": ownership,
        "square_footage": sqft,
        "custom": {},
    }
