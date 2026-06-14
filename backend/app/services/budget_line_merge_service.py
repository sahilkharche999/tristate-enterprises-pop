"""GL merge lifecycle for active budget drafts."""

from __future__ import annotations

import json
import sqlite3
import asyncio
from contextlib import contextmanager
from difflib import SequenceMatcher
from typing import Any, Iterable, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from app.ai_implementation.pipeline import llm_client
from app.dre_extraction import wire_schemas as ws
from app.dre_extraction.prompts.gl_merge_suggester import PROMPT_TEXT
from app.dre_extraction.wire_to_domain import to_merge_suggestions
from app.services.assessment_budget_mapping_rule_service import normalize_budget_label


MergeSource = Literal["manual", "gemini_suggestion", "auto_applied"]

_MONEY_FIELDS = (
    "current_period",
    "ytd",
    "annual_budget",
    "projection",
    "variance",
)


class GLIdentity(BaseModel):
    account_code: Optional[str] = None
    label: str
    normalized_label: Optional[str] = None
    line_item_key: Optional[str] = None
    section: str
    category: str
    fund_type: str


class MergeApplication(BaseModel):
    id: int
    merge_id: int
    property_id: int
    budget_draft_id: int
    assessment_setup_id: Optional[int]
    source: str
    status: str
    match_strategy: Optional[str] = None


class CommitMergeResult(BaseModel):
    merge_id: int
    application: MergeApplication
    draft_version: int


class UnmergeResult(BaseModel):
    application: MergeApplication
    draft_version: int


class ListedMerge(BaseModel):
    id: int
    property_id: int
    primary_account_code: Optional[str]
    primary_label: str
    primary_normalized_label: str
    secondary_account_code: Optional[str]
    secondary_label: str
    secondary_normalized_label: str
    status: str
    application_id: Optional[int] = None
    application_status: Optional[str] = None
    source: Optional[str] = None


class MergeSuggestion(BaseModel):
    primary_account_code: Optional[str]
    secondary_account_code: Optional[str]
    primary_label: str
    secondary_label: str
    primary_normalized_label: str
    secondary_normalized_label: str
    confidence: float
    reason: str
    local_only: bool
    wire_schema_sha256: str


@contextmanager
def _savepoint(connection: sqlite3.Connection):
    already_in_transaction = connection.in_transaction
    connection.execute("SAVEPOINT budget_line_merge")
    try:
        yield
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT budget_line_merge")
        connection.execute("RELEASE SAVEPOINT budget_line_merge")
        raise
    else:
        connection.execute("RELEASE SAVEPOINT budget_line_merge")
        if not already_in_transaction:
            connection.commit()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _fetchone_dict(
    connection: sqlite3.Connection,
    sql: str,
    params: Iterable[Any],
) -> Optional[dict[str, Any]]:
    cursor = connection.execute(sql, tuple(params))
    row = cursor.fetchone()
    if row is None:
        return None
    if hasattr(row, "keys"):
        return _row_to_dict(row)
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row))


def _account_code(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _identity_normalized_label(identity: GLIdentity) -> str:
    return normalize_budget_label(identity.normalized_label or identity.label)


def _identity_tuple(identity: GLIdentity) -> tuple[Optional[str], str, Optional[str]]:
    return (
        _account_code(identity.account_code),
        _identity_normalized_label(identity),
        identity.line_item_key,
    )


def _same_identity(primary: GLIdentity, secondary: GLIdentity) -> bool:
    primary_account, primary_label, primary_key = _identity_tuple(primary)
    secondary_account, secondary_label, secondary_key = _identity_tuple(secondary)
    if primary_key and secondary_key and primary_key == secondary_key:
        return True
    return primary_account == secondary_account and primary_label == secondary_label


def _line_account_code(line: dict[str, Any]) -> Optional[str]:
    return _account_code(line.get("account_code"))


def _line_normalized_label(line: dict[str, Any]) -> str:
    return normalize_budget_label(
        str(line.get("normalized_label") or line.get("label") or "")
    )


def _line_matches_identity(line: dict[str, Any], identity: GLIdentity) -> bool:
    line_key = line.get("line_item_key")
    if identity.line_item_key and str(line_key or "") == identity.line_item_key:
        return True
    identity_account = _account_code(identity.account_code)
    if identity_account is not None and _line_account_code(line) == identity_account:
        return True
    return _line_normalized_label(line) == _identity_normalized_label(identity)


def _find_line_index(line_items: list[dict[str, Any]], identity: GLIdentity) -> int:
    matches = [
        index
        for index, line in enumerate(line_items)
        if _line_matches_identity(line, identity)
    ]
    if not matches:
        raise HTTPException(status_code=404, detail=f"Budget line not found: {identity.label}")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=f"Budget line match is ambiguous: {identity.label}")
    return matches[0]


