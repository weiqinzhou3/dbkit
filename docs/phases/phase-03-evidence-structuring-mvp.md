# Phase-03 — Evidence Structuring Subagent MVP

Version: v0.3
Status: Active Planning
Depends on: Phase-02.1 Real MySQL Evidence Collection MVP
Runtime Foundation: DeepAgents SDK

---

# 1. Purpose

Phase-03 turns Phase-02.1 collected `RawEvidence` into structured, bounded, deduplicated, LLM-safe `EvidenceBundle`.

Phase-02.1 already completed real evidence collection:

```text
NormalizedRequest
  -> MySQL Analyzer Agent / mode=evidence_planning
  -> EvidenceRequest
  -> CollectionPlan
  -> Collector Tools
  -> RawEvidence
```

Phase-03 starts after collection.

Phase-03 output:

```text
RawEvidence index + RawEvidence artifacts
  -> Evidence Structuring Subagent
  -> Evidence Processing Tools
  -> EvidenceBundle
```

Phase-03 must not perform final MySQL diagnosis.

---

# 2. Subagent Ownership

The Evidence Structuring Subagent is the **subagent of the MySQL Analyzer Agent** in the MySQL analysis workflow.

Conceptually:

```text
MySQL Analyzer Agent
  -> plans evidence
  -> receives RawEvidence collection result
  -> delegates evidence structuring to Evidence Structuring Subagent
  -> receives EvidenceBundle
  -> continues later in findings_generation mode
```

Runtime / Coordinator is not the semantic owner of the Evidence Structuring Subagent.

Runtime / Coordinator only:

```text
registers the subagent
wires it into the execution graph
passes inputs and artifacts
enforces guardrails
persists artifacts
emits telemetry
```

The MySQL Analyzer Agent owns the domain workflow and delegates to the Evidence Structuring Subagent when RawEvidence needs to be converted into EvidenceBundle.

This relationship must be visible in config / agent registration.

Recommended conceptual config:

```yaml
agents:
  mysql_analyzer:
    system_prompt: agents/mysql-analyzer/system.md
    skills:
      - skills/mysql-analyzer/SKILL.md
    subagents:
      evidence_structuring:
        agent: evidence_structuring
        skill: skills/evidence/SKILL.md
        allowed_tools:
          - load_raw_evidence_index
          - load_raw_artifact
          - classify_raw_evidence
          - parse_mysql_processlist
          - parse_mysql_runtime_status
          - parse_mysql_innodb_status
          - parse_mysql_variables
          - parse_mysql_service_metadata
          - parse_mysql_log_paths
          - parse_mysql_error_log
          - parse_mysql_slow_log
          - parse_os_cpu_snapshot
          - parse_os_memory_snapshot
          - parse_os_disk_snapshot
          - parse_os_mysql_service_status
          - filter_by_time_window
          - deduplicate_events
          - aggregate_log_patterns
          - aggregate_processlist
          - aggregate_mysql_status
          - aggregate_os_metrics
          - estimate_token_size
          - validate_raw_refs
          - build_evidence_bundle

  evidence_structuring:
    system_prompt: agents/evidence-structuring/system.md
    skills:
      - skills/evidence/SKILL.md
```

The exact file layout may follow the repository’s current conventions, but the relationship must remain:

```text
mysql_analyzer -> delegates to evidence_structuring subagent
```

For future domains, the same Evidence Structuring Subagent may be reused or specialized:

```text
redis_analyzer -> evidence_structuring(domain=redis)
mongodb_analyzer -> evidence_structuring(domain=mongodb)
```

But in this phase, the parent caller is `mysql_analyzer`.

---

# 3. Agentic Runtime Flow

The intended runtime shape is:

```text
User Prompt
  -> Intake Agent
  -> NormalizedRequest
  -> MySQL Analyzer Agent / mode=evidence_planning
  -> EvidenceRequest
  -> Collector Tools
  -> RawEvidence
  -> MySQL Analyzer Agent delegates to Evidence Structuring Subagent
  -> Evidence Structuring Subagent / skills/evidence/SKILL.md
  -> EvidenceBundle
  -> MySQL Analyzer Agent / mode=findings_generation
  -> FindingsDraft
  -> Validation Agent
  -> Verdict / Summary
```

Important:

