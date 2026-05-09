import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dbkit.cli import main as cli_main
from dbkit.runtime.analysis import Phase04AnalysisPipeline
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.observability import TelemetryRecorder
from dbkit.schemas.evidence import EvidencePipelineResult, EvidenceStructuringResult
from dbkit.schemas.runtime import ArtifactRecord, NormalizedRequest, RuntimeResult
from dbkit.tools.normalize_request import normalize_request


class Phase04FindingsValidationTest(unittest.TestCase):
    def test_replay_from_evidence_bundle_writes_findings_validation_verdict_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle_path = _write_evidence_bundle(root)
            telemetry = TelemetryRecorder()

            result = Phase04AnalysisPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=telemetry,
                mysql_analyzer_runtime=FakeAnalyzerRuntime(_findings_payload("req_phase04")),
                validation_runtime=FakeValidationRuntime(_validation_payload("req_phase04")),
            ).run(bundle_path)

            findings = _artifact_payload(result.artifacts, "FindingsDraft")
            validation = _artifact_payload(result.artifacts, "ValidationResult")
            verdict = _artifact_payload(result.artifacts, "Verdict")
            summary = _artifact_text(result.artifacts, "Summary")

        self.assertEqual(result.status, "analysis_completed_with_warnings")
        self.assertEqual(result.phase, "phase-04")
        self.assertEqual(findings["mode"], "findings_generation")
        self.assertEqual(findings["target_agent"], "mysql_analyzer")
        self.assertEqual(findings["findings"][0]["evidence_refs"][0]["evidence_id"], "ev_error_log")
        self.assertEqual(validation["validated_findings"][0]["validation_status"], "passed")
        self.assertEqual(verdict["status"], "analysis_completed_with_warnings")
        self.assertEqual(verdict["primary_findings"], ["finding_aborted_connections"])
        self.assertIn("# DBKit MySQL Analysis Summary", summary)
        self.assertIn("Large number of aborted MySQL connections observed", summary)
        serialized = json.dumps(
            {
                "findings": findings,
                "validation": validation,
                "verdict": verdict,
                "summary": summary,
            },
            ensure_ascii=False,
        )
        self.assertNotIn("Root@1234", serialized)
        event_types = [event.event_type for event in result.telemetry]
        for expected in (
            "phase04_started",
            "evidence_bundle_loaded",
            "mysql_analyzer_findings_generation_started",
            "mysql_analyzer_findings_generation_completed",
            "findings_draft_created",
            "validation_started",
            "validation_completed",
            "verdict_created",
            "summary_created",
            "phase04_completed",
        ):
            self.assertIn(expected, event_types)
        self.assertEqual(result.telemetry[0].attributes["request_id"], "req_phase04")
        self.assertNotIn(
            "unknown",
            [
                event.attributes.get("request_id")
                for event in result.telemetry
                if "request_id" in event.attributes
            ],
        )

    def test_findings_without_existing_evidence_refs_are_blocked_by_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle_path = _write_evidence_bundle(root)
            bad_findings = _findings_payload("req_phase04")
            bad_findings["findings"][0]["evidence_refs"] = [
                {"evidence_id": "ev_missing", "evidence_type": "mysql.error_log", "raw_refs": []}
            ]

            result = Phase04AnalysisPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
                mysql_analyzer_runtime=FakeAnalyzerRuntime(bad_findings),
                validation_runtime=FakeValidationRuntime(_validation_payload("req_phase04")),
            ).run(bundle_path)

            validation = _artifact_payload(result.artifacts, "ValidationResult")
            verdict = _artifact_payload(result.artifacts, "Verdict")

        self.assertEqual(result.status, "validation_failed")
        self.assertEqual(validation["blocked_findings"][0]["finding_id"], "finding_aborted_connections")
        self.assertEqual(validation["blocked_findings"][0]["reason"], "evidence_ref_not_found")
        self.assertEqual(verdict["primary_findings"], [])
        self.assertNotIn("finding_aborted_connections", verdict["primary_findings"])
        self.assertIn("validation_failed", [event.event_type for event in result.telemetry])

    def test_phase04_blocks_evidence_bundle_request_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle_path = _write_evidence_bundle(root, request_id="req_bundle")

            result = Phase04AnalysisPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
                mysql_analyzer_runtime=FakeAnalyzerRuntime(_findings_payload("req_workflow")),
                validation_runtime=FakeValidationRuntime(_validation_payload("req_workflow")),
            ).run(bundle_path, expected_request_id="req_workflow")

        self.assertEqual(result.status, "blocked")
        self.assertIn("artifact_lineage_mismatch", result.blocking_issues)
        self.assertIn("phase04_failed", [event.event_type for event in result.telemetry])

    def test_category_aliases_are_normalized_before_findings_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle_path = _write_evidence_bundle(root)
            findings = _findings_payload("req_phase04")
            findings["findings"][0]["category"] = "connectivity"

            result = Phase04AnalysisPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
                mysql_analyzer_runtime=FakeAnalyzerRuntime(findings),
                validation_runtime=FakeValidationRuntime(_validation_payload("req_phase04")),
            ).run(bundle_path)

            findings_payload = _artifact_payload(result.artifacts, "FindingsDraft")

        self.assertEqual(result.status, "analysis_completed_with_warnings")
        self.assertEqual(findings_payload["findings"][0]["category"], "connection")
        category_events = [
            event for event in result.telemetry
            if event.event_type == "finding_category_normalized"
        ]
        self.assertEqual(category_events[0].attributes["original_category"], "connectivity")
        self.assertEqual(category_events[0].attributes["normalized_category"], "connection")

    def test_unknown_invalid_category_retries_once_then_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle_path = _write_evidence_bundle(root)
            first_findings = _findings_payload("req_phase04")
            first_findings["findings"][0]["category"] = "network_weirdness"
            second_findings = _findings_payload("req_phase04")
            second_findings["findings"][0]["category"] = "also_bad"
            analyzer = FakeSequenceAnalyzerRuntime([first_findings, second_findings])

            result = Phase04AnalysisPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
                mysql_analyzer_runtime=analyzer,
                validation_runtime=FakeValidationRuntime(_validation_payload("req_phase04")),
            ).run(bundle_path)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(len(analyzer.invocations), 2)
        self.assertTrue(any("findings_draft_invalid" in issue for issue in result.blocking_issues))
        self.assertIn("findings_generation_retry_requested", [event.event_type for event in result.telemetry])

    def test_confidence_numeric_string_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle_path = _write_evidence_bundle(root)
            findings = _findings_payload("req_phase04")
            findings["findings"][0]["confidence"] = "0.78"

            result = Phase04AnalysisPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
                mysql_analyzer_runtime=FakeAnalyzerRuntime(findings),
                validation_runtime=FakeValidationRuntime(_validation_payload("req_phase04")),
            ).run(bundle_path)

            findings_payload = _artifact_payload(result.artifacts, "FindingsDraft")

        self.assertEqual(result.status, "analysis_completed_with_warnings")
        self.assertEqual(findings_payload["findings"][0]["confidence"], 0.78)
        confidence_events = [
            event for event in result.telemetry
            if event.event_type == "finding_confidence_normalized"
        ]
        self.assertEqual(confidence_events[0].attributes["confidence_normalization_status"], "normalized")

    def test_confidence_label_triggers_retry_and_valid_retry_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle_path = _write_evidence_bundle(root)
            first_findings = _findings_payload("req_phase04")
            first_findings["findings"][0]["confidence"] = "high"
            retry_findings = _findings_payload("req_phase04")
            retry_findings["findings"][0]["confidence"] = 0.78
            analyzer = FakeSequenceAnalyzerRuntime([first_findings, retry_findings])

            result = Phase04AnalysisPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
                mysql_analyzer_runtime=analyzer,
                validation_runtime=FakeValidationRuntime(_validation_payload("req_phase04")),
            ).run(bundle_path)

            findings_payload = _artifact_payload(result.artifacts, "FindingsDraft")
            invalid_payload = _artifact_payload(result.artifacts, "InvalidFindingsDraft")

        self.assertEqual(result.status, "analysis_completed_with_warnings")
        self.assertEqual(len(analyzer.invocations), 2)
        self.assertEqual(findings_payload["findings"][0]["confidence"], 0.78)
        self.assertIn(
            "Finding.confidence must be a number between 0.0 and 1.0, got string 'high'",
            invalid_payload["validation_errors"],
        )
        self.assertEqual(invalid_payload["retry_attempt"], 0)
        event_types = [event.event_type for event in result.telemetry]
        self.assertIn("findings_draft_schema_validation_failed", event_types)
        self.assertIn("findings_generation_retry_requested", event_types)
        self.assertIn("findings_generation_retry_completed", event_types)
        self.assertIn("findings_draft_schema_validation_passed", event_types)

    def test_confidence_label_retry_still_invalid_blocks_with_clear_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle_path = _write_evidence_bundle(root)
            first_findings = _findings_payload("req_phase04")
            first_findings["findings"][0]["confidence"] = "high"
            second_findings = _findings_payload("req_phase04")
            second_findings["findings"][0]["confidence"] = "medium"
            analyzer = FakeSequenceAnalyzerRuntime([first_findings, second_findings])

            result = Phase04AnalysisPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
                mysql_analyzer_runtime=analyzer,
                validation_runtime=FakeValidationRuntime(_validation_payload("req_phase04")),
            ).run(bundle_path)

            invalid_payload = _artifact_payload(result.artifacts, "InvalidFindingsDraft")

        self.assertEqual(result.status, "blocked")
        self.assertEqual(len(analyzer.invocations), 2)
        self.assertIn(
            "findings_draft_invalid: Finding.confidence must be a number between 0.0 and 1.0, got string 'medium'",
            result.blocking_issues,
        )
        self.assertEqual(invalid_payload["retry_attempt"], 1)
        self.assertNotIn("Root@1234", json.dumps(invalid_payload, ensure_ascii=False))

    def test_cli_from_evidence_bundle_replay_runs_phase04(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle_path = _write_evidence_bundle(root)
            config_path = _write_config(root)
            stdout = io.StringIO()

            with patch("dbkit.cli.build_agent_model", return_value=object()), patch(
                "dbkit.cli.DeepAgentsRuntimeFactory.create_mysql_analyzer_runtime",
                return_value=FakeAnalyzerRuntime(_findings_payload("req_phase04")),
            ), patch(
                "dbkit.cli.DeepAgentsRuntimeFactory.create_validation_runtime",
                return_value=FakeValidationRuntime(_validation_payload("req_phase04")),
            ), contextlib.redirect_stdout(stdout):
                exit_code = cli_main(
                    [
                        "--config",
                        str(config_path),
                        "--from-evidence-bundle",
                        str(bundle_path),
                    ]
                )

            output = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertIn("phase=phase-04", output)
        self.assertIn("status=analysis_completed_with_warnings", output)
        self.assertIn("target_agent=mysql_analyzer", output)
        self.assertIn("findings_artifact=", output)
        self.assertIn("validation_artifact=", output)
        self.assertIn("verdict_artifact=", output)
        self.assertIn("summary_artifact=", output)

    def test_cli_stops_after_raw_collection_when_requested(self) -> None:
        exit_code, output = _run_normal_cli_with_stop_after_phase("phase-02.1")

        self.assertEqual(exit_code, 0)
        self.assertIn("phase=phase-02.1", output)
        self.assertIn("status=raw_evidence_collected", output)
        self.assertNotIn("phase=phase-03", output)
        self.assertNotIn("phase=phase-04", output)

    def test_cli_stops_after_evidence_bundle_when_requested(self) -> None:
        exit_code, output = _run_normal_cli_with_stop_after_phase("phase-03")

        self.assertEqual(exit_code, 0)
        self.assertIn("phase=phase-03", output)
        self.assertIn("status=evidence_bundle_created", output)
        self.assertNotIn("phase=phase-04", output)

    def test_normal_cli_workflow_continues_from_evidence_bundle_to_phase04(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bundle_path = _write_evidence_bundle(root)
            raw_index_path = root / ".dbkit" / "artifacts" / "req_phase04.raw-evidence-index.json"
            raw_index_path.write_text(
                json.dumps({"request_id": "req_phase04", "raw_evidence": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            config_path = _write_config(root)
            normalized = NormalizedRequest(
                request_id="req_phase04",
                original_input="分析 MySQL",
                redacted_input="分析 MySQL",
                target_domain="mysql",
                requested_capability="analysis",
                missing_fields=(),
                phase="phase-02.1",
                target_agent="mysql_analyzer",
                input_mode="live_collection",
            )
            runtime_result = RuntimeResult(
                normalized_request=normalized,
                route_decision=None,
                artifacts=(),
                telemetry=(),
                deepagents_runtime_ready=True,
                blocked=False,
            )
            evidence_result = EvidencePipelineResult(
                request_id="req_phase04",
                phase="phase-02.1",
                status="raw_evidence_collected",
                evidence_request=None,
                collection_plan=None,
                raw_evidence=(),
                artifacts=(ArtifactRecord(kind="RawEvidenceIndex", path=raw_index_path),),
                telemetry=(),
            )
            structuring_result = EvidenceStructuringResult(
                request_id="req_phase04",
                phase="phase-03",
                status="evidence_bundle_created",
                bundle=None,
                bundle_artifact=ArtifactRecord(kind="EvidenceBundle", path=bundle_path),
                artifacts=(ArtifactRecord(kind="EvidenceBundle", path=bundle_path),),
                telemetry=(),
            )
            stdout = io.StringIO()

            with patch("dbkit.cli.build_agent_model", return_value=object()), patch(
                "dbkit.cli.Orchestrator.run",
                return_value=runtime_result,
            ), patch(
                "dbkit.cli.EvidencePipeline.run",
                return_value=evidence_result,
            ), patch(
                "dbkit.cli.EvidenceStructuringDelegator.run",
                return_value=structuring_result,
            ), patch(
                "dbkit.cli.DeepAgentsRuntimeFactory.create_mysql_analyzer_runtime",
                return_value=FakeAnalyzerRuntime(_findings_payload("req_phase04")),
            ), patch(
                "dbkit.cli.DeepAgentsRuntimeFactory.create_validation_runtime",
                return_value=FakeValidationRuntime(_validation_payload("req_phase04")),
            ), contextlib.redirect_stdout(stdout):
                exit_code = cli_main(
                    [
                        "--config",
                        str(config_path),
                        "请分析 MySQL",
                    ]
                )

            output = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertIn("phase=phase-04", output)
        self.assertIn("status=analysis_completed_with_warnings", output)
        self.assertIn("findings_artifact=", output)
        self.assertNotIn("status=raw_evidence_collected", output)

    def test_normal_cli_workflow_blocks_when_evidence_bundle_lineage_differs(self) -> None:
        exit_code, output = _run_normal_cli_with_stop_after_phase(
            None,
            evidence_bundle_request_id="req_other",
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("phase=phase-03", output)
        self.assertIn("reason=artifact_lineage_mismatch", output)
        self.assertNotIn("phase=phase-04", output)

    def test_mysql_analyzer_and_validation_skills_define_phase04_contracts(self) -> None:
        mysql_skill = Path("skills/mysql-analyzer/SKILL.md").read_text(encoding="utf-8")
        intake_skill = Path("skills/intake/SKILL.md").read_text(encoding="utf-8")
        validation_skill = Path("skills/validation/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Findings Generation Mode", mysql_skill)
        self.assertIn("EvidenceBundle Input Contract", mysql_skill)
        self.assertIn("Validation Handoff", mysql_skill)
        self.assertIn("FindingsDraft", mysql_skill)
        self.assertIn("Finding.category", mysql_skill)
        self.assertIn("Finding.confidence", mysql_skill)
        self.assertIn("Confidence must never be", mysql_skill)
        self.assertIn("Execution Boundary", intake_skill)
        self.assertIn("stop_after_phase", intake_skill)
        self.assertIn("Validation Agent", validation_skill)
        self.assertIn("evidence_refs", validation_skill)
        self.assertIn("ValidationResult", validation_skill)

    def test_normalize_request_preserves_execution_boundary_from_intake_json(self) -> None:
        normalized = normalize_request(
            "当前阶段只做 evidence planning 和 raw evidence collection，不要做根因分析",
            llm_json={
                "target_agent": "mysql_analyzer",
                "target_domain": "mysql",
                "task_type": "alert_analysis",
                "input_mode": "live_collection",
                "execution_goal": "evidence_collection_only",
                "stop_after_phase": "phase-02.1",
                "target": {"type": "mysql", "host": "127.0.0.1", "username": "root", "password_ref": "<SECRET_REF:x>"},
                "collection_policy": {
                    "allow_live_collection": True,
                    "allow_mysql_login": True,
                    "allow_ssh": False,
                    "allow_metrics_query": False,
                },
                "event": {"event_time": "2026-05-09T17:00:00+08:00"},
            },
        )

        self.assertEqual(normalized.metadata["execution_goal"], "evidence_collection_only")
        self.assertEqual(normalized.metadata["stop_after_phase"], "phase-02.1")


class FakeAnalyzerRuntime:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.invocations: list[dict] = []

    def invoke(self, payload: dict) -> dict:
        self.invocations.append(payload)
        return {"messages": [{"role": "assistant", "content": json.dumps(self.payload, ensure_ascii=False)}]}


class FakeSequenceAnalyzerRuntime:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.invocations: list[dict] = []

    def invoke(self, payload: dict) -> dict:
        self.invocations.append(payload)
        response = self.payloads.pop(0)
        return {"messages": [{"role": "assistant", "content": json.dumps(response, ensure_ascii=False)}]}


class FakeValidationRuntime:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.invocations: list[dict] = []

    def invoke(self, payload: dict) -> dict:
        self.invocations.append(payload)
        return {"messages": [{"role": "assistant", "content": json.dumps(self.payload, ensure_ascii=False)}]}


def _write_evidence_bundle(root: Path, *, request_id: str = "req_phase04") -> Path:
    artifacts = root / ".dbkit" / "artifacts"
    artifacts.mkdir(parents=True)
    bundle = {
        "request_id": request_id,
        "phase": "phase-03",
        "bundle_id": "evb_req_phase04",
        "input_raw_evidence_index": ".dbkit/artifacts/req_phase04.raw-evidence-index.json",
        "source_raw_evidence_count": 2,
        "processed_raw_evidence_count": 2,
        "time_window": {
            "start": "2026-05-09T11:00:00+08:00",
            "end": "2026-05-09T18:00:00+08:00",
        },
        "evidence_items": [
            {
                "evidence_id": "ev_error_log",
                "raw_evidence_id": "rawev_error_log",
                "evidence_type": "mysql.error_log",
                "source": {"kind": "ssh", "tool_name": "collect_mysql_error_log"},
                "time_range": {"start": "2026-05-09T11:00:00+08:00", "end": "2026-05-09T18:00:00+08:00"},
                "summary": "Error log contains 404 Aborted connection events inside the requested time window.",
                "structured_payload": {
                    "retained_lines": 404,
                    "top_patterns": [
                        {
                            "pattern": "Note Aborted connection",
                            "count": 404,
                            "semantic_hint": "aborted_connection",
                            "operational_relevance": "high",
                            "raw_refs": [
                                {
                                    "content_ref": ".dbkit/artifacts/raw/rawev_error_log.txt",
                                    "line_start": 1,
                                    "line_end": 404,
                                }
                            ],
                        }
                    ],
                },
                "raw_refs": [
                    {
                        "content_ref": ".dbkit/artifacts/raw/rawev_error_log.txt",
                        "line_start": 1,
                        "line_end": 404,
                    }
                ],
                "quality_flags": [],
                "llm_safe": True,
            },
            {
                "evidence_id": "ev_status",
                "raw_evidence_id": "rawev_status",
                "evidence_type": "mysql.runtime_status",
                "source": {"kind": "mysql", "tool_name": "collect_mysql_runtime_status"},
                "time_range": {},
                "summary": "Runtime status contains selected counters.",
                "structured_payload": {
                    "selected_counters": {
                        "Aborted_clients": 404,
                        "Aborted_connects": 0,
                        "Threads_connected": 32,
                    }
                },
                "raw_refs": [{"content_ref": ".dbkit/artifacts/raw/rawev_status.json"}],
                "quality_flags": [],
                "llm_safe": True,
            },
        ],
        "coverage": {"unavailable_evidence": [{"evidence_type": "mysql.slow_log", "reason": "slow_query_log_disabled"}]},
        "quality": {"overall_status": "usable_with_warnings", "warnings": ["mysql.slow_log not available: slow_query_log_disabled"], "llm_safe": True},
        "processing_summary": {"estimated_tokens_after": 512},
        "skipped_raw_evidence": [],
        "metadata": {"subagent": "evidence_structuring", "parent_agent": "mysql_analyzer"},
    }
    path = artifacts / f"{request_id}.evidence-bundle.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _findings_payload(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "phase": "phase-04",
        "mode": "findings_generation",
        "target_agent": "mysql_analyzer",
        "input_evidence_bundle": ".dbkit/artifacts/req_phase04.evidence-bundle.json",
        "findings": [
            {
                "finding_id": "finding_aborted_connections",
                "title": "Large number of aborted MySQL connections observed",
                "category": "connection",
                "severity": "medium",
                "confidence": 0.78,
                "status": "candidate",
                "statement": "The error log contains repeated aborted connection events during the incident window.",
                "evidence_refs": [
                    {
                        "evidence_id": "ev_error_log",
                        "evidence_type": "mysql.error_log",
                        "raw_refs": [
                            {
                                "content_ref": ".dbkit/artifacts/raw/rawev_error_log.txt",
                                "line_start": 1,
                                "line_end": 404,
                            }
                        ],
                    },
                    {
                        "evidence_id": "ev_status",
                        "evidence_type": "mysql.runtime_status",
                        "raw_refs": [{"content_ref": ".dbkit/artifacts/raw/rawev_status.json"}],
                    },
                ],
                "supporting_signals": [
                    "mysql.error_log top pattern: aborted_connection",
                    "mysql.runtime_status Aborted_clients is elevated",
                ],
                "contradicting_signals": [],
                "assumptions": [],
                "missing_evidence": [],
                "recommended_next_checks": [
                    "Review client connection lifecycle and connection timeout settings."
                ],
            }
        ],
        "insufficient_evidence": [
            {"evidence_type": "mysql.slow_log", "reason": "slow_query_log_disabled"}
        ],
        "metadata": {"skill": "skills/mysql-analyzer/SKILL.md", "runtime_foundation": "DeepAgents SDK"},
    }


def _validation_payload(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "phase": "phase-04",
        "input_findings_artifact": ".dbkit/artifacts/req_phase04.findings-draft.json",
        "input_evidence_bundle": ".dbkit/artifacts/req_phase04.evidence-bundle.json",
        "validated_findings": [
            {
                "finding_id": "finding_aborted_connections",
                "validation_status": "passed",
                "confidence_after_validation": 0.76,
                "evidence_ref_check": "passed",
                "support_check": "passed",
                "contradiction_check": "none",
                "validation_notes": [],
            }
        ],
        "blocked_findings": [],
        "downgraded_findings": [],
        "requires_human_review": False,
        "validation_summary": {"passed": 1, "blocked": 0, "downgraded": 0},
    }


def _artifact_payload(artifacts, kind: str) -> dict:
    artifact = [item for item in artifacts if item.kind == kind][0]
    return json.loads(artifact.path.read_text(encoding="utf-8"))


def _artifact_text(artifacts, kind: str) -> str:
    artifact = [item for item in artifacts if item.kind == kind][0]
    return artifact.path.read_text(encoding="utf-8")


def _write_config(root: Path) -> Path:
    config_path = root / "config.yaml"
    config_path.write_text(
        f"""
model:
  provider_kind: openai_compatible
  model_name: test-model
  base_url: https://example.invalid
  api_key: test-key
runtime:
  artifact_dir: {root / ".dbkit" / "artifacts"}
  invoke_llm: false
  interactive: false
  repo_dir: .
  workspace_dir: .
  skills_dir: skills
  agents_dir: agents
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _run_normal_cli_with_stop_after_phase(
    stop_after_phase: str | None,
    *,
    evidence_bundle_request_id: str = "req_phase04",
) -> tuple[int, str]:
    root = Path(tempfile.mkdtemp())
    bundle_path = _write_evidence_bundle(root, request_id=evidence_bundle_request_id)
    raw_index_path = root / ".dbkit" / "artifacts" / "req_phase04.raw-evidence-index.json"
    raw_index_path.write_text(
        json.dumps({"request_id": "req_phase04", "raw_evidence": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    config_path = _write_config(root)
    metadata = {}
    if stop_after_phase is not None:
        metadata = {
            "execution_goal": "evidence_collection_only"
            if stop_after_phase == "phase-02.1"
            else "evidence_bundle_only",
            "stop_after_phase": stop_after_phase,
        }
    normalized = NormalizedRequest(
        request_id="req_phase04",
        original_input="分析 MySQL",
        redacted_input="分析 MySQL",
        target_domain="mysql",
        requested_capability="analysis",
        missing_fields=(),
        phase="phase-02.1",
        target_agent="mysql_analyzer",
        input_mode="live_collection",
        metadata=metadata,
    )
    runtime_result = RuntimeResult(
        normalized_request=normalized,
        route_decision=None,
        artifacts=(),
        telemetry=(),
        deepagents_runtime_ready=True,
        blocked=False,
    )
    from dbkit.schemas.evidence import EvidencePipelineResult, EvidenceStructuringResult

    evidence_result = EvidencePipelineResult(
        request_id="req_phase04",
        phase="phase-02.1",
        status="raw_evidence_collected",
        evidence_request=None,
        collection_plan=None,
        raw_evidence=(),
        artifacts=(ArtifactRecord(kind="RawEvidenceIndex", path=raw_index_path),),
        telemetry=(),
    )
    structuring_result = EvidenceStructuringResult(
        request_id=evidence_bundle_request_id,
        phase="phase-03",
        status="evidence_bundle_created",
        bundle=None,
        bundle_artifact=ArtifactRecord(kind="EvidenceBundle", path=bundle_path),
        artifacts=(ArtifactRecord(kind="EvidenceBundle", path=bundle_path),),
        telemetry=(),
    )
    stdout = io.StringIO()
    with patch("dbkit.cli.build_agent_model", return_value=object()), patch(
        "dbkit.cli.Orchestrator.run",
        return_value=runtime_result,
    ), patch(
        "dbkit.cli.EvidencePipeline.run",
        return_value=evidence_result,
    ), patch(
        "dbkit.cli.EvidenceStructuringDelegator.run",
        return_value=structuring_result,
    ), patch(
        "dbkit.cli.DeepAgentsRuntimeFactory.create_mysql_analyzer_runtime",
        return_value=FakeAnalyzerRuntime(_findings_payload("req_phase04")),
    ), patch(
        "dbkit.cli.DeepAgentsRuntimeFactory.create_validation_runtime",
        return_value=FakeValidationRuntime(_validation_payload("req_phase04")),
    ), contextlib.redirect_stdout(stdout):
        exit_code = cli_main(["--config", str(config_path), "请分析 MySQL"])
    return exit_code, stdout.getvalue()
