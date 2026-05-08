from __future__ import annotations

import importlib.util

from dbkit.schemas.evidence import CollectionPlan
from dbkit.schemas.runtime import NormalizedRequest

INSTALL_HINT = 'pip install -e ".[collection]"'

_MYSQL_TOOLS = frozenset(
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
        "collect_os_service_status",
        "collect_os_cpu_snapshot",
        "collect_os_memory_snapshot",
        "collect_os_disk_snapshot",
    }
)
_MYSQL_LOG_TOOLS = frozenset({"collect_mysql_error_log", "collect_mysql_slow_log"})


def find_missing_collection_dependencies(
    plan: CollectionPlan,
    request: NormalizedRequest,
) -> tuple[str, ...]:
    required: list[str] = []
    for step in plan.steps:
        if step.tool_name in _MYSQL_TOOLS:
            required.append("pymysql")
        if step.tool_name in _SSH_TOOLS:
            required.append("paramiko")
        if step.tool_name in _MYSQL_LOG_TOOLS and request.ssh_target:
            required.append("paramiko")

    missing = [
        module_name
        for module_name in dict.fromkeys(required)
        if not is_module_available(module_name)
    ]
    return tuple(missing)


def is_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None
