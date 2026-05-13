from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config") / "config.yaml"


class ProviderKind(str, Enum):
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True)
class ModelConfig:
    provider_kind: ProviderKind
    model_name: str
    base_url: str
    api_key: str
    temperature: float = 0.0
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] | None = None


@dataclass(frozen=True)
class RuntimeConfig:
    artifact_dir: Path = Path(".dbkit") / "artifacts"
    invoke_llm: bool = True
    interactive: bool = False
    timezone: str = "Asia/Shanghai"
    locale: str = "zh-CN"
    repo_dir: Path = Path(".")
    workspace_dir: Path = Path(".")
    skills_dir: Path = Path("skills")
    agents_dir: Path = Path("agents")
    allowed_workspace_root: str = "/workspace/"
    max_discovered_files: int = 100
    max_evidence_file_size_bytes: int = 50_000_000
    blocked_paths: tuple[str, ...] = ()
    phase04_max_prompt_chars: int = 30_000
    phase04_findings_generation_timeout_seconds: int = 120
    phase04_validation_timeout_seconds: int = 60
    phase04_max_findings_generation_retries: int = 1
    phase04_max_validation_retries: int = 1
    phase04_max_agent_iterations: int = 6
    phase04_max_findings: int = 5
    phase04_per_finding_validation_timeout_seconds: int = 15
    phase04_semantic_validation_enabled: bool = True


@dataclass(frozen=True)
class AgentConfig:
    tool_calling: bool = True
    tool_calling_thinking_type: str | None = "disabled"


@dataclass(frozen=True)
class MySQLCollectionConfig:
    connect_timeout_seconds: int = 5
    read_timeout_seconds: int = 30
    write_timeout_seconds: int = 30


@dataclass(frozen=True)
class SSHCollectionConfig:
    connect_timeout_seconds: int = 5
    command_timeout_seconds: int = 30


@dataclass(frozen=True)
class LogCollectionConfig:
    max_bytes: int = 10_485_760
    tail_lines: int = 5000
    time_window_scan_max_bytes: int = 52_428_800
    prefer_time_window_scan: bool = True


@dataclass(frozen=True)
class MetricsCollectionConfig:
    mysqld_exporter_url: str | None = None


@dataclass(frozen=True)
class CollectionConfig:
    mysql: MySQLCollectionConfig = field(default_factory=MySQLCollectionConfig)
    ssh: SSHCollectionConfig = field(default_factory=SSHCollectionConfig)
    logs: LogCollectionConfig = field(default_factory=LogCollectionConfig)
    metrics: MetricsCollectionConfig = field(default_factory=MetricsCollectionConfig)


@dataclass(frozen=True)
class EvidenceStructuringConfig:
    max_workers: int = 4
    per_item_timeout_seconds: int = 30
    total_timeout_seconds: int = 120
    recursion_limit: int = 8
    max_tool_calls: int = 1
    required_tool: str = "build_evidence_bundle"


@dataclass(frozen=True)
class Phase04Config:
    findings_generation_timeout_seconds: int = 120
    validation_timeout_seconds: int = 60
    max_findings_generation_retries: int = 1
    max_validation_retries: int = 1
    max_findings: int = 5
    max_prompt_chars: int = 30_000
    max_agent_iterations: int = 6
    per_finding_validation_timeout_seconds: int = 15
    semantic_validation_enabled: bool = True


@dataclass(frozen=True)
class AppConfig:
    model: ModelConfig
    agent: AgentConfig
    runtime: RuntimeConfig
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    evidence_structuring: EvidenceStructuringConfig = field(default_factory=EvidenceStructuringConfig)
    phase04: Phase04Config = field(default_factory=Phase04Config)


def load_app_config(config_path: str | Path | None = None) -> AppConfig:
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    document = _load_yaml_document(path)
    runtime = _load_runtime_config(_require_mapping(document, "runtime"))
    return AppConfig(
        model=_load_model_config(_require_mapping(document, "model")),
        agent=_load_agent_config(_optional_mapping(document, "agent")),
        runtime=runtime,
        collection=_load_collection_config(_optional_mapping(document, "collection")),
        evidence_structuring=_load_evidence_structuring_config(
            _optional_mapping(document, "evidence_structuring")
        ),
        phase04=_load_phase04_config(_optional_mapping(document, "phase04"), runtime),
    )


