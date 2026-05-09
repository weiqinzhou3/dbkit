# Phase-04 — MySQL Findings, Validation, Verdict & Summary MVP

Version: v0.1  
Status: Active Planning  
Depends on: Phase-03 Evidence Structuring Subagent MVP  
Runtime Foundation: DeepAgents SDK  
Domain Agent: `mysql_analyzer`  
Required Input: `EvidenceBundle`

---

# 1. Purpose

Phase-04 turns Phase-03 `EvidenceBundle` into evidence-bound MySQL diagnostic outputs:

```text
EvidenceBundle
  -> MySQL Analyzer Agent / mode=findings_generation
  -> FindingsDraft
  -> Validation Agent
  -> ValidatedFindings
  -> Verdict
  -> Human-readable Summary
```

Phase-04 is the first phase that may produce MySQL diagnostic findings.

Phase-04 must not perform new collection, raw evidence parsing, or evidence structuring. Those responsibilities belong to Phase-02.1 and Phase-03.

---

# 2. Phase Goal

Build the first end-to-end DBA analysis output over existing structured evidence.

This phase must support:

- loading Phase-03 `EvidenceBundle`
- invoking `mysql_analyzer` in `findings_generation` mode
- generating structured findings with evidence refs
- validating every finding against `EvidenceBundle`
- blocking unsupported conclusions
- producing a verdict with confidence and human-review flags
- producing a bounded human-readable summary
- preserving artifact lineage from request -> raw evidence -> evidence bundle -> findings -> verdict
- emitting observability events for analysis and validation

---

# 3. Current Architecture Assumption

Current MVP may invoke `mysql_analyzer` in separate continuation calls:

```text
mode=evidence_planning
mode=evidence_structuring_delegation
mode=findings_generation
```

This is acceptable for Phase-04 MVP.

However, the semantic owner remains one domain workflow:

```text
mysql_analyzer owns the MySQL incident analysis workflow.
```

Known limitation:

```text
The target future architecture is a single mysql_analyzer workflow session with internal state transitions.
```

Do not attempt that single-session runtime refactor in Phase-04.

---

# 4. End-to-End Flow

Expected Phase-04 flow:

```text
User Prompt
  -> Intake Agent
  -> NormalizedRequest
  -> mysql_analyzer / mode=evidence_planning
  -> EvidenceRequest
  -> Collector Tools
  -> RawEvidence
  -> mysql_analyzer delegates evidence_structuring subagent
  -> EvidenceBundle
  -> mysql_analyzer / mode=findings_generation
  -> FindingsDraft
  -> Validation Agent
  -> ValidationResult
  -> Verdict
  -> Summary
```

Replay flow:

```text
EvidenceBundle artifact
  -> mysql_analyzer / mode=findings_generation
  -> FindingsDraft
  -> Validation Agent
  -> Verdict
  -> Summary
```

---

# 5. Inputs

Phase-04 input is a Phase-03 `EvidenceBundle`.

Required artifact:

```text
.dbkit/artifacts/<request_id>.evidence-bundle.json
```

Phase-04 may also load lineage artifacts for traceability:

```text
.dbkit/artifacts/<request_id>.normalized-request.json
.dbkit/artifacts/<request_id>.evidence-request.json
.dbkit/artifacts/<request_id>.raw-evidence-index.json
.dbkit/artifacts/<request_id>.collection-plan.json
.dbkit/artifacts/<request_id>.evidence-processing-telemetry.jsonl
```

Important rule:

```text
Phase-04 must consume EvidenceBundle, not raw logs directly.
```

If EvidenceBundle is missing, stale, or invalid, Phase-04 must block and instruct the workflow to run Phase-03 first.

---

# 6. Outputs

Required artifacts:

```text
.dbkit/artifacts/<request_id>.findings-draft.json
.dbkit/artifacts/<request_id>.validation-result.json
.dbkit/artifacts/<request_id>.verdict.json
.dbkit/artifacts/<request_id>.summary.md
.dbkit/artifacts/<request_id>.analysis-telemetry.jsonl
```

Optional artifacts:

```text
.dbkit/artifacts/<request_id>.finding-evidence-map.json
.dbkit/artifacts/<request_id>.human-review.json
```

CLI output should include:

```text
DBKit 0.1.0
phase=phase-04
status=analysis_completed | analysis_completed_with_warnings | human_review_required | validation_failed
target_agent=mysql_analyzer
evidence_bundle_artifact=.dbkit/artifacts/<request_id>.evidence-bundle.json
findings_artifact=.dbkit/artifacts/<request_id>.findings-draft.json
validation_artifact=.dbkit/artifacts/<request_id>.validation-result.json
verdict_artifact=.dbkit/artifacts/<request_id>.verdict.json
summary_artifact=.dbkit/artifacts/<request_id>.summary.md
```

---

# 7. Responsibility Split

## 7.1 MySQL Analyzer Agent

`mysql_analyzer` in `findings_generation` mode may:

```text
read EvidenceBundle
identify candidate findings
rank candidate findings
bind findings to evidence_refs
state assumptions
state missing evidence
assign preliminary severity and confidence
produce FindingsDraft
```

`mysql_analyzer` must not:

```text
read raw logs directly
invent evidence
skip evidence refs
call live collectors
modify remote systems
generate final verdict without validation
```

## 7.2 Validation Agent

Validation Agent may:

```text
validate finding schema
validate evidence refs exist
validate evidence refs support finding
detect unsupported or overstated claims
detect contradiction
downgrade confidence
request retry/revision
mark human_review_required
produce ValidationResult
```

Validation Agent must not:

```text
invent new findings
perform new live collection
silently rewrite findings without trace
```

## 7.3 Runtime

Runtime may:

```text
load artifacts
invoke mysql_analyzer findings_generation mode
invoke validation agent
enforce guardrails
persist artifacts
emit telemetry
block invalid outputs
```

Runtime must not:

```text
decide root cause
generate findings through hardcoded MySQL rules
bypass Validation Agent
```

---

# 8. MySQL Analyzer Skill Updates

Update `skills/mysql-analyzer/SKILL.md`.

Must add or confirm sections:

```text
Findings Generation Mode
EvidenceBundle Input Contract
Evidence Ref Requirements
Finding Severity Rules
Confidence Rules
Missing Evidence Rules
Forbidden Claims
Validation Handoff
```

Required policy text:

```text
In findings_generation mode, you must consume EvidenceBundle only.
Do not directly analyze RawEvidence artifacts.
Every finding must cite existing evidence_refs.
Do not produce root cause claims without supporting evidence_refs.
Do not produce final verdict directly; output FindingsDraft for Validation Agent.
If evidence is insufficient, produce an insufficient_evidence finding or request human review.
```

---

# 9. Evidence Ref Contract

Every finding must bind to `EvidenceBundle.evidence_items`.

Valid reference forms:

```json
{
  "evidence_id": "ev_xxx",
  "raw_evidence_id": "rawev_xxx",
  "evidence_type": "mysql.error_log",
  "raw_ref": {
    "content_ref": ".dbkit/artifacts/raw/rawev_xxx.txt",
    "line_start": 10,
    "line_end": 40
  }
}
```

Rules:

- `evidence_id` must exist in EvidenceBundle.
- `evidence_type` must match the referenced EvidenceItem.
- raw_refs must exist when the claim depends on raw text or rows.
- finding must not cite nonexistent evidence.
- finding must not cite low-quality evidence as high-confidence without explanation.
- if evidence is unavailable, finding must state limitation.

---

# 10. FindingsDraft Schema

Required shape:

```json
{
  "request_id": "req_xxx",
  "phase": "phase-04",
  "mode": "findings_generation",
  "target_agent": "mysql_analyzer",
  "input_evidence_bundle": ".dbkit/artifacts/req_xxx.evidence-bundle.json",
  "findings": [
    {
      "finding_id": "finding_xxx",
      "title": "Large number of aborted MySQL connections observed",
      "category": "connection",
      "severity": "medium",
      "confidence": 0.78,
      "status": "candidate",
      "statement": "The error log contains repeated aborted connection events during the incident window.",
      "evidence_refs": [
        {
          "evidence_id": "ev_xxx",
          "evidence_type": "mysql.error_log",
          "raw_refs": [
            {
              "content_ref": ".dbkit/artifacts/raw/rawev_xxx.txt",
              "line_start": 1,
              "line_end": 20
            }
          ]
        }
      ],
      "supporting_signals": [
        "mysql.error_log top pattern: aborted_connection",
        "mysql.runtime_status Aborted_clients is elevated"
      ],
      "contradicting_signals": [],
      "assumptions": [],
      "missing_evidence": [],
      "recommended_next_checks": []
    }
  ],
  "insufficient_evidence": [],
  "metadata": {
    "skill": "skills/mysql-analyzer/SKILL.md",
    "runtime_foundation": "DeepAgents SDK"
  }
}
```

---

# 11. Finding Categories

Allowed categories:

```text
connection
availability
performance
high_cpu
lock_contention
slow_query
configuration
resource_pressure
log_signal
service_state
unknown
```

---

# 12. Severity Rules

Allowed severities:

```text
critical
high
medium
low
info
```

Guidance:

```text
critical:
  service unavailable, crash/restart, no healthy connection path, severe data risk

high:
  clear incident-level degradation, too many connections, severe lock contention, persistent failure

medium:
  meaningful anomaly but service may still be partially available

low:
  weak anomaly, non-critical warning, limited scope

info:
  context-only observation
```

Severity must be justified by evidence.

Do not escalate severity solely because a pattern appears many times unless operational impact evidence exists.

---

# 13. Confidence Rules

Confidence must be numeric:

```text
0.0 - 1.0
```

Guidance:

```text
0.9+:
  multiple strong evidence sources, low contradiction, direct support

0.7 - 0.89:
  strong evidence but some gaps

0.5 - 0.69:
  plausible but missing important corroboration

<0.5:
  weak or incomplete evidence
```

Validation Agent may downgrade confidence.

If evidence_refs are missing, confidence must be forced below `0.5` or finding must be blocked.

---

# 14. ValidationResult Schema

Required shape:

```json
{
  "request_id": "req_xxx",
  "phase": "phase-04",
  "input_findings_artifact": ".dbkit/artifacts/req_xxx.findings-draft.json",
  "input_evidence_bundle": ".dbkit/artifacts/req_xxx.evidence-bundle.json",
  "validated_findings": [
    {
      "finding_id": "finding_xxx",
      "validation_status": "passed",
      "confidence_after_validation": 0.76,
      "evidence_ref_check": "passed",
      "support_check": "passed",
      "contradiction_check": "none",
      "validation_notes": []
    }
  ],
  "blocked_findings": [],
  "downgraded_findings": [],
  "requires_human_review": false,
  "validation_summary": {
    "passed": 1,
    "blocked": 0,
    "downgraded": 0
  }
}
```

Validation statuses:

```text
passed
downgraded
blocked
requires_human_review
```

---

# 15. Verdict Schema

Required shape:

```json
{
  "request_id": "req_xxx",
  "phase": "phase-04",
  "status": "analysis_completed_with_warnings",
  "overall_severity": "medium",
  "overall_confidence": 0.76,
  "primary_findings": ["finding_xxx"],
  "secondary_findings": [],
  "insufficient_evidence": [],
  "requires_human_review": false,
  "human_review_reasons": [],
  "next_actions": [
    {
      "action": "Review client or DBKit connection lifecycle for aborted connections",
      "risk": "low",
      "requires_approval": false
    }
  ],
  "artifact_refs": {
    "evidence_bundle": ".dbkit/artifacts/req_xxx.evidence-bundle.json",
    "findings": ".dbkit/artifacts/req_xxx.findings-draft.json",
    "validation": ".dbkit/artifacts/req_xxx.validation-result.json"
  }
}
```

Rules:

- Verdict must be derived only after validation.
- Verdict cannot include findings that were blocked.
- Verdict should mention uncertainty and evidence gaps.
- Action items must not execute anything.
- Risky actions must be marked as requiring approval.

---

# 16. Summary Output

`summary.md` should be human-readable.

Recommended structure:

```markdown
# DBKit MySQL Analysis Summary

## 1. Analysis Scope

## 2. Evidence Used

## 3. Primary Findings

## 4. Supporting Evidence

## 5. Evidence Gaps / Limitations

## 6. Suggested Next Checks

## 7. Artifact References
```

Summary rules:

- Chinese output by default if user prompt is Chinese.
- Do not expose raw secrets.
- Do not dump raw logs.
- Every finding must have evidence references.
- If confidence is low, say so explicitly.
- If validation blocked findings, mention they were blocked, not silently removed.
- No remediation execution.

---

# 17. Handling Common MySQL Signals

Phase-04 should understand structured evidence signals such as:

```text
mysql.error_log top_patterns.semantic_hint=aborted_connection
mysql.error_log semantic_hint=too_many_connections
mysql.runtime_status Aborted_clients
mysql.runtime_status Aborted_connects
mysql.runtime_status Threads_connected
mysql.runtime_status Threads_running
mysql.variables max_connections
mysql.processlist sleeping_connections
mysql.processlist active_query_count
mysql.innodb_status lock wait hints
os.mysql_service_status service inactive
metrics.os_cpu high load / top CPU process
metrics.os_memory swap or low available memory
metrics.os_disk high usage
mysql.slow_log unavailable: slow_query_log_disabled
```

Important:

```text
These are evidence signals, not automatic root causes.
```

Example:

- `Aborted connection` + high `Aborted_clients` may support a connection issue finding.
- `Threads_connected` near `max_connections` may support connection saturation.
- service inactive may support availability failure.
- CPU snapshot must corroborate high CPU before making high CPU finding.
- slow log disabled should be reported as an evidence gap.

---

# 18. Validation Rules

Validation Agent must block or downgrade findings when:

```text
finding has no evidence_refs
evidence_refs do not exist
finding cites unavailable evidence as if available
finding overstates severity
finding asserts root cause with only one weak signal
finding contradicts EvidenceBundle
finding includes raw secret
finding recommends dangerous action without approval flag
```

Validation Agent may request one retry/revision from `mysql_analyzer`.

Retry limit:

```text
max_validation_retries=1
```

If still invalid:

```text
status=validation_failed or human_review_required
```

---

# 19. Guardrails

Phase-04 guardrails must check:

```text
EvidenceBundle exists
EvidenceBundle schema valid
FindingsDraft schema valid
every finding has evidence_refs
evidence_refs exist
confidence values in range
severity values allowed
no raw secrets
no root claim without evidence
no unsupported remediation execution
ValidationResult exists before Verdict
Verdict does not include blocked findings
Summary matches Verdict
```

If guardrails fail:

```text
status=blocked
reason=phase04_guardrails_failed
```

---

# 20. Telemetry

Required events:

```text
phase04_started
evidence_bundle_loaded
mysql_analyzer_findings_generation_started
mysql_analyzer_findings_generation_completed
findings_draft_created
validation_started
validation_completed
validation_retry_requested
finding_blocked
finding_downgraded
verdict_created
summary_created
phase04_completed
phase04_failed
```

Event fields:

```text
request_id
target_agent=mysql_analyzer
mode=findings_generation
input_evidence_bundle
finding_count
validated_count
blocked_count
overall_confidence
overall_severity
duration_ms
status
```

Telemetry must not include raw secrets or chain-of-thought.

---

# 21. CLI Behavior

## 21.1 Normal Full Workflow

For the current Phase-04 MVP, a normal MySQL analysis command may run:

```text
Phase-01 -> Phase-02.1 -> Phase-03 -> Phase-04
```

Example:

```bash
python3.11 main.py --config config/config.yaml \
  '<MySQL live incident request>'
```

Expected output:

```text
DBKit 0.1.0
phase=phase-04
status=analysis_completed_with_warnings
target_agent=mysql_analyzer
overall_severity=medium
overall_confidence=0.76
findings=1
human_review_required=false
summary=.dbkit/artifacts/<request_id>.summary.md
verdict=.dbkit/artifacts/<request_id>.verdict.json
```

## 21.2 Replay from EvidenceBundle

Phase-04 must support repeatable replay from existing EvidenceBundle.

