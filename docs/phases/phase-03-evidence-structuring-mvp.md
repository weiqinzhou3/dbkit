# Phase-03 — Evidence Structuring MVP

Version: v0.2
Status: Active Planning
Depends on: Phase-02.1 Real MySQL Evidence Collection MVP
Runtime Foundation: DeepAgents SDK

---

# 1. Purpose

Phase-03 turns Phase-02.1 collected `RawEvidence` into structured, bounded, deduplicated, LLM-safe `EvidenceBundle`.

Phase-02.1 already completed real evidence collection:

```text
NormalizedRequest
  -> MySQL Analyzer Agent in evidence_planning mode
  -> EvidenceRequest
  -> CollectionPlan
  -> Collector Tools
  -> RawEvidence
```

Phase-03 starts after collection.

Phase-03 output:

```text
RawEvidence index + RawEvidence artifacts
  -> Evidence Agent / Evidence Tools
  -> EvidenceBundle
```

Phase-03 must not perform final MySQL diagnosis.

---

# 2. Core Responsibility Split

```text
MySQL Analyzer Agent:
  already decided what evidence was needed in Phase-02 / Phase-02.1

Collector Tools:
  already collected RawEvidence in Phase-02.1

Evidence Agent:
  cleans, filters, deduplicates, aggregates, normalizes, and structures RawEvidence

Evidence Tools:
  perform deterministic parsing, filtering, aggregation, token estimation, raw_ref mapping, and bundle construction

Runtime:
  orchestrates Evidence Agent / Evidence Tools, enforces guardrails, persists artifacts, and emits telemetry
```

Evidence Agent must not decide what to collect.

Evidence Agent must not create new collection requests.

Evidence Agent must not generate root cause, findings, verdict, or final summary.

---

# 3. Phase Goal

Build the Evidence Structuring layer.

This phase must support:

- loading Phase-02.1 `raw-evidence-index.json`
- loading full raw artifacts from `payload.content_ref`
- validating raw artifact availability
- classifying raw evidence
- parsing supported MySQL / OS raw evidence types
- time-window filtering where possible
- deduplicating semantically overlapping raw evidence
- aggregating logs and metrics into bounded summaries
- preserving raw references
- producing evidence quality flags
- creating a bounded `EvidenceBundle`
- emitting evidence processing telemetry
- providing a repeatable CLI/test entrypoint from existing raw evidence

---

# 4. Phase Inputs

Phase-03 input is the Phase-02.1 artifact set.

Required input:

```text
.dbkit/artifacts/<request_id>.raw-evidence-index.json
```

Raw evidence contents are loaded through each item’s:

```text
payload.content_ref
```

Possible raw artifact paths:

```text
.dbkit/artifacts/raw/<raw_evidence_id>.json
.dbkit/artifacts/raw/<raw_evidence_id>.txt
```

Important rule:

```text
raw-evidence-index is only an index.
It contains status, source, metadata, payload.content_ref, bytes, line_count, and preview.
Phase-03 must read full raw artifact content through content_ref.
Do not structure evidence only from preview.
```

If `content_ref` is missing for a collected RawEvidence item, Phase-03 must mark it as low quality or invalid.

---

# 5. Supported RawEvidence Types

Phase-03 must support the following Phase-02.1 evidence types.

## 5.1 MySQL Baseline Evidence

```text
mysql.processlist
mysql.runtime_status
mysql.innodb_status
mysql.variables
mysql.service_metadata
mysql.log_paths
```

## 5.2 MySQL Log Evidence

```text
mysql.error_log
mysql.slow_log
```

## 5.3 OS / SSH Evidence

```text
metrics.os_cpu
metrics.os_memory
metrics.os_disk
os.mysql_service_status
```

## 5.4 Deprecated / Removed Evidence Types

Phase-03 must not reintroduce these deprecated duplicate types:

```text
metrics.mysql
metrics.mysql_status
metrics.mysql_variables
```

If they appear in old artifacts, Phase-03 may either:

```text
skip them as deprecated
```

or normalize them to existing canonical equivalents, but must not create duplicate EvidenceItems.

Canonical equivalents:

```text
metrics.mysql_status    -> mysql.runtime_status
metrics.mysql_variables -> mysql.variables
metrics.mysql           -> duplicate of mysql.runtime_status + mysql.variables
```

---

# 6. EvidenceBundle Schema

Required shape:

