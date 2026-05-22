# Assessment Engine & Disclosure Package — Adversarial Review

**Reviewed:** 2026-05-22
**Scope:** Assessment engine orchestrator, pool math, approval flow, preflight, snapshots, compile inputs, optimistic lock
**Reviewer:** Claude (adversarial stance)

---

## CRITICAL — Incorrect financial calculations that could produce wrong dollar amounts on homeowner PDFs

### CR-01: Pool override replaces the TOTAL with the same value for EVERY recipient

**File:** `backend/app/assessment_engine/engine.py:314-327`
**Impact:** A pool-scope override intended to set the pool's total monthly to $X instead sets EVERY recipient's component to $X, multiplying the intended override by the number of recipients.

```python
for row in pool_allocations:
    ov = pool_overrides.get(row.pool_id)
    if ov is not None:
        out.append(
            row.model_copy(
                update={
                    "unrounded_component_monthly": ov.override_monthly_amount,
                    "source": "override",
                }
            )
        )
```

If pool has 279 units and the operator sets pool override to $5000/month, this code sets EACH of the 279 recipients' component to $5000, producing $1,395,000/month instead of $5000/month total. The override amount should be re-distributed across recipients (e.g., divided equally or proportionally), not stamped verbatim onto every row.

**Fix:** The pool override needs to redistribute the override amount across recipients using the same allocation method, or the override semantics need to be "per-recipient override" (and documented as such). If the latter, the `pre_override_totals` audit delta is also wrong — it compares the sum of all recipients' old values against the per-recipient new value.

---

### CR-02: `_summarize_recipients` annual calculation ignores `unit_count` for grouped HOAs

**File:** `backend/app/assessment_engine/engine.py:383-396`

```python
annual = rounded * TWELVE
```

The comment at line 383-385 says "All pool components are normalized to per-recipient dollars (per-group for groups)". For grouped HOAs, `annual_total` should be `rounded * TWELVE * unit_count` to represent the total annual revenue from that group. The reconciliation at step 7 compares `sum(t.annual_total)` against `approved_assessment_revenue_annual`, which is the HOA's total revenue. If a group has 50 units paying $200/month, the annual should be $200 * 12 * 50 = $120,000, not $200 * 12 = $2,400.

The spec comment at line 19 says: "7. Compute annual_total = rounded x 12 x unit_count." The implementation omits `* unit_count`.

**Fix:**
```python
annual = rounded * TWELVE * Decimal(r.unit_count)
```

---