def _is_income(identity: GLIdentity, line: Optional[dict[str, Any]] = None) -> bool:
    parts = [
        identity.section,
        identity.category,
        identity.fund_type,
    ]
    if line is not None:
        parts.extend(
            str(line.get(field) or "")
            for field in ("section", "category", "fund_type")
        )
    normalized = " ".join(parts).lower()
    return "income" in normalized


def _numeric_or_none(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _merge_line_items(
    line_items: list[dict[str, Any]],
    primary: GLIdentity,
    secondary: GLIdentity,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    primary_index = _find_line_index(line_items, primary)
    secondary_index = _find_line_index(line_items, secondary)
    if primary_index == secondary_index:
        raise HTTPException(
            status_code=422,
            detail="Cannot merge a GL with itself; duplicate GL identity",
        )

    primary_line = dict(line_items[primary_index])
    secondary_line = dict(line_items[secondary_index])
    if _is_income(primary, primary_line) != _is_income(secondary, secondary_line):
        raise HTTPException(
            status_code=422,
            detail="Cannot merge income and expense GLs",
        )

    for field in _MONEY_FIELDS:
        primary_value = _numeric_or_none(primary_line.get(field))
        secondary_value = _numeric_or_none(secondary_line.get(field))
        if primary_value is not None or secondary_value is not None:
            primary_line[field] = (primary_value or 0.0) + (secondary_value or 0.0)

    absorbed = list(primary_line.get("merged_gls") or [])
    absorbed.append(
        {
            "account_code": _line_account_code(secondary_line),
            "label": secondary_line.get("label"),
            "line_item_key": secondary_line.get("line_item_key"),
            "normalized_label": _line_normalized_label(secondary_line),
            "contributions": {
                field: secondary_line.get(field)
                for field in _MONEY_FIELDS
                if field in secondary_line
            },
        }
    )
    primary_line["merged_gls"] = absorbed
    primary_line["merged_count"] = len(absorbed)

    merged_items: list[dict[str, Any]] = []
    for index, line in enumerate(line_items):
        if index == secondary_index:
            continue
        merged_items.append(primary_line if index == primary_index else line)
    return merged_items, primary_line, secondary_line


def _load_active_draft(
    *,
    property_id: int,
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    row = _fetchone_dict(
        connection,
        """
        SELECT id, line_items_json, version_int
          FROM budget_drafts
         WHERE property_id = ?
           AND status = 'active'
         ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (property_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Active budget draft not found")
    return row


def _assessment_setup_id(
    *,
    property_id: int,
    connection: sqlite3.Connection,
) -> Optional[int]:
    property_row = connection.execute(
        "SELECT default_assessment_setup_id FROM properties WHERE id = ?",
        (property_id,),
    ).fetchone()
    if property_row is not None and property_row[0] is not None:
        return int(property_row[0])
    setup_row = connection.execute(
        """
        SELECT id
          FROM assessment_setups
         WHERE property_id = ?
           AND status = 'approved'
         ORDER BY id DESC
         LIMIT 1
        """,
        (property_id,),
    ).fetchone()
    return int(setup_row[0]) if setup_row is not None else None


def _check_draft_version(draft_row: dict[str, Any], expected_draft_version: int) -> None:
    actual = int(draft_row["version_int"])
    if actual != expected_draft_version:
        raise HTTPException(
            status_code=412,
            detail=f"Draft version mismatch (expected={expected_draft_version}, actual={actual})",
        )


def _update_draft_json_with_lock(
    *,
    draft_id: int,
    expected_draft_version: int,
    line_items_json: str,
    connection: sqlite3.Connection,
) -> int:
    cursor = connection.execute(
        """
        UPDATE budget_drafts
           SET line_items_json = ?,
               version_int = version_int + 1,
               updated_at = datetime('now')
         WHERE id = ?
           AND version_int = ?
        """,
        (line_items_json, draft_id, expected_draft_version),
    )
    if cursor.rowcount != 1:
        raise HTTPException(status_code=412, detail="Draft version is stale")
    return expected_draft_version + 1


def _select_durable_rule(
    *,
    property_id: int,
    primary: GLIdentity,
    secondary: GLIdentity,
    connection: sqlite3.Connection,
) -> Optional[int]:
    row = connection.execute(
        """
        SELECT id
          FROM budget_line_merges
         WHERE tenant_id = 1
           AND property_id = ?
           AND COALESCE(primary_account_code, '') = ?
           AND primary_normalized_label = ?
           AND COALESCE(secondary_account_code, '') = ?
           AND secondary_normalized_label = ?
           AND status = 'active'
        """,
        (
            property_id,
            _account_code(primary.account_code) or "",
            _identity_normalized_label(primary),
            _account_code(secondary.account_code) or "",
            _identity_normalized_label(secondary),
        ),
    ).fetchone()
    return int(row[0]) if row is not None else None


def _decision_source(source: MergeSource) -> str:
    return "system" if source == "auto_applied" else source


def _get_or_create_durable_rule(
    *,
    property_id: int,
    primary: GLIdentity,
    secondary: GLIdentity,
    source: MergeSource,
    actor: str,
    connection: sqlite3.Connection,
) -> int:
    existing_id = _select_durable_rule(
        property_id=property_id,
        primary=primary,
        secondary=secondary,
        connection=connection,
    )
    if existing_id is not None:
        return existing_id
    cursor = connection.execute(
        """
        INSERT INTO budget_line_merges (
            tenant_id, property_id,
            primary_account_code, primary_label, primary_normalized_label,
            secondary_account_code, secondary_label, secondary_normalized_label,
            status, decision_source, actor
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (
            property_id,
            _account_code(primary.account_code),
            primary.label,
            _identity_normalized_label(primary),
            _account_code(secondary.account_code),
            secondary.label,
            _identity_normalized_label(secondary),
            _decision_source(source),
            actor,
        ),
    )
    return int(cursor.lastrowid)


def _rows_for_query(
    connection: sqlite3.Connection,
    sql: str,
    params: Iterable[Any],
) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, tuple(params))
    rows = cursor.fetchall()
    if not rows:
        return []
    if hasattr(rows[0], "keys"):
        return [_row_to_dict(row) for row in rows]
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def _pool_target_rows(
    *,
    property_id: int,
    assessment_setup_id: int,
    primary: GLIdentity,
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    return _rows_for_query(
        connection,
        """
        SELECT *
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND budget_line_normalized_label = ?
           AND section = ?
           AND category = ?
           AND fund_type = ?
           AND COALESCE(account_code, '') = ?
        """,
        (
            property_id,
            assessment_setup_id,
            _identity_normalized_label(primary),
            primary.section,
            primary.category,
            primary.fund_type,
            _account_code(primary.account_code) or "",
        ),
    )


def _pool_source_rows(
    *,
    property_id: int,
    assessment_setup_id: int,
    secondary: GLIdentity,
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    return _rows_for_query(
        connection,
        """
        SELECT *
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND budget_line_normalized_label = ?
           AND section = ?
           AND category = ?
           AND fund_type = ?
           AND COALESCE(account_code, '') = ?
           AND active = 1
        """,
        (
            property_id,
            assessment_setup_id,
            _identity_normalized_label(secondary),
            secondary.section,
            secondary.category,
            secondary.fund_type,
            _account_code(secondary.account_code) or "",
        ),
    )


def _compatible_pool_target(source: dict[str, Any], target: dict[str, Any]) -> bool:
    return (
        source["pool_key"] == target["pool_key"]
        and int(source["active"]) == 1
        and int(target["active"]) == 1
        and target["approval_status"] in ("approved", "auto_approved")
    )


def _apply_pool_mapping_side_effects(
    *,
    property_id: int,
    assessment_setup_id: Optional[int],
    primary: GLIdentity,
    secondary: GLIdentity,
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    if assessment_setup_id is None:
        return []
    actions: list[dict[str, Any]] = []
    sources = _pool_source_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        secondary=secondary,
        connection=connection,
    )
    if not sources:
        return actions
    targets = _pool_target_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        primary=primary,
        connection=connection,
    )
    for source in sources:
        if targets:
            compatible = [
                target
                for target in targets
                if _compatible_pool_target(source, target)
            ]
            if len(compatible) != 1 or len(targets) != 1:
                raise HTTPException(
                    status_code=409,
                    detail="Pool mapping conflict for merged GL",
                )
            before = dict(source)
            connection.execute(
                """
                UPDATE budget_line_pool_mappings
                   SET active = 0,
                       review_state = 'disabled'
                 WHERE id = ?
                """,
                (source["id"],),
            )
            after = _fetchone_dict(
                connection,
                "SELECT * FROM budget_line_pool_mappings WHERE id = ?",
                (source["id"],),
            )
            actions.append({"action": "deactivate_source", "before": before, "after": after})
            target = compatible[0]
            actions.append({"action": "keep_target", "before": target, "after": target})
            continue

        before = dict(source)
        connection.execute(
            """
            UPDATE budget_line_pool_mappings
               SET budget_line_normalized_label = ?,
                   section = ?,
                   category = ?,
                   fund_type = ?,
                   account_code = ?
             WHERE id = ?
            """,
            (
                _identity_normalized_label(primary),
                primary.section,
                primary.category,
                primary.fund_type,
                _account_code(primary.account_code),
                source["id"],
            ),
        )
        after = _fetchone_dict(
            connection,
            "SELECT * FROM budget_line_pool_mappings WHERE id = ?",
            (source["id"],),
        )
        actions.append({"action": "move_source", "before": before, "after": after})
    return actions


def _alias_source_rows(
    *,
    property_id: int,
    assessment_setup_id: int,
    secondary: GLIdentity,
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    secondary_code = _account_code(secondary.account_code)
    return _rows_for_query(
        connection,
        """
        SELECT *
          FROM assessment_mapping_aliases
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND normalized_budget_label = ?
           AND active = 1
           AND (
                account_code IS NULL
                OR COALESCE(account_code, '') = ?
           )
        """,
        (
            property_id,
            assessment_setup_id,
            _identity_normalized_label(secondary),
            secondary_code or "",
        ),
    )


def _alias_target_rows(
    *,
    source: dict[str, Any],
    primary: GLIdentity,
    target_account_code: Optional[str],
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    return _rows_for_query(
        connection,
        """
        SELECT *
          FROM assessment_mapping_aliases
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND normalized_dre_label = ?
           AND normalized_budget_label = ?
           AND COALESCE(account_code, '') = ?
        """,
        (
            source["property_id"],
            source["assessment_setup_id"],
            source["normalized_dre_label"],
            _identity_normalized_label(primary),
            target_account_code or "",
        ),
    )


def _compatible_alias_target(source: dict[str, Any], target: dict[str, Any]) -> bool:
    return (
        source["pool_key"] == target["pool_key"]
        and int(source["active"]) == 1
        and int(target["active"]) == 1
        and source["approval_status"] == "approved"
        and target["approval_status"] == "approved"
    )


def _target_alias_account_code(
    *,
    source: dict[str, Any],
    primary: GLIdentity,
    secondary: GLIdentity,
) -> Optional[str]:
    source_code = _account_code(source.get("account_code"))
    if source_code is None:
        return None
    if source_code == _account_code(secondary.account_code):
        return _account_code(primary.account_code)
    return source_code


def _apply_alias_side_effects(
    *,
    property_id: int,
    assessment_setup_id: Optional[int],
    primary: GLIdentity,
    secondary: GLIdentity,
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    if assessment_setup_id is None:
        return []
    actions: list[dict[str, Any]] = []
    sources = _alias_source_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        secondary=secondary,
        connection=connection,
    )
    for source in sources:
        target_account_code = _target_alias_account_code(
            source=source,
            primary=primary,
            secondary=secondary,
        )
        targets = _alias_target_rows(
            source=source,
            primary=primary,
            target_account_code=target_account_code,
            connection=connection,
        )
        if targets:
            compatible = [
                target
                for target in targets
                if _compatible_alias_target(source, target)
            ]
            if len(compatible) != 1 or len(targets) != 1:
                raise HTTPException(
                    status_code=409,
                    detail="Assessment mapping alias conflict for merged GL",
                )
            before = dict(source)
            connection.execute(
                """
                UPDATE assessment_mapping_aliases
                   SET active = 0,
                       updated_at = datetime('now')
                 WHERE id = ?
                """,
                (source["id"],),
            )
            after = _fetchone_dict(
                connection,
                "SELECT * FROM assessment_mapping_aliases WHERE id = ?",
                (source["id"],),
            )
            actions.append({"action": "deactivate_source", "before": before, "after": after})
            target = compatible[0]
            actions.append({"action": "keep_target", "before": target, "after": target})
            continue

        before = dict(source)
        connection.execute(
            """
            UPDATE assessment_mapping_aliases
               SET budget_label = ?,
                   normalized_budget_label = ?,
                   account_code = ?,
                   updated_at = datetime('now')
             WHERE id = ?
            """,
            (
                primary.label,
                _identity_normalized_label(primary),
                target_account_code,
                source["id"],
            ),
        )
        after = _fetchone_dict(
            connection,
            "SELECT * FROM assessment_mapping_aliases WHERE id = ?",
            (source["id"],),
        )
        actions.append({"action": "move_source", "before": before, "after": after})
    return actions


def _apply_side_effects(
    *,
    property_id: int,
    assessment_setup_id: Optional[int],
    primary: GLIdentity,
    secondary: GLIdentity,
    connection: sqlite3.Connection,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "pool_mappings": _apply_pool_mapping_side_effects(
            property_id=property_id,
            assessment_setup_id=assessment_setup_id,
            primary=primary,
            secondary=secondary,
            connection=connection,
        ),
        "aliases": _apply_alias_side_effects(
            property_id=property_id,
            assessment_setup_id=assessment_setup_id,
            primary=primary,
            secondary=secondary,
            connection=connection,
        ),
    }


def _restore_row(
    *,
    table_name: str,
    row: dict[str, Any],
    connection: sqlite3.Connection,
) -> None:
    columns = [column for column in row.keys() if column != "id"]
    assignments = ", ".join(f"{column} = ?" for column in columns)
    values = [row[column] for column in columns]
    cursor = connection.execute(
        f"UPDATE {table_name} SET {assignments} WHERE id = ?",
        (*values, row["id"]),
    )
    if cursor.rowcount:
        return
    insert_columns = list(row.keys())
    placeholders = ", ".join("?" for _ in insert_columns)
    connection.execute(
        f"INSERT INTO {table_name} ({', '.join(insert_columns)}) VALUES ({placeholders})",
        [row[column] for column in insert_columns],
    )


def _restore_side_effects(
    *,
    snapshot: dict[str, Any],
    connection: sqlite3.Connection,
) -> None:
    for action in snapshot.get("pool_mappings", []):
        _restore_row(
            table_name="budget_line_pool_mappings",
            row=action["before"],
            connection=connection,
        )
    for action in snapshot.get("aliases", []):
        _restore_row(
            table_name="assessment_mapping_aliases",
            row=action["before"],
            connection=connection,
        )


def _insert_audit_event(
    *,
    property_id: int,
    draft_id: Optional[int],
    event_type: str,
    summary: str,
    actor: str,
    payload: dict[str, Any],
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        INSERT INTO budget_audit_events (
            property_id, draft_id, event_type, summary, actor_name, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            property_id,
            draft_id,
            event_type,
            summary,
            actor,
            _json_dumps(payload),
        ),
    )


def _application_from_row(row: sqlite3.Row) -> MergeApplication:
    return MergeApplication(
        id=int(row["id"]),
        merge_id=int(row["merge_id"]),
        property_id=int(row["property_id"]),
        budget_draft_id=int(row["budget_draft_id"]),
        assessment_setup_id=(
            int(row["assessment_setup_id"])
            if row["assessment_setup_id"] is not None
            else None
        ),
        source=str(row["source"]),
        status=str(row["status"]),
        match_strategy=row["match_strategy"],
    )


def _load_application(
    *,
    application_id: int,
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    row = _fetchone_dict(
        connection,
        "SELECT * FROM budget_line_merge_applications WHERE id = ?",
        (application_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Merge application not found")
    return row


def commit_merge(
    *,
    property_id: int,
    primary: GLIdentity,
    secondary: GLIdentity,
    source: MergeSource,
    actor: str,
    expected_draft_version: int,
    db_conn: sqlite3.Connection,
    _merge_id: Optional[int] = None,
    _match_strategy: str = "manual",
    _audit_event_type: str = "merge_committed",
) -> CommitMergeResult:
    if _same_identity(primary, secondary):
        raise HTTPException(
            status_code=422,
            detail="Cannot merge a GL with itself; duplicate GL identity",
        )

    with _savepoint(db_conn):
        draft_row = _load_active_draft(property_id=property_id, connection=db_conn)
        _check_draft_version(draft_row, expected_draft_version)
        before_line_items_json = draft_row["line_items_json"]
        line_items = _json_loads(before_line_items_json, [])
        if not isinstance(line_items, list):
            raise HTTPException(status_code=409, detail="Active draft line_items_json is invalid")

        merged_items, primary_line, secondary_line = _merge_line_items(
            line_items,
            primary,
            secondary,
        )
        after_line_items_json = _json_dumps(merged_items)
        assessment_setup_id = _assessment_setup_id(
            property_id=property_id,
            connection=db_conn,
        )

        side_effect_snapshot = _apply_side_effects(
            property_id=property_id,
            assessment_setup_id=assessment_setup_id,
            primary=primary,
            secondary=secondary,
            connection=db_conn,
        )
        merge_id = _merge_id
        if merge_id is None:
            merge_id = _get_or_create_durable_rule(
                property_id=property_id,
                primary=primary,
                secondary=secondary,
                source=source,
                actor=actor,
                connection=db_conn,
            )
        before_snapshot = {
            "line_items_json": before_line_items_json,
            "primary_line": primary_line,
            "secondary_line": secondary_line,
        }
        after_snapshot = {
            "line_items_json": after_line_items_json,
            "line_items": merged_items,
        }
        cursor = db_conn.execute(
            """
            INSERT INTO budget_line_merge_applications (
                tenant_id, merge_id, property_id, budget_draft_id,
                assessment_setup_id, source, status, match_strategy,
                before_snapshot_json, after_snapshot_json,
                side_effect_snapshot_json, actor
            )
            VALUES (1, ?, ?, ?, ?, ?, 'applied', ?, ?, ?, ?, ?)
            """,
            (
                merge_id,
                property_id,
                int(draft_row["id"]),
                assessment_setup_id,
                source,
                _match_strategy,
                _json_dumps(before_snapshot),
                _json_dumps(after_snapshot),
                _json_dumps(side_effect_snapshot),
                actor,
            ),
        )
        application_id = int(cursor.lastrowid)
        new_version = _update_draft_json_with_lock(
            draft_id=int(draft_row["id"]),
            expected_draft_version=expected_draft_version,
            line_items_json=after_line_items_json,
            connection=db_conn,
        )
        _insert_audit_event(
            property_id=property_id,
            draft_id=int(draft_row["id"]),
            event_type=_audit_event_type,
            summary=f"Merged {secondary.label} into {primary.label}",
            actor=actor,
            payload={
                "merge_id": merge_id,
                "application_id": application_id,
                "source": source,
                "before": before_snapshot,
                "after": after_snapshot,
                "side_effects": side_effect_snapshot,
            },
            connection=db_conn,
        )
        application_row = _load_application(
            application_id=application_id,
            connection=db_conn,
        )
        return CommitMergeResult(
            merge_id=merge_id,
            application=_application_from_row(application_row),
            draft_version=new_version,
        )


def unmerge_merge(
    *,
    application_id: int,
    actor: str,
    expected_draft_version: int,
    db_conn: sqlite3.Connection,
) -> UnmergeResult:
    with _savepoint(db_conn):
        application_row = _load_application(
            application_id=application_id,
            connection=db_conn,
        )
        if application_row["status"] == "finalized":
            raise HTTPException(
                status_code=409,
                detail="merges are immutable after finalization",
            )
        if application_row["status"] != "applied":
            raise HTTPException(status_code=409, detail="Merge application is not applied")

        draft_row = _fetchone_dict(
            db_conn,
            "SELECT id, version_int FROM budget_drafts WHERE id = ?",
            (application_row["budget_draft_id"],),
        )
        if draft_row is None:
            raise HTTPException(status_code=404, detail="Budget draft not found")
        _check_draft_version(draft_row, expected_draft_version)

        before_snapshot = _json_loads(application_row["before_snapshot_json"], {})
        side_effect_snapshot = _json_loads(application_row["side_effect_snapshot_json"], {})
        restored_line_items_json = before_snapshot.get("line_items_json")
        if not isinstance(restored_line_items_json, str):
            raise HTTPException(status_code=409, detail="Merge snapshot is invalid")

        _restore_side_effects(snapshot=side_effect_snapshot, connection=db_conn)
        new_version = _update_draft_json_with_lock(
            draft_id=int(draft_row["id"]),
            expected_draft_version=expected_draft_version,
            line_items_json=restored_line_items_json,
            connection=db_conn,
        )
        db_conn.execute(
            """
            UPDATE budget_line_merge_applications
               SET status = 'unmerged',
                   unmerged_at = datetime('now')
             WHERE id = ?
            """,
            (application_id,),
        )
        _insert_audit_event(
            property_id=int(application_row["property_id"]),
            draft_id=int(draft_row["id"]),
            event_type="merge_unmerged",
            summary="Un-merged budget GL application",
            actor=actor,
            payload={
                "merge_id": int(application_row["merge_id"]),
                "application_id": application_id,
            },
            connection=db_conn,
        )
        refreshed = _load_application(application_id=application_id, connection=db_conn)
        return UnmergeResult(
            application=_application_from_row(refreshed),
            draft_version=new_version,
        )


def _single_match_by_account(
    line_items: list[dict[str, Any]],
    account_code: Optional[str],
) -> Optional[dict[str, Any]]:
    if account_code is None:
        return None
    matches = [
        line
        for line in line_items
        if _line_account_code(line) == account_code
    ]
    return matches[0] if len(matches) == 1 else None


def _matches_by_normalized_label(
    line_items: list[dict[str, Any]],
    normalized_label: str,
) -> list[dict[str, Any]]:
    return [
        line
        for line in line_items
        if _line_normalized_label(line) == normalized_label
    ]


def _identity_from_line(line: dict[str, Any], fallback_label: str) -> GLIdentity:
    return GLIdentity(
        account_code=_line_account_code(line),
        label=str(line.get("label") or fallback_label),
        normalized_label=_line_normalized_label(line),
        line_item_key=(
            str(line.get("line_item_key"))
            if line.get("line_item_key") not in (None, "")
            else None
        ),
        section=str(line.get("section") or ""),
        category=str(line.get("category") or ""),
        fund_type=str(line.get("fund_type") or ""),
    )


def _match_auto_apply_rule(
    *,
    rule: sqlite3.Row,
    line_items: list[dict[str, Any]],
) -> tuple[Optional[GLIdentity], Optional[GLIdentity], Optional[str], Optional[str]]:
    primary_account = _account_code(rule["primary_account_code"])
    secondary_account = _account_code(rule["secondary_account_code"])
    primary_line = _single_match_by_account(line_items, primary_account)
    secondary_line = _single_match_by_account(line_items, secondary_account)
    if primary_line is not None and secondary_line is not None:
        return (
            _identity_from_line(primary_line, rule["primary_label"]),
            _identity_from_line(secondary_line, rule["secondary_label"]),
            "account_code",
            None,
        )

    primary_matches = _matches_by_normalized_label(
        line_items,
        rule["primary_normalized_label"],
    )
    secondary_matches = _matches_by_normalized_label(
        line_items,
        rule["secondary_normalized_label"],
    )
    if len(primary_matches) == 1 and len(secondary_matches) == 1:
        return (
            _identity_from_line(primary_matches[0], rule["primary_label"]),
            _identity_from_line(secondary_matches[0], rule["secondary_label"]),
            "normalized_label",
            None,
        )
    if len(primary_matches) > 1 or len(secondary_matches) > 1:
        return None, None, None, "ambiguous normalized-label match"
    return None, None, None, "no matching GL pair"


def auto_apply_merges_on_upload(
    *,
    property_id: int,
    budget_draft_id: int,
    new_draft_line_items: list[dict[str, Any]],
    db_conn: sqlite3.Connection,
) -> int:
    rules = _rows_for_query(
        db_conn,
        """
        SELECT *
          FROM budget_line_merges
         WHERE tenant_id = 1
           AND property_id = ?
           AND status = 'active'
        ORDER BY id
        """,
        (property_id,),
    )
    applied = 0
    for rule in rules:
        draft_row = _fetchone_dict(
            db_conn,
            """
            SELECT id, line_items_json, version_int
              FROM budget_drafts
             WHERE id = ?
               AND property_id = ?
               AND status = 'active'
            """,
            (budget_draft_id, property_id),
        )
        if draft_row is None:
            raise HTTPException(status_code=404, detail="Active budget draft not found")
        current_items = _json_loads(draft_row["line_items_json"], new_draft_line_items)
        if not isinstance(current_items, list):
            current_items = new_draft_line_items

        primary, secondary, match_strategy, skip_reason = _match_auto_apply_rule(
            rule=rule,
            line_items=current_items,
        )
        if primary is None or secondary is None or match_strategy is None:
            with _savepoint(db_conn):
                _insert_audit_event(
                    property_id=property_id,
                    draft_id=budget_draft_id,
                    event_type="merge_auto_apply_skipped",
                    summary="Skipped automatic GL merge",
                    actor="system",
                    payload={
                        "merge_id": int(rule["id"]),
                        "reason": skip_reason or "unknown",
                    },
                    connection=db_conn,
                )
            continue

        commit_merge(
            property_id=property_id,
            primary=primary,
            secondary=secondary,
            source="auto_applied",
            actor="system",
            expected_draft_version=int(draft_row["version_int"]),
            db_conn=db_conn,
            _merge_id=int(rule["id"]),
            _match_strategy=match_strategy,
            _audit_event_type="merge_auto_applied",
        )
        applied += 1
    return applied


_GENERIC_MERGE_TOKENS = {
    "service",
    "services",
    "maintenance",
    "maint",
    "repair",
    "repairs",
    "expense",
    "expenses",
    "general",
}


def _merge_tokens(label: str) -> set[str]:
    tokens: set[str] = set()
    for token in normalize_budget_label(label).split():
        if not token or token in _GENERIC_MERGE_TOKENS:
            continue
        tokens.add(token)
        if token.endswith("s") and len(token) > 3:
            tokens.add(token[:-1])
    return tokens


def _same_merge_bucket(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        str(left.get("section") or "") == str(right.get("section") or "")
        and str(left.get("category") or "") == str(right.get("category") or "")
        and _is_income(_identity_from_line(left, str(left.get("label") or "")), left)
        == _is_income(_identity_from_line(right, str(right.get("label") or "")), right)
    )


def _local_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_label = str(left.get("label") or "")
    right_label = str(right.get("label") or "")
    left_tokens = _merge_tokens(left_label)
    right_tokens = _merge_tokens(right_label)
    token_score = 0.0
    if left_tokens and right_tokens:
        token_score = len(left_tokens & right_tokens) / max(
            1,
            min(len(left_tokens), len(right_tokens)),
        )
    text_score = SequenceMatcher(
        None,
        normalize_budget_label(left_label),
        normalize_budget_label(right_label),
    ).ratio()
    return max(token_score, text_score)


def _local_merge_shortlist(line_items: list[dict[str, Any]]) -> list[MergeSuggestion]:
    suggestions: list[MergeSuggestion] = []
    for left_index, left in enumerate(line_items):
        if not isinstance(left, dict):
            continue
        for right in line_items[left_index + 1:]:
            if not isinstance(right, dict) or not _same_merge_bucket(left, right):
                continue
            score = _local_similarity(left, right)
            if score < 0.45:
                continue
            suggestions.append(
                MergeSuggestion(
                    primary_account_code=_line_account_code(left),
                    secondary_account_code=_line_account_code(right),
                    primary_label=str(left.get("label") or ""),
                    secondary_label=str(right.get("label") or ""),
                    primary_normalized_label=_line_normalized_label(left),
                    secondary_normalized_label=_line_normalized_label(right),
                    confidence=round(min(0.85, 0.55 + (score * 0.3)), 4),
                    reason="Local-only suggestion from label similarity and matching budget bucket.",
                    local_only=True,
                    wire_schema_sha256=ws.WIRE_MERGE_SUGGESTION_SCHEMA_SHA256,
                )
            )
    suggestions.sort(key=lambda item: item.confidence, reverse=True)
    return suggestions[:20]


def _lines_by_account_code(line_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in line_items:
        code = _line_account_code(line)
        if code is not None:
            out[code] = line
    return out


def _gemini_candidate_payload(candidates: list[MergeSuggestion]) -> list[dict[str, Any]]:
    return [
        {
            "primary": {
                "account_code": item.primary_account_code,
                "label": item.primary_label,
                "normalized_label": item.primary_normalized_label,
            },
            "secondary": {
                "account_code": item.secondary_account_code,
                "label": item.secondary_label,
                "normalized_label": item.secondary_normalized_label,
            },
            "local_confidence": item.confidence,
        }
        for item in candidates
    ]


def _suggestions_from_wire(
    *,
    wire: ws.WireMergeSuggestionList,
    line_items: list[dict[str, Any]],
) -> list[MergeSuggestion]:
    converted = to_merge_suggestions(
        wire,
        lines_by_account_code=_lines_by_account_code(line_items),
    )
    return [
        MergeSuggestion(
            primary_account_code=item["primary_account_code"],
            secondary_account_code=item["secondary_account_code"],
            primary_label=item["primary_label"],
            secondary_label=item["secondary_label"],
            primary_normalized_label=item["primary_normalized_label"],
            secondary_normalized_label=item["secondary_normalized_label"],
            confidence=float(item["confidence"]),
            reason=item["reason"],
            local_only=False,
            wire_schema_sha256=ws.WIRE_MERGE_SUGGESTION_SCHEMA_SHA256,
        )
        for item in converted
    ]


def suggest_merges(
    *,
    property_id: int,
    db_conn: sqlite3.Connection,
) -> list[MergeSuggestion]:
    draft_row = _load_active_draft(property_id=property_id, connection=db_conn)
    line_items = _json_loads(draft_row["line_items_json"], [])
    if not isinstance(line_items, list):
        return []
    candidates = _local_merge_shortlist(line_items)
    if not candidates:
        return []

    messages = [
        {"role": "system", "content": PROMPT_TEXT},
        {
            "role": "user",
            "content": _json_dumps(
                {
                    "candidate_pairs": _gemini_candidate_payload(candidates),
                }
            ),
        },
    ]
    try:
        wire_result = asyncio.run(
            llm_client.call_llm(
                messages,
                ws.WireMergeSuggestionList,
                temperature=0.0,
                timeout=30.0,
            )
        )
    except Exception:
        return candidates
    if wire_result is None:
        return candidates
    suggestions = _suggestions_from_wire(wire=wire_result, line_items=line_items)
    return suggestions or candidates


def list_merges(
    *,
    property_id: int,
    status: Optional[str] = None,
    db_conn: sqlite3.Connection,
) -> list[ListedMerge]:
    params: list[Any] = [property_id]
    status_filter = ""
    if status is not None:
        status_filter = "AND m.status = ?"
        params.append(status)
    rows = _rows_for_query(
        db_conn,
        f"""
        SELECT m.id, m.property_id, m.primary_account_code, m.primary_label,
               m.primary_normalized_label, m.secondary_account_code,
               m.secondary_label, m.secondary_normalized_label, m.status,
               a.id AS application_id, a.status AS application_status,
               a.source AS source
          FROM budget_line_merges AS m
          LEFT JOIN budget_line_merge_applications AS a
            ON a.merge_id = m.id
           AND a.id = (
                SELECT MAX(id)
                  FROM budget_line_merge_applications
                 WHERE merge_id = m.id
           )
         WHERE m.property_id = ?
           {status_filter}
         ORDER BY m.id
        """,
        params,
    )
    return [
        ListedMerge(
            id=int(row["id"]),
            property_id=int(row["property_id"]),
            primary_account_code=row["primary_account_code"],
            primary_label=row["primary_label"],
            primary_normalized_label=row["primary_normalized_label"],
            secondary_account_code=row["secondary_account_code"],
            secondary_label=row["secondary_label"],
            secondary_normalized_label=row["secondary_normalized_label"],
            status=row["status"],
            application_id=(
                int(row["application_id"])
                if row["application_id"] is not None
                else None
            ),
            application_status=row["application_status"],
            source=row["source"],
        )
        for row in rows
    ]


def finalize_applied_merges(
    *,
    property_id: int,
    budget_draft_id: int,
    db_conn: sqlite3.Connection,
) -> int:
    with _savepoint(db_conn):
        rows = _rows_for_query(
            db_conn,
            """
            SELECT *
              FROM budget_line_merge_applications
             WHERE property_id = ?
               AND budget_draft_id = ?
               AND status = 'applied'
            """,
            (property_id, budget_draft_id),
        )
        for row in rows:
            db_conn.execute(
                """
                UPDATE budget_line_merge_applications
                   SET status = 'finalized',
                       finalized_at = datetime('now')
                 WHERE id = ?
                """,
                (row["id"],),
            )
            _insert_audit_event(
                property_id=property_id,
                draft_id=budget_draft_id,
                event_type="merge_application_finalized",
                summary="Finalized budget GL merge application",
                actor="system",
                payload={
                    "merge_id": int(row["merge_id"]),
                    "application_id": int(row["id"]),
                },
                connection=db_conn,
            )
        return len(rows)


__all__ = [
    "CommitMergeResult",
    "GLIdentity",
    "ListedMerge",
    "MergeSuggestion",
    "MergeApplication",
    "UnmergeResult",
    "auto_apply_merges_on_upload",
    "commit_merge",
    "finalize_applied_merges",
    "list_merges",
    "suggest_merges",
    "unmerge_merge",
]
