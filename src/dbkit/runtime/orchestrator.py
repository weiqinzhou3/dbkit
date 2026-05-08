from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from dbkit.agents.intake import IntakeAgent
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.deepagents_runtime import DeepAgentsRuntimeFactory
from dbkit.runtime.guardrails import Guardrails
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.runtime.redactor import Redactor, estimate_tokens
from dbkit.runtime.router import Router
from dbkit.runtime.time_context import TimeProvider
from dbkit.runtime.user_message import render_user_message, validate_user_message
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
    time_provider: Any
    phase: str

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
        time_provider: Any | None = None,
        phase: str = "phase-01.2",
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
        self.time_provider = time_provider or TimeProvider()
        self.phase = phase

    def run(
        self,
        user_input: str,
        *,
        interactive: bool = False,
        supplement_reader: Callable[..., str] | None = None,
    ) -> RuntimeResult:
        # --- Redaction ---
        redaction_result = self.redactor.redact(user_input)
        redacted_input = redaction_result.redacted_text

        # Generate request_id from redacted input so secrets never influence IDs.
        from dbkit.tools.normalize_request import _request_id
        request_id = _request_id(redacted_input.strip())
        runtime_context = _validated_runtime_context(self.time_provider.runtime_context())
        self.telemetry.emit_runtime_context_injected(
            request_id=request_id,
            runtime_context=runtime_context,
        )

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
                intake_runtime,
                redacted_input,
                request_id,
                runtime_context=runtime_context,
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
            phase=self.phase,
        )
        tool_latency_ms = (perf_counter() - tool_started_at) * 1000
        self.telemetry.emit_normalize_request_completed(
            request_id=normalized_request.request_id,
            missing_fields=list(normalized_request.missing_fields),
        )
        if normalized_request.event and normalized_request.event.get("event_time"):
            self.telemetry.emit_relative_time_resolved(
                request_id=normalized_request.request_id,
                event_time=str(normalized_request.event["event_time"]),
                runtime_context=runtime_context,
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
            if interactive:
                return self._run_interactive_supplement(
                    intake_runtime=intake_runtime,
                    normalized_request=normalized_request,
                    blocking_issues=guardrails_result.blocking_issues,
                    runtime_context=runtime_context,
                    supplement_reader=supplement_reader,
                )

            user_message = None
            if self.invoke_llm:
                user_message = self._request_blocked_user_message(
                    intake_runtime=intake_runtime,
                    normalized_request=normalized_request,
                    blocking_issues=guardrails_result.blocking_issues,
                    runtime_context=runtime_context,
                )
            else:
                self.telemetry.emit_blocked_message_fallback_used(
                    request_id=normalized_request.request_id,
                    reason="invoke_llm disabled",
                )
            blocked_artifact = self.artifact_store.persist_blocked_request(
                normalized_request,
                guardrails_result.blocking_issues,
                user_message=user_message,
                supplement_fields=_supplement_fields(guardrails_result.blocking_issues),
            )
            rendered = render_user_message(
                user_message,
                guardrails_result.blocking_issues,
                artifact_path=str(blocked_artifact.path),
            )
            self.telemetry.emit_blocked_message_rendered(
                request_id=normalized_request.request_id
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
                user_message=user_message,
                rendered_user_message=rendered,
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
        *,
        runtime_context: dict[str, str],
        mode: str = "intake",
        extra_payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        invoke = getattr(intake_runtime, "invoke", None)
        if not callable(invoke):
            raise TypeError("DeepAgents intake runtime must expose invoke()")

        self.telemetry.emit_intake_agent_started(request_id=request_id)
        invocation_context: dict[str, Any] = {
            "mode": mode,
            "runtime_context": runtime_context,
        }
        if mode == "supplement_patch":
            invocation_context["sanitized_supplement_text"] = user_input
        else:
            invocation_context["sanitized_user_prompt"] = user_input
        invocation_context.update(extra_payload or {})

        payload: dict[str, Any] = dict(invocation_context)
        payload["messages"] = [
            {
                "role": "user",
                "content": _intake_instruction(mode, invocation_context),
            }
        ]
        invoke_result = invoke(payload)
        self.telemetry.emit_intake_agent_completed(request_id=request_id)

        return self._extract_llm_json(invoke_result, request_id)

    def _request_blocked_user_message(
        self,
        *,
        intake_runtime: object,
        normalized_request: Any,
        blocking_issues: tuple[str, ...],
        runtime_context: dict[str, str],
    ) -> dict[str, Any] | None:
        self.telemetry.emit_blocked_message_requested(
            request_id=normalized_request.request_id
        )
        llm_json, reason = self._invoke_intake_runtime(
            intake_runtime,
            normalized_request.redacted_input,
            normalized_request.request_id,
            runtime_context=runtime_context,
            mode="blocked_message",
            extra_payload={
                "normalized_request": normalized_request.to_dict(),
                "blocking_issues": list(blocking_issues),
                "missing_fields": list(normalized_request.missing_fields),
                "input_mode": normalized_request.input_mode,
                "collection_policy": normalized_request.collection_policy or {},
            },
        )
        raw_message = (llm_json or {}).get("user_message") if llm_json else None
        validation = validate_user_message(
            raw_message,
            blocking_issues=blocking_issues,
        )
        self.telemetry.emit_blocked_message_validated(
            request_id=normalized_request.request_id,
            valid=validation.valid,
            errors=list(validation.errors),
        )
        if not validation.valid:
            self.telemetry.emit_blocked_message_fallback_used(
                request_id=normalized_request.request_id,
                reason=reason or "; ".join(validation.errors) or "invalid user_message",
            )
            return None
        return validation.user_message

    def _run_interactive_supplement(
        self,
        *,
        intake_runtime: object,
        normalized_request: Any,
        blocking_issues: tuple[str, ...],
        runtime_context: dict[str, str],
        supplement_reader: Callable[..., str] | None,
    ) -> RuntimeResult:
        user_message = self._request_blocked_user_message(
            intake_runtime=intake_runtime,
            normalized_request=normalized_request,
            blocking_issues=blocking_issues,
            runtime_context=runtime_context,
        ) if self.invoke_llm else None
        if not self.invoke_llm:
            self.telemetry.emit_blocked_message_fallback_used(
                request_id=normalized_request.request_id,
                reason="invoke_llm disabled",
            )
        rendered = render_user_message(user_message, blocking_issues)
        self.telemetry.emit_blocked_message_rendered(
            request_id=normalized_request.request_id
        )
        self.telemetry.emit_interactive_event(
            event_type="interactive_supplement_started",
            request_id=normalized_request.request_id,
            message="Interactive supplement started",
        )
        if supplement_reader is None:
            supplement_reader = input
        supplement_text = _call_supplement_reader(supplement_reader, rendered)
        if not supplement_text.strip():
            self.telemetry.emit_interactive_event(
                event_type="interactive_supplement_cancelled",
                request_id=normalized_request.request_id,
                message="Interactive supplement cancelled",
            )
            return self._persist_blocked_result(
                normalized_request,
                blocking_issues,
                user_message,
                rendered,
            )

        self.telemetry.emit_interactive_event(
            event_type="interactive_supplement_input_received",
            request_id=normalized_request.request_id,
            message="Interactive supplement input received",
        )
        redaction_result = self.redactor.redact(supplement_text)
        self.telemetry.emit_interactive_event(
            event_type="interactive_supplement_redacted",
            request_id=normalized_request.request_id,
            message="Interactive supplement redacted",
            attributes={
                "secret_count": len(redaction_result.secret_refs),
                "redacted_patterns": redaction_result.redaction_summary.get(
                    "redacted_patterns", []
                ),
            },
        )
        if redaction_result.secret_refs:
            self.telemetry.emit_interactive_event(
                event_type="interactive_secret_collected",
                request_id=normalized_request.request_id,
                message="Interactive supplement contained redacted secrets",
                attributes={"secret_count": len(redaction_result.secret_refs)},
            )

        self.telemetry.emit_interactive_event(
            event_type="interactive_supplement_patch_requested",
            request_id=normalized_request.request_id,
            message="Interactive supplement patch requested",
        )
        llm_json, reason = self._invoke_intake_runtime(
            intake_runtime,
            redaction_result.redacted_text,
            normalized_request.request_id,
            runtime_context=runtime_context,
            mode="supplement_patch",
            extra_payload={
                "normalized_request": normalized_request.to_dict(),
                "blocking_issues": list(blocking_issues),
            },
        )
        patch = (llm_json or {}).get("supplement_patch") if llm_json else None
        if not isinstance(patch, dict):
            self.telemetry.emit_interactive_event(
                event_type="interactive_supplement_patch_rejected",
                request_id=normalized_request.request_id,
                message="Interactive supplement patch rejected",
                attributes={"reason": reason or "missing supplement_patch"},
            )
            return self._persist_blocked_result(
                normalized_request,
                blocking_issues,
                user_message,
                rendered,
            )

        patch_result = self.guardrails.validate_supplement_patch(
            patch,
            base_request=normalized_request,
        )
        if not patch_result.passed:
            self.telemetry.emit_interactive_event(
                event_type="interactive_supplement_patch_rejected",
                request_id=normalized_request.request_id,
                message="Interactive supplement patch rejected",
                attributes={"blocking_issues": list(patch_result.blocking_issues)},
            )
            return self._persist_blocked_result(
                normalized_request,
                patch_result.blocking_issues,
                user_message,
                rendered,
            )

        self.telemetry.emit_interactive_event(
            event_type="interactive_supplement_patch_validated",
            request_id=normalized_request.request_id,
            message="Interactive supplement patch validated",
        )
        merged_json = _merge_normalized_request_patch(normalized_request.to_dict(), patch)
        merged_json["missing_fields"] = []
        supplement_history = list(
            normalized_request.metadata.get("supplement_history", [])
        )
        supplement_history.append(
            {
                "timestamp": runtime_context["current_datetime"],
                "redaction_summary": redaction_result.redaction_summary,
                "patch_applied": patch,
            }
        )
        merged_json["metadata"] = dict(merged_json.get("metadata") or {})
        merged_json["metadata"]["supplement_history"] = supplement_history
        supplemented_request = normalize_request(
            normalized_request.redacted_input,
            llm_json=merged_json,
            redaction_summary=normalized_request.redaction_summary,
            phase=self.phase,
        )
        self.telemetry.emit_interactive_event(
            event_type="interactive_supplement_patch_merged",
            request_id=supplemented_request.request_id,
            message="Interactive supplement patch merged",
        )

        self.telemetry.emit_guardrails_started(
            request_id=supplemented_request.request_id
        )
        rerun_result = self.guardrails.validate(supplemented_request)
        if not rerun_result.passed:
            self.telemetry.emit_guardrails_blocked(
                request_id=supplemented_request.request_id,
                blocking_issues=list(rerun_result.blocking_issues),
            )
            return self._persist_blocked_result(
                supplemented_request,
                rerun_result.blocking_issues,
                user_message,
                rendered,
            )

        self.telemetry.emit_guardrails_passed(
            request_id=supplemented_request.request_id
        )
        history = supplemented_request.metadata.get("supplement_history")
        if isinstance(history, list) and history:
            history[-1]["guardrails_status"] = "passed"
        self.telemetry.emit_interactive_event(
            event_type="interactive_supplement_completed",
            request_id=supplemented_request.request_id,
            message="Interactive supplement completed",
        )
        route_decision = self.router.route(supplemented_request)
        self.telemetry.emit_route_selected(
            request_id=supplemented_request.request_id,
            target_agent=route_decision.target_agent_name,
            target_domain=route_decision.target_domain,
        )
        request_artifact = self.artifact_store.persist_request(supplemented_request)
        self.telemetry.emit_artifact_written(
            request_id=supplemented_request.request_id,
            kind="NormalizedRequest",
            path=str(request_artifact.path),
        )
        telemetry_artifact = self.artifact_store.persist_telemetry(
            supplemented_request.request_id, self.telemetry.events
        )
        return RuntimeResult(
            normalized_request=supplemented_request,
            route_decision=route_decision,
            artifacts=(request_artifact, telemetry_artifact),
            telemetry=tuple(self.telemetry.events),
            deepagents_runtime_ready=True,
            blocked=False,
            user_message=user_message,
            rendered_user_message=rendered,
        )

    def _persist_blocked_result(
        self,
        normalized_request: Any,
        blocking_issues: tuple[str, ...],
        user_message: dict[str, Any] | None,
        rendered: str,
    ) -> RuntimeResult:
        blocked_artifact = self.artifact_store.persist_blocked_request(
            normalized_request,
            blocking_issues,
            user_message=user_message,
            supplement_fields=_supplement_fields(blocking_issues),
        )
        rendered_with_artifact = render_user_message(
            user_message,
            blocking_issues,
            artifact_path=str(blocked_artifact.path),
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
            deepagents_runtime_ready=True,
            blocked=True,
            blocking_issues=blocking_issues,
            user_message=user_message,
            rendered_user_message=rendered_with_artifact or rendered,
        )

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


def _intake_instruction(mode: str, invocation_context: dict[str, Any]) -> str:
    if mode == "blocked_message":
        instruction = (
            "Generate a DBKit structured user_message JSON for this blocked "
            "intake request. Do not output prose. Do not include secrets."
        )
    elif mode == "supplement_patch":
        instruction = (
            "Interpret this redacted supplement text and output structured "
            "supplement_patch JSON."
        )
    else:
        instruction = (
            "Parse this DBKit intake request and output structured JSON. Use the "
            "provided runtime_context for relative time."
        )
    return (
        f"{instruction}\n\n"
        "DBKit invocation context JSON:\n"
        f"{json.dumps(invocation_context, ensure_ascii=False, indent=2, sort_keys=True)}"
    )


def _validated_runtime_context(context: object) -> dict[str, str]:
    if not isinstance(context, dict):
        raise RuntimeError("runtime_context must be a mapping")
    required = ("current_datetime", "timezone", "locale")
    missing = [field for field in required if not str(context.get(field) or "").strip()]
    if missing:
        raise RuntimeError(
            "Missing internal runtime dependency: "
            + ", ".join(f"runtime_context.{field}" for field in missing)
        )
    return {field: str(context[field]) for field in required}


def _supplement_fields(blocking_issues: tuple[str, ...]) -> list[str]:
    fields: list[str] = []
    for issue in blocking_issues:
        if issue.startswith("missing required field: "):
            fields.append(issue.removeprefix("missing required field: ").strip())
    return fields


def _merge_normalized_request_patch(
    base: dict[str, Any],
    patch: dict[str, object],
) -> dict[str, Any]:
    merged = json.loads(json.dumps(base, ensure_ascii=False))
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _deep_merge_dict(base: dict[str, Any], patch: dict[str, object]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _call_supplement_reader(
    supplement_reader: Callable[..., str],
    rendered_message: str,
) -> str:
    try:
        return supplement_reader(rendered_message)
    except TypeError:
        return supplement_reader()


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
