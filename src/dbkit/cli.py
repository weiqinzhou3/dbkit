from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from dbkit import __version__
from dbkit.agents.evidence_structuring import EvidenceStructuringSubagentRegistration
from dbkit.agents.mysql_analyzer import MySQLAnalyzerAgent
from dbkit.config import DEFAULT_CONFIG_PATH, load_app_config
from dbkit.model_provider import build_agent_model
from dbkit.runtime.artifact_paths import (
    to_deepagents_repo_virtual_path,
    to_repo_relative_path,
)
from dbkit.runtime.analysis import Phase04AnalysisPipeline
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.deepagents_runtime import DeepAgentsRuntimeFactory
from dbkit.runtime.evidence_delegation import EvidenceStructuringDelegator
from dbkit.runtime.evidence_pipeline import EvidencePipeline
from dbkit.runtime.evidence_structuring import EvidenceStructuringPipeline
from dbkit.runtime.guardrails import Guardrails
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.runtime.orchestrator import Orchestrator
from dbkit.runtime.secret_store import SecretStore
from dbkit.runtime.time_context import TimeProvider
from dbkit.tools.collectors import CollectorRegistry
from dbkit.tools.evidence_deepagent import create_evidence_structuring_tools


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    if any(arg in {"--help", "-h"} for arg in args):
        _print_help()
        return 0
    (
        config_path,
        interactive_flag,
        raw_evidence_index_path,
        evidence_bundle_path,
        prompt_args,
    ) = _parse_args(args)
    config = load_app_config(config_path)
    if evidence_bundle_path is not None:
        artifact_store = ArtifactStore(config.runtime.artifact_dir)
        model = build_agent_model(config.model, config.agent)
        runtime_factory = DeepAgentsRuntimeFactory(
            model=model,
            tools_enabled=False,
            repo_dir=config.runtime.repo_dir,
            workspace_dir=config.runtime.workspace_dir,
            skills_dir=config.runtime.skills_dir,
            agents_dir=config.runtime.agents_dir,
        )
        mysql_analyzer = MySQLAnalyzerAgent.from_skills_dir(
            config.runtime.skills_dir,
            agents_dir=config.runtime.agents_dir,
        )
        validation_skill = _validation_skill(config.runtime.skills_dir)
        phase04_result = Phase04AnalysisPipeline(
            artifact_store=artifact_store,
            telemetry=TelemetryRecorder(),
            mysql_analyzer_runtime=runtime_factory.create_mysql_analyzer_runtime(
                mysql_analyzer.skill_text
            ),
            validation_runtime=runtime_factory.create_validation_runtime(validation_skill),
            repo_dir=config.runtime.repo_dir,
            max_prompt_chars=config.phase04.max_prompt_chars,
            findings_generation_timeout_seconds=(
                config.phase04.findings_generation_timeout_seconds
            ),
            validation_timeout_seconds=config.phase04.validation_timeout_seconds,
            max_findings_generation_retries=(
                config.phase04.max_findings_generation_retries
            ),
            max_validation_retries=config.phase04.max_validation_retries,
            max_agent_iterations=config.phase04.max_agent_iterations,
            max_findings=config.phase04.max_findings,
            model_name=config.model.model_name,
        ).run(evidence_bundle_path)
        print(f"DBKit {__version__}")
        print("mode=replay")
        _print_phase04_result(phase04_result)
        return 0 if phase04_result.status in {
            "analysis_completed",
            "analysis_completed_with_warnings",
            "human_review_required",
        } else 1

    if raw_evidence_index_path is not None:
        result = EvidenceStructuringPipeline(
            artifact_store=ArtifactStore(config.runtime.artifact_dir),
            telemetry=TelemetryRecorder(),
            subagent_registration=EvidenceStructuringSubagentRegistration.from_dirs(
                skills_dir=config.runtime.skills_dir,
                agents_dir=config.runtime.agents_dir,
            ),
            max_workers=config.evidence_structuring.max_workers,
            per_item_timeout_seconds=config.evidence_structuring.per_item_timeout_seconds,
            total_timeout_seconds=config.evidence_structuring.total_timeout_seconds,
        ).run(raw_evidence_index_path)
        print(f"DBKit {__version__}")
        print(f"phase={result.phase}")
        print(f"status={result.status}")
        print("mode=replay")
        print("subagent_invocation=false")
        if result.bundle is not None:
            print(f"subagent={result.bundle.metadata.get('subagent')}")
            print(f"parent_agent={result.bundle.metadata.get('parent_agent')}")
        if result.bundle is not None:
            print(f"evidence_items={len(result.bundle.evidence_items)}")
            print(
                "unavailable_evidence="
                f"{len(result.bundle.coverage.get('unavailable_evidence') or [])}"
            )
            print(f"quality={result.bundle.quality.get('overall_status')}")
        if result.bundle_artifact is not None:
            print(f"artifact={result.bundle_artifact.path}")
        elif result.artifacts:
            print(f"artifact={result.artifacts[-1].path}")
        if result.blocking_issues:
            print(f"blocking_issues={';'.join(result.blocking_issues)}")
        return 0 if result.status == "evidence_bundle_created" else 1

    user_input = (
        " ".join(prompt_args) if prompt_args else "MySQL runtime intake smoke test"
    )
    repo_root = config.runtime.repo_dir
    artifact_root = config.runtime.artifact_dir
    model = build_agent_model(config.model, config.agent)
    runtime_factory = DeepAgentsRuntimeFactory(
        model=model,
        tools_enabled=False,
        repo_dir=config.runtime.repo_dir,
        workspace_dir=config.runtime.workspace_dir,
        skills_dir=config.runtime.skills_dir,
        agents_dir=config.runtime.agents_dir,
    )
    orchestrator = Orchestrator(
        repo_root=repo_root,
        skills_dir=config.runtime.skills_dir,
        artifact_store=ArtifactStore(artifact_root),
        telemetry=TelemetryRecorder(),
        guardrails=Guardrails(
            allowed_workspace_root=config.runtime.allowed_workspace_root,
            max_discovered_files=config.runtime.max_discovered_files,
            max_evidence_file_size_bytes=config.runtime.max_evidence_file_size_bytes,
            blocked_paths=config.runtime.blocked_paths,
        ),
        deepagents_runtime_factory=runtime_factory,
        invoke_llm=config.runtime.invoke_llm,
        time_provider=TimeProvider(
            timezone=config.runtime.timezone,
            locale=config.runtime.locale,
        ),
    )
    interactive = interactive_flag or config.runtime.interactive
    result = orchestrator.run(
        user_input,
        interactive=interactive,
        supplement_reader=_read_supplement if interactive else None,
    )

    print(f"DBKit {__version__}")

    if result.blocked:
        print("status=blocked")
        print(
            "reason=missing_required_fields"
            if any(i.startswith("missing required field: ") for i in result.blocking_issues)
            else "reason=guardrails_failed"
        )
        missing = [
            i.removeprefix("missing required field: ")
            for i in result.blocking_issues
            if i.startswith("missing required field: ")
        ]
        if missing:
            print(f"missing_fields={','.join(missing)}")
        if result.rendered_user_message:
            print()
            print(result.rendered_user_message)
        print()
        print("提示：使用 --interactive 可以交互式补充缺失信息。")
        return 1

    try:
        mysql_analyzer = MySQLAnalyzerAgent.from_skills_dir(
            config.runtime.skills_dir,
            agents_dir=config.runtime.agents_dir,
        )
    except TypeError:
        mysql_analyzer = MySQLAnalyzerAgent.from_skills_dir(config.runtime.skills_dir)
    collection_telemetry = TelemetryRecorder()
    artifact_store = ArtifactStore(artifact_root)
    subagents = getattr(mysql_analyzer, "subagents", {}) or {}
    subagent_registration = subagents.get(
        "evidence_structuring",
        EvidenceStructuringSubagentRegistration.from_dirs(
            skills_dir=config.runtime.skills_dir,
            agents_dir=config.runtime.agents_dir,
        ),
    )
    evidence_structuring_results = []
    evidence_structuring_tools = create_evidence_structuring_tools(
        artifact_store=artifact_store,
        telemetry=collection_telemetry,
        subagent_registration=subagent_registration,
        result_sink=evidence_structuring_results.append,
        repo_dir=config.runtime.repo_dir,
        max_workers=config.evidence_structuring.max_workers,
        per_item_timeout_seconds=config.evidence_structuring.per_item_timeout_seconds,
        total_timeout_seconds=config.evidence_structuring.total_timeout_seconds,
    )
    analyzer_runtime = runtime_factory.create_mysql_analyzer_runtime(
        mysql_analyzer.skill_text,
        evidence_structuring_subagent=subagent_registration,
        evidence_structuring_tools=evidence_structuring_tools,
    )
    evidence_result = EvidencePipeline(
        artifact_store=artifact_store,
        telemetry=collection_telemetry,
        collectors=CollectorRegistry(
            workspace_root=config.runtime.workspace_dir,
            secret_store=SecretStore(result.secret_values),
            log_tail_lines=config.collection.logs.tail_lines,
            log_max_bytes=config.collection.logs.max_bytes,
            log_time_window_scan_max_bytes=config.collection.logs.time_window_scan_max_bytes,
            log_prefer_time_window_scan=config.collection.logs.prefer_time_window_scan,
            mysql_connect_timeout_seconds=config.collection.mysql.connect_timeout_seconds,
            mysql_read_timeout_seconds=config.collection.mysql.read_timeout_seconds,
            mysql_write_timeout_seconds=config.collection.mysql.write_timeout_seconds,
        ),
        time_provider=TimeProvider(
            timezone=config.runtime.timezone,
            locale=config.runtime.locale,
        ),
        mysql_analyzer_runtime=analyzer_runtime,
    ).run(result.normalized_request)

    if evidence_result.status in {
        "evidence_request_parse_failed",
        "evidence_request_validation_failed",
        "missing_collection_dependencies",
    }:
        print(f"phase={evidence_result.phase}")
        print("status=blocked")
        print(f"reason={evidence_result.status}")
        if evidence_result.status == "missing_collection_dependencies":
            missing_dependencies = evidence_result.metadata.get("missing_dependencies") or []
            install_hint = evidence_result.metadata.get("install_hint")
            print(f"missing_dependencies={','.join(missing_dependencies)}")
            print(f"install_hint={install_hint}")
        _print_collection_summary(evidence_result, result.normalized_request.input_mode)
        _print_collection_artifact(evidence_result)
        return 1

    index_artifacts = _raw_evidence_index_artifacts(evidence_result)
    if evidence_result.status in {
        "collection_blocked",
        "collection_failed",
        "collection_not_implemented",
    }:
        print(f"phase={evidence_result.phase}")
        print(f"status={evidence_result.status}")
        _print_collection_summary(evidence_result, result.normalized_request.input_mode)
        _print_collection_errors(evidence_result)
        _print_collection_artifact(evidence_result)
        return 1

    if _stop_after_phase(result.normalized_request) == "phase-02.1":
        print(f"phase={evidence_result.phase}")
        print(f"status={evidence_result.status}")
        _print_collection_summary(evidence_result, result.normalized_request.input_mode)
        _print_collection_artifact(evidence_result)
        return 0 if evidence_result.status in {
            "raw_evidence_collected",
            "collection_completed_with_warnings",
        } else 1

    if not index_artifacts:
        print(f"phase={evidence_result.phase}")
        print("status=blocked")
        print("reason=raw_evidence_index_missing")
        _print_collection_summary(evidence_result, result.normalized_request.input_mode)
        _print_collection_artifact(evidence_result)
        return 1

    raw_evidence_index_path = index_artifacts[0].path
    raw_evidence_index_repo_relative = to_repo_relative_path(
        raw_evidence_index_path,
        repo_dir=config.runtime.repo_dir,
    )
    raw_evidence_index_virtual_path = to_deepagents_repo_virtual_path(
        raw_evidence_index_path,
        repo_dir=config.runtime.repo_dir,
    )
    collection_telemetry.emit(
        event_type="evidence_subagent_delegation_started",
        stage="evidence_structuring",
        message="MySQL analyzer delegated RawEvidence structuring to evidence_structuring",
        attributes={
            "request_id": evidence_result.request_id,
            "parent_agent": "mysql_analyzer",
            "subagent": "evidence_structuring",
            "raw_evidence_index": raw_evidence_index_virtual_path,
            "raw_evidence_index_repo_relative": raw_evidence_index_repo_relative,
            "raw_evidence_index_virtual_path": raw_evidence_index_virtual_path,
            "artifact_root": str(artifact_root),
            "filesystem_root": "/repo",
            "status": "started",
        },
    )
    structuring_result = EvidenceStructuringDelegator(
        mysql_analyzer_runtime=analyzer_runtime,
        telemetry=collection_telemetry,
        repo_dir=config.runtime.repo_dir,
        artifact_root=artifact_root,
        recursion_limit=config.evidence_structuring.recursion_limit,
        max_tool_calls=config.evidence_structuring.max_tool_calls,
        required_tool=config.evidence_structuring.required_tool,
    ).run(
        request_id=evidence_result.request_id,
        raw_evidence_index=raw_evidence_index_path,
        result_sink=evidence_structuring_results,
    )
    if structuring_result is None:
        artifact_store.persist_evidence_processing_telemetry(
            evidence_result.request_id, collection_telemetry.events
        )
        print("phase=phase-03")
        print("status=blocked")
        print("reason=evidence_subagent_invocation_failed")
        print("parent_agent=mysql_analyzer")
        print("subagent=evidence_structuring")
        print(f"raw_evidence_artifact={raw_evidence_index_path}")
        return 1
    artifact_store.persist_evidence_processing_telemetry(
        structuring_result.request_id, collection_telemetry.events
    )

    print(f"phase={structuring_result.phase}")
    print(f"status={structuring_result.status}")
    print("parent_agent=mysql_analyzer")
    print("subagent=evidence_structuring")
    print(f"raw_evidence_artifact={raw_evidence_index_path}")
    if structuring_result.bundle is not None:
        print(f"evidence_items={len(structuring_result.bundle.evidence_items)}")
        print(f"quality={structuring_result.bundle.quality.get('overall_status')}")
    if structuring_result.bundle_artifact is not None:
        print(f"artifact={structuring_result.bundle_artifact.path}")
    elif structuring_result.artifacts:
        print(f"artifact={structuring_result.artifacts[-1].path}")
    if structuring_result.blocking_issues:
        print(f"blocking_issues={';'.join(structuring_result.blocking_issues)}")
    if structuring_result.status != "evidence_bundle_created":
        return 1

    if structuring_result.request_id != evidence_result.request_id:
        collection_telemetry.emit(
            event_type="artifact_lineage_checked",
            stage="lineage",
            message="EvidenceBundle request_id does not match RawEvidence index request_id",
            attributes={
                "request_id": evidence_result.request_id,
                "raw_evidence_index": str(raw_evidence_index_path),
                "evidence_bundle_artifact": str(structuring_result.bundle_artifact.path)
                if structuring_result.bundle_artifact is not None
                else None,
                "lineage_check_status": "failed",
                "reason": "artifact_lineage_mismatch",
            },
        )
        artifact_store.persist_evidence_processing_telemetry(
            evidence_result.request_id, collection_telemetry.events
        )
        print("phase=phase-03")
        print("status=blocked")
        print("reason=artifact_lineage_mismatch")
        print(f"raw_evidence_artifact={raw_evidence_index_path}")
        if structuring_result.bundle_artifact is not None:
            print(f"evidence_bundle_artifact={structuring_result.bundle_artifact.path}")
        return 1

    if _stop_after_phase(result.normalized_request) == "phase-03":
        return 0

    if structuring_result.bundle_artifact is None:
        print("phase=phase-04")
        print("status=blocked")
        print("reason=evidence_bundle_artifact_missing")
        return 1

    phase04_result = Phase04AnalysisPipeline(
        artifact_store=artifact_store,
        telemetry=TelemetryRecorder(),
        mysql_analyzer_runtime=analyzer_runtime,
        validation_runtime=runtime_factory.create_validation_runtime(
            _validation_skill(config.runtime.skills_dir)
        ),
        repo_dir=config.runtime.repo_dir,
        max_prompt_chars=config.phase04.max_prompt_chars,
        findings_generation_timeout_seconds=(
            config.phase04.findings_generation_timeout_seconds
        ),
        validation_timeout_seconds=config.phase04.validation_timeout_seconds,
        max_findings_generation_retries=(
            config.phase04.max_findings_generation_retries
        ),
        max_validation_retries=config.phase04.max_validation_retries,
        max_agent_iterations=config.phase04.max_agent_iterations,
        max_findings=config.phase04.max_findings,
        model_name=config.model.model_name,
    ).run(
        structuring_result.bundle_artifact.path,
        expected_request_id=evidence_result.request_id,
    )
    _print_phase04_result(phase04_result)
    return 0 if phase04_result.status in {
        "analysis_completed",
        "analysis_completed_with_warnings",
        "human_review_required",
    } else 1