```text
MySQL Analyzer Agent is invoked in Phase-02 for evidence planning.
Evidence Structuring Subagent is invoked in Phase-03 as a child/subagent of MySQL Analyzer.
MySQL Analyzer Agent is invoked again in Phase-04 for findings generation.
```

The two MySQL Analyzer calls are different modes of the same domain agent.

Phase-03 is the boundary between raw collection and final domain reasoning.

---

# 4. Core Responsibility Split

```text
MySQL Analyzer Agent:
  owns the MySQL analysis workflow
  decides what evidence is needed in Phase-02 / Phase-02.1
  delegates RawEvidence transformation to Evidence Structuring Subagent in Phase-03
  consumes EvidenceBundle in Phase-04
  does not clean RawEvidence directly

Collector Tools:
  collect RawEvidence in Phase-02.1
  do not structure EvidenceBundle
  do not generate findings

Evidence Structuring Subagent:
  decides how to process available RawEvidence
  selects evidence processing tools
  cleans, filters, deduplicates, aggregates, normalizes, and structures RawEvidence
  generates EvidenceBundle only
  returns EvidenceBundle to MySQL Analyzer workflow

Evidence Processing Tools:
  perform deterministic parsing, filtering, aggregation, token estimation, raw_ref mapping, and bundle construction

Runtime:
  registers MySQL Analyzer and Evidence Structuring Subagent
  exposes allowed tools
  invokes subagent according to the workflow
  validates EvidenceBundle schema
  enforces guardrails
  persists artifacts
  emits telemetry
```

Evidence Structuring Subagent must not decide what to collect.

Evidence Structuring Subagent must not create new collection requests.

Evidence Structuring Subagent must not call live collection tools.

Evidence Structuring Subagent must not generate root cause, findings, verdict, or final summary.

---

# 5. Runtime and Subagent Boundary

## 5.1 Runtime May Do

Runtime may:

```text
load configuration
resolve artifact paths
instantiate agents and subagents
register evidence processing tools
pass RawEvidence index path
pass runtime context
enforce tool allowlist
validate EvidenceBundle schema
write artifacts
write telemetry
block unsafe outputs
```

## 5.2 Runtime Must Not Do

Runtime must not:

```text
parse MySQL error logs itself
aggregate Aborted connection patterns itself
decide which evidence is semantically important
generate EvidenceItems by hardcoded MySQL logic
generate MySQL findings
generate summary/verdict
invent additional collection steps
```

If runtime contains deterministic helper code, that code must be exposed as an evidence processing tool or schema/guardrail utility.

## 5.3 MySQL Analyzer May Delegate

MySQL Analyzer Agent may delegate to Evidence Structuring Subagent when:

```text
RawEvidence exists
RawEvidence index path exists
EvidenceBundle is missing or stale
analysis requires structured evidence
```

The delegation input must include:

```text
request_id
raw_evidence_index path
NormalizedRequest metadata
EvidenceRequest metadata if available
time_window
domain=mysql
```

The delegation output must be:

```text
EvidenceBundle artifact path
EvidenceBundle object or summary metadata
Evidence quality status
coverage summary
```

## 5.4 Evidence Subagent May Call

The Evidence Structuring Subagent may call:

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

## 5.5 Evidence Subagent Must Not Call

The Evidence Structuring Subagent must not call:

```text
live MySQL collectors
SSH collectors
remote file readers
remediation tools
query kill tools
configuration change tools
finding generation tools
validation verdict tools
```

Phase-03 does not re-collect data.

---

# 6. Phase Goal

Build the Evidence Structuring Subagent layer.

This phase must support:

- being registered as a subagent under `mysql_analyzer`
- being callable by MySQL Analyzer after collection
- being callable from CLI for repeatable testing
- loading Phase-02.1 `raw-evidence-index.json`
- loading full raw artifacts from `payload.content_ref`
- validating raw artifact availability
- classifying raw evidence
- selecting appropriate evidence processing tools
- parsing supported MySQL / OS raw evidence types
- time-window filtering where possible
- deduplicating semantically overlapping raw evidence
- aggregating logs and metrics into bounded summaries
- preserving raw references
- producing evidence quality flags
- creating a bounded `EvidenceBundle`
- returning EvidenceBundle metadata to MySQL Analyzer
- emitting evidence processing telemetry

---

# 7. Phase Inputs

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

# 8. Supported RawEvidence Types