```json
{
  "request_id": "req_xxx",
  "phase": "phase-03",
  "bundle_id": "evb_xxx",
  "input_raw_evidence_index": ".dbkit/artifacts/req_xxx.raw-evidence-index.json",
  "source_raw_evidence_count": 10,
  "processed_raw_evidence_count": 9,
  "time_window": {
    "start": "2026-05-09T10:00:00+08:00",
    "end": "2026-05-09T17:00:00+08:00",
    "source": "skill_default_from_event_time"
  },
  "evidence_items": [],
  "coverage": {
    "required_evidence": [],
    "available_evidence": [],
    "missing_evidence": [],
    "unavailable_evidence": [],
    "low_quality_evidence": [],
    "deprecated_evidence_types": []
  },
  "quality": {
    "overall_status": "usable",
    "warnings": []
  },
  "processing_summary": {
    "raw_bytes": 0,
    "structured_bytes": 0,
    "estimated_tokens_before": 0,
    "estimated_tokens_after": 0,
    "compression_ratio": 0.0,
    "dedup_count": 0,
    "discarded_events": 0,
    "retained_events": 0
  },
  "skipped_raw_evidence": [],
  "metadata": {
    "skill": "skills/evidence/SKILL.md",
    "runtime_foundation": "DeepAgents SDK"
  }
}
```

---

# 7. EvidenceItem Schema

Required shape:

```json
{
  "evidence_id": "ev_xxx",
  "raw_evidence_id": "rawev_xxx",
  "evidence_type": "mysql.error_log",
  "source": {
    "kind": "ssh_file",
    "host": "192.168.23.176",
    "path": "/mysqldata/log/mysqld_err.log",
    "tool_name": "collect_mysql_error_log"
  },
  "time_range": {
    "start": "2026-05-09T10:00:00+08:00",
    "end": "2026-05-09T17:00:00+08:00",
    "timestamp_parse_status": "ok"
  },
  "summary": "Error log contains repeated connection warnings during the incident window.",
  "structured_payload": {},
  "raw_refs": [
    {
      "raw_evidence_id": "rawev_xxx",
      "content_ref": ".dbkit/artifacts/raw/rawev_xxx.txt",
      "line_start": 10,
      "line_end": 40
    }
  ],
  "quality_flags": [],
  "llm_safe": true
}
```

Rules:

- `raw_refs` are mandatory when evidence is derived from raw text or raw rows.
- `structured_payload` must be bounded.
- Large raw content must not be embedded.
- No raw secret may be present.
- EvidenceItem must not contain root cause, findings, verdict, or final recommendations.

---

# 8. Handling RawEvidence Status

Phase-03 must respect Phase-02.1 collection status.

## 8.1 collected

If status is:

```text
collected
```

Phase-03 should process the raw artifact via `payload.content_ref`.

## 8.2 not_available

If status is:

```text
not_available
```

Example:

```text
mysql.slow_log reason=slow_query_log_disabled
```

Phase-03 must:

- not fabricate an EvidenceItem
- record it in `coverage.unavailable_evidence`
- add a quality warning if the evidence would have been useful
- preserve reason from RawEvidence
- not treat it as collector failure

Example:

```json
{
  "evidence_type": "mysql.slow_log",
  "status": "not_available",
  "reason": "slow_query_log_disabled"
}
```

## 8.3 failed / blocked

If status is:

```text
failed
blocked
```

Phase-03 must:

- record it in coverage
- add quality warnings
- avoid pretending evidence exists
- preserve error reason

## 8.4 deprecated

If RawEvidence type is deprecated, Phase-03 must:

- record in `coverage.deprecated_evidence_types`
- skip or normalize without creating duplicate evidence

---

# 9. Evidence Processing Tools

Minimum tools:

```text
load_raw_evidence_index
load_raw_artifact
classify_raw_evidence
parse_mysql_processlist
parse_mysql_runtime_status
parse_mysql_innodb_status
parse_mysql_variables
parse_mysql_service_metadata
parse_mysql_log_paths
parse_mysql_error_log
parse_mysql_slow_log
parse_os_cpu_snapshot
parse_os_memory_snapshot
parse_os_disk_snapshot
parse_os_mysql_service_status
filter_by_time_window
deduplicate_events
aggregate_log_patterns
aggregate_processlist
aggregate_mysql_status
aggregate_os_metrics
estimate_token_size
validate_raw_refs
build_evidence_bundle
```

Tools perform deterministic transformations.

Evidence Agent decides which processing tools to use based on RawEvidence type and Evidence Skill.

---

# 10. Processing Requirements by Evidence Type

## 10.1 mysql.processlist

Input:

```text
SHOW FULL PROCESSLIST
```

Must produce:

- total connection count
- active query count
- sleeping connection count
- long-running query count
- top users
- top hosts
- top command types
- top states
- representative query samples with raw_refs
- quality flag if processlist has very few rows or only DBKit connection

Required fields to support:

```text
Id
User
Host
db
Command
Time
State
Info
```

No root cause judgment.

## 10.2 mysql.runtime_status

Input:

```text
SHOW GLOBAL STATUS
```

Must produce:

- selected key counters
- connection-related counters
- thread-related counters
- query / command counters
- handler counters where available
- temporary table counters
- aborted connection counters
- status variable count
- raw_refs or row refs

Examples of useful variables:

```text
Threads_connected
Threads_running
Max_used_connections
Connections
Aborted_connects
Aborted_clients
Questions
Queries
Slow_queries
Created_tmp_disk_tables
Created_tmp_tables
Handler_read_rnd_next
Innodb_row_lock_waits
Innodb_buffer_pool_reads
```

Do not calculate root cause.

## 10.3 mysql.innodb_status

Input:

```text
SHOW ENGINE INNODB STATUS
```

Must produce:

- section summaries where detectable
- transaction section presence
- lock wait hints
- deadlock section presence
- buffer pool hints
- row operation hints
- raw_refs

Allowed to extract structured sections, but not diagnose final root cause.

## 10.4 mysql.variables

Input:

```text
SHOW GLOBAL VARIABLES
```

Must produce:

- selected variable map
- max_connections
- slow_query_log
- slow_query_log_file
- log_output
- long_query_time
- datadir
- log_error
- innodb_buffer_pool_size
- key relevant config values
- raw_refs

## 10.5 mysql.service_metadata

Must structure:

```text
version
hostname
port
datadir
log_error
slow_query_log_file
```

If version is MySQL 5.7 / 8.0 / MariaDB, record normalized family if detectable.

## 10.6 mysql.log_paths

Must structure:

```text
error_log_path
slow_log_path
slow_query_log_enabled
log_output
datadir
```

This EvidenceItem is required when available because Phase-04 needs to explain:

- where logs came from
- why slow log is unavailable
- whether log output is FILE or TABLE

## 10.7 mysql.error_log

Must support:

- timestamp extraction
- time-window filtering when timestamps parse
- repeated event grouping
- error pattern extraction
- severity hints
- top patterns
- retained lines count
- discarded lines count
- raw line references
- timestamp parse failure handling

If timestamps cannot be parsed:

```text
timestamp_parse_status=failed
```

Do not silently treat all lines as in-window.

## 10.8 mysql.slow_log

If status is collected, must support:

- timestamp extraction
- query time
- lock time
- rows examined
- rows sent
- user / host where available
- SQL digest grouping
- top N slow query patterns
- raw_refs

If status is not_available due to `slow_query_log_disabled`, record unavailable evidence only.

Do not put large SQL dumps into EvidenceBundle.

## 10.9 metrics.os_cpu

Input may include:

```text
uptime
top
vmstat
```

Must produce:

- load average
- CPU usage hints if parseable
- top CPU processes if available
- run queue / context switch hints if vmstat available
- raw_refs
- parse quality flags

## 10.10 metrics.os_memory

Input may include:

```text
free -m
vmstat
```

Must produce:

- total / used / free / available memory if parseable
- swap usage
- memory pressure hints
- raw_refs

## 10.11 metrics.os_disk

Input may include:

```text
df -h
du -sh <datadir>
```

Must produce:

- filesystem usage summary
- MySQL datadir size if available
- high disk usage hints
- raw_refs

## 10.12 os.mysql_service_status

Input may include:

```text
systemctl status mysqld/mysql
ps -ef | grep mysqld
```

Must produce:

- service active/inactive state if parseable
- process presence
- mysqld command line if available
- recent service log snippets if available
- raw_refs

---

# 11. Time Window Filtering

Phase-03 must use `time_window` from RawEvidence metadata or NormalizedRequest.

Rules:

- apply filtering to evidence types with timestamps
- preserve evidence without timestamps but mark `timestamp_parse_status=not_applicable`
- if timestamps cannot be parsed, set `timestamp_parse_status=failed`
- record discarded / retained counts
- do not drop all evidence silently

Time-window filtering applies primarily to:

```text
mysql.error_log
mysql.slow_log
```

It may partially apply to OS command output if timestamps exist.

---

# 12. Deduplication Rules

Phase-03 must deduplicate semantically overlapping evidence.

Required rules:

