from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dbkit.agents.intake import IntakeAgent
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.deepagents_runtime import DeepAgentsRuntimeFactory
from dbkit.runtime.guardrails import Guardrails
from dbkit.runtime.observability import TelemetryRecorder
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
    ) -> None:
        self.repo_root = repo_root
        self.artifact_store = artifact_store
        self.telemetry = telemetry
        self.deepagents_runtime_factory = (
            deepagents_runtime_factory or DeepAgentsRuntimeFactory()
        )
        self.guardrails = guardrails or Guardrails()
        self.router = router or Router()
        self.invoke_llm = invoke_llm

    def run(self, user_input: str) -> RuntimeResult:
        self._start("redactor")
        redacted_input = self._redact(user_input)
        self._complete("redactor")

        self._start("intake")
        intake_agent = IntakeAgent.from_repo_root(self.repo_root)
        intake_runtime = self.deepagents_runtime_factory.create_intake_runtime(
            intake_agent.skill_text
        )
        if self.invoke_llm:
            self._invoke_intake_runtime(intake_runtime, redacted_input)
        normalized_request = normalize_request(redacted_input)
        self._complete("intake")

        self._start("guardrails")
        validated_request = self.guardrails.validate_normalized_request(
            normalized_request
        )
        self._complete("guardrails")

        self._start("router")
        route_decision = self.router.route(validated_request)
        artifact = self.artifact_store.persist_request(validated_request)
        self._complete("router")

        return RuntimeResult(
            normalized_request=validated_request,
            route_decision=route_decision,
            artifacts=(artifact,),
            telemetry=tuple(self.telemetry.events),
            deepagents_runtime_ready=intake_runtime is not None,
        )

    def _redact(self, user_input: str) -> str:
        return user_input

    def _invoke_intake_runtime(self, intake_runtime: object, user_input: str) -> None:
        invoke = getattr(intake_runtime, "invoke", None)
        if not callable(invoke):
            raise TypeError("DeepAgents intake runtime must expose invoke()")
        invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Normalize this DBKit Phase 01 intake request with the "
                            f"available normalize_request tool: {user_input}"
                        ),
                    }
                ]
            }
        )

    def _start(self, stage: str) -> None:
        self.telemetry.emit(
            event_type="stage_started",
            stage=stage,
            message=f"{stage} stage started",
            attributes={"phase": "phase-01"},
        )

    def _complete(self, stage: str) -> None:
        self.telemetry.emit(
            event_type="stage_completed",
            stage=stage,
            message=f"{stage} stage completed",
            attributes={"phase": "phase-01"},
        )
