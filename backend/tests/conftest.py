import importlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: live network call (Gemini API) — runs only with -m live",
    )

from app import main as main_module
from app import config as app_config
from app.ai_implementation import database as database_module
from app.ai_implementation import db as db_package
from app.ai_implementation.db import User
from app.ai_implementation.db import session as session_module
from app.ai_implementation.db import (
    BUDGET_DRAFT_ACTIVE,
    BUDGET_VERSION_STAGE_FINAL,
    BUDGET_VERSION_STAGE_INTERIM,
    BudgetDraft,
    BudgetNote,
    BudgetUpload,
    BudgetVersion,
    Property,
)
from app.services import macros_service
from app.ai_implementation.seed.seed_database import sync_portfolio_properties
from app.auth.utils import create_access_token


def _maybe_import(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None


def _maybe_patch(monkeypatch, module_name: str, attr_name: str, value) -> None:
    module = _maybe_import(module_name)
    if module is not None and hasattr(module, attr_name):
        monkeypatch.setattr(module, attr_name, value)


class FakeBudgetPipeline:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.intermediate_path = kwargs["intermediate_path"]
        self.output_path = kwargs["output_path"]

    def run(self):
        Path(self.intermediate_path).write_bytes(b"fake enriched workbook")
        Path(self.output_path).write_bytes(b"fake generated workbook")


def fake_write_percent_changes_by_label(path: str, sheet: str, changes):
    return len(changes) if changes else 0


def fake_infer_growth_factor_from_input(input_path: str, fiscal_year_start_month: int):
    return 1.25, 6, f"stub:{fiscal_year_start_month}"


def fake_read_sheet_as_table(path: str, sheet: str):
    return {
        "sheet": sheet,
        "headers": ["Account Code", "Label", "Annual Budget", "Projection"],
        "rows": [
            [4010, "Assessment Revenue", 120000.0, 126000.0],
            [6100, "Insurance", 32000.0, 35200.0],
        ],
    }


def fake_read_first_sheet_preview(path: str, max_rows: int):
    return {
        "sheet": "Budget",
        "headers": ["Account Code", "Line Item", "YTD Actual", "Annual Budget",
                     "% Change", "Proposed Budget", "Monthly", "Notes"],
        "rows": [
            ["", "Total Income", None, 126000.0, None, 126000.0, 10500.0, None],
            ["", "Total Expense", None, 35200.0, None, 35200.0, 2933.33, None],
            ["", "Net Operating Income", None, 90800.0, None, 90800.0, 7566.67, None],
        ],
    }


def _seed_compare_line_items(*, insurance: float, circulation_pump: float) -> list[dict[str, object]]:
    return [
        {
            "line_item_key": "55000",
            "account_code": 55000,
            "category": "operating",
            "label": "55000 - General Insurance",
            "annual_budget": insurance,
            "percent_change": 0.0,
            "read_only": False,
        },
        {
            "line_item_key": "91432",
            "account_code": 91432,
            "category": "reserve",
            "label": "91432 - Circulation Pump 5 H.P.",
            "annual_budget": circulation_pump,
            "percent_change": 0.0,
            "read_only": True,
        },
        {
            "line_item_key": "90000",
            "account_code": 90000,
            "category": "reserve",
            "label": "90000 - Reserve - Allocation/Transfer",
            "annual_budget": 24000.0,
            "percent_change": 0.0,
            "read_only": True,
        },
        {
            "line_item_key": "45000",
            "account_code": 45000,
            "category": "reserve",
            "label": "45000 - Reserve Income",
            "annual_budget": 12000.0,
            "percent_change": 0.0,
            "read_only": True,
        },
        {
            "line_item_key": "93620",
            "account_code": 93620,
            "category": "reserve",
            "label": "93620 - Roof",
            "annual_budget": 0.0,
            "percent_change": 0.0,
            "read_only": True,
        },
    ]


def _seed_compare_preview(line_items: list[dict[str, object]]) -> dict[str, object]:
    total_expense = round(
        sum(float(item["annual_budget"]) for item in line_items if item["category"] != "income"),
        2,
    )
    return {
        "sheet": "Budget",
        "headers": ["Account Code", "Line Item", "YTD Actual", "Annual Budget",
                     "% Change", "Proposed Budget", "Monthly", "Notes"],
        "rows": [
            ["", "Total Income", None, 0.0, None, 0.0, 0.0, None],
            ["", "Total Expense", None, total_expense, None, total_expense, round(total_expense / 12, 2), None],
            ["", "Net Operating Income", None, -total_expense, None, -total_expense, round(-total_expense / 12, 2), None],
        ],
    }


@pytest.fixture
def budget_storage_root(tmp_path, monkeypatch) -> Path:
    storage_root = tmp_path / "budget-storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    settings_owners = (
        app_config.settings,
        main_module.settings,
        database_module.settings,
    )
    previous_values: list[tuple[object, bool, object | None]] = []
    for settings_owner in settings_owners:
        had_attr = hasattr(settings_owner, "BUDGET_STORAGE_ROOT")
        previous_values.append(
            (settings_owner, had_attr, getattr(settings_owner, "BUDGET_STORAGE_ROOT", None))
        )
        object.__setattr__(settings_owner, "BUDGET_STORAGE_ROOT", str(storage_root))

    try:
        yield storage_root
    finally:
        for settings_owner, had_attr, previous_value in previous_values:
            if had_attr:
                object.__setattr__(settings_owner, "BUDGET_STORAGE_ROOT", previous_value)
            elif hasattr(settings_owner, "BUDGET_STORAGE_ROOT"):
                object.__delattr__(settings_owner, "BUDGET_STORAGE_ROOT")


