from __future__ import annotations

from dbkit.schemas.evidence import CollectionGuardrailsResult, CollectionPlan
from dbkit.schemas.runtime import NormalizedRequest


_PROVIDED_EVIDENCE_TOOLS = frozenset(
    {"read_provided_evidence_file", "read_provided_evidence_directory"}
)
_MYSQL_LOGIN_TOOLS = frozenset(
    {
        "collect_mysql_processlist",
        "collect_processlist",
        "collect_mysql_runtime_status",
        "collect_mysql_innodb_status",
        "collect_innodb_status",
        "collect_mysql_variables",
        "collect_mysql_service_metadata",
        "discover_mysql_log_paths",
        "collect_mysql_error_log",
        "collect_mysql_slow_log",
        "collect_mysql_metrics_snapshot",
        "collect_metrics_snapshot",
        "collect_mysql_status_metrics",
        "collect_mysql_variable_metrics",
    }
)
_SSH_TOOLS = frozenset(
    {
        "read_remote_file",
        "collect_mysql_error_log",
        "collect_mysql_slow_log",
        "collect_os_service_status",
        "collect_os_cpu_snapshot",
        "collect_os_memory_snapshot",
        "collect_os_disk_snapshot",
    }
)
_LIVE_COLLECTION_TOOLS = frozenset(
    {
        *_MYSQL_LOGIN_TOOLS,
        *_SSH_TOOLS,
    }
)
_ALL_TOOLS = _PROVIDED_EVIDENCE_TOOLS | _LIVE_COLLECTION_TOOLS

_ALLOWED_SQL = frozenset(
    {
        "SHOW FULL PROCESSLIST",
        "SHOW GLOBAL STATUS",
        "SHOW GLOBAL VARIABLES",
        "SHOW ENGINE INNODB STATUS",
        "SELECT VERSION()",
        "SELECT @@hostname, @@port, @@datadir, @@log_error, @@slow_query_log_file",
        "SHOW GLOBAL VARIABLES LIKE 'log_error'",
        "SHOW GLOBAL VARIABLES LIKE 'slow_query_log_file'",
        "SHOW GLOBAL VARIABLES LIKE 'slow_query_log'",
        "SHOW GLOBAL VARIABLES LIKE 'log_output'",
        "SHOW GLOBAL VARIABLES LIKE 'datadir'",
        "SHOW GLOBAL VARIABLES LIKE 'log_timestamps'",
        "SHOW GLOBAL VARIABLES LIKE 'time_zone'",
        "SHOW GLOBAL VARIABLES LIKE 'system_time_zone'",
        "SELECT @@global.time_zone, @@system_time_zone",
    }
)
_ALLOWED_SSH_EXACT = frozenset(
    {
        "uptime",
        "top -b -n 1 | head -50",
        "vmstat 1 3",
        "free -m",
        "df -h",
        "systemctl status mysqld --no-pager",
        "systemctl status mysql --no-pager",
        "ps -ef | grep -E 'mysqld|mysql' | grep -v grep",
    }
)


class CollectionGuardrails:
    def validate(
        self,
        plan: CollectionPlan,
        request: NormalizedRequest,
    ) -> CollectionGuardrailsResult:
        issues: list[str] = []
        policy = request.collection_policy or {}

        for step in plan.steps:
            if step.tool_name not in _ALL_TOOLS:
                issues.append(f"collector tool does not exist: {step.tool_name}")
                continue

            if request.input_mode == "provided_evidence" and step.tool_name not in _PROVIDED_EVIDENCE_TOOLS:
                issues.append(
                    f"tool not allowed for provided_evidence input_mode: {step.tool_name}"
                )

            if step.tool_name in _PROVIDED_EVIDENCE_TOOLS:
                if not step.source_path:
                    issues.append(f"source_path is required for {step.tool_name}")
                elif not step.source_path.startswith("/workspace/"):
                    issues.append(
                        f"provided evidence path outside workspace: {step.source_path}"
                    )

            if step.tool_name in _LIVE_COLLECTION_TOOLS:
                if not policy.get("allow_live_collection"):
                    issues.append(
                        f"collection_policy does not permit live collection: {step.tool_name}"
                    )
                if step.tool_name in _MYSQL_LOGIN_TOOLS and not policy.get("allow_mysql_login"):
                    issues.append(
                        f"collection_policy does not permit MySQL login: {step.tool_name}"
                    )
                if step.tool_name in _SSH_TOOLS and not policy.get("allow_ssh"):
                    issues.append(
                        f"collection_policy does not permit SSH collection: {step.tool_name}"
                    )
                if step.tool_name in _MYSQL_LOGIN_TOOLS:
                    target = request.target or {}
                    if not target.get("host"):
                        issues.append("target.host is required for live collection")
                    if not target.get("username"):
                        issues.append("target.username is required for live collection")
                if step.tool_name in _SSH_TOOLS:
                    ssh_target = request.ssh_target or {}
                    if not ssh_target.get("host"):
                        issues.append("ssh_target.host is required for SSH collection")
                    if not ssh_target.get("username"):
                        issues.append("ssh_target.username is required for SSH collection")
                for secret_ref in step.requires_secret_refs:
                    if not str(secret_ref).startswith("<SECRET_REF:"):
                        issues.append(
                            f"invalid secret ref for collection step {step.step_id}"
                        )

            if step.timeout_seconds <= 0:
                issues.append(f"timeout_seconds must be positive for {step.step_id}")

        return CollectionGuardrailsResult(
            passed=not issues,
            blocking_issues=tuple(dict.fromkeys(issues)),
        )


def is_mysql_sql_allowed(sql: str) -> bool:
    normalized = " ".join(sql.strip().rstrip(";").split())
    return normalized in _ALLOWED_SQL


def is_ssh_command_allowed(command: str) -> bool:
    normalized = " ".join(command.strip().split())
    if normalized in _ALLOWED_SSH_EXACT:
        return True
    if normalized.startswith("tail -n ") and " -- " in normalized:
        return _valid_tail_command(normalized, flag="-n")
    if normalized.startswith("tail -c ") and " -- " in normalized:
        return _valid_tail_command(normalized, flag="-c")
    if normalized.startswith("du -sh "):
        return "--" not in normalized and not any(token in normalized for token in (";", "&", "|", "`", "$("))
    return False


def _valid_tail_command(command: str, *, flag: str) -> bool:
    parts = command.split(" -- ", 1)
    prefix, path = parts[0], parts[1]
    count = prefix.removeprefix(f"tail {flag} ").strip()
    if not count.isdigit() or int(count) <= 0:
        return False
    if not path.startswith("/"):
        return False
    return not any(token in path for token in ("..", ";", "&", "|", "`", "$("))
