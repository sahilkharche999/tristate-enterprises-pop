# Design: Minimal Medium Issues Fix Using Ponytail Skills

## Context
The Medium findings represent safety rail gaps that could allow wrong numbers or silent overrides to reach finalized PDFs. Previous exploration showed issues in override audit grain mixing, missing/deleted recipient handling, rounding delta conflation, unit factor key matching, locking on settings and appendices, exception string exposure, word-to-column collisions, unreachable 'rendered' status, and reserve component merge. A minimal pony-tail approach is used: reuse existing optimistic_lock.py patterns, remove dead code, normalize strings, add simple guards, and keep changes short while ensuring the fixes actually work.

## Goals / Non-Goals
**Goals:** Close all Medium safety gaps with the shortest possible code, reuse existing patterns, ensure audit always surfaces missing/deleted cases and collisions, prevent DoS and phantom units, tighten lifecycle checks, and maintain Decimal-only discipline.

**Non-Goals:** No new dependencies, no new UI features, no changes to CRITICAL/HIGH items or production parsers, no full over-engineering, no test suite changes beyond minimal.

## Decisions
- Use existing `require_if_match` + SQL predicate + rowcount guard pattern from annual_package_service.py for hoa_settings and appendix update/retire (M8/M9) — minimal addition of 2-3 lines.
- In engine.py, separate rounding contribution in _summarize_recipients (remove hardcoded 0, compute from quantize difference) and make deltas conflation explicit in reconciliation (M4).
- In ccr_approval_service.py and promotion.py, add whitespace stripping for unit keys (M6) and provenance check for unknown denominators (M7) — simple string ops, no new functions.
- In income_statement_parser.py, make word-to-column snapping append a collision suffix instead of overwriting (M13).
- In annual_package_service.py, remove 'rendered' from status enum and finalize check (M14).
- In reserve_study_extractor.py, add unique key for components in merge and run Pydantic strict validation (M15).
- In routers/budget_history.py, ccr.py, appendices.py, add bounded `file.read(max_size)` and sanitize exception strings (M11).
- In engine.py and tests, ensure _apply_recipient_overrides always logs audit even for missing recipients (M2) and fix grain mix in pool override (M1).

**Alternatives considered:** Full versioned DB migration for all tables (too much); new LLM validation (overkill); complex UI (out of scope). Ponytail chose the reuse-and-minimize path.

## Risks / Trade-offs
- Risk: Rowcount guard could be bypassed in rare concurrent cases → Mitigation: Keep as-is since other endpoints use it safely.
- Risk: String stripping misses some edge cases (e.g., non-ASCII) → Mitigation: Keep simple, operator review will catch bad data.
- Risk: Minimal changes miss subtle interactions → Mitigation: Reuse existing test patterns and run full test suite after.

## Migration Plan
1. Apply the change (PR or direct edit).
2. Deploy to staging.
3. Run full test suite and preflight checks.
4. Monitor for any new operator feedback on review surfaces.
5. Rollback: Simple git revert (no data migration needed).

## Open Questions
- None — all decisions resolved via pony-tail minimalism.

This design follows the "laziest solution that actually works" principle: reuse existing code, add 1-2 lines per issue, ensure fixes are verifiable, and keep the total diff under 100 lines where possible.
