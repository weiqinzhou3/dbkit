from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class RuntimeConfig:
    artifact_dir: Path = Path(".dbkit") / "artifacts"
    invoke_llm: bool = True


@dataclass(frozen=True)
class AppConfig:
    model: ModelConfig
    runtime: RuntimeConfig


def load_app_config(config_path: str | Path | None = None) -> AppConfig:
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    document = _load_yaml_document(path)
    return AppConfig(
        model=_load_model_config(_require_mapping(document, "model")),
        runtime=_load_runtime_config(_require_mapping(document, "runtime")),
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
    )


def _load_runtime_config(data: dict[str, Any]) -> RuntimeConfig:
    return RuntimeConfig(
        artifact_dir=Path(_require_string(data, "artifact_dir", "runtime")),
        invoke_llm=_optional_bool(data, "invoke_llm", True),
    )


def _require_mapping(document: dict[str, Any], field: str) -> dict[str, Any]:
    value = document.get(field)
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