def _collection_summary(raw_evidence) -> dict[str, int]:
    from dbkit.schemas.evidence import collection_summary

    return collection_summary(tuple(raw_evidence))


def _raw_evidence_index_artifacts(evidence_result) -> list:
    return [
        artifact for artifact in evidence_result.artifacts
        if artifact.kind == "RawEvidenceIndex"
    ]


def _print_collection_summary(evidence_result, input_mode: str) -> None:
    print(f"input_mode={input_mode}")
    print(f"raw_evidence_count={len(evidence_result.raw_evidence)}")
    summary = _collection_summary(evidence_result.raw_evidence)
    for key in (
        "collected",
        "partial",
        "failed",
        "blocked",
        "not_available",
        "not_configured",
        "not_implemented",
    ):
        print(f"{key}={summary.get(key + '_count', 0)}")


def _print_collection_artifact(evidence_result) -> None:
    index_artifacts = _raw_evidence_index_artifacts(evidence_result)
    if index_artifacts:
        print(f"artifact={index_artifacts[0].path}")
    elif evidence_result.artifacts:
        print(f"artifact={evidence_result.artifacts[-1].path}")


def _print_collection_errors(evidence_result) -> None:
    errors: list[str] = []
    for item in evidence_result.raw_evidence:
        collection = getattr(item, "collection", {}) or {}
        if collection.get("status") not in {"failed", "blocked"}:
            continue
        source = getattr(item, "source", {}) or {}
        tool_name = source.get("tool_name") or getattr(item, "evidence_type", "unknown")
        for error in collection.get("errors") or []:
            text = str(error).strip().splitlines()[0]
            if text:
                errors.append(f"{tool_name}: {text}")
    unique_errors = list(dict.fromkeys(errors))[:5]
    if unique_errors:
        print(f"collection_errors={' | '.join(unique_errors)}")