Phase-03 must support the following Phase-02.1 evidence types.

## 8.1 MySQL Baseline Evidence

```text
mysql.processlist
mysql.runtime_status
mysql.innodb_status
mysql.variables
mysql.service_metadata
mysql.log_paths
```

## 8.2 MySQL Log Evidence

```text
mysql.error_log
mysql.slow_log
```

## 8.3 OS / SSH Evidence

```text
metrics.os_cpu
metrics.os_memory
metrics.os_disk
os.mysql_service_status
```

## 8.4 Deprecated / Removed Evidence Types

Phase-03 must not reintroduce these deprecated duplicate types:

```text
metrics.mysql
metrics.mysql_status
metrics.mysql_variables
```

If they appear in old artifacts, Phase-03 may either skip them as deprecated or normalize them to existing canonical equivalents, but must not create duplicate EvidenceItems.

Canonical equivalents:

```text
metrics.mysql_status    -> mysql.runtime_status
metrics.mysql_variables -> mysql.variables
metrics.mysql           -> duplicate of mysql.runtime_status + mysql.variables
```

---

# 9. EvidenceBundle Schema

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
    "start_utc": "2026-05-09T02:00:00+00:00",
    "end_utc": "2026-05-09T09:00:00+00:00",
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
    "llm_safe": true,
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
    "subagent": "evidence_structuring",
    "parent_agent": "mysql_analyzer",
    "runtime_foundation": "DeepAgents SDK"
  }
}
```

---

# 10. EvidenceItem Schema

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
    "start_utc": "2026-05-09T02:00:00+00:00",
    "end_utc": "2026-05-09T09:00:00+00:00",
    "timestamp_parse_status": "ok",
    "timezone_handling": "normalized_to_utc",
    "source_timezone": "UTC"
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

# 11. Handling RawEvidence Status

Phase-03 must respect Phase-02.1 collection status.

## 11.1 collected

If status is `collected`, Phase-03 should process the raw artifact via `payload.content_ref`.

## 11.2 not_available

If status is `not_available`, such as `mysql.slow_log reason=slow_query_log_disabled`, Phase-03 must:

- not fabricate an EvidenceItem
- record it in `coverage.unavailable_evidence`
- add a quality warning if the evidence would have been useful
- preserve reason from RawEvidence
- not treat it as collector failure

## 11.3 failed / blocked

If status is `failed` or `blocked`, Phase-03 must record it in coverage, add quality warnings, avoid pretending evidence exists, and preserve error reason.

## 11.4 deprecated

If RawEvidence type is deprecated, Phase-03 must record it in `coverage.deprecated_evidence_types` and skip or normalize without creating duplicate evidence.

---

# 12. Evidence Processing Tools

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

Evidence Structuring Subagent decides which processing tools to use based on RawEvidence type and Evidence Skill.

---

# 13. Processing Requirements by Evidence Type

## 13.1 mysql.processlist

Input: `SHOW FULL PROCESSLIST`

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

No root cause judgment.

## 13.2 mysql.runtime_status

Input: `SHOW GLOBAL STATUS`

Must produce selected key counters, connection/thread/query counters, temporary table counters, aborted connection counters, status variable count, and raw_refs or row refs.

Useful variables include:

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

## 13.3 mysql.innodb_status

Input: `SHOW ENGINE INNODB STATUS`

Must produce section summaries, transaction section presence, lock wait hints, deadlock section presence, buffer pool hints, row operation hints, and raw_refs.

Do not diagnose final root cause.

## 13.4 mysql.variables

Input: `SHOW GLOBAL VARIABLES`

Must produce selected variable map including:

```text
max_connections
slow_query_log
slow_query_log_file
log_output
long_query_time
datadir
log_error
innodb_buffer_pool_size
log_timestamps
time_zone
system_time_zone
```

## 13.5 mysql.service_metadata

Must structure:

```text
version
hostname
port
datadir
log_error
slow_query_log_file
log_timestamps
time_zone
system_time_zone
```

If version is MySQL 5.7 / 8.0 / MariaDB, record normalized family if detectable.

## 13.6 mysql.log_paths

Must structure:

```text
error_log_path
slow_log_path
slow_query_log_enabled
log_output
datadir
log_timestamps
```

This EvidenceItem is required because Phase-04 needs to explain where logs came from, why slow log is unavailable, whether log output is FILE or TABLE, and whether logs use UTC or system time.

## 13.7 mysql.error_log

Must support:

- timestamp extraction
- timezone-aware timestamp normalization
- time-window filtering when timestamps parse
- repeated event grouping
- error pattern extraction
- severity hints
- semantic hints, such as `aborted_connection`, `access_denied`, `too_many_connections`, `timeout`, `crash_or_restart`, `oom_or_memory`, `replication_error`, `innodb_error`
- top patterns
- retained lines count
- discarded lines count
- raw line references
- timestamp parse failure handling

If timestamps cannot be parsed, set `timestamp_parse_status=failed`.

Do not silently treat all lines as in-window.

If the raw artifact is readable, do not skip `mysql.error_log` entirely just because part of the parser fails. Produce an EvidenceItem with quality flags.

## 13.8 mysql.slow_log

If status is collected, must support timestamp extraction, query time, lock time, rows examined, rows sent, user / host, SQL digest grouping, top N slow query patterns, and raw_refs.

If status is not_available due to `slow_query_log_disabled`, record unavailable evidence only.

Do not put large SQL dumps into EvidenceBundle.

## 13.9 metrics.os_cpu

Input may include `uptime`, `top`, and `vmstat`.

Must produce load average, CPU usage hints if parseable, top CPU processes if available, run queue / context switch hints if vmstat available, raw_refs, and parse quality flags.

## 13.10 metrics.os_memory

Input may include `free -m` and `vmstat`.

Must produce total / used / free / available memory if parseable, swap usage, memory pressure hints, and raw_refs.

## 13.11 metrics.os_disk

Input may include `df -h` and `du -sh <datadir>`.

Must produce filesystem usage summary, MySQL datadir size if available, high disk usage hints, and raw_refs.

## 13.12 os.mysql_service_status

Input may include `systemctl status mysqld/mysql` and `ps -ef | grep mysqld`.

Must produce service active/inactive state if parseable, process presence, mysqld command line if available, recent service log snippets if available, and raw_refs.

---

# 14. Time Window and Timezone Handling

Time window must be preserved and enforced in both collection and structuring.

Phase-02.1 collection stage should:

```text
attempt time_window-aware log collection where possible
record collection_strategy
record time_window_aware
record time_window_coverage
record coverage warnings when only bounded tail was possible
```

Phase-03 structuring stage must:

```text
read raw artifact from content_ref
parse timestamps
normalize timestamps and time_window to UTC
strictly filter timestamped evidence by time_window
record retained/discarded counts
record timestamp and timezone quality flags
```

Rules:

- if log timestamp contains `Z`, treat it as UTC
- if log timestamp contains explicit offset, use that offset
- if log timestamp has no offset, infer from `log_timestamps`, MySQL timezone variables, or OS timezone metadata
- if timezone cannot be inferred, mark `timezone_inference_failed`
- never compare local-time strings directly to UTC log timestamps
- do not treat `tail 5000 lines` as proof of full time_window coverage
- if timestamps cannot be parsed, set `timestamp_parse_status=failed`
- if filtering is only partial, set `time_window_filter_status=partial`
- if filtering cannot be performed, set `time_window_filter_status=unavailable`
- do not drop all evidence silently

Time-window filtering applies primarily to:

```text
mysql.error_log
mysql.slow_log
```

It may partially apply to OS command output if timestamps exist.

---

# 15. Log Pattern Aggregation Rules

For log evidence, the order must be:

```text
load raw artifact
parse timestamp
normalize timezone
filter by time_window
normalize pattern
aggregate top_patterns
preserve raw_refs
```

Top patterns must be based on retained in-window events only.

Basic pattern normalization should:

```text
remove timestamp
remove thread id
remove IP:port
remove obvious numeric IDs
preserve severity marker such as Note / Warning / ERROR
preserve important MySQL message type
```

Each top pattern should include:

```json
{
  "pattern": "...",
  "count": 1,
  "semantic_hint": "aborted_connection",
  "operational_relevance": "high",
  "raw_refs": []
}
```

Evidence-level summary should mention the leading pattern without generating root cause.

Example:

```text
Error log contains 404 Aborted connection events inside the requested time window.
```

This is evidence summarization, not final diagnosis.

---

# 16. Deduplication Rules

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

# 17. Bounded Context Reduction

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

# 18. Evidence Quality Flags

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
time_window_filter_unavailable
time_window_filter_partial
timezone_inference_failed
coverage_unknown
collection_tail_fallback
```

