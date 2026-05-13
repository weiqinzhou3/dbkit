# Phase-04.1 — Evidence Structuring Token & Performance Remediation Spec

Version: v0.1
Status: Active Planning
Branch Target: `phase-04-mysql-findings-validation-verdict`
Related Commit Observed: `22100b4 phase-04: optimize evidence structuring and timeout handling`
Depends on: Phase-03 Evidence Structuring Subagent MVP, Phase-04 Findings / Validation / Verdict MVP
Runtime Foundation: DeepAgents SDK
Primary Concern: Phase-03 evidence structuring token explosion and runtime latency

---

# 1. Purpose

This spec fixes a serious architecture and performance issue discovered during Phase-04 validation:

```text
Evidence Structuring Subagent is reading RawEvidence artifacts with read_file.
read_file results enter LLM context.
Phase-03 consumes ~200K tokens.
Evidence structuring takes several minutes.
```

This violates DBKit's intended architecture.

Correct architecture:

```text
Phase-02.1:
  Collect RawEvidence.

Phase-03:
  Perform large-scale cleaning, filtering, aggregation, deduplication, compression.
  RawEvidence -> EvidenceBundle.
  Deterministic tools do the heavy data processing.
  LLM/subagent only orchestrates.

Phase-04:
  Create a compact LLM analysis view from EvidenceBundle.
  EvidenceBundle -> compact_analysis_context.
  This is only secondary selection/reordering, not the main compression step.
```

Phase-04.1 is a remediation phase to restore this boundary.

---

# 2. Problem Statement

LangSmith shows Phase-03 consuming about:

```text
Input tokens:  ~182K
Output tokens: ~17K
Total tokens:  ~200K
```

This is not acceptable.

The likely root cause is:

```text
evidence_structuring subagent
  -> read_file raw-evidence-index
  -> read_file raw artifact A
  -> read_file raw artifact B
  -> read_file raw artifact C
  -> read_file error log / processlist / status / variables
  -> raw file contents enter LLM context
  -> LLM participates in data cleaning
```

This is wrong.

The correct shape is:

```text
evidence_structuring subagent
  -> call build_evidence_bundle exactly once
  -> build_evidence_bundle internally reads raw artifacts
  -> deterministic parsers clean/filter/aggregate/compress
  -> build_evidence_bundle writes EvidenceBundle artifact
  -> build_evidence_bundle returns small JSON result to subagent
```

---

# 3. Non-Negotiable Architecture Boundary

## 3.1 LLM / Subagent Role

The Evidence Structuring Subagent may:

```text
receive the raw_evidence_index path
call build_evidence_bundle
return the small tool result
```

The Evidence Structuring Subagent must not:

```text
read raw evidence artifacts with read_file
inspect raw logs manually
inspect raw SHOW GLOBAL STATUS / VARIABLES manually
inspect every raw artifact one by one
summarize raw file content directly
perform final findings / diagnosis / verdict
```

## 3.2 Tool Role

The `build_evidence_bundle` tool must own:

```text
loading raw-evidence-index
loading raw artifacts from payload.content_ref
path normalization
time-window filtering
timezone normalization
log parsing
status / variables / processlist parsing
OS metrics parsing
deduplication
aggregation
EvidenceBundle assembly
artifact writing
small result return
```

## 3.3 Runtime Role

Runtime may:

```text
register subagent
register build_evidence_bundle tool
pass raw_evidence_index path to subagent
validate EvidenceBundle schema
write telemetry
enforce guardrails
```

Runtime must not:

```text
become semantic owner of evidence cleaning
parse MySQL evidence directly
generate EvidenceItems with hardcoded business logic outside tools
bypass evidence_structuring subagent
```

---

# 4. Required Workflow

## 4.1 Normal Workflow

```text
User Prompt
  -> Intake Agent
  -> mysql_analyzer / evidence_planning
  -> Collector Tools
  -> RawEvidenceIndex
  -> mysql_analyzer delegation step
  -> evidence_structuring subagent
  -> build_evidence_bundle tool
  -> EvidenceBundle
  -> mysql_analyzer / findings_generation
  -> Validation
  -> Verdict / Summary
```

