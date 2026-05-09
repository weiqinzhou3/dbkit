import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from dbkit.cli import main as cli_main
from dbkit.agents.mysql_analyzer import MySQLAnalyzerAgent
from dbkit.runtime.artifacts import ArtifactStore
from dbkit.runtime.evidence_structuring import EvidenceStructuringPipeline
from dbkit.runtime.observability import TelemetryRecorder


class Phase03EvidenceStructuringTest(unittest.TestCase):
    def test_evidence_structuring_is_registered_as_mysql_analyzer_subagent(self) -> None:
        agent = MySQLAnalyzerAgent.from_skills_dir(Path("skills"))
        registration = agent.subagents["evidence_structuring"]

        self.assertEqual(agent.name, "mysql_analyzer")
        self.assertEqual(registration.parent_agent, "mysql_analyzer")
        self.assertEqual(registration.name, "evidence_structuring")
        self.assertEqual(registration.skill_path, Path("skills/evidence/SKILL.md"))
        self.assertTrue(registration.is_tool_allowed("parse_mysql_error_log"))
        self.assertTrue(registration.is_tool_allowed("build_evidence_bundle"))
        self.assertFalse(registration.is_tool_allowed("collect_mysql_error_log"))
        self.assertFalse(registration.is_tool_allowed("read_remote_file"))
        self.assertFalse(registration.is_tool_allowed("kill_mysql_query"))

    def test_raw_evidence_index_becomes_bounded_evidence_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_raw_evidence_fixture(root)
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)

            bundle = json.loads(result.bundle_artifact.path.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "evidence_bundle_created")
        self.assertEqual(bundle["phase"], "phase-03")
        self.assertEqual(bundle["source_raw_evidence_count"], 13)
        self.assertGreaterEqual(bundle["processed_raw_evidence_count"], 10)
        self.assertEqual(bundle["coverage"]["deprecated_evidence_types"], [
            "metrics.mysql_status"
        ])
        self.assertEqual(bundle["coverage"]["unavailable_evidence"][0]["reason"], "slow_query_log_disabled")
        self.assertEqual(bundle["quality"]["overall_status"], "usable_with_warnings")
        self.assertEqual(bundle["metadata"]["subagent"], "evidence_structuring")
        self.assertEqual(bundle["metadata"]["parent_agent"], "mysql_analyzer")
        self.assertEqual(bundle["metadata"]["skill"], "skills/evidence/SKILL.md")
        self.assertEqual(bundle["metadata"]["runtime_foundation"], "DeepAgents SDK")

        evidence_types = {item["evidence_type"] for item in bundle["evidence_items"]}
        for expected in (
            "mysql.processlist",
            "mysql.runtime_status",
            "mysql.innodb_status",
            "mysql.variables",
            "mysql.service_metadata",
            "mysql.log_paths",
            "mysql.error_log",
            "metrics.os_cpu",
            "metrics.os_memory",
            "metrics.os_disk",
            "os.mysql_service_status",
        ):
            self.assertIn(expected, evidence_types)
        self.assertNotIn("metrics.mysql_status", evidence_types)
        self.assertNotIn("mysql.slow_log", evidence_types)

        serialized = json.dumps(bundle, ensure_ascii=False)
        for forbidden in ("root_cause", "findings", "verdict", "final_summary", "recommendations", "Root@1234"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("SHOW GLOBAL STATUS", serialized)
        self.assertLess(bundle["processing_summary"]["estimated_tokens_after"], bundle["processing_summary"]["estimated_tokens_before"])

    def test_mysql_evidence_items_have_expected_structured_payloads_and_raw_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_raw_evidence_fixture(root)
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)
            bundle = json.loads(result.bundle_artifact.path.read_text(encoding="utf-8"))

        by_type = {item["evidence_type"]: item for item in bundle["evidence_items"]}

        processlist = by_type["mysql.processlist"]["structured_payload"]
        self.assertEqual(processlist["total_connections"], 3)
        self.assertEqual(processlist["active_queries"], 2)
        self.assertEqual(processlist["sleeping_connections"], 1)
        self.assertEqual(processlist["top_users"]["app"], 2)

        status = by_type["mysql.runtime_status"]["structured_payload"]
        self.assertEqual(status["selected_counters"]["Threads_running"], 8)
        self.assertEqual(status["status_variable_count"], 4)

        variables = by_type["mysql.variables"]["structured_payload"]
        self.assertEqual(variables["selected_variables"]["max_connections"], "151")
        self.assertEqual(variables["selected_variables"]["slow_query_log"], "OFF")

        error_log = by_type["mysql.error_log"]
        self.assertEqual(error_log["time_range"]["timestamp_parse_status"], "ok")
        self.assertEqual(error_log["structured_payload"]["retained_lines"], 2)
        self.assertEqual(error_log["structured_payload"]["discarded_lines"], 1)
        self.assertTrue(error_log["raw_refs"])

    def test_cli_from_raw_evidence_entrypoint_writes_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_raw_evidence_fixture(root)
            config_path = _write_config(root)
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = cli_main([
                    "--config",
                    str(config_path),
                    "--from-raw-evidence",
                    str(index_path),
                ])

            output = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertIn("phase=phase-03", output)
        self.assertIn("status=evidence_bundle_created", output)
        self.assertIn("subagent=evidence_structuring", output)
        self.assertIn("parent_agent=mysql_analyzer", output)
        self.assertIn("quality=usable_with_warnings", output)
        self.assertIn(".evidence-bundle.json", output)

    def test_phase03_telemetry_records_subagent_delegation_and_tool_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_raw_evidence_fixture(root)
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)
            telemetry_artifact = [
                artifact for artifact in result.artifacts
                if artifact.kind == "EvidenceProcessingTelemetry"
            ][0]
            events = [
                json.loads(line)
                for line in telemetry_artifact.path.read_text(encoding="utf-8").splitlines()
            ]

        event_types = [event["event_type"] for event in events]
        self.assertIn("evidence_subagent_invoked", event_types)
        self.assertIn("evidence_subagent_completed", event_types)
        self.assertIn("deduplication_started", event_types)
        self.assertIn("deduplication_completed", event_types)
        self.assertIn("evidence_artifact_written", event_types)
        for event in events:
            attrs = event.get("attributes") or {}
            self.assertEqual(attrs.get("parent_agent"), "mysql_analyzer")
            self.assertEqual(attrs.get("subagent"), "evidence_structuring")
        tool_names = {
            (event.get("attributes") or {}).get("tool_name")
            for event in events
        }
        self.assertIn("parse_mysql_error_log", tool_names)
        self.assertIn("filter_by_time_window", tool_names)
        self.assertNotIn("collect_mysql_error_log", tool_names)

    def test_missing_content_ref_is_blocked_by_evidence_guardrails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_raw_evidence_fixture(root, missing_content_ref=True)
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)

        self.assertEqual(result.status, "evidence_guardrails_failed")
        self.assertIn("content_ref missing for collected raw evidence", result.blocking_issues[0])

    def test_error_log_parser_filters_multiple_timestamp_formats_and_continuations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_error_log_fixture(
                root,
                (
                    "2026-05-09T11:00:01.123456+08:00 [Warning] Aborted connection 10 from 10.0.0.1:52100\n"
                    "continuation detail for aborted connection\n"
                    "2026-05-09 09:59:59 [ERROR] Outside before window\n"
                    "260509 16:00:01 [Note] MySQL old style timestamp in window thread 12\n"
                    "2026-05-09T19:00:00.000000Z [ERROR] Outside after UTC window\n"
                ),
                source_timezone={"os_timezone_offset": "+0800"},
            )
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)
            bundle = json.loads(result.bundle_artifact.path.read_text(encoding="utf-8"))
            expected_content_ref = _last_error_log_content_ref(root)

        item = bundle["evidence_items"][0]
        payload = item["structured_payload"]
        self.assertEqual(item["evidence_type"], "mysql.error_log")
        self.assertEqual(payload["total_lines"], 5)
        self.assertEqual(payload["parsed_timestamp_lines"], 4)
        self.assertEqual(payload["unparseable_lines"], 1)
        self.assertEqual(payload["retained_lines"], 3)
        self.assertEqual(payload["discarded_lines"], 2)
        self.assertEqual(payload["retained_events"], 2)
        self.assertEqual(payload["discarded_events"], 2)
        self.assertEqual(payload["timestamp_parse_status"], "partial")
        self.assertEqual(payload["time_window_filter_status"], "partial")
        patterns = json.dumps(payload["top_patterns"], ensure_ascii=False)
        self.assertIn("Warning", patterns)
        self.assertIn("Note", patterns)
        self.assertNotIn("Outside before window", patterns)
        self.assertTrue(item["raw_refs"])
        self.assertEqual(item["raw_refs"][0]["content_ref"], expected_content_ref)

    def test_error_log_all_lines_outside_window_still_creates_low_signal_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_error_log_fixture(
                root,
                (
                    "2026-05-09T09:00:00+08:00 [ERROR] before window\n"
                    "2026-05-09T19:00:00+08:00 [ERROR] after window\n"
                ),
            )
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)
            bundle = json.loads(result.bundle_artifact.path.read_text(encoding="utf-8"))

        item = bundle["evidence_items"][0]
        payload = item["structured_payload"]
        self.assertEqual(payload["retained_lines"], 0)
        self.assertEqual(payload["discarded_lines"], 2)
        self.assertEqual(payload["retained_events"], 0)
        self.assertIn("out_of_time_window", item["quality_flags"])
        self.assertIn("low_signal", item["quality_flags"])
        self.assertNotIn("mysql.error_log", json.dumps(bundle["skipped_raw_evidence"], ensure_ascii=False))

    def test_error_log_unparseable_lines_do_not_fail_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_error_log_fixture(
                root,
                "unparseable startup line\nanother unparseable line\n",
            )
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)
            bundle = json.loads(result.bundle_artifact.path.read_text(encoding="utf-8"))

        item = bundle["evidence_items"][0]
        payload = item["structured_payload"]
        self.assertEqual(payload["timestamp_parse_status"], "failed")
        self.assertEqual(payload["time_window_filter_status"], "unavailable")
        self.assertIn("timestamp_parse_failed", item["quality_flags"])
        self.assertIn("parser_partial", item["quality_flags"])
        self.assertNotIn("mysql.error_log parser failed", bundle["quality"]["warnings"])

    def test_error_log_filters_utc_timestamps_against_plus_8_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_error_log_fixture(
                root,
                (
                    "2026-05-09T02:59:59.000000Z [ERROR] outside before utc\n"
                    "2026-05-09T03:00:00.000000Z [ERROR] inside utc lower bound\n"
                    "2026-05-09T10:00:00.000000Z [Warning] inside utc upper bound\n"
                    "2026-05-09T10:00:01.000000Z [ERROR] outside after utc\n"
                ),
            )
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)
            bundle = json.loads(result.bundle_artifact.path.read_text(encoding="utf-8"))

        item = bundle["evidence_items"][0]
        payload = item["structured_payload"]
        self.assertEqual(payload["retained_lines"], 2)
        self.assertEqual(payload["discarded_lines"], 2)
        self.assertEqual(payload["timezone_handling"], "normalized_to_utc")
        self.assertEqual(payload["source_timezone"], "UTC")
        self.assertEqual(item["time_range"]["start_utc"], "2026-05-09T03:00:00+00:00")
        self.assertEqual(item["time_range"]["end_utc"], "2026-05-09T10:00:00+00:00")
        patterns = json.dumps(payload["top_patterns"], ensure_ascii=False)
        self.assertIn("inside utc lower bound", patterns)
        self.assertNotIn("outside before utc", patterns)

    def test_error_log_infers_naive_timestamp_timezone_from_log_timestamps_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_error_log_fixture(
                root,
                (
                    "2026-05-09 02:59:59 [ERROR] outside before inferred utc\n"
                    "2026-05-09 03:00:00 [ERROR] inside inferred utc\n"
                    "2026-05-09 10:00:01 [ERROR] outside after inferred utc\n"
                ),
                source_timezone={"mysql_log_timestamps": "UTC"},
            )
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)
            bundle = json.loads(result.bundle_artifact.path.read_text(encoding="utf-8"))

        item = bundle["evidence_items"][0]
        payload = item["structured_payload"]
        self.assertEqual(payload["retained_lines"], 1)
        self.assertEqual(payload["discarded_lines"], 2)
        self.assertEqual(payload["timezone_handling"], "inferred")
        self.assertEqual(payload["source_timezone"], "UTC")
        patterns = json.dumps(payload["top_patterns"], ensure_ascii=False)
        self.assertIn("inside inferred utc", patterns)
        self.assertNotIn("outside before inferred utc", patterns)

    def test_error_log_naive_timestamp_without_timezone_inference_is_marked_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_error_log_fixture(
                root,
                "2026-05-09 03:00:00 [ERROR] naive timestamp without inference\n",
                source_timezone={},
            )
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)
            bundle = json.loads(result.bundle_artifact.path.read_text(encoding="utf-8"))

        item = bundle["evidence_items"][0]
        payload = item["structured_payload"]
        self.assertEqual(payload["timezone_handling"], "failed")
        self.assertEqual(payload["source_timezone"], "unknown")
        self.assertIn("timezone_inference_failed", item["quality_flags"])

    def test_aborted_connection_pattern_has_evidence_summary_and_semantic_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lines = "".join(
                "2026-05-09T11:00:00+08:00 [Note] Aborted connection "
                f"{index} to db: 'unconnected' user: 'root' host: '10.0.0.1' "
                "(Got an error reading communication packets)\n"
                for index in range(404)
            )
            index_path = _write_error_log_fixture(root, lines)
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)
            bundle = json.loads(result.bundle_artifact.path.read_text(encoding="utf-8"))

        item = bundle["evidence_items"][0]
        payload = item["structured_payload"]
        self.assertIn(
            "Error log contains 404 Aborted connection events inside the requested time window.",
            item["summary"],
        )
        self.assertEqual(len(payload["top_patterns"]), 1)
        pattern = payload["top_patterns"][0]
        self.assertEqual(pattern["count"], 404)
        self.assertEqual(pattern["semantic_hint"], "aborted_connection")
        self.assertEqual(pattern["operational_relevance"], "high")
        serialized = json.dumps(bundle, ensure_ascii=False)
        for forbidden in ("root_cause", "findings", "verdict"):
            self.assertNotIn(forbidden, serialized)

    def test_low_quality_evidence_produces_bundle_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index_path = _write_error_log_fixture(
                root,
                "slow query line without timestamp\n",
                evidence_type="mysql.slow_log",
            )
            result = EvidenceStructuringPipeline(
                artifact_store=ArtifactStore(root / ".dbkit" / "artifacts"),
                telemetry=TelemetryRecorder(),
            ).run(index_path)
            bundle = json.loads(result.bundle_artifact.path.read_text(encoding="utf-8"))

        self.assertEqual(
            bundle["coverage"]["low_quality_evidence"][0]["evidence_type"],
            "mysql.slow_log",
        )
        self.assertIn(
            "mysql.slow_log parsed with low quality: timestamp_parse_failed",
            bundle["quality"]["warnings"],
        )