Bundle-level quality statuses:

```text
usable
usable_with_warnings
insufficient
failed
```

---

# 19. Evidence Guardrails

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
subagent did not call live collection tools
```

If guardrails fail:

```text
status=blocked
reason=evidence_guardrails_failed
```

---

# 20. Artifacts

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

# 21. Telemetry

Required events:

```text
evidence_structuring_started
evidence_subagent_invoked
evidence_subagent_completed
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
parent_agent=mysql_analyzer
subagent=evidence_structuring
raw_evidence_id
evidence_type
tool_name
input_bytes
output_bytes
duration_ms
status
quality_flags
```

Telemetry must not include raw secrets or chain-of-thought.

---

# 22. CLI Behavior

## 22.1 Normal Phase-03 Workflow

The normal MySQL workflow must ask `mysql_analyzer` to delegate to the
DeepAgents `evidence_structuring` subagent after Phase-02.1 RawEvidence
collection completes.

Preferred CLI:

```bash
python3.11 main.py --config config/config.yaml \
  "<MySQL analysis request>"
```

Expected output:

```text
DBKit 0.1.0
phase=phase-03
status=evidence_bundle_created
parent_agent=mysql_analyzer
subagent=evidence_structuring
raw_evidence_artifact=.dbkit/artifacts/<request_id>.raw-evidence-index.json
evidence_items=...
quality=usable_with_warnings
artifact=.dbkit/artifacts/<request_id>.evidence-bundle.json
```

The command must stop after EvidenceBundle creation in Phase-03. It must not
generate root cause, findings, verdict, final summary, or recommendations.

The normal workflow must produce a traceable DeepAgents subagent invocation:

```text
mysql_analyzer delegation prompt
  -> task(subagent_type=evidence_structuring)
  -> evidence_structuring system prompt + skills/evidence/SKILL.md
  -> build_evidence_bundle evidence processing tool
