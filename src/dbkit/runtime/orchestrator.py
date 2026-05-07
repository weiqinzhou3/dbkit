from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from dbkit.agents.intake import IntakeAgent
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.deepagents_runtime import DeepAgentsRuntimeFactory
from dbkit.runtime.guardrails import Guardrails
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.runtime.redactor import Redactor, estimate_tokens
from dbkit.runtime.router import Router
from dbkit.schemas.runtime import RuntimeResult
from dbkit.tools.normalize_request import normalize_request


@dataclass
class Orchestrator:
    repo_root: Path
    artifact_store: ArtifactStore
    telemetry: TelemetryRecorder
    deepagents_runtime_factory: DeepAgentsRuntimeFactory
    guardrails: Guardrails
    router: Router
    invoke_llm: bool
    redactor: Redactor
    skills_dir: Path

    def __init__(
        self,
        *,
        repo_root: Path,
        artifact_store: ArtifactStore,
        telemetry: TelemetryRecorder,
        deepagents_runtime_factory: DeepAgentsRuntimeFactory | None = None,
        guardrails: Guardrails | None = None,
        router: Router | None = None,
        invoke_llm: bool = True,
        redactor: Redactor | None = None,
        skills_dir: Path | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.skills_dir = skills_dir or repo_root / "skills"
        self.artifact_store = artifact_store
        self.telemetry = telemetry
        self.deepagents_runtime_factory = (
            deepagents_runtime_factory or DeepAgentsRuntimeFactory()
        )
        self.guardrails = guardrails or Guardrails()
        self.router = router or Router()
        self.invoke_llm = invoke_llm
        self.redactor = redactor or Redactor()

    def run(self, user_input: str) -> RuntimeResult:
        # --- Redaction ---
        redaction_result = self.redactor.redact(user_input)
        redacted_input = redaction_result.redacted_text

        # Generate a provisional request_id from the original input for telemetry
        from dbkit.tools.normalize_request import _request_id
        request_id = _request_id(user_input.strip())

        self.telemetry.emit_redaction_completed(
            request_id=request_id,
            secret_count=len(redaction_result.secret_refs),
            patterns=redaction_result.redaction_summary.get("redacted_patterns", []),
            raw_bytes=redaction_result.raw_bytes,
            filtered_bytes=redaction_result.filtered_bytes,
        )
        self.telemetry.emit_runtime_cost(
            stage="redactor",
            raw_bytes=redaction_result.raw_bytes,
            filtered_bytes=redaction_result.filtered_bytes,
            compression_ratio=redaction_result.compression_ratio,
            estimated_tokens=redaction_result.estimated_tokens,
            tool_latency_ms=0.0,
        )

        # --- Intake Agent ---
        intake_agent = IntakeAgent.from_skills_dir(self.skills_dir)
        intake_runtime = self.deepagents_runtime_factory.create_intake_runtime(
            intake_agent.skill_text
        )

        llm_json: dict[str, Any] | None = None
        fallback_reason: str | None = None
        if self.invoke_llm:
            llm_json, fallback_reason = self._invoke_intake_runtime(
                intake_runtime, redacted_input, request_id
            )

        # --- Normalize ---
        self.telemetry.emit_normalize_request_started(request_id=request_id)
        tool_started_at = perf_counter()
        normalized_request = normalize_request(
            redacted_input,
            llm_json=llm_json,
            redaction_summary=redaction_result.redaction_summary,
            llm_intake_failed=self.invoke_llm and llm_json is None,
            fallback_reason=fallback_reason,
        )
        tool_latency_ms = (perf_counter() - tool_started_at) * 1000
        self.telemetry.emit_normalize_request_completed(
            request_id=normalized_request.request_id,
            missing_fields=list(normalized_request.missing_fields),
        )
        self.telemetry.emit_runtime_cost(
            stage="normalize_request",
            raw_bytes=len(redacted_input.encode("utf-8")),
            filtered_bytes=len(
                json.dumps(normalized_request.to_dict(), ensure_ascii=False).encode("utf-8")
            ),
            compression_ratio=_ratio(
                len(json.dumps(normalized_request.to_dict(), ensure_ascii=False).encode("utf-8")),
                len(redacted_input.encode("utf-8")),
            ),
            estimated_tokens=estimate_tokens(redacted_input),
            tool_latency_ms=tool_latency_ms,
        )

        # --- Guardrails ---
        self.telemetry.emit_guardrails_started(
            request_id=normalized_request.request_id
        )
        guardrails_result = self.guardrails.validate(normalized_request)

        if not guardrails_result.passed:
            self.telemetry.emit_guardrails_blocked(
                request_id=normalized_request.request_id,
                blocking_issues=list(guardrails_result.blocking_issues),
            )
            blocked_artifact = self.artifact_store.persist_blocked_request(
                normalized_request, guardrails_result.blocking_issues
            )
            self.telemetry.emit_artifact_written(
                request_id=normalized_request.request_id,
                kind="BlockedRequest",
                path=str(blocked_artifact.path),
            )
            telemetry_artifact = self.artifact_store.persist_telemetry(
                normalized_request.request_id, self.telemetry.events
            )
            return RuntimeResult(
                normalized_request=normalized_request,
                route_decision=None,
                artifacts=(blocked_artifact, telemetry_artifact),
                telemetry=tuple(self.telemetry.events),
                deepagents_runtime_ready=intake_runtime is not None,
                blocked=True,
                blocking_issues=guardrails_result.blocking_issues,
            )

        self.telemetry.emit_guardrails_passed(
            request_id=normalized_request.request_id
        )

        # --- Route ---
        route_decision = self.router.route(normalized_request)
        self.telemetry.emit_route_selected(
            request_id=normalized_request.request_id,
            target_agent=route_decision.target_agent_name,
            target_domain=route_decision.target_domain,
        )

        # --- Persist ---
        request_artifact = self.artifact_store.persist_request(normalized_request)
        self.telemetry.emit_artifact_written(
            request_id=normalized_request.request_id,
            kind="NormalizedRequest",
            path=str(request_artifact.path),
        )
        telemetry_artifact = self.artifact_store.persist_telemetry(
            normalized_request.request_id, self.telemetry.events
        )

        return RuntimeResult(
            normalized_request=normalized_request,
            route_decision=route_decision,
            artifacts=(request_artifact, telemetry_artifact),
            telemetry=tuple(self.telemetry.events),
            deepagents_runtime_ready=intake_runtime is not None,
            blocked=False,
        )

    def _invoke_intake_runtime(
        self,
        intake_runtime: object,
        user_input: str,
        request_id: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        invoke = getattr(intake_runtime, "invoke", None)
        if not callable(invoke):
            raise TypeError("DeepAgents intake runtime must expose invoke()")

        self.telemetry.emit_intake_agent_started(request_id=request_id)
        invoke_result = invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Parse this DBKit intake request and output structured JSON: "
                            f"{user_input}"
                        ),
                    }
                ]
            }
        )
        self.telemetry.emit_intake_agent_completed(request_id=request_id)

        return self._extract_llm_json(invoke_result, request_id)

    def _extract_llm_json(
        self, invoke_result: object, request_id: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not isinstance(invoke_result, dict):
            reason = "invoke result is not a dict"
            self.telemetry.emit_intake_json_parse_failed(
                request_id=request_id,
                reason=reason,
            )
            return None, reason

        messages = invoke_result.get("messages", [])
        for msg in reversed(messages):
            role = _message_role(msg)
            if role not in {"assistant", "ai"}:
                continue
            content = _message_content(msg)
            if not content:
                continue
            parsed = _parse_json_object(content)
            if parsed is not None:
                return parsed, None

        reason = "no parseable JSON found in assistant messages"
        self.telemetry.emit_intake_json_parse_failed(
            request_id=request_id,
            reason=reason,
        )
        return None, reason


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)


def _message_role(message: object) -> str | None:
    if isinstance(message, dict):
        role = message.get("role") or message.get("type")
    else:
        role = getattr(message, "role", None) or getattr(message, "type", None)
    return str(role).lower() if role else None


def _message_content(message: object) -> str:
    if isinstance(message, dict):
        content = message.get("content") or ""
    else:
        content = getattr(message, "content", "") or ""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


def _parse_json_object(content: str) -> dict[str, Any] | None:
    candidates = [content.strip()]
    fenced = _extract_fenced_json(content)
    if fenced:
        candidates.append(fenced)
    braced = _extract_balanced_json_object(content)
    if braced:
        candidates.append(braced)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_fenced_json(content: str) -> str | None:
    marker = "```"
    start = content.find(marker)
    if start < 0:
        return None
    body_start = content.find("\n", start + len(marker))
    if body_start < 0:
        return None
    end = content.find(marker, body_start + 1)
    if end < 0:
        return None
    return content[body_start + 1 : end].strip()


def _extract_balanced_json_object(content: str) -> str | None:
    start = content.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]
    return None
