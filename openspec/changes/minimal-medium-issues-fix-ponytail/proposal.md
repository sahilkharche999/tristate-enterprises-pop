# Proposal: Minimal Medium Issues Fix Using Ponytail Skills

## Why
The Medium findings (M1-M4, M6-M9, M11, M13-M15) represent low-severity but important safety rail gaps in audit logging, locking, rendering, parsing, and override handling. These could allow wrong numbers, phantom overrides, or silent no-ops to reach finalized PDFs without operator signal. A minimal "ponytail" approach (laziest solution that actually works, shortest, most minimal) is needed to close these without over-engineering or adding new dependencies. This solves the "safety rails are largely unwired" problem with the least code and risk.

## What Changes
- Add missing optimistic locking (version checks, rowcount guards) to hoa_settings PUT and appendix update/retire (M8/M9).
- Separate rounding deltas from revenue gaps in engine (M4).
- Normalize unit keys and surface collisions in word-to-column snapping (M6/M13).
- Surface audit entries for missing/deleted recipients and overrides (M2).
- Remove unreachable 'rendered' status and tighten finalize checks (M14).
- Strengthen reserve component merge and validation (M15).
- Add bounded file reads and sanitized exception strings in upload routes (M11).
- Add minimal semantic guards for unit factors and scopes in CC&R/DRE promotion (M6/M7).
- Update audit tests and ensure no silent overrides (M1/M2).

**BREAKING:** None (all changes are additive or tightening existing behavior).

## Capabilities
### New Capabilities
- minimal-medium-safety-rails (covers all Medium issues with shortest pony-tail fixes)
- simple-override-audit (ensures missing/deleted cases always log)
- bounded-file-uploads (prevents DoS while keeping simple)
- normalized-unit-factors (no more phantom units)
- separated-rounding-deltas (clean audit)

### Modified Capabilities
- None (no existing spec requirement changes)

## Impact
- engine.py, compiler.py, income_statement_parser.py, ccr_approval_service.py, appendix_service.py, annual_package_service.py, reserve_study_extractor.py, routers/hoa_settings.py
- Audit logging and review surfaces will improve operator visibility
- No new dependencies; reuses existing optimistic_lock.py pattern
- Test coverage will increase for previously silent paths

This proposal uses the minimal pony-tail approach: reuse existing code patterns, remove dead code, add simple guards, and keep changes as short as possible while actually working.
