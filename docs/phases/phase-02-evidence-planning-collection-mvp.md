# Phase-02 — Evidence Planning & Collection MVP

Version: v0.1
Status: Active Planning
Depends on: Phase-01 / Phase-01.1 / Phase-01.2
Runtime Foundation: DeepAgents SDK

---

# 1. Purpose

Phase-02 introduces the controlled bridge from `NormalizedRequest` to raw operational evidence.

Core responsibility split:

```text
MySQL Analyzer Agent:
  decides what evidence is needed and produces EvidenceRequest

Collector Tools:
  execute deterministic collection or file reading

Evidence Agent:
  not responsible for deciding what to collect in this phase
```

This phase does not produce root cause findings, verdicts, or final summaries.

Phase-02 output:

```text
NormalizedRequest
  -> MySQL Analyzer Agent in evidence_planning mode
  -> EvidenceRequest
  -> CollectionPlan
  -> RawEvidence
```

---

# 2. Phase Goal

Build the first evidence planning and collection workflow.

The phase must support:

- using Phase-01 `NormalizedRequest`
- invoking MySQL Analyzer Agent in evidence planning mode
- producing structured `EvidenceRequest`
- converting EvidenceRequest into guarded `CollectionPlan`
- executing collector/file tools through Tool Executor and Guardrails
- supporting `provided_evidence`, `live_collection`, and `hybrid`
- persisting `RawEvidence`
- emitting collection telemetry

---

# 3. Architecture Rules

Follow:

```text
/docs/master-spec.md
/docs/architecture/architecture-responsibility.md
/docs/phases/phase-01-runtime-intake-mvp.md
/docs/phases/phase-01.1-runtime-intake-closure.md
/docs/phases/phase-01.2-intake-ux-runtime-time-context.md
AGENTS.md
```

Rules:

- Runtime must not hardcode MySQL troubleshooting SOP.
- MySQL Analyzer decides evidence needs, not Runtime.
- Evidence Agent must not decide what to collect.
- Collector Tools execute collection only.
- No live collection may bypass Guardrails.
- Raw secrets must never enter LLM, artifacts, telemetry, or logs.

---

# 4. In Scope

## 4.1 MySQL Analyzer Evidence Planning Mode

`skills/mysql-analyzer/SKILL.md` must define `evidence_planning` mode.

In this mode the MySQL Analyzer Agent outputs only `EvidenceRequest`.

It must not output:

```text
root_cause
findings
verdict
summary
recommended remediation
```

## 4.2 EvidenceRequest Schema

Required shape:

```json
{
  "request_id": "req_xxx",
  "phase": "phase-02",
  "target_agent": "mysql_analyzer",
  "target_domain": "mysql",
  "task_type": "alert_analysis",
  "input_mode": "live_collection",
  "reasoning_mode": "evidence_planning",
  "evidence_request": {
    "goal": "collect evidence for MySQL CPU alert analysis",
    "required_evidence": [
      {
        "evidence_type": "mysql.runtime_status",
        "priority": "required",
        "purpose": "capture MySQL runtime counters around the incident window",
        "source": "mysql",
        "tool_hint": "collect_mysql_runtime_status"
      }
    ],
    "optional_evidence": [],
    "not_required_evidence": [],
    "missing_inputs": [],
    "approval_requirements": []
  },
  "metadata": {
    "skill": "skills/mysql-analyzer/SKILL.md",
    "mode": "evidence_planning"
  }
}
```

## 4.3 CollectionPlan Schema

Runtime converts validated EvidenceRequest into CollectionPlan.

Required shape:

```json
{
  "request_id": "req_xxx",
  "collection_plan_id": "cp_xxx",
  "phase": "phase-02",
  "input_mode": "live_collection",
  "steps": [
    {
      "step_id": "step_001",
      "evidence_type": "mysql.runtime_status",
      "tool_name": "collect_mysql_runtime_status",
      "target_ref": "target",
      "requires_secret_refs": ["<SECRET_REF:mysql_password_001>"],
      "requires_approval": false,
      "timeout_seconds": 30,
      "purpose": "capture MySQL runtime counters"
    }
  ],
  "guardrails_status": "pending"
}
```

## 4.4 Collector Tools

Minimum tools:

```text
read_provided_evidence_file
read_provided_evidence_directory
collect_mysql_runtime_status
collect_processlist
collect_innodb_status
collect_mysql_variables
collect_mysql_error_log
collect_mysql_slow_log
collect_metrics_snapshot
```

Every tool returns a structured `RawEvidence` object.

Unimplemented tools must return:

```text
status=not_implemented
```

Do not fake successful live collection.

## 4.5 RawEvidence Schema

Required shape:

```json
{
  "raw_evidence_id": "rawev_xxx",
  "request_id": "req_xxx",
  "evidence_type": "mysql.error_log",
  "source": {
    "kind": "file",
    "path": "/workspace/tmp/mysql-error.log",
    "host": null,
    "tool_name": "read_provided_evidence_file"
  },
  "collection": {
    "status": "collected",
    "started_at": "2026-05-08T10:00:00+08:00",
    "completed_at": "2026-05-08T10:00:02+08:00",
    "duration_ms": 2000,
    "errors": []
  },
  "payload": {
    "content_ref": ".dbkit/artifacts/raw/rawev_xxx.txt",
    "bytes": 10240,
    "line_count": 120
  },
  "metadata": {
    "time_window": {
      "start": "2026-05-08T11:00:00+08:00",
      "end": "2026-05-08T18:00:00+08:00"
    }
  }
}
```

Large raw payloads must be referenced, not embedded.

## 4.6 Input Modes

### provided_evidence

Use `NormalizedRequest.provided_evidence.files`.

Do not require MySQL/SSH login.

Do not execute live collectors unless mode is hybrid and policy allows it.

### live_collection

Use `target`, `ssh_target`, and `collection_policy`.

Live collection must pass Guardrails.

### hybrid

Use provided files first.

Live collection only for explicitly allowed collection types.

---

# 5. Guardrails

Collection Guardrails must check:

```text
tool exists
tool allowed for input_mode
collection_policy permits tool
secret refs exist
no raw secrets in parameters
path inside workspace
file size within limit
target/ssh_target required fields exist
timeout configured
approval required if configured
dangerous commands blocked
```

---

# 6. Artifacts

Required:

```text
.dbkit/artifacts/<request_id>.evidence-request.json
.dbkit/artifacts/<request_id>.collection-plan.json
.dbkit/artifacts/<request_id>.raw-evidence-index.json
.dbkit/artifacts/raw/<raw_evidence_id>.<ext>
.dbkit/artifacts/<request_id>.collection-telemetry.jsonl
```

JSON must use `ensure_ascii=False`, `indent=2`, `sort_keys=True`.

---

# 7. Telemetry

Required events:

```text
evidence_planning_started
evidence_planning_completed
evidence_request_validated
collection_plan_created
collection_guardrails_started
collection_guardrails_passed
collection_guardrails_blocked
collector_started
collector_completed
collector_failed
raw_evidence_written
collection_plan_completed
```

---

# 8. Out of Scope

Do not implement:

- EvidenceBundle generation
- Evidence Agent structuring
- final MySQL findings
- root cause analysis
- validation verdict
- remediation recommendations
- report generation

---

# 9. CLI Behavior

Provided evidence example:

```bash
python3.11 main.py --config config/config.yaml   "请帮我分析这个 MySQL，今天17:00触发 mysql cpu usage > 85%，只需要分析本地文件，文件在/tmp/mysql_conn_full_mock/。"
```

Expected:

```text
DBKit 0.1.0
phase=phase-02
status=raw_evidence_collected
input_mode=provided_evidence
raw_evidence_count=4
artifact=.dbkit/artifacts/<request_id>.raw-evidence-index.json
```

---

# 10. Required Tests

- CPU alert request produces EvidenceRequest.
- Evidence planning output contains no findings/root cause/verdict.
- provided_evidence files become RawEvidence.
- provided_evidence directory becomes RawEvidence entries.
- live_collection missing target is blocked.
- raw secrets absent from artifacts and telemetry.
- collection artifacts are Chinese-readable.

---

# 11. Success Criteria

Phase-02 is complete when:

1. MySQL Analyzer evidence planning mode produces EvidenceRequest.
2. Runtime creates CollectionPlan.
3. Collector tools create RawEvidence.
4. provided_evidence mode works end-to-end.
5. live_collection is guarded.
6. No findings/root cause/verdict are produced.
7. Artifacts and telemetry exist.
8. Required tests pass.
9. GitHub CI passes.