```text
Do not create duplicate EvidenceItems for the same raw_evidence_id and evidence_type.
Do not create duplicate EvidenceItems from deprecated metrics.mysql* types.
If two RawEvidence items have identical content_ref or same source/tool/evidence_type, process once and record duplicate in skipped_raw_evidence.
```

Repeated log lines should be grouped by pattern.

Duplicate SQL digest entries should be grouped.

Do not remove raw_refs needed for traceability.

---

# 13. Bounded Context Reduction

EvidenceBundle must be bounded and LLM-safe.

Do not embed:

- full error log
- full slow log
- full SHOW GLOBAL STATUS rows
- full SHOW GLOBAL VARIABLES rows
- full InnoDB status text
- full OS command output

Instead include:

- concise summary
- structured counters
- top patterns
- top states
- top samples
- raw_refs
- content_ref for drill-down

Telemetry must include:

```text
raw_bytes
filtered_bytes
structured_bytes
compression_ratio
estimated_tokens_before
estimated_tokens_after
discarded_lines
retained_events
dedup_count
top_patterns
```

---

# 14. Evidence Quality Flags

EvidenceItems may include quality flags:

```text
timestamp_parse_failed
timestamp_parse_partial
out_of_time_window
large_raw_truncated
source_not_available
source_failed
low_signal
deprecated_evidence_type
raw_ref_missing
secret_redacted
parser_partial
```

Bundle-level quality statuses:

```text
usable
usable_with_warnings
insufficient
failed
```

---

# 15. Evidence Guardrails

Evidence Guardrails must check:

```text
raw evidence index exists
raw artifact content_ref exists for collected items
raw artifact path is allowed
file size within configured limit
EvidenceBundle size within configured limit
raw secrets absent/redacted
deprecated evidence is not duplicated
time-window filtering status recorded
raw_refs valid
EvidenceItem schema valid
EvidenceBundle schema valid
no root cause / findings / verdict fields
```

If guardrails fail:

```text
status=blocked
reason=evidence_guardrails_failed
```

---

# 16. Artifacts

Required artifacts:

```text
.dbkit/artifacts/<request_id>.evidence-bundle.json
.dbkit/artifacts/<request_id>.evidence-processing-telemetry.jsonl
.dbkit/artifacts/evidence/<evidence_id>.json
```

Optional artifacts:

```text
.dbkit/artifacts/<request_id>.evidence-quality.json
.dbkit/artifacts/<request_id>.evidence-coverage.json
```

Artifacts must use:

```python
ensure_ascii=False
indent=2
sort_keys=True
```

Artifacts must not contain raw secrets.

Artifacts must not contain raw chain-of-thought.

---

# 17. Telemetry

Required events:

```text
evidence_structuring_started
raw_evidence_index_loaded
raw_artifact_loaded
raw_artifact_load_failed
raw_evidence_classified
evidence_parser_started
evidence_parser_completed
evidence_parser_failed
time_window_filter_started
time_window_filter_completed
deduplication_started
deduplication_completed
evidence_item_created
evidence_item_skipped
evidence_bundle_created
evidence_guardrails_started
evidence_guardrails_passed
evidence_guardrails_blocked
evidence_artifact_written
```

Telemetry must include:

```text
request_id
raw_evidence_id
evidence_type
tool_name
input_bytes
output_bytes
duration_ms
status
quality_flags
```

Telemetry must not include raw secrets.

---

# 18. CLI Behavior

## 18.1 Run Phase-03 from Existing RawEvidence

Phase-03 must support a repeatable entrypoint from an existing raw evidence index.

Preferred CLI:

```bash
python3.11 main.py --config config/config.yaml \
  --from-raw-evidence .dbkit/artifacts/<request_id>.raw-evidence-index.json
```

Expected output:

```text
DBKit 0.1.0
phase=phase-03
status=evidence_bundle_created
evidence_items=...
unavailable_evidence=...
quality=usable_with_warnings
artifact=.dbkit/artifacts/<request_id>.evidence-bundle.json
```

If this exact CLI flag is not implemented, an equivalent testable entrypoint must exist.

## 18.2 Run Phase-01 -> Phase-02.1 -> Phase-03 in One Command

Phase-03 may also run after Phase-02.1 in the same command if the runtime supports phase chaining.

Expected behavior:

```text
No root cause summary.
No findings.
No verdict.
Only EvidenceBundle artifact.
```

---

# 19. Skill Requirements

Create or update:

```text
skills/evidence/SKILL.md
```

Required sections:

```text
Role
Input Contract
Output Contract
Supported Evidence Types
Deprecated Evidence Types
Processing Rules
Time Window Rules
Deduplication Rules
Aggregation Rules
Raw Reference Rules
Unavailable Evidence Rules
Evidence Quality Rules
LLM-safe Context Rules
Forbidden Behavior
```

Skill must state:

```text
Do not diagnose root cause.
Do not generate findings.
Do not generate verdict.
Do not invent missing raw data.
Do not request additional collection.
Do not reintroduce deprecated metrics.mysql* evidence.
Always preserve raw_refs.
Use content_ref to read full raw artifacts.
Prefer summaries and aggregation over raw dumps.
```

---

# 20. Out of Scope

Do not implement:

- root cause judgment
- findings
- confidence verdict
- remediation recommendations
- additional evidence planning
- additional live collectors
- new collection tools
- docx/pdf reports
- FastAPI
- MCP
- production remediation
- query killing
- config changes

---

# 21. Required Tests

## 21.1 Load RawEvidence Index

Given a Phase-02.1 raw-evidence-index, Phase-03 loads it and resolves content_ref.

## 21.2 Process MySQL Baseline

Raw evidence types produce EvidenceItems:

```text
mysql.processlist
mysql.runtime_status
mysql.innodb_status
mysql.variables
mysql.service_metadata
mysql.log_paths
```

## 21.3 Process MySQL+SSH Extension

Raw evidence types produce EvidenceItems or unavailable coverage:

```text
mysql.error_log
mysql.slow_log
metrics.os_cpu
metrics.os_memory
metrics.os_disk
os.mysql_service_status
```

## 21.4 Slow Log Disabled

If `mysql.slow_log` status is `not_available` and reason is `slow_query_log_disabled`, EvidenceBundle records unavailable evidence and does not create fake slow log EvidenceItem.

## 21.5 Error Log Structuring

Error log becomes EvidenceItem with:

```text
top_patterns
timestamp_parse_status
raw_refs
retained_lines
discarded_lines
```

## 21.6 Processlist Structuring

Processlist becomes structured counts and top states/users.

## 21.7 Runtime Status Structuring

Runtime status extracts selected counters and raw_refs.

## 21.8 Variables Structuring

Variables extracts key config values and raw_refs.

## 21.9 Service Metadata Structuring

Service metadata extracts version, hostname, port, datadir, and log paths.

## 21.10 Log Paths Structuring

Log paths extracts error/slow log path and slow_query_log_enabled.

## 21.11 OS Metrics Structuring

OS CPU/memory/disk snapshots become structured payloads with parse quality flags.

## 21.12 Dedup Deprecated Metrics

If old artifacts contain:

```text
metrics.mysql
metrics.mysql_status
metrics.mysql_variables
```

Phase-03 does not create duplicate EvidenceItems.

## 21.13 Time Window Filtering

Timestamped logs outside window are excluded or marked out-of-window.

## 21.14 Timestamp Parse Failure

Unparseable timestamps are marked explicitly.

## 21.15 Bounded Output

EvidenceBundle does not embed large raw content.

## 21.16 No Findings

EvidenceBundle must not contain:

```text
root_cause
findings
verdict
final_summary
recommendations
```

## 21.17 Secret Safety

No raw secrets in EvidenceBundle or telemetry.

## 21.18 CLI Entrypoint

`--from-raw-evidence` or equivalent repeatable entrypoint works.

---

# 22. Success Criteria

Phase-03 is complete when:

1. Phase-02.1 RawEvidence becomes EvidenceBundle.
2. EvidenceItems are structured for MySQL baseline evidence.
3. EvidenceItems are structured for SSH/log/OS extension evidence.
4. `not_available` evidence is handled correctly.
5. `mysql.log_paths` and `mysql.service_metadata` are first-class EvidenceItems.
6. Time-window filtering works where applicable.
7. Deduplication works for repeated logs and deprecated duplicate metrics.
8. Raw refs are preserved and valid.
9. Output is bounded and LLM-safe.
10. Evidence processing telemetry exists.
11. Evidence Guardrails pass.
12. No findings/root cause/verdict are generated.
13. Required tests pass.
14. GitHub CI passes.

---

# 23. Closeout Requirements

Implementation closeout must report:

```text
Branch
Commit
Tests run
CI URL/status
Manual command from existing raw-evidence-index
Input raw-evidence-index path
EvidenceBundle artifact path
Evidence processing telemetry path
Evidence item count
Unavailable evidence count
Quality status
Known limitations
Remaining risks
```

Do not mark Phase-03 complete without a repeatable test using a real Phase-02.1 raw-evidence-index.
