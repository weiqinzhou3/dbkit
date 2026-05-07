from __future__ import annotations

from copy import deepcopy

from langchain_openai import ChatOpenAI

from dbkit.config import AgentConfig, ModelConfig, ProviderKind


def build_model(config: ModelConfig) -> ChatOpenAI:
    if config.provider_kind is not ProviderKind.OPENAI_COMPATIBLE:
        raise ValueError(f"Unsupported provider kind: {config.provider_kind}")

    return ChatOpenAI(**_model_kwargs(config))


def build_agent_model(config: ModelConfig, agent: AgentConfig) -> ChatOpenAI:
    if not agent.tool_calling:
        return build_model(config)

    kwargs = _model_kwargs(config)
    extra_body = deepcopy(config.extra_body)

    if extra_body is not None and isinstance(extra_body.get("thinking"), dict):
        thinking_type = agent.tool_calling_thinking_type
        if thinking_type:
            extra_body["thinking"]["type"] = thinking_type
            kwargs["extra_body"] = extra_body
            if thinking_type == "disabled":
                kwargs.pop("reasoning_effort", None)

    return ChatOpenAI(**kwargs)


def _model_kwargs(config: ModelConfig) -> dict[str, object]:
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

    return kwargs