def _write_raw_evidence_fixture(root: Path, *, missing_content_ref: bool = False) -> Path:
    artifacts = root / ".dbkit" / "artifacts"
    raw_dir = artifacts / "raw"
    raw_dir.mkdir(parents=True)
    request_id = "req_phase03"

    entries = [
        _write_json_raw(raw_dir, request_id, "rawev_processlist", "mysql.processlist", {"sql": "SHOW FULL PROCESSLIST", "rows": [
            {"Id": 1, "User": "app", "Host": "10.0.0.1:52000", "db": "shop", "Command": "Query", "Time": 12, "State": "executing", "Info": "select * from orders"},
            {"Id": 2, "User": "app", "Host": "10.0.0.2:52001", "db": "shop", "Command": "Sleep", "Time": 3, "State": "", "Info": None},
            {"Id": 3, "User": "root", "Host": "localhost", "db": None, "Command": "Query", "Time": 90, "State": "Sending data", "Info": "show processlist"},
        ]}),
        _write_json_raw(raw_dir, request_id, "rawev_status", "mysql.runtime_status", {"sql": "SHOW GLOBAL STATUS", "rows": [
            {"Variable_name": "Threads_connected", "Value": "32"},
            {"Variable_name": "Threads_running", "Value": "8"},
            {"Variable_name": "Connections", "Value": "2048"},
            {"Variable_name": "Slow_queries", "Value": "7"},
        ]}),
        _write_json_raw(raw_dir, request_id, "rawev_innodb", "mysql.innodb_status", {"sql": "SHOW ENGINE INNODB STATUS", "rows": [{"Status": "LATEST DETECTED DEADLOCK\nTRANSACTIONS\nBUFFER POOL AND MEMORY\n"}]}),
        _write_json_raw(raw_dir, request_id, "rawev_variables", "mysql.variables", {"sql": "SHOW GLOBAL VARIABLES", "rows": [
            {"Variable_name": "max_connections", "Value": "151"},
            {"Variable_name": "slow_query_log", "Value": "OFF"},
            {"Variable_name": "slow_query_log_file", "Value": "/var/log/mysql/slow.log"},
            {"Variable_name": "log_error", "Value": "/var/log/mysql/error.log"},
            {"Variable_name": "datadir", "Value": "/var/lib/mysql"},
        ]}),
        _write_json_raw(raw_dir, request_id, "rawev_metadata", "mysql.service_metadata", {"queries": [
            {"sql": "SELECT VERSION()", "rows": [{"VERSION()": "8.0.36"}]},
            {"sql": "SELECT @@hostname, @@port, @@datadir, @@log_error, @@slow_query_log_file", "rows": [{"@@hostname": "mysql-01", "@@port": 3306, "@@datadir": "/var/lib/mysql", "@@log_error": "/var/log/mysql/error.log", "@@slow_query_log_file": "/var/log/mysql/slow.log"}]},
        ]}),
        _write_json_raw(raw_dir, request_id, "rawev_log_paths", "mysql.log_paths", {"error_log_path": "/var/log/mysql/error.log", "slow_log_path": "/var/log/mysql/slow.log", "slow_query_log_enabled": False, "log_output": "FILE", "datadir": "/var/lib/mysql"}),
        _write_text_raw(raw_dir, request_id, "rawev_error_log", "mysql.error_log", "2026-05-09T11:10:00+08:00 [Warning] Aborted connection 10\n2026-05-09T11:12:00+08:00 [ERROR] Too many connections\n2026-05-09T19:00:00+08:00 [ERROR] outside window\n"),
        _raw_index_item(request_id, "rawev_slow_log", "mysql.slow_log", None, status="not_available", reason="slow_query_log_disabled"),
        _write_text_raw(raw_dir, request_id, "rawev_cpu", "metrics.os_cpu", "$ uptime\n11:00 up 1 day, load average: 2.10, 3.20, 4.30\n\n$ vmstat 1 3\nr b swpd free buff cache si so bi bo in cs us sy id wa st\n2 0 0 1024 1 1 0 0 0 0 100 200 20 5 70 5 0\n"),
        _write_text_raw(raw_dir, request_id, "rawev_memory", "metrics.os_memory", "$ free -m\n              total        used        free      shared  buff/cache   available\nMem:           7976        5000        1000          10        1976        2500\nSwap:          2048         128        1920\n"),
        _write_text_raw(raw_dir, request_id, "rawev_disk", "metrics.os_disk", "$ df -h\nFilesystem Size Used Avail Use% Mounted on\n/dev/vda1 100G 85G 15G 85% /\n"),
        _write_text_raw(raw_dir, request_id, "rawev_service", "os.mysql_service_status", "$ systemctl status mysqld --no-pager\nActive: active (running)\n$ ps -ef | grep -E 'mysqld|mysql' | grep -v grep\nmysql 1234 1 /usr/sbin/mysqld\n"),
        _write_json_raw(raw_dir, request_id, "rawev_deprecated_status", "metrics.mysql_status", {"sql": "SHOW GLOBAL STATUS", "rows": [{"Variable_name": "Threads_running", "Value": "8"}]}),
    ]
    if missing_content_ref:
        entries[0]["payload"]["content_ref"] = None

    index = {
        "request_id": request_id,
        "phase": "phase-02.1",
        "raw_evidence_count": len(entries),
        "raw_evidence": entries,
    }
    index_path = artifacts / f"{request_id}.raw-evidence-index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return index_path


