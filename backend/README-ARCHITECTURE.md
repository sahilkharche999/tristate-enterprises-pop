Python Conversion - Architecture

Overview
--------
This small FastAPI app converts the Excel VBA macros (in the original repo under `Macros/`) into deterministic Python functions that operate on uploaded workbooks and return JSON results. The design focuses on pipeline behavior: computing values and returning them to a frontend.

Components
----------
- app/main.py - creates the FastAPI app and registers routers
- app/routers/macros.py - endpoints mapped to macro-like operations
- app/services/macros_service.py - core functions that read and manipulate Excel files
- app/models/schemas.py - response schemas for endpoints
- requirements.txt - dependencies for the minimal app

Data flow
---------
1. Client uploads a file and posts to an endpoint (e.g., `/macros/sum-and-format`).
2. Router saves file to a temporary path and calls service function with parameters.
3. Service function reads/modifies workbook using `openpyxl` and returns JSON.
4. Router returns JSON to client and deletes temporary file.

Deployment notes
----------------
- For production, run via Uvicorn/Gunicorn with multiple workers and a process manager.
- Consider background job queue (Redis + RQ/Celery) for long-running Excel operations.
- Scan uploaded files and enforce size limits.

