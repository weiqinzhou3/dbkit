# Phase-01.1 — Runtime + Intake Closure

Version: v0.1  
Status: Active Planning  
Parent Phase: Phase-01 Runtime + Intake MVP  
Runtime Foundation: DeepAgents SDK

---

# 1. Purpose

Phase-01 has established the initial Runtime + Intake skeleton, but Intake is not yet closed enough to safely enter Phase-02.

Phase-01.1 exists to close these gaps:

- LLM Intake output is visible in tracing but is not consumed into final `NormalizedRequest`.
- `NormalizedRequest` schema is too thin for routing, guardrails, evidence planning, and future domain agents.
- `time_window` is not inferred from user-provided event time.
- Redactor does not reliably prevent secrets from reaching LLM context, artifacts, telemetry, or logs.
- `missing_fields` does not block CLI execution.
- `skills/intake/SKILL.md` is too generic and does not define a strict extraction contract.
- Agent system prompts must be externalized from code.
- JSON artifacts must be human-readable for Chinese text.
- Request Guardrails must become a first-class Phase-01 boundary.

DeepSeek V4 Runtime Compatibility has already been fixed and is out of scope for this phase.

---

# 2. Phase Goal

Turn Intake into a real structured request gateway:

```text
Raw User Input
  -> Runtime Redactor
  -> Intake Agent
  -> Machine-readable Intake JSON
  -> normalize_request
  -> Request Guardrails
  -> Router-ready NormalizedRequest
```

The output of this phase is not MySQL analysis.  
The output is a trustworthy, structured, routeable, redacted, validated `NormalizedRequest`.

---

# 3. Architecture Rules

This phase must follow:

```text
/docs/master-spec.md
/docs/architecture/architecture-responsibility.md
/docs/phases/phase-01-runtime-intake-mvp.md
AGENTS.md
```

Do not redefine architecture ownership.

Runtime may:

- load agents
- load skills
- load system prompts
- execute tools
- validate schemas
- enforce guardrails
- persist artifacts
- emit telemetry
- route requests

Runtime must not:

- define DBA business policies
- hardcode time-window defaults
- hardcode MySQL troubleshooting logic
- infer domain-specific evidence strategy outside skills
- replace LLM intake reasoning

Skills must define business rules and methodology.

For this phase, `skills/intake/SKILL.md` must define:

- target agent selection policy
- target domain extraction policy
- task type extraction policy
- time expression parsing policy
- default time-window policy
- target credential extraction policy
- SSH target extraction policy
- evidence plan generation policy
- missing field rules
- secret handling rules
- machine-readable output contract

Tools perform deterministic execution and transformation only.

---

# 4. In Scope

## 4.1 Intake Result Consumption

Implement a closed loop where the Intake Agent produces machine-readable structured output consumed by `normalize_request`.

Final `NormalizedRequest` must be derived from:

```text
redacted user input
+ CLI structured overrides
+ Intake Agent structured JSON
+ normalize_request deterministic validation/finalization
```

It must not ignore LLM structured output.

## 4.2 Expanded NormalizedRequest Schema

Required top-level fields:

```text
request_id
phase
target_agent
target_domain
task_type
routing_confidence
input_mode
target
ssh_target
provided_evidence
collection_policy
event
evidence_plan
missing_fields
redaction_summary
metadata
```

## 4.3 Time Window Inference

If user provides an event time but no explicit time window:

```text
time_window = event_time - before + event_time + after
```

The `before` and `after` defaults must come from `skills/intake/SKILL.md`, not hardcoded runtime code.

Default policy for this phase:

```text
alert_analysis: before=6h, after=1h
incident_analysis: before=6h, after=1h
```

User-specified time windows take precedence over skill defaults.

## 4.4 Redactor Closure

Implement deterministic regex-based redaction before any LLM invocation.

Redactor must prevent secrets from appearing in:

- LLM input
- artifacts
- telemetry
- logs
- traces

## 4.5 Request Guardrails

Implement Request Guardrails as Phase-01 behavior:

- schema validation
- missing field validation
- target agent validation
- target domain validation
- secret leakage validation
- routing validation
- time-window legality validation

## 4.6 Missing Field Blocking

If required fields are missing and cannot be defaulted, CLI must not silently succeed.

Acceptable behavior for Phase-01.1:

- print a clear missing-field message
- write blocked-request artifact
- exit non-zero

Interactive supplement flow may be added later.

## 4.7 Agent System Prompt Externalization

System prompts must be externalized from Python code.

Required files:

```text
agents/intake/system.md
agents/evidence/system.md
agents/validation/system.md
agents/mysql-analyzer/system.md
```

Only `agents/intake/system.md` must be actively used in Phase-01.1. Others may be placeholders.

## 4.8 Artifact Readability

All JSON artifacts must use:

```python
ensure_ascii=False
indent=2
sort_keys=True
```

Chinese text must be readable.

## 4.9 Structured Telemetry

Emit structured telemetry for:

- redaction
- intake agent call
- normalize_request
- request guardrails
- routing decision
- artifact write

Telemetry is not a business artifact.

---

# 5. Out of Scope

Do not implement:

- MySQL evidence collection
- MySQL reasoning
- Evidence Agent execution
- Validation Agent execution
- Findings generation
- Verdict generation
- Redis support
- FastAPI
- MCP
- Image evidence analysis
- Docx/PDF output
- Workflow engine
- Full interactive HITL approval system
- DeepSeek V4 compatibility work

---

# 6. Required Directory Changes

Expected conceptual structure:

```text
agents/
  intake/system.md
  evidence/system.md
  validation/system.md
  mysql-analyzer/system.md

skills/
  intake/SKILL.md

src/dbkit/
  runtime/
  guardrails/
  tools/
  schemas/
```

Exact Python module paths may follow the existing repo style, but conceptual boundaries must remain.

---

# 7. NormalizedRequest Contract

Required example shape:

```json
{
  "request_id": "req_xxx",
  "phase": "phase-01.1",
  "target_agent": "mysql_analyzer",
  "target_domain": "mysql",
  "task_type": "alert_analysis",
  "routing_confidence": 0.92,
  "input_mode": "live_collection",
  "target": {
    "type": "mysql",
    "host": "192.168.1.1",
    "port": 3306,
    "username": "root",
    "password_ref": "<SECRET_REF:mysql_password_001>"
  },
  "ssh_target": {
    "host": "192.168.1.1",
    "port": 22,
    "username": "root",
    "password_ref": "<SECRET_REF:ssh_password_001>"
  },
  "provided_evidence": {
    "mode": "unknown",
    "files": [],
    "pasted_text": false,
    "description": ""
  },
  "collection_policy": {
    "allow_live_collection": true,
    "allow_mysql_login": true,
    "allow_ssh": true,
    "allow_metrics_query": false
  },
  "event": {
    "event_time": "2026-05-07T17:00:00+08:00",
    "time_window": {
      "start": "2026-05-07T11:00:00+08:00",
      "end": "2026-05-07T18:00:00+08:00",
      "source": "skill_default_from_event_time",
      "before": "6h",
      "after": "1h"
    },
    "alerts": [
      {
        "raw": "mysql cpu usage > 85%",
        "name": "mysql cpu usage",
        "operator": ">",
        "threshold": 85,
        "unit": "percent",
        "semantic_hint": "high_cpu",
        "confidence": 0.8
      }
    ],
    "symptoms": ["high_cpu"]
  },
  "evidence_plan": {
    "required_evidence": [
      "mysql.runtime_status",
      "mysql.processlist",
      "mysql.slow_log",
      "mysql.error_log",
      "metrics.cpu"
    ],
    "provided_evidence": [],
    "missing_evidence": []
  },
  "missing_fields": [],
  "redaction_summary": {
    "redacted": true,
    "secret_refs": ["<SECRET_REF:mysql_password_001>"],
    "redacted_patterns": ["chinese_password_assignment"]
  },
  "metadata": {
    "normalizer": "llm_intake_plus_normalize_request",
    "skill": "skills/intake/SKILL.md"
  }
}
```