def _write_json_raw(raw_dir: Path, request_id: str, raw_id: str, evidence_type: str, data: dict) -> dict:
    path = raw_dir / f"{raw_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return _raw_index_item(request_id, raw_id, evidence_type, path, bytes_count=path.stat().st_size)


def _write_text_raw(raw_dir: Path, request_id: str, raw_id: str, evidence_type: str, content: str) -> dict:
    path = raw_dir / f"{raw_id}.txt"
    path.write_text(content, encoding="utf-8")
    return _raw_index_item(request_id, raw_id, evidence_type, path, bytes_count=path.stat().st_size, line_count=len(content.splitlines()))


def _write_error_log_fixture(
    root: Path,
    content: str,
    *,
    source_timezone: dict | None = None,
    evidence_type: str = "mysql.error_log",
) -> Path:
    artifacts = root / ".dbkit" / "artifacts"
    raw_dir = artifacts / "raw"
    raw_dir.mkdir(parents=True)
    request_id = "req_error_log"
    entry = _write_text_raw(raw_dir, request_id, "rawev_error_log_only", evidence_type, content)
    entry["metadata"]["collection_strategy"] = "bounded_tail_fallback"
    entry["metadata"]["time_window_aware"] = False
    entry["metadata"]["time_window_coverage"] = "unknown"
    entry["metadata"]["coverage_warning"] = "tail_lines may not cover requested time_window"
    if source_timezone is not None:
        entry["metadata"]["source_timezone"] = source_timezone
    index = {
        "request_id": request_id,
        "phase": "phase-02.1",
        "raw_evidence_count": 1,
        "raw_evidence": [entry],
    }
    index_path = artifacts / f"{request_id}.raw-evidence-index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return index_path


def _last_error_log_content_ref(root: Path) -> str:
    raw_dir = root / ".dbkit" / "artifacts" / "raw"
    return str(next(raw_dir.glob("rawev_error_log_only*.txt")))


def _raw_index_item(
    request_id: str,
    raw_id: str,
    evidence_type: str,
    content_ref: Path | None,
    *,
    status: str = "collected",
    reason: str | None = None,
    bytes_count: int = 0,
    line_count: int = 0,
) -> dict:
    collection = {"status": status, "errors": []}
    if reason:
        collection["reason"] = reason
    return {
        "raw_evidence_id": raw_id,
        "request_id": request_id,
        "evidence_type": evidence_type,
        "source": {"kind": "mysql" if evidence_type.startswith("mysql.") else "ssh", "tool_name": f"tool_{raw_id}", "path": str(content_ref) if content_ref else None},
        "collection": collection,
        "payload": {"content_ref": str(content_ref) if content_ref else None, "bytes": bytes_count, "line_count": line_count},
        "metadata": {"time_window": {"start": "2026-05-09T11:00:00+08:00", "end": "2026-05-09T18:00:00+08:00", "source": "skill_default_from_event_time"}},
    }


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