def _parse_args(args: list[str]) -> tuple[Path, bool, Path | None, Path | None, list[str]]:
    config_path = DEFAULT_CONFIG_PATH
    interactive = False
    raw_evidence_index_path: Path | None = None
    evidence_bundle_path: Path | None = None
    prompt_args: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--config":
            if index + 1 >= len(args):
                raise ValueError("--config requires a path")
            config_path = Path(args[index + 1])
            index += 2
        elif arg == "--interactive":
            interactive = True
            index += 1
        elif arg == "--from-raw-evidence":
            if index + 1 >= len(args):
                raise ValueError("--from-raw-evidence requires a path")
            raw_evidence_index_path = Path(args[index + 1])
            index += 2
        elif arg == "--from-evidence-bundle":
            if index + 1 >= len(args):
                raise ValueError("--from-evidence-bundle requires a path")
            evidence_bundle_path = Path(args[index + 1])
            index += 2
        else:
            prompt_args.extend(args[index:])
            break
    return config_path, interactive, raw_evidence_index_path, evidence_bundle_path, prompt_args


def _print_help() -> None:
    print(f"DBKit {__version__}")
    print("usage: python3.11 main.py [--config PATH] [--interactive] [--from-raw-evidence PATH] [--from-evidence-bundle PATH] [PROMPT]")
    print()
    print("Normal workflow runs Intake -> MySQL evidence planning -> collection -> evidence_structuring -> findings validation verdict.")
    print("--from-raw-evidence replays EvidenceBundle creation from an existing raw evidence index.")
    print("--from-evidence-bundle replays Phase-04 findings, validation, verdict, and summary from an existing EvidenceBundle.")