## Field Rules

`target_agent` allowed routing values:

```text
mysql_analyzer
redis_rdb_analyzer
redis_inspector
```

Do not route to system agents:

```text
intake_agent
evidence_agent
validation_agent
```

`target_domain` allowed minimum values:

```text
mysql
redis
unknown
```

`task_type` allowed minimum values:

```text
alert_analysis
incident_analysis
general_question
unknown
```

`missing_fields` should include only fields that are required and cannot be inferred or defaulted.

Do not mark `time_window` missing when `event_time` exists and skill default can apply.

---

# 8. Intake Skill Requirements

Rewrite `skills/intake/SKILL.md` as a strict extraction contract.

Required sections:

```text
Role
Input
Output Contract
Target Agent Selection
Target Domain Extraction
Task Type Extraction
Input Mode Classification
Time Understanding
Default Time Window Policy
Target Extraction
SSH Target Extraction
Provided Evidence Extraction
Collection Policy
Alert Parsing
Evidence Plan Guidance
Missing Field Rules
Secret Handling Rules
JSON Output Rules
Forbidden Behavior
```

Mandatory policy text:

```text
If the user provides event_time but does not provide time_window:
- alert_analysis defaults to event_time before 6h and after 1h.
- incident_analysis defaults to event_time before 6h and after 1h.
- user-specified time_window overrides this default.
```

Secret handling policy:

```text
Never output raw secrets.
Only output secret_ref placeholders produced by the runtime redactor.
If a raw secret appears in input, mark secret_leakage_suspected.
```

JSON output policy:

```text
Output JSON only.
No markdown fences.
No prose summary.
No free-form reasoning.
```

---

# 9. Redactor Requirements

Redaction must run before:

- DeepAgents invocation
- LangSmith tracing of LLM input
- artifact persistence
- telemetry emission

Minimum required patterns:

```text
中文:
  密码是xxx
  密码为xxx
  口令是xxx
  口令为xxx

English:
  password=xxx
  password: xxx
  password is xxx
  passwd=xxx
  pwd=xxx
  token=xxx
  secret=xxx
  api_key=xxx
  Authorization: Bearer xxx

Connection URIs:
  mysql://user:pass@host
  redis://:pass@host
  mongodb://user:pass@host
  postgres://user:pass@host
  postgresql://user:pass@host
```

Redactor must produce:

```text
redacted_text
secret_refs
redaction_summary
```

No raw secret may be persisted in normal artifacts.

---

# 10. Request Guardrails Requirements

Request Guardrails must check:

```text
NormalizedRequest schema valid
target_agent allowed
target_domain allowed
missing_fields policy
time_window legality
secret leakage
routing confidence threshold
```

If blocking issues exist:

- CLI exits non-zero
- prints actionable missing/invalid fields
- writes telemetry
- does not route to domain agent

Example blocked output:

```text
DBKit intake blocked by Request Guardrails.

Missing fields:
- target.host
- target.username

Suggested next input:
请补充 MySQL host、port、username。密码会通过安全输入方式读取，不会进入 LLM。
```

---

# 11. LLM Intake Consumption Requirements

Allowed flow:

```text
LLM structured JSON
  -> parse JSON
  -> normalize_request tool
  -> schema validation
  -> request guardrails
```

Disallowed flow:

```text
LLM returns natural language
  -> ignored
  -> deterministic parser produces final request
```

If LLM output is invalid JSON:

- record structured telemetry
- retry once with correction prompt, or
- fall back to deterministic parser only if artifact records `llm_intake_failed=true`