## 4.2 Phase-03 Subagent Expected Behavior

Normal Phase-03 subagent behavior must be:

```text
1 model call
1 build_evidence_bundle tool call
small JSON response
```

It must not show many `read_file` calls against:

```text
.dbkit/artifacts/*.raw-evidence-index.json
.dbkit/artifacts/raw/*.json
.dbkit/artifacts/raw/*.txt
```

---

# 5. Tool Contract: build_evidence_bundle

## 5.1 Input

Required input:

```json
{
  "request_id": "req_xxx",
  "raw_evidence_index_virtual_path": "/repo/.dbkit/artifacts/req_xxx.raw-evidence-index.json",
  "raw_evidence_index_repo_path": ".dbkit/artifacts/req_xxx.raw-evidence-index.json",
  "artifact_root": ".dbkit/artifacts",
  "max_workers": 4,
  "per_item_timeout_seconds": 30,
  "total_timeout_seconds": 120
}
```

The exact path names may follow repository convention, but the tool must receive enough information to resolve paths without asking the LLM to read files.

## 5.2 Internal Processing

The tool must internally:

```text
1. Resolve raw_evidence_index path.
2. Load raw-evidence-index from filesystem.
3. Validate request_id lineage.
4. Resolve each payload.content_ref.
5. Load raw artifacts directly in Python/tool implementation.
6. Dispatch each RawEvidence item to deterministic parser.
7. Process independent RawEvidence items concurrently.
8. Collect parser results.
9. Apply deduplication and coverage aggregation.
10. Assemble EvidenceBundle.
11. Persist EvidenceBundle artifact.
12. Return small status JSON.
```

## 5.3 Output

The tool must return a small JSON object only:

```json
{
  "status": "evidence_bundle_created",
  "request_id": "req_xxx",
  "artifact": ".dbkit/artifacts/req_xxx.evidence-bundle.json",
  "evidence_items": 12,
  "quality": "usable_with_warnings",
  "warnings": [],
  "duration_ms": 12345
}
```

The tool must not return:

```text
full EvidenceBundle
raw logs
raw processlist rows
raw status rows
raw variables rows
full InnoDB text
full OS command output
```

---

# 6. Tool Allowlist Changes

For `evidence_structuring` subagent, the preferred allowed tool list for this remediation phase is:

```text
build_evidence_bundle
```

Optional additional tools are allowed only if they do not return raw artifact content to the LLM.

The following must be blocked for raw artifact paths:

```text
read_file
ls
glob
```

If DeepAgents filesystem tools cannot be globally disabled, enforce at the prompt and guardrail layer:

```text
evidence_structuring must not use read_file for .dbkit/artifacts or .dbkit/artifacts/raw.
```

A guardrail should block `read_file` calls matching:

```text
/repo/.dbkit/artifacts/*.raw-evidence-index.json
/repo/.dbkit/artifacts/raw/*
.dbkit/artifacts/*.raw-evidence-index.json
.dbkit/artifacts/raw/*
```

---

# 7. Parallel Processing Requirement

Phase-03 must not process RawEvidence items serially if they are independent.

The following evidence types can be processed concurrently:

```text
mysql.processlist
mysql.runtime_status
mysql.innodb_status
mysql.variables
mysql.service_metadata
mysql.log_paths
mysql.error_log
mysql.slow_log
metrics.os_cpu
metrics.os_memory
metrics.os_disk
os.mysql_service_status
```

Recommended config:

```yaml
evidence_structuring:
  max_workers: 4
  per_item_timeout_seconds: 30
  total_timeout_seconds: 120
  recursion_limit: 8
  max_tool_calls: 1
  required_tool: build_evidence_bundle
```

Rules:

- Per-item parser failure must not fail the whole bundle.
- Failed item must be recorded as `low_quality_evidence` or `skipped_raw_evidence`.
- Slow item timeout must be recorded with quality warning.
- Final EvidenceBundle should still be created when enough evidence is usable.