def _validation_skill(skills_dir: Path) -> str:
    path = skills_dir / "validation" / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(f"Validation skill not found: {path}")
    return path.read_text(encoding="utf-8")


def _print_phase04_result(result) -> None:
    print(f"phase={result.phase}")
    print(f"status={result.status}")
    print("target_agent=mysql_analyzer")
    if result.verdict is not None:
        print(f"overall_severity={result.verdict.get('overall_severity')}")
        print(f"overall_confidence={result.verdict.get('overall_confidence')}")
    if result.metadata.get("reason"):
        print(f"reason={result.metadata.get('reason')}")
    if result.metadata.get("input_evidence_bundle"):
        print(f"evidence_bundle_artifact={result.metadata.get('input_evidence_bundle')}")
    for artifact in result.artifacts:
        if artifact.kind == "FindingsDraft":
            print(f"findings_artifact={artifact.path}")
        elif artifact.kind == "ValidationResult":
            print(f"validation_artifact={artifact.path}")
        elif artifact.kind == "Verdict":
            print(f"verdict_artifact={artifact.path}")
        elif artifact.kind == "Summary":
            print(f"summary_artifact={artifact.path}")
        elif artifact.kind == "AnalysisTimeout":
            print(f"timeout_artifact={artifact.path}")
        elif artifact.kind == "AnalysisTelemetry":
            print(f"analysis_telemetry={artifact.path}")
    if result.blocking_issues:
        print(f"blocking_issues={';'.join(result.blocking_issues)}")


def _stop_after_phase(normalized_request) -> str | None:
    value = (normalized_request.metadata or {}).get("stop_after_phase")
    if value in {"phase-02.1", "phase-03"}:
        return str(value)
    return None


def _read_supplement(rendered_user_message: str) -> str:
    print()
    print(rendered_user_message)
    print()
    print("请补充缺失信息：")
    return input("> ")