@pytest.fixture
def budget_history_test_harness(monkeypatch, budget_storage_root):
    monkeypatch.setattr(macros_service, "read_sheet_as_table", fake_read_sheet_as_table)
    monkeypatch.setattr(macros_service, "read_first_sheet_preview", fake_read_first_sheet_preview)
    monkeypatch.setattr(
        macros_service,
        "write_percent_changes_by_label",
        fake_write_percent_changes_by_label,
    )

    _maybe_patch(monkeypatch, "app.services.budget_history_service", "BudgetPipeline", FakeBudgetPipeline)
    _maybe_patch(
        monkeypatch,
        "app.services.budget_history_service",
        "infer_growth_factor_from_input",
        fake_infer_growth_factor_from_input,
    )

    service_module = _maybe_import("app.services.budget_history_service")
    if service_module is not None and hasattr(service_module, "macros_service"):
        monkeypatch.setattr(service_module.macros_service, "read_sheet_as_table", fake_read_sheet_as_table)
        monkeypatch.setattr(
            service_module.macros_service,
            "read_first_sheet_preview",
            fake_read_first_sheet_preview,
        )
        monkeypatch.setattr(
            service_module.macros_service,
            "write_percent_changes_by_label",
            fake_write_percent_changes_by_label,
        )

    return {
        "storage_root": budget_storage_root,
        "expected_growth_factor": 1.25,
    }


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    db_path = tmp_path / "test_budget_ai.db"

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    event.listen(engine, "connect", session_module._set_sqlite_pragmas)
    testing_session_local = sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr(main_module.settings, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(database_module.settings, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(session_module, "engine", engine)
    monkeypatch.setattr(session_module, "SessionLocal", testing_session_local)
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(db_package, "engine", engine)
    monkeypatch.setattr(db_package, "SessionLocal", testing_session_local)
    monkeypatch.setattr(main_module, "run_seed", lambda *args, **kwargs: {"skipped": True})

    database_module.init_db()
    raw_db = database_module.get_db()
    try:
        sync_portfolio_properties(raw_db)
    finally:
        raw_db.close()

    session = testing_session_local()
    try:
        user = User(
            email="tester@example.com",
            name="Test User",
            hashed_password="not-used-in-api-tests",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        access_token = create_access_token(user.id)
    finally:
        session.close()

    app = main_module.create_app()
    app.state.test_db_path = str(db_path)

    with TestClient(app) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {access_token}"})
        yield test_client


@pytest.fixture
def db_session(client):
    session = session_module.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def session() -> Iterator:
    """Lightweight in-memory SQLite session loaded with schema.sql.

    Used by ORM smoke tests that don't need the full FastAPI/TestClient stack.
    Each test gets a fresh engine with foreign keys enabled.
    """
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "ai_implementation"
        / "schema.sql"
    )
    schema_sql = schema_path.read_text()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    event.listen(engine, "connect", session_module._set_sqlite_pragmas)

    with engine.begin() as conn:
        # Strip line-comments before splitting so semicolons inside
        # ``-- ... ;`` comments don't fragment a statement. SQLite's
        # native executescript handles this; SQLAlchemy's split-and-
        # execute path doesn't, so we pre-clean here.
        stripped_sql = "\n".join(
            line.split("--", 1)[0] if line.lstrip().startswith("--") else
            line if "--" not in line else line[: line.index("--")]
            for line in schema_sql.splitlines()
        )
        for statement in stripped_sql.split(";"):
            stmt = statement.strip()
            if stmt:
                conn.exec_driver_sql(stmt)

    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    test_session = TestingSessionLocal()
    try:
        yield test_session
    finally:
        test_session.close()
        engine.dispose()


@pytest.fixture
def budget_compare_seed(db_session, budget_history_test_harness):
    hoa = db_session.get(Property, 9)
    assert hoa is not None

    current_line_items = _seed_compare_line_items(
        insurance=186593.4,
        circulation_pump=114458.4,
    )
    baseline_line_items = _seed_compare_line_items(
        insurance=177708.0,
        circulation_pump=109008.0,
    )
    latest_generated_line_items = _seed_compare_line_items(
        insurance=181500.0,
        circulation_pump=111250.0,
    )

    upload = BudgetUpload(
        property_id=hoa.id,
        original_filename="hoa-9-income-statement.xlsx",
        storage_key="hoa/9/uploads/seed-upload/source.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        byte_size=32,
        sha256="a" * 64,
        enrichment_status="completed",
        line_items_json=json.dumps(current_line_items),
        budget_preview_json=json.dumps(_seed_compare_preview(current_line_items)),
        statement_month=8,
        growth_factor=1.25,
        growth_factor_note="stub:1",
        uploaded_by_user_id=1,
        uploaded_by_name="Test User",
        created_at="2026-03-21T09:00:00+00:00",
    )
    db_session.add(upload)
    db_session.flush()

    storage_root = Path(budget_history_test_harness["storage_root"])
    upload_path = storage_root / upload.storage_key
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(b"seed workbook bytes")

    draft = BudgetDraft(
        property_id=hoa.id,
        source_upload_id=upload.id,
        reopened_from_version_id=None,
        status=BUDGET_DRAFT_ACTIVE,
        line_items_json=json.dumps(current_line_items),
        global_note="Seeded compare draft",
        statement_month=8,
        growth_factor=1.25,
        growth_factor_note="stub:1",
        budget_preview_json=json.dumps(_seed_compare_preview(current_line_items)),
        enriched_storage_key=None,
        created_by_user_id=1,
        updated_by_user_id=1,
        actor_name="Test User",
        created_at="2026-03-21T10:00:00+00:00",
        updated_at="2026-03-21T10:00:00+00:00",
    )
    db_session.add(draft)
    db_session.flush()

    db_session.add(
        BudgetNote(
            property_id=hoa.id,
            upload_id=upload.id,
            draft_id=draft.id,
            version_id=None,
            note_scope="line_item",
            line_item_key="55000",
            title="Insurance renewal",
            body="Carrier communicated a likely 5 percent increase.",
            created_by_user_id=1,
            created_by_name="Test User",
            actor_name="Test User",
            created_at="2026-03-21T10:05:00+00:00",
        )
    )

    versions = [
        BudgetVersion(
            property_id=hoa.id,
            source_upload_id=upload.id,
            source_draft_id=draft.id,
            reopened_from_version_id=None,
            storage_key=None,
            output_storage_key=None,
            version_number=1,
            version_code="V1",
            stage=BUDGET_VERSION_STAGE_INTERIM,
            label="Early draft",
            summary_note="First generated version",
            line_items_json=json.dumps(_seed_compare_line_items(insurance=176000.0, circulation_pump=107500.0)),
            budget_preview_json=json.dumps(_seed_compare_preview(baseline_line_items)),
            total_income=0.0,
            total_expense=307500.0,
            net_operating_income=-307500.0,
            growth_factor=1.25,
            growth_factor_note="stub:1",
            statement_month=8,
            fiscal_year_start_month=hoa.fiscal_year_start_month or 1,
            fiscal_year_end_month=hoa.fiscal_year_end_month or 12,
            created_by_user_id=1,
            created_by_name="Test User",
            actor_name="Test User",
            created_at="2026-01-10T09:00:00+00:00",
        ),
        BudgetVersion(
            property_id=hoa.id,
            source_upload_id=upload.id,
            source_draft_id=draft.id,
            reopened_from_version_id=None,
            storage_key=None,
            output_storage_key=None,
            version_number=2,
            version_code="V2",
            stage=BUDGET_VERSION_STAGE_FINAL,
            label="Board approved",
            summary_note="Approved final baseline",
            line_items_json=json.dumps(baseline_line_items),
            budget_preview_json=json.dumps(_seed_compare_preview(baseline_line_items)),
            total_income=0.0,
            total_expense=298716.0,
            net_operating_income=-298716.0,
            growth_factor=1.25,
            growth_factor_note="stub:1",
            statement_month=8,
            fiscal_year_start_month=hoa.fiscal_year_start_month or 1,
            fiscal_year_end_month=hoa.fiscal_year_end_month or 12,
            created_by_user_id=1,
            created_by_name="Test User",
            actor_name="Test User",
            created_at="2026-02-10T09:00:00+00:00",
        ),
        BudgetVersion(
            property_id=hoa.id,
            source_upload_id=upload.id,
            source_draft_id=draft.id,
            reopened_from_version_id=None,
            storage_key=None,
            output_storage_key=None,
            version_number=3,
            version_code="V3",
            stage=BUDGET_VERSION_STAGE_INTERIM,
            label="Latest generated",
            summary_note="Newest interim version",
            line_items_json=json.dumps(latest_generated_line_items),
            budget_preview_json=json.dumps(_seed_compare_preview(latest_generated_line_items)),
            total_income=0.0,
            total_expense=304750.0,
            net_operating_income=-304750.0,
            growth_factor=1.25,
            growth_factor_note="stub:1",
            statement_month=8,
            fiscal_year_start_month=hoa.fiscal_year_start_month or 1,
            fiscal_year_end_month=hoa.fiscal_year_end_month or 12,
            created_by_user_id=1,
            created_by_name="Test User",
            actor_name="Test User",
            created_at="2026-03-10T09:00:00+00:00",
        ),
    ]
    db_session.add_all(versions)
    db_session.commit()

    return {
        "hoa_id": hoa.id,
        "draft_id": draft.id,
        "draft_line_items": current_line_items,
        "final_version_id": versions[1].id,
        "latest_generated_version_id": versions[2].id,
    }


# ── Phase 11 disclosure-package fixtures ─────────────────────────────────────


@pytest.fixture
def disclosure_storage_root(tmp_path, monkeypatch) -> Path:
    """Per-test isolated disclosure-packages root.

    RESEARCH Pitfall 7: production volume path does not exist locally.
    Tests use tmp_path; production reads from settings.BUDGET_STORAGE_ROOT.
    Plan 11-06's filename builder will sanitize hoa_id and fiscal_year before
    joining to BUDGET_STORAGE_ROOT to mitigate T-11-05 (path traversal).
    """
    root = tmp_path / "disclosure-packages"
    root.mkdir()
    monkeypatch.setenv("BUDGET_STORAGE_ROOT", str(tmp_path))
    return root


@pytest.fixture
def qpdf_required():
    """Skip marker for tests that need the qpdf system binary.

    RESEARCH Pitfall 4: qpdf is a system binary, not a pip package. The Docker
    image installs it (Dockerfile apt-get layer); developer machines may or may
    not have it. Tests that exercise the `qpdf --check` finalization step in
    `merge.py` should depend on this fixture so they skip cleanly when qpdf is
    absent rather than failing with FileNotFoundError.
    """
    import shutil
    if shutil.which("qpdf") is None:
        pytest.skip("qpdf binary not installed in test environment")