---

# 8. Phase-04 compact_analysis_context Boundary

Phase-04 may keep `compact_analysis_context`, but its role must be precise.

## 8.1 Correct Role

`compact_analysis_context` is:

```text
A bounded LLM analysis view over an already-structured EvidenceBundle.
```

It is not:

```text
the main data cleaning step
the primary compression step
a replacement for Phase-03 evidence structuring
a place to compensate for raw evidence leaking into LLM context
```

## 8.2 Required Contents

It must preserve:

```text
request_id
incident/event/time_window
coverage summary
quality warnings
all evidence_id / evidence_type / summary
mysql.error_log top_patterns
mysql.runtime_status key counters
mysql.processlist aggregates
mysql.variables key values
metrics.os_* summaries
mysql.slow_log unavailable/low-quality status
raw_refs summary
artifact refs
```

## 8.3 Forbidden Contents

It must not include:

```text
full raw logs
full SHOW GLOBAL STATUS rows
full SHOW GLOBAL VARIABLES rows
full processlist rows
full InnoDB status text
full OS command output
```

## 8.4 Audit Artifact

Phase-04 must write:

```text
.dbkit/artifacts/<request_id>.compact-analysis-context.json
```

This is required for auditing whether compaction lost important signals.

Telemetry must include:

```text
input_chars_before
input_chars_after
max_prompt_chars
context_truncated
omitted_sections
included_evidence_count
included_evidence_ids
included_signal_sections
```

---

# 9. Performance Acceptance Criteria

## 9.1 Token Budget

Evidence structuring subagent target:

```text
small dataset: < 20K total LLM tokens
```

Hard failure threshold for normal small MySQL incident case:

```text
evidence_structuring total LLM tokens must not approach 200K
```

If token usage exceeds threshold, closeout must explain why.

## 9.2 Timing Budget

For the current small MySQL incident scenario:

```text
Phase-03 evidence_structuring target: < 60s
Phase-03 evidence_structuring warning threshold: > 120s
Phase-04 findings_generation target: < 120s
```

If Phase-04 model times out, status must be:

```text
analysis_timeout
```

not:

```text
human_review_required
```

---

# 10. Timeout Semantics

`human_review_required` must not be used for model timeout.

Correct timeout result:

```json
{
  "phase": "phase-04",
  "status": "analysis_timeout",
  "reason": "findings_generation_timeout",
  "request_id": "req_xxx",
  "evidence_bundle_artifact": ".dbkit/artifacts/req_xxx.evidence-bundle.json",
  "analysis_telemetry": ".dbkit/artifacts/req_xxx.analysis-telemetry.jsonl"
}
```

`human_review_required` is only for:

```text
evidence contradiction
insufficient evidence requiring DBA judgement
validation cannot determine support
high-risk action requiring approval
```

---

# 11. Deprecation Warning Remediation

Fix these warnings by changing initialization code, not by hiding warnings.

## 11.1 files_update

Deprecated warning:

```text
files_update was deprecated in deepagents 0.5.0
```

Action:

```text
remove files_update usage
use current DeepAgents backend/internal file state handling
```

## 11.2 allowed_objects

Pending warning:

```text
The default value of allowed_objects will change in a future version.
```

Action:

```text
explicitly pass allowed_objects
```

Preferred:

```python
JsonPlusSerializer(allowed_objects="messages")
```

Use `"core"` only if required by actual stored checkpoint objects.

---

# 12. Required Telemetry

Add or confirm:

```text
evidence_subagent_invoked
build_evidence_bundle_tool_started
build_evidence_bundle_tool_completed
raw_artifact_loaded_inside_tool
evidence_item_processing_started
evidence_item_processing_completed
evidence_item_processing_failed
evidence_processing_parallel_started
evidence_processing_parallel_completed
evidence_bundle_created
compact_analysis_context_created
phase04_findings_generation_timeout
```

Required telemetry attributes:

```text
request_id
parent_agent=mysql_analyzer
subagent=evidence_structuring
raw_evidence_index
evidence_bundle_artifact
duration_ms
subagent_input_chars
tool_result_chars
raw_bytes_processed_inside_tool
evidence_items_processed
parallel_workers
context_truncated
omitted_sections
```

Telemetry must not include secrets or raw logs.

---

# 13. Required Tests

## 13.1 Evidence Subagent Does Not Read Raw Artifacts

Assert evidence_structuring subagent does not call `read_file` for:

```text
.dbkit/artifacts/*.raw-evidence-index.json
.dbkit/artifacts/raw/*
```

## 13.2 Single Tool Call

Assert normal Phase-03 subagent path calls `build_evidence_bundle` exactly once.

## 13.3 Tool Returns Small JSON

Assert `build_evidence_bundle` tool result does not include:

```text
full EvidenceBundle
raw logs
full rows
large structured payloads
```

## 13.4 Tool Internally Loads Raw Artifacts

Assert `build_evidence_bundle` internally reads raw_evidence_index and content_ref artifacts.

## 13.5 EvidenceBundle Quality Regression

Assert generated EvidenceBundle still contains:

```text
mysql.error_log top_patterns
aborted_connection semantic_hint
mysql.processlist aggregates
mysql.runtime_status key counters
mysql.variables key values
mysql.service_metadata
mysql.log_paths
OS metrics summaries
raw_refs
```

## 13.6 Parallel Processing

Assert independent evidence items can be processed concurrently.

## 13.7 Compact Context Audit

Assert `.compact-analysis-context.json` exists and contains:

```text
all evidence IDs
summaries
top_patterns
key counters
quality warnings
coverage
```

Assert it does not contain raw logs or full rows.

## 13.8 Timeout Semantics

Assert findings generation timeout returns:

```text
status=analysis_timeout
reason=findings_generation_timeout
```

not `human_review_required`.

## 13.9 Deprecation Warnings

Smoke test confirms no `files_update` warning and no `allowed_objects` warning.

---

# 14. Manual Acceptance

Use the same live MySQL incident prompt.

Expected CLI behavior:

```text
DBKit 0.1.0
phase=phase-03
status=evidence_bundle_created
parent_agent=mysql_analyzer
subagent=evidence_structuring
artifact=.dbkit/artifacts/<request_id>.evidence-bundle.json
phase=phase-04
status=analysis_completed... OR analysis_timeout
```

LangSmith expectations:

```text
Evidence structuring subagent should not show many read_file calls for raw artifacts.
Evidence structuring token usage should drop significantly.
build_evidence_bundle tool call should be visible.
Tool result should be small.
```

Artifacts expected:

```text
.dbkit/artifacts/<request_id>.evidence-bundle.json
.dbkit/artifacts/<request_id>.compact-analysis-context.json
.dbkit/artifacts/<request_id>.analysis-telemetry.jsonl
```

If Phase-04 succeeds:

```text
.dbkit/artifacts/<request_id>.findings-draft.json
.dbkit/artifacts/<request_id>.validation-result.json
.dbkit/artifacts/<request_id>.verdict.json
.dbkit/artifacts/<request_id>.summary.md
```

If Phase-04 times out:

```text
.dbkit/artifacts/<request_id>.analysis-timeout.json
```

---

# 15. Closeout Requirements

Closeout must report:

```text
Branch
Commit
Tests run
CI status
Live command used
EvidenceBundle artifact path
Compact analysis context artifact path
Phase-03 LangSmith token usage before/after
Phase-03 duration before/after
Whether read_file raw artifact calls disappeared
build_evidence_bundle tool call count
parallel worker count
Phase-04 status
Timeout behavior if any
Warnings removed
Known limitations
Remaining risks
```

Do not mark Phase-04.1 complete if:

```text
evidence_structuring still reads raw artifacts through LLM read_file
Phase-03 still consumes near 200K tokens for small case
build_evidence_bundle returns full EvidenceBundle to LLM
Phase-04 compact context omits evidence IDs or key signals
timeout is reported as human_review_required
deprecation warnings remain
```
