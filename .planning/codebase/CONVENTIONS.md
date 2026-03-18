# Coding Conventions

**Analysis Date:** 2026-03-18

## Naming Patterns

**Files:**
- Python source uses `snake_case.py` naming throughout `backend/app/`
- Feature-level React components use `PascalCase.tsx`
- Shared UI primitives use lowercase or kebab-case names under `frontend/src/app/components/ui/`
- No test filename convention exists yet because no first-party tests are present

**Functions:**
- Python functions use `snake_case`
- Frontend functions and helpers use `camelCase`
- Event handlers in React follow `handle*` naming such as `handleLogin`, `handleFileChange`, and `handleFetchAISuggestions`
- Async functions do not use a special prefix beyond descriptive verbs (`refreshToken`, `runGenerateBudget`, `run_pipeline`)

**Variables:**
- Python locals and module variables use `snake_case`
- TypeScript locals and state setters use `camelCase`
- Constants use uppercase or module-prefixed underscore forms such as `REFRESH_COOKIE`, `COOKIE_MAX_AGE`, and `_AM_COL`
- Private or internal helpers often use a leading underscore (`_tmp_save_path`, `_set_refresh_cookie`, `_getAccessToken`)

**Types:**
- Frontend interfaces and type aliases use `PascalCase` (`LineItem`, `GenerateBudgetResponse`, `AISuggestionResponse`)
- Pydantic models also use `PascalCase` on the backend (`UserCreate`, `TokenResponse`, `TableResponse`)
- No enums are used in the inspected app code; unions and string literals are preferred on the frontend

## Code Style

**Formatting:**
- Python code uses docstrings, type hints, and 4-space indentation
- Frontend app code uses 2-space indentation, semicolons in most component files, and single quotes in most app modules
- Config files are less rigid; `frontend/vite.config.ts` does not fully match the app-file punctuation style
- Comments exist around tricky workbook-column logic and auth/session behavior, but most UI code is self-describing

**Linting:**
- No ESLint, Prettier, Ruff, or Black config was found in the tracked repo
- Formatting appears to be maintained manually or by editor defaults
- The practical rule is to match the surrounding file rather than assuming a repo-enforced formatter

## Import Organization

**Order:**
1. Standard library imports first in Python modules
2. Third-party packages next
3. Local package-relative imports last
4. Frontend files import external packages first, then relative app modules

**Grouping:**
- Blank lines usually separate import groups in Python
- Frontend imports are commonly grouped in one block, but external modules still appear before local modules
- Type imports are often inline with value imports (`type ReactNode`, `type User`) rather than isolated in a separate block

**Path Aliases:**
- `@` is configured in `frontend/vite.config.ts` to point at `frontend/src`
- Current app code mostly uses relative imports instead of the alias

## Error Handling

**Patterns:**
- Backend service functions raise `ValueError` for invalid workbook inputs and let unexpected failures bubble
- FastAPI routers catch boundary errors and convert them to `HTTPException`
- Many workbook endpoints intentionally return sanitized `500` messages rather than raw stack details
- Frontend API wrappers throw plain objects shaped like `{ status, message }` instead of custom `Error` instances

**Error Types:**
- Authentication failures return `401` and the frontend clears state or redirects to login
- Validation and missing-sheet issues return `400` when the route explicitly catches `ValueError`
- Silent recovery is used in a few places, such as logout failure or startup seeding failures, when the app prefers degraded behavior over hard failure

## Logging

**Framework:**
- Python standard `logging` configured once in `backend/app/main.py`
- No dedicated frontend logging framework is present

**Patterns:**
- Backend logs route starts/completions, durations, and selected context like workbook path, sheet name, counts, and user email
- AI pipeline stages log milestone timings and activation decisions
- Frontend uses `sonner` toasts for user-facing feedback rather than persistent logs

## Comments

**When to Comment:**
- Python modules use docstrings heavily for functions and modules
- Inline comments explain workbook column mappings, request semantics, and cleanup behavior
- React files use section-divider comments sparingly around major UI blocks

**JSDoc/TSDoc:**
- Not used consistently in frontend code
- Backend relies on Python docstrings instead

**TODO Comments:**
- No meaningful `TODO`, `FIXME`, or `HACK` markers were found in tracked app code

## Function Design

**Size:**
- FastAPI route handlers stay relatively thin and delegate heavy work to services or pipeline modules
- Large multi-responsibility modules exist for workbook logic and AI seeding/training

**Parameters:**
- Backend HTTP boundaries favor explicit form/body fields and Pydantic models
- Frontend API wrappers often accept a single object parameter when there are multiple related inputs
- React handlers commonly close over component state instead of accepting long argument lists

**Return Values:**
- Backend helpers return JSON-serializable dictionaries or Pydantic models
- Frontend utilities prefer explicit transformed return types like `LineItem[]`, `SheetTable`, or typed response objects
- Guard clauses are common in both Python and TypeScript

## Module Design

**Exports:**
- Most frontend modules use named exports
- `frontend/src/app/App.tsx` is a default export, which is the exception rather than the rule
- Python modules export functions, router instances, or ORM models directly without barrel layers

**Barrel Files:**
- No general barrel-file pattern is used in the frontend
- Python packages include `__init__.py`, but imports still point to concrete modules
- New code should usually import from the real module path instead of adding an index layer

---
*Convention analysis: 2026-03-18*
*Update when formatting, import, or error-handling patterns change*
