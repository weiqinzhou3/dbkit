from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from dbkit.runtime.collection_guardrails import is_mysql_sql_allowed
from dbkit.schemas.evidence import CollectionStep, RawEvidence
from dbkit.schemas.runtime import NormalizedRequest
from dbkit.tools.collectors.common import error_raw_evidence, json_raw_evidence


class MySQLClient(Protocol):
    def execute(self, sql: str) -> list[dict[str, Any]]:
        ...


class PyMySQLClient:
    def __init__(self, request: NormalizedRequest, password: str | None) -> None:
        target = request.target or {}
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("pymysql is required for MySQL live collection") from exc
        self._connection = pymysql.connect(
            host=str(target.get("host") or ""),
            port=int(target.get("port") or 3306),
            user=str(target.get("username") or ""),
            password=password or "",
            connect_timeout=5,
            read_timeout=30,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def execute(self, sql: str) -> list[dict[str, Any]]:
        with self._connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        return [dict(row) for row in rows]


_SERVICE_SQL = {
    "collect_mysql_processlist": "SHOW FULL PROCESSLIST",
    "collect_processlist": "SHOW FULL PROCESSLIST",
    "collect_mysql_runtime_status": "SHOW GLOBAL STATUS",
    "collect_mysql_innodb_status": "SHOW ENGINE INNODB STATUS",
    "collect_innodb_status": "SHOW ENGINE INNODB STATUS",
    "collect_mysql_variables": "SHOW GLOBAL VARIABLES",
}
_METADATA_SQL = (
    "SELECT VERSION()",
    "SELECT @@hostname, @@port, @@datadir, @@log_error, @@slow_query_log_file",
)


def collect_mysql_service(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    raw_root: Path,
    mysql_client: MySQLClient,
    started_at: str,
    completed_at: str,
) -> RawEvidence:
    try:
        if step.tool_name == "collect_mysql_service_metadata":
            results = []
            for sql in _METADATA_SQL:
                _ensure_sql_allowed(sql)
                results.append({"sql": sql, "rows": mysql_client.execute(sql)})
            data: dict[str, Any] = {"queries": results}
        else:
            sql = _SERVICE_SQL[step.tool_name]
            _ensure_sql_allowed(sql)
            data = {"sql": sql, "rows": mysql_client.execute(sql)}
        return json_raw_evidence(
            step=step,
            request=request,
            raw_root=raw_root,
            data=data,
            source={
                "kind": "mysql",
                "path": None,
                "host": (request.target or {}).get("host"),
                "tool_name": step.tool_name,
            },
            started_at=started_at,
            completed_at=completed_at,
        )
    except Exception as exc:
        return error_raw_evidence(
            step=step,
            request=request,
            status="failed",
            started_at=started_at,
            completed_at=completed_at,
            error=str(exc),
            source_kind="mysql",
        )


def collect_mysql_status_metrics(**kwargs: Any) -> RawEvidence:
    step = kwargs["step"]
    metrics_step = CollectionStep(
        step_id=step.step_id,
        evidence_type=step.evidence_type,
        tool_name="collect_mysql_runtime_status",
        target_ref=step.target_ref,
        requires_secret_refs=step.requires_secret_refs,
        requires_approval=step.requires_approval,
        timeout_seconds=step.timeout_seconds,
        purpose=step.purpose,
        source_path=step.source_path,
    )
    kwargs["step"] = metrics_step
    item = collect_mysql_service(**kwargs)
    return _with_original_tool(item, step.tool_name)


def collect_mysql_variable_metrics(**kwargs: Any) -> RawEvidence:
    step = kwargs["step"]
    metrics_step = CollectionStep(
        step_id=step.step_id,
        evidence_type=step.evidence_type,
        tool_name="collect_mysql_variables",
        target_ref=step.target_ref,
        requires_secret_refs=step.requires_secret_refs,
        requires_approval=step.requires_approval,
        timeout_seconds=step.timeout_seconds,
        purpose=step.purpose,
        source_path=step.source_path,
    )
    kwargs["step"] = metrics_step
    item = collect_mysql_service(**kwargs)
    return _with_original_tool(item, step.tool_name)


def collect_mysql_metrics_snapshot(
    *,
    step: CollectionStep,
    request: NormalizedRequest,
    raw_root: Path,
    mysql_client: MySQLClient,
    started_at: str,
    completed_at: str,
) -> RawEvidence:
    try:
        status_sql = "SHOW GLOBAL STATUS"
        variables_sql = "SHOW GLOBAL VARIABLES"
        _ensure_sql_allowed(status_sql)
        _ensure_sql_allowed(variables_sql)
        data = {
            "queries": [
                {"sql": status_sql, "rows": mysql_client.execute(status_sql)},
                {"sql": variables_sql, "rows": mysql_client.execute(variables_sql)},
            ]
        }
        return json_raw_evidence(
            step=step,
            request=request,
            raw_root=raw_root,
            data=data,
            source={
                "kind": "mysql",
                "path": None,
                "host": (request.target or {}).get("host"),
                "tool_name": step.tool_name,
            },
            started_at=started_at,
            completed_at=completed_at,
        )
    except Exception as exc:
        return error_raw_evidence(
            step=step,
            request=request,
            status="failed",
            started_at=started_at,
            completed_at=completed_at,
            error=str(exc),
            source_kind="mysql",
        )


def _ensure_sql_allowed(sql: str) -> None:
    if not is_mysql_sql_allowed(sql):
        raise RuntimeError(f"blocked unsafe SQL: {sql}")


def _with_original_tool(item: RawEvidence, tool_name: str) -> RawEvidence:
    source = dict(item.source)
    source["tool_name"] = tool_name
    return RawEvidence(
        raw_evidence_id=item.raw_evidence_id,
        request_id=item.request_id,
        evidence_type=item.evidence_type,
        source=source,
        collection=item.collection,
        payload=item.payload,
        metadata=item.metadata,
    )
