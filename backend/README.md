# Python Conversion - Excel Macro Pipeline

This folder contains a standalone FastAPI application that implements the VBA macros from `Macros/` as a deterministic, server-side pipeline. The service accepts an uploaded Excel workbook and parameters, performs the computations previously implemented in VBA (without UI/formatting), and returns JSON results suitable for a frontend.

This is intentionally focused on pipeline behavior (compute values) rather than presentation formatting.

## Architecture

- app/main.py - FastAPI application factory and router registration.
- app/routers/macros.py - HTTP endpoints that accept file uploads and macro parameters.
- app/services/macros_service.py - Core logic that reads/writes Excel files using `openpyxl` and performs computations.
- app/models/schemas.py - Pydantic models for request/response validation.
- app/config.py - Externalized configuration (pydantic.BaseSettings).

All endpoints accept a multipart file upload (`file`) and macro-specific parameters. The uploaded file is saved temporarily on disk, processed, and removed.

## Configuration

The application supports externalized configuration via environment variables or a `.env` file (the `app.config.Settings` class). Default values are provided for all settings.

Available environment variables:

- `APP_HOST` (default `127.0.0.1`) - host to bind the server
- `APP_PORT` (default `8000`) - port to bind the server
- `ALLOW_ORIGINS` (default `*`) - comma-separated list of allowed CORS origins
- `TEMP_DIR` (default `./tmp`) - directory for temporary uploads and processing
- `MAX_PREVIEW_ROWS` (default `200`) - maximum rows returned in budget preview
- `REPO_ROOT` (optional) - override repository root path used to locate pipeline modules
- `DEFAULT_TEMPLATE_PATH` (optional) - path to default budget template

You can create a `.env` file in the project root with values like:

```bash
APP_HOST=127.0.0.1
APP_PORT=8000
ALLOW_ORIGINS=http://localhost:3000
TEMP_DIR=/tmp/tri-state-pop-tmp
MAX_PREVIEW_ROWS=250
REPO_ROOT=/Users/sahil/Desktop/POP/tri-state-pop
```

The application will load these values automatically on startup.

## Endpoints (summary)

- POST /macros/payment-search
  - Params: file, sheet (optional)
  - Returns: JSON containing rows and headers after sorting by column F then D

- POST /macros/sum-and-format
  - Params: file, sheet, start_cell (A1), row_count (int), target_col (letter)
  - Returns: JSON with computed sum and the cell where it was written

- POST /macros/ach-sum
  - Params: file, sheet, start_cell (A1), find_text (default: "ACH Draft")
  - Returns: JSON with sum between start_cell row and last found occurrence of find_text

- POST /macros/one-cell
  - Params: file, sheet, start_cell (A1)
  - Returns: JSON describing value copied

- POST /macros/find-dups
  - Params: file, sheet, lookup_col (letter)
  - Returns: JSON listing duplicates found

- POST /macros/cumulative-sum
  - Params: file, sheet, start_row (int), start_col (int), end_row (int)
  - Returns: JSON with cumulative sum

- POST /macros/remove-protection
  - Params: file
  - Returns: unlocked workbook bytes as an attachment

- POST /macros/generate-budget
  - Params: file, (optional) growth_factor, fiscal_year_start_month, reserve_contribution, template_file, am_seed_file, aliases_csv, enrich_only
  - Returns: JSON with growth factor used, enriched intermediate sheet, and budget preview

## How to run (local development)

1. Create a venv and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. (Optional) Create a `.env` file in the project root to override defaults.

3. Start the API server

```bash
uvicorn app.main:app --reload --host ${APP_HOST:-127.0.0.1} --port ${APP_PORT:-8000}
```

4. Use the OpenAPI docs

Open `http://127.0.0.1:8000/docs` to interact with the endpoints.

## Notes and next steps

- The service returns computed numeric values; `openpyxl` does not evaluate Excel formulas. Where the original VBA wrote formulas, the Python implementation computes the numeric result directly for the JSON response.
- `remove_protection` edits the XLSX zip content and returns an unlocked file as attachment; this can be adjusted to return bytes or a URL if preferred.
- For production, consider background tasks or job queueing for heavy files and add input validation, rate limiting, and antivirus scanning of uploads.

## Files

See `app/` for the code.