```

Artifact paths passed to DeepAgents subagents must use DBKit virtual filesystem
paths:

```text
host_path: /Users/.../dbkit/.dbkit/artifacts/<request_id>.raw-evidence-index.json
repo_relative_path: .dbkit/artifacts/<request_id>.raw-evidence-index.json
deepagents_virtual_path: /repo/.dbkit/artifacts/<request_id>.raw-evidence-index.json
```

Runtime must pass `/repo/.dbkit/...` paths to the subagent. It must never pass
`/.dbkit/...`.

## 22.2 Replay Phase-03 from Existing RawEvidence

Phase-03 must also support a repeatable replay entrypoint from an existing raw
evidence index for debug and regression testing.

Preferred CLI:

```bash
python3.11 main.py --config config/config.yaml \
  --from-raw-evidence .dbkit/artifacts/<request_id>.raw-evidence-index.json
```

`--from-raw-evidence` is not the normal Phase-03 trigger.
If replay bypasses DeepAgents subagent invocation for deterministic regression,
CLI/telemetry must clearly mark replay mode and whether a subagent was invoked.

Expected behavior:

```text
No root cause summary.
No findings.
No verdict.
Only EvidenceBundle artifact.
```

---

# 23. Skill Requirements

Create or update:

```text
skills/evidence/SKILL.md
```

Required sections:

```text
Role
Parent Agent Relationship
Input Contract
Output Contract
Supported Evidence Types
Deprecated Evidence Types
Allowed Processing Tools
Forbidden Tools
Processing Rules
Time Window Rules
Timezone Rules
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
You are the Evidence Structuring Subagent for mysql_analyzer.
You transform RawEvidence into EvidenceBundle.
You may select evidence processing tools.
You must not call live collection tools.
You must not request additional collection.
You must not diagnose root cause.
You must not generate findings.
You must not generate verdict.
You must not invent missing raw data.
You must not reintroduce deprecated metrics.mysql* evidence.
Always preserve raw_refs.
Use content_ref to read full raw artifacts.
Prefer summaries and aggregation over raw dumps.
```

---

# 24. Out of Scope

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

# 25. Required Tests

## 25.1 Subagent Registration

Evidence Structuring Subagent is registered under MySQL Analyzer workflow.

## 25.2 Subagent Delegation

MySQL Analyzer can delegate RawEvidence structuring to Evidence Structuring Subagent.

## 25.3 Subagent Tool Allowlist

Evidence Structuring Subagent can call evidence processing tools but cannot call live MySQL/SSH collectors.

## 25.4 Load RawEvidence Index

Given a Phase-02.1 raw-evidence-index, Phase-03 loads it and resolves content_ref.

## 25.5 Process MySQL Baseline

Raw evidence types produce EvidenceItems:

```text
mysql.processlist
mysql.runtime_status
mysql.innodb_status
mysql.variables
mysql.service_metadata
mysql.log_paths
```

## 25.6 Process MySQL+SSH Extension

Raw evidence types produce EvidenceItems or unavailable coverage:

```text
mysql.error_log
mysql.slow_log
metrics.os_cpu
metrics.os_memory
metrics.os_disk
os.mysql_service_status
```

## 25.7 Slow Log Disabled

If `mysql.slow_log` status is `not_available` and reason is `slow_query_log_disabled`, EvidenceBundle records unavailable evidence and does not create fake slow log EvidenceItem.

## 25.8 Error Log Structuring

Error log becomes EvidenceItem with:

```text
top_patterns
semantic_hint
timestamp_parse_status
timezone_handling
time_window_filter_status
raw_refs
retained_lines
discarded_lines
```

## 25.9 Timezone-Aware Filtering

User window in `+08:00` matches error log timestamps in `Z` correctly after UTC normalization.

## 25.10 Processlist Structuring

Processlist becomes structured counts and top states/users.

## 25.11 Runtime Status Structuring

Runtime status extracts selected counters and raw_refs.

## 25.12 Variables Structuring

Variables extracts key config values and raw_refs.

## 25.13 Service Metadata Structuring

Service metadata extracts version, hostname, port, datadir, and log paths.

## 25.14 Log Paths Structuring

Log paths extracts error/slow log path and slow_query_log_enabled.

## 25.15 OS Metrics Structuring

OS CPU/memory/disk snapshots become structured payloads with parse quality flags.

## 25.16 Dedup Deprecated Metrics

If old artifacts contain:

```text
metrics.mysql
metrics.mysql_status
metrics.mysql_variables
```

Phase-03 does not create duplicate EvidenceItems.

## 25.17 Time Window Filtering

Timestamped logs outside window are excluded or marked out-of-window.

## 25.18 Timestamp Parse Failure

Unparseable timestamps are marked explicitly.

## 25.19 Bounded Output

EvidenceBundle does not embed large raw content.

## 25.20 No Findings

EvidenceBundle must not contain:

```text
root_cause
findings
verdict
final_summary
recommendations
```

## 25.21 Secret Safety

No raw secrets in EvidenceBundle or telemetry.

## 25.22 CLI Entrypoint

`--from-raw-evidence` or equivalent repeatable entrypoint works.

---

# 26. Success Criteria

Phase-03 is complete when:

1. Evidence Structuring Subagent is registered under MySQL Analyzer workflow.
2. MySQL Analyzer can delegate RawEvidence structuring to Evidence Structuring Subagent.
3. Evidence Structuring Subagent can only call evidence processing tools.
4. Phase-02.1 RawEvidence becomes EvidenceBundle.
5. EvidenceItems are structured for MySQL baseline evidence.
6. EvidenceItems are structured for SSH/log/OS extension evidence.
7. `not_available` evidence is handled correctly.
8. `mysql.log_paths` and `mysql.service_metadata` are first-class EvidenceItems.
9. Time-window and timezone-aware filtering work where applicable.
10. Deduplication works for repeated logs and deprecated duplicate metrics.
11. Raw refs are preserved and valid.
12. Output is bounded and LLM-safe.
13. Evidence processing telemetry exists.
14. Evidence Guardrails pass.
15. No findings/root cause/verdict are generated.
16. Required tests pass.
17. GitHub CI passes.

---

# 27. Closeout Requirements

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
Subagent registration path
Parent agent relationship
Known limitations
Remaining risks
```

Do not mark Phase-03 complete without:

```text
a repeatable test using a real Phase-02.1 raw-evidence-index
evidence_structuring registered as mysql_analyzer subagent
tool allowlist proving no live collectors are callable from Evidence Subagent
```
