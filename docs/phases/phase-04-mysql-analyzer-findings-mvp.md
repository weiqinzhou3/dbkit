# Phase-04 — MySQL Analyzer Findings MVP

Version: v0.1
Status: Planned
Depends on: Phase-03 Evidence Structuring MVP
Runtime Foundation: DeepAgents SDK

---

# 1. Purpose

Phase-04 introduces the first real DBA analytical output.

Input:

```text
NormalizedRequest
EvidenceRequest
EvidenceBundle
```

Output:

```text
Findings
Evidence Mapping
Validation
Verdict
Summary
```

The MySQL Analyzer Agent performs final domain reasoning based on structured evidence.

---

# 2. Phase Goal

Build MySQL Analyzer Findings MVP.

Support:

- MySQL Analyzer final analysis mode
- structured Findings
- evidence_refs binding
- missing evidence reasoning
- confidence scoring
- Validation Agent execution
- evidence mapping validation
- contradiction detection
- verdict generation
- summary output
- artifacts and telemetry

---

# 3. Architecture Rules

Responsibility split:

```text
MySQL Analyzer Agent:
  performs MySQL domain reasoning over EvidenceBundle

Validation Agent:
  checks evidence mapping, confidence, contradictions, and verdict

Runtime:
  orchestrates and persists artifacts

Tools:
  perform deterministic validation/mapping checks
```

Runtime must not generate findings.

Tools must not perform uncontrolled root cause reasoning.

No raw chain-of-thought may be persisted.

---

# 4. MySQL Analyzer Final Analysis Mode

Input:

```text
NormalizedRequest
EvidenceRequest
EvidenceBundle
```

Output:

```text
FindingsDraft
```

No final answer before validation.

## Finding Schema

```json
{
  "finding_id": "finding_xxx",
  "title": "High CPU likely related to slow queries",
  "severity": "warning",
  "confidence": 0.78,
  "summary": "Slow log and processlist indicate long-running queries during incident window.",
  "reasoning_summary": "Bounded explanation without chain-of-thought.",
  "evidence_refs": [
    "ev_mysql_slow_log_001",
    "ev_processlist_001"
  ],
  "supporting_evidence": [],
  "contradicting_evidence": [],
  "missing_evidence": [],
  "recommended_actions": [
    {
      "action": "Review top slow query digest",
      "risk": "low",
      "requires_approval": false
    }
  ]
}
```

## FindingsDraft Schema

```json
{
  "request_id": "req_xxx",
  "phase": "phase-04",
  "target_agent": "mysql_analyzer",
  "findings": [],
  "analysis_status": "draft",
  "metadata": {
    "skill": "skills/mysql-analyzer/SKILL.md",
    "mode": "findings_generation"
  }
}
```

---

# 5. Validation Agent

Validation Agent must run after FindingsDraft.

Checks:

```text
finding schema valid
all evidence_refs exist in EvidenceBundle
confidence values valid
missing_evidence explicit
contradictions identified
unsupported findings blocked
verdict generated
```

## Verdict Schema

```json
{
  "request_id": "req_xxx",
  "verdict": "pass",
  "allowed_outputs": ["summary"],
  "confidence_overall": 0.76,
  "blocked_findings": [],
  "warnings": [],
  "requires_human_review": false,
  "retry_recommended": false
}
```

Allowed verdict states:

```text
pass
retry
human_review
failed
```

---

# 6. Summary Output

Only after verdict pass or human_review.

Summary includes:

```text
incident overview
key findings
evidence references
confidence
missing evidence
recommended next actions
verdict status
artifact paths
```

No raw chain-of-thought.

---

# 7. Out of Scope

Do not implement:

- production remediation execution
- automatic SQL kill
- automatic config changes
- full approval workflow
- docx/pdf report
- web UI
- API
- full multi-round collection loop
```

If additional evidence is needed, output `additional_evidence_request`, but do not implement a full loop.

---

# 8. Skill Requirements

## MySQL Analyzer Skill

`skills/mysql-analyzer/SKILL.md` must include:

```text
Role
Modes
Findings Generation Mode
Evidence Usage Rules
Finding Schema
Confidence Rules
Missing Evidence Rules
Recommended Action Rules
Forbidden Behavior
```

Rules:

```text
Use only EvidenceBundle and evidence_refs.
Do not invent evidence.
Do not cite raw evidence without raw_ref.
Do not output final verdict.
Do not claim certainty when evidence is weak.
```

## Validation Skill

`skills/validation/SKILL.md` must include:

```text
Role
Input Contract
Evidence Mapping Rules
Confidence Rules
Contradiction Rules
Verdict Rules
Output Contract
Forbidden Behavior
```

---

# 9. Tools

Minimum tools:

```text
validate_findings_schema
map_evidence_refs
check_evidence_ref_exists
detect_contradictions
evaluate_confidence
generate_verdict
render_summary
```

---

# 10. Guardrails

Result Guardrails enforce:

```text
no finding without evidence_refs
no invalid evidence_refs
no raw secrets
no raw chain-of-thought
confidence in allowed range
verdict exists
summary only after validation
```

---

# 11. Artifacts

Required:

```text
.dbkit/artifacts/<request_id>.findings-draft.json
.dbkit/artifacts/<request_id>.evidence-mapping.json
.dbkit/artifacts/<request_id>.verdict.json
.dbkit/artifacts/<request_id>.summary.md
.dbkit/artifacts/<request_id>.analysis-telemetry.jsonl
```

---

# 12. Telemetry

Required events:

```text
mysql_analysis_started
mysql_analysis_completed
findings_draft_created
validation_started
evidence_mapping_completed
confidence_evaluated
contradiction_check_completed
verdict_generated
summary_rendered
analysis_blocked
```

No raw secrets or chain-of-thought.

---

# 13. CLI Behavior

Expected final output:

```text
DBKit 0.1.0
phase=phase-04
status=summary_generated
verdict=pass
findings=2
summary=.dbkit/artifacts/<request_id>.summary.md
verdict_artifact=.dbkit/artifacts/<request_id>.verdict.json
```

If evidence insufficient:

```text
status=human_review
reason=insufficient_evidence
```

---

# 14. Required Tests

- every finding has valid evidence_refs
- invalid evidence_ref is blocked
- confidence range valid
- no raw chain-of-thought
- no summary without verdict
- CPU alert scenario generates evidence-linked findings
- missing evidence scenario marks missing evidence
- contradiction scenario lowers confidence or flags contradiction
- no raw secrets

---

# 15. Success Criteria

Phase-04 is complete when:

1. MySQL Analyzer produces structured findings from EvidenceBundle.
2. Findings bind valid evidence_refs.
3. Validation Agent runs after findings.
4. Verdict is generated.
5. Summary is generated only after validation.
6. Unsupported findings are blocked or downgraded.
7. Missing evidence is explicit.
8. No raw chain-of-thought is persisted.
9. Required tests pass.
10. GitHub CI passes.