def _load_yaml_document(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Config file must contain a mapping: {path}")
    return loaded


def _load_model_config(data: dict[str, Any]) -> ModelConfig:
    return ModelConfig(
        provider_kind=ProviderKind(_require_string(data, "provider_kind", "model")),
        model_name=_require_string(data, "model_name", "model"),
        base_url=_require_string(data, "base_url", "model"),
        api_key=_require_string(data, "api_key", "model"),
        temperature=float(data.get("temperature", 0.0)),
        reasoning_effort=_optional_string(data, "reasoning_effort"),
        extra_body=_optional_mapping(data, "extra_body"),
    )


def _load_runtime_config(data: dict[str, Any]) -> RuntimeConfig:
    return RuntimeConfig(
        artifact_dir=Path(_require_string(data, "artifact_dir", "runtime")),
        invoke_llm=_optional_bool(data, "invoke_llm", True),
        interactive=_optional_bool(data, "interactive", False),
        timezone=_optional_string(data, "timezone") or "Asia/Shanghai",
        locale=_optional_string(data, "locale") or "zh-CN",
        repo_dir=Path(_optional_string(data, "repo_dir") or "."),
        workspace_dir=Path(_optional_string(data, "workspace_dir") or "."),
        skills_dir=Path(_optional_string(data, "skills_dir") or "skills"),
        agents_dir=Path(_optional_string(data, "agents_dir") or "agents"),
        allowed_workspace_root=_optional_string(
            data, "allowed_workspace_root"
        )
        or "/workspace/",
        max_discovered_files=_optional_int(data, "max_discovered_files", 100),
        max_evidence_file_size_bytes=_optional_int(
            data, "max_evidence_file_size_bytes", 50_000_000
        ),
        blocked_paths=tuple(_optional_string_list(data, "blocked_paths")),
        phase04_max_prompt_chars=_optional_int(data, "phase04_max_prompt_chars", 30_000),
        phase04_findings_generation_timeout_seconds=_optional_int(
            data, "phase04_findings_generation_timeout_seconds", 120
        ),
        phase04_validation_timeout_seconds=_optional_int(
            data, "phase04_validation_timeout_seconds", 60
        ),
        phase04_max_findings_generation_retries=_optional_int(
            data, "phase04_max_findings_generation_retries", 1
        ),
        phase04_max_validation_retries=_optional_int(
            data, "phase04_max_validation_retries", 1
        ),
        phase04_max_agent_iterations=_optional_int(
            data, "phase04_max_agent_iterations", 6
        ),
        phase04_max_findings=_optional_int(data, "phase04_max_findings", 5),
        phase04_per_finding_validation_timeout_seconds=_optional_int(
            data, "phase04_per_finding_validation_timeout_seconds", 15
        ),
        phase04_semantic_validation_enabled=_optional_bool(
            data, "phase04_semantic_validation_enabled", True
        ),
    )


def _load_agent_config(data: dict[str, Any] | None) -> AgentConfig:
    data = data or {}
    return AgentConfig(
        tool_calling=_optional_bool(data, "tool_calling", True),
        tool_calling_thinking_type=_optional_string(
            data, "tool_calling_thinking_type"
        )
        or "disabled",
    )


def _load_collection_config(data: dict[str, Any] | None) -> CollectionConfig:
    data = data or {}
    mysql = _optional_mapping(data, "mysql") or {}
    ssh = _optional_mapping(data, "ssh") or {}
    logs = _optional_mapping(data, "logs") or {}
    metrics = _optional_mapping(data, "metrics") or {}
    return CollectionConfig(
        mysql=MySQLCollectionConfig(
            connect_timeout_seconds=_optional_int(mysql, "connect_timeout_seconds", 5),
            read_timeout_seconds=_optional_int(mysql, "read_timeout_seconds", 30),
            write_timeout_seconds=_optional_int(mysql, "write_timeout_seconds", 30),
        ),
        ssh=SSHCollectionConfig(
            connect_timeout_seconds=_optional_int(ssh, "connect_timeout_seconds", 5),
            command_timeout_seconds=_optional_int(ssh, "command_timeout_seconds", 30),
        ),
        logs=LogCollectionConfig(
            max_bytes=_optional_int(logs, "max_bytes", 10_485_760),
            tail_lines=_optional_int(logs, "tail_lines", 5000),
            time_window_scan_max_bytes=_optional_int(
                logs, "time_window_scan_max_bytes", 52_428_800
            ),
            prefer_time_window_scan=_optional_bool(
                logs, "prefer_time_window_scan", True
            ),
        ),
        metrics=MetricsCollectionConfig(
            mysqld_exporter_url=_optional_string(metrics, "mysqld_exporter_url"),
        ),
    )


def _load_evidence_structuring_config(data: dict[str, Any] | None) -> EvidenceStructuringConfig:
    data = data or {}
    return EvidenceStructuringConfig(
        max_workers=_optional_int(data, "max_workers", 4),
        per_item_timeout_seconds=_optional_int(data, "per_item_timeout_seconds", 30),
        total_timeout_seconds=_optional_int(data, "total_timeout_seconds", 120),
        recursion_limit=_optional_int(
            data,
            "recursion_limit",
            _optional_int(data, "max_agent_iterations", 8),
        ),
        max_tool_calls=_optional_int(data, "max_tool_calls", 1),
        required_tool=_optional_string(data, "required_tool") or "build_evidence_bundle",
    )


def _load_phase04_config(data: dict[str, Any] | None, runtime: RuntimeConfig) -> Phase04Config:
    data = data or {}
    return Phase04Config(
        findings_generation_timeout_seconds=_optional_int(
            data,
            "findings_generation_timeout_seconds",
            runtime.phase04_findings_generation_timeout_seconds,
        ),
        validation_timeout_seconds=_optional_int(
            data,
            "validation_timeout_seconds",
            runtime.phase04_validation_timeout_seconds,
        ),
        max_findings_generation_retries=_optional_int(
            data,
            "max_findings_generation_retries",
            runtime.phase04_max_findings_generation_retries,
        ),
        max_validation_retries=_optional_int(
            data,
            "max_validation_retries",
            runtime.phase04_max_validation_retries,
        ),
        max_findings=_optional_int(data, "max_findings", runtime.phase04_max_findings),
        max_prompt_chars=_optional_int(
            data, "max_prompt_chars", runtime.phase04_max_prompt_chars
        ),
        max_agent_iterations=_optional_int(
            data, "max_agent_iterations", runtime.phase04_max_agent_iterations
        ),
        per_finding_validation_timeout_seconds=_optional_int(
            data,
            "per_finding_validation_timeout_seconds",
            runtime.phase04_per_finding_validation_timeout_seconds,
        ),
        semantic_validation_enabled=_optional_bool(
            data,
            "semantic_validation_enabled",
            runtime.phase04_semantic_validation_enabled,
        ),
    )


def _require_mapping(document: dict[str, Any], field: str) -> dict[str, Any]:
    value = document.get(field)
    if not isinstance(value, dict):
        raise ValueError(f"Config section {field} must be a mapping.")
    return value


def _optional_mapping(document: dict[str, Any], field: str) -> dict[str, Any] | None:
    value = document.get(field)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Config section {field} must be a mapping.")
    return value


def _require_string(data: dict[str, Any], field: str, section: str) -> str:
    value = data.get(field)
    if value is None:
        raise ValueError(f"Config field {section}.{field} is required.")

    text = str(value).strip()
    if not text:
        raise ValueError(f"Config field {section}.{field} is required.")
    return text


def _optional_string(data: dict[str, Any], field: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(data: dict[str, Any], field: str, default: bool) -> bool:
    value = data.get(field, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    raise ValueError(f"Config field {field} must be a boolean.")


def _optional_int(data: dict[str, Any], field: str, default: int) -> int:
    value = data.get(field, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Config field {field} must be an integer.") from None


def _optional_string_list(data: dict[str, Any], field: str) -> list[str]:
    value = data.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Config field {field} must be a list.")
    return [str(item).strip() for item in value if str(item).strip()]
