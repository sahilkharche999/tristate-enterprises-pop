# Tasks: Minimal Medium Issues Fix Using Ponytail Skills

**Change:** minimal-medium-issues-fix-ponytail  
**Approach:** Pony-tail (laziest solution that actually works, shortest, most minimal). Reuse existing optimistic_lock.py patterns, remove dead code, normalize strings, add simple guards. Total estimated effort: 1-2 hours.

## Tasks

### Task 1: Add optimistic locking to hoa_settings PUT and appendix update/retire (M8/M9)
- [x] Edit `routers/hoa_settings.py` to require If-Match header and pass expected_version to service.
- [x] Edit `hoa_settings_service.py` to accept expected_version, perform Python check, and use full SQL predicate + rowcount guard in UPDATE.
- [ ] Edit `appendix_service.py` to add rowcount check after update (and for retire_appendix).
- [ ] Create/update tests in relevant test files for the new guards.
- [ ] Verify with: `openspec status --change "minimal-medium-issues-fix-ponytail"`

### Task 2: Separate rounding deltas and fix grain mix in engine (M4/M1)
- Edit `engine.py` in `_summarize_recipients`: compute proper rounding_delta_contribution from raw vs rounded difference (remove hardcoded 0).
- Edit `_apply_pool_overrides` to ensure grain-aware original sum calculation for pool overrides (use per-recipient in grouped cases where appropriate).
- Update `test_assessment_engine_override_audit.py` if needed for new delta values (minimal test adjustment only).
- Verify with full test run.

### Task 3: Normalize unit keys and surface collisions (M6/M13)
- Edit `ccr_approval_service.py` and `promotion.py`: add simple strip() for unit factor keys to prevent phantom units.
- Edit `income_statement_parser.py`: change word-to-column snapping to append collision suffix instead of unconditional overwrite; persist collision marker.
- Add simple audit entry for any collision detected.
- Verify with parser tests.

### Task 4: Override handling for missing/deleted recipients (M2)
- Edit `engine.py`: ensure _apply_recipient_overrides always appends audit entry (even if no-op) for every override scope.
- Edit `resolve_recipients` (recipients.py) if needed for better error/surface on missing.
- Update engine tests to cover missing recipient paths.
- Verify no silent billing for overrides.

### Task 5: Other Medium items (M7, M11, M14, M15)
- Add provenance check and `human_review_questions` for unknown CC&R denominators (M7).
- Add bounded file reads + sanitized exception strings in upload routes (M11).
- Remove unreachable 'rendered' state and tighten finalize check (M14).
- Add unique key for reserve components in merge + strict Pydantic validation (M15).
- Each as separate small edit, following existing patterns.

## Ponytail Notes
- Laziest path: Reuse existing lock pattern (2-3 lines added per endpoint) instead of new module or DB migration.
- Keep changes short: No new functions if possible; use existing helpers.
- Verify at end: Run full backend test suite (`pytest backend/tests/ -q`) and check for any new operator review surfaces.
- If any task unclear, ask user before proceeding.

**Total estimated lines added:** ~80-120 (very minimal). Ready for implementation once proposal/design approved.