Fallback must be visible.

---

# 12. CLI Behavior

Success output:

```text
DBKit 0.1.0
phase=phase-01.1
target_agent=mysql_analyzer
target_domain=mysql
task_type=alert_analysis
artifact=.dbkit/artifacts/<request_id>.normalized-request.json
```

Blocked output:

```text
DBKit 0.1.0
status=blocked
reason=missing_required_fields
missing_fields=target.host,target.username
artifact=.dbkit/artifacts/<request_id>.blocked-request.json
```

No silent success when blocking `missing_fields` exist.

---

# 13. Artifact Requirements

Required artifacts:

```text
.dbkit/artifacts/<request_id>.normalized-request.json
.dbkit/artifacts/<request_id>.telemetry.jsonl
```

For blocked requests:

```text
.dbkit/artifacts/<request_id>.blocked-request.json
.dbkit/artifacts/<request_id>.telemetry.jsonl
```

Artifacts must not contain raw secrets.

---

# 14. Telemetry Requirements

Telemetry must be structured JSONL.

Required events:

```text
redaction_completed
intake_agent_started
intake_agent_completed
intake_json_parse_failed
normalize_request_started
normalize_request_completed
request_guardrails_started
request_guardrails_blocked
request_guardrails_passed
route_selected
artifact_written
```

Telemetry must not contain raw secrets.

---

# 15. Required Tests

## Time Window

Input:

```text
请帮我分析这个 MySQL，今天17:00触发 mysql cpu usage > 85%
```

Expected:

```text
event_time exists
time_window exists
time_window.source = skill_default_from_event_time
missing_fields does not contain time_window
```

## Explicit Time Window Override

Input:

```text
请分析今天17:00的告警，只看前1小时
```

Expected:

```text
time_window reflects user explicit range
source = user_explicit
```

## Chinese Secret Redaction

Input:

```text
MySQL密码是Root
```

Expected:

```text
Root does not appear in LLM input
Root does not appear in artifact
Root does not appear in telemetry
password_ref exists
```

## Connection URI Redaction

Input:

```text
mysql://root:Root@192.168.1.1:3306
```

Expected:

```text
Root does not appear
password_ref exists
target.host = 192.168.1.1
target.port = 3306
target.username = root
```

## Target Agent

Input:

```text
请分析 MySQL 连接数告警
```

Expected:

```text
target_agent = mysql_analyzer
target_domain = mysql
```

## System Agent Not Used as Route Target

Expected:

```text
target_agent != evidence_agent
target_agent != validation_agent
```

## Missing Field Blocking

Input:

```text
请分析数据库故障
```

Expected:

```text
guardrails block or request supplement
CLI does not silently succeed
```

## JSON Readability

Expected:

```text
artifact contains readable Chinese
artifact does not contain escaped Chinese such as \u8bf7
```

---

# 16. Success Criteria

Phase-01.1 is complete when:

1. Intake Agent output is machine-readable JSON.
2. LLM Intake output is consumed by `normalize_request`.
3. `NormalizedRequest` schema supports routing, target info, event info, evidence plan, missing fields, and redaction summary.
4. `time_window` is inferred from `event_time` using skill-defined defaults.
5. Redactor prevents secrets from entering LLM, artifacts, telemetry, and logs.
6. Request Guardrails enforce schema, missing fields, target validation, time validation, and secret leakage checks.
7. CLI blocks on missing required fields.
8. Agent system prompt is externalized under `agents/`.
9. Chinese artifacts are readable.
10. Required tests pass.
11. GitHub CI passes.

---

# 17. Closeout Requirements

Implementation closeout must report:

```text
Branch
Commit
Tests run
CI URL/status
Manual CLI test commands
Example successful output
Example blocked output
Known limitations
Remaining risks
```

Do not mark Phase-01.1 complete without verification evidence.
