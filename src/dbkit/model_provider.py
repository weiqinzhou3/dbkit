from __future__ import annotations

from langchain_openai import ChatOpenAI

from dbkit.config import ModelConfig, ProviderKind


def build_model(config: ModelConfig) -> ChatOpenAI:
    if config.provider_kind is not ProviderKind.OPENAI_COMPATIBLE:
        raise ValueError(f"Unsupported provider kind: {config.provider_kind}")

    kwargs: dict[str, object] = {
        "model": config.model_name,
        "api_key": config.api_key,
        "base_url": config.base_url,
        "temperature": config.temperature,
        "stream_usage": False,
        "max_retries": 5,
    }
    if config.reasoning_effort is not None:
        kwargs["reasoning_effort"] = config.reasoning_effort
    if config.extra_body is not None:
        kwargs["extra_body"] = config.extra_body

    return ChatOpenAI(**kwargs)