Preferred CLI:

```bash
python3.11 main.py --config config/config.yaml \
  --from-evidence-bundle .dbkit/artifacts/<request_id>.evidence-bundle.json
```

Expected:

```text
phase=phase-04
status=analysis_completed...
```

This replay path is for debugging/regression.

---

# 22. Required Tests

## 22.1 Load EvidenceBundle

Given a valid EvidenceBundle, Phase-04 loads it.

## 22.2 Findings Generation

`mysql_analyzer` in `findings_generation` mode produces FindingsDraft.

## 22.3 Evidence Ref Required

A finding without evidence refs is blocked.

## 22.4 Evidence Ref Exists

A finding referencing nonexistent evidence is blocked.

## 22.5 Aborted Connection Finding

Given EvidenceBundle with `mysql.error_log` `semantic_hint=aborted_connection` and runtime status `Aborted_clients`, Phase-04 produces an evidence-bound connection finding.

## 22.6 Slow Log Disabled Evidence Gap

Given `mysql.slow_log not_available`, Phase-04 records evidence gap, not fake slow query finding.

## 22.7 No Raw Evidence Direct Read

Phase-04 must not read raw logs directly for findings generation.

## 22.8 Validation Downgrade

Weakly supported finding is downgraded.

## 22.9 Verdict Requires Validation

Verdict is not produced before validation.

## 22.10 Summary Matches Verdict

Summary uses validated findings only.

## 22.11 No Secret Leakage

No raw secrets in findings, verdict, summary, telemetry.

## 22.12 Replay CLI

`--from-evidence-bundle` works.

## 22.13 Full Workflow CLI

Normal CLI can run through Phase-04.

---

# 23. Manual Acceptance Test

Use a real Phase-03 EvidenceBundle, for example:

```text
.dbkit/artifacts/<request_id>.evidence-bundle.json
```

Run:

```bash
python3.11 main.py --config config/config.yaml \
  --from-evidence-bundle .dbkit/artifacts/<request_id>.evidence-bundle.json
```

Verify:

```text
findings-draft.json exists
validation-result.json exists
verdict.json exists
summary.md exists
findings have evidence_refs
validation passed or downgraded findings
summary cites artifact references
no root_cause without evidence
no secrets
```

Then run normal full workflow without replay:

```bash
python3.11 main.py --config config/config.yaml \
  '<MySQL incident prompt with live collection allowed>'
```

Verify it reaches Phase-04.

---

# 24. Out of Scope

Do not implement:

```text
single mysql_analyzer session refactor
new live collectors
new evidence parsers unless required by validation
remediation execution
query killing
config changes
docx/pdf report
web UI
MCP
FastAPI
```

Do not let Phase-04 bypass:

```text
EvidenceBundle
Validation Agent
Guardrails
```

---

# 25. Success Criteria

Phase-04 is complete when:

1. Existing EvidenceBundle can be analyzed.
2. `mysql_analyzer` findings_generation mode generates structured FindingsDraft.
3. Every finding has valid evidence refs.
4. Validation Agent blocks/downgrades unsupported findings.
5. Verdict is generated only after validation.
6. Summary is generated from validated findings.
7. No raw secrets appear in artifacts or telemetry.
8. No raw logs are dumped into findings or summary.
9. Replay from EvidenceBundle works.
10. Normal workflow can reach Phase-04.
11. Required tests pass.
12. GitHub CI passes.
13. Manual acceptance using real EvidenceBundle succeeds.

---

# 26. Closeout Requirements

Implementation closeout must report:

```text
Branch
Commit
Tests run
CI URL/status
Manual --from-evidence-bundle command
Manual full workflow command
EvidenceBundle input path
FindingsDraft artifact path
ValidationResult artifact path
Verdict artifact path
Summary artifact path
Finding count
Validated finding count
Blocked finding count
Overall severity
Overall confidence
Human review status
Known limitations
Remaining risks
```

Do not mark Phase-04 complete if:

```text
findings lack evidence refs
validation is bypassed
verdict exists without validation
summary includes unsupported claims
raw secrets leak
normal workflow cannot reach Phase-04
```
