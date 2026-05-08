# Phase-03 — Evidence Structuring MVP

Version: v0.1
Status: Planned
Depends on: Phase-02 Evidence Planning & Collection MVP
Runtime Foundation: DeepAgents SDK

---

# 1. Purpose

Phase-03 turns collected `RawEvidence` into structured, bounded, LLM-safe `EvidenceBundle`.

Core responsibility split:

```text
MySQL Analyzer Agent:
  already decided evidence needs in Phase-02

Collector Tools:
  already collected RawEvidence in Phase-02

Evidence Agent:
  cleans, filters, deduplicates, aggregates, and structures RawEvidence
```

This phase does not perform final MySQL diagnosis.

Phase-03 output:

```text
RawEvidence
  -> Evidence Agent / Evidence Tools
  -> EvidenceBundle
```

---

# 2. Phase Goal

Build the Evidence Structuring layer.

Support:

- RawEvidence index input
- time-window filtering
- MySQL error log parsing
- MySQL slow log parsing
- processlist structuring
- Prometheus metrics parsing
- deduplication
- aggregation
- raw refs
- evidence quality flags
- bounded EvidenceBundle
- evidence processing telemetry

---

# 3. Architecture Rules

Evidence Agent does not decide what to collect.

Evidence Agent does not generate root cause, findings, verdict, or final summary.

Runtime orchestrates and enforces guardrails.

Tools perform deterministic transformations.

---

# 4. EvidenceBundle Schema

Required shape:

```json
{
  "request_id": "req_xxx",
  "phase": "phase-03",
  "bundle_id": "evb_xxx",
  "time_window": {
    "start": "2026-05-08T11:00:00+08:00",
    "end": "2026-05-08T18:00:00+08:00"
  },
  "evidence_items": [],
  "coverage": {
    "required_evidence": [],
    "available_evidence": [],
    "missing_evidence": [],
    "low_quality_evidence": []
  },
  "quality": {
    "overall_status": "usable",
    "warnings": []
  },
  "processing_summary": {},
  "metadata": {
    "skill": "skills/evidence/SKILL.md"
  }
}
```

## EvidenceItem Schema

```json
{
  "evidence_id": "ev_xxx",
  "raw_evidence_id": "rawev_xxx",
  "evidence_type": "mysql.error_log",
  "source": {
    "kind": "file",
    "path": "/workspace/tmp/mysql-error.log",
    "tool_name": "read_provided_evidence_file"
  },
  "time_range": {
    "start": "2026-05-08T11:00:00+08:00",
    "end": "2026-05-08T18:00:00+08:00",
    "timestamp_parse_status": "ok"
  },
  "summary": "Error log contains repeated connection errors around incident window.",
  "structured_payload": {},
  "raw_refs": [
    {
      "raw_evidence_id": "rawev_xxx",
      "line_start": 10,
      "line_end": 40
    }
  ],
  "quality_flags": [],
  "llm_safe": true
}
```

---

# 5. Evidence Processing Tools

Minimum tools:

```text
classify_raw_evidence
parse_mysql_error_log
parse_mysql_slow_log
parse_processlist
parse_prometheus_metrics
filter_by_time_window
deduplicate_events
aggregate_log_patterns
aggregate_metrics_timeseries
estimate_token_size
build_evidence_bundle
```

---

# 6. Processing Requirements

## Error Log

Support:

- timestamp extraction
- time-window filtering
- repeated event grouping
- error pattern extraction
- severity hints
- raw line references
- unparseable timestamp handling

If timestamps cannot be parsed, set:

```text
timestamp_parse_status=failed
```

## Slow Log

Support:

- query time
- lock time
- rows examined / sent
- user / host
- SQL digest grouping
- top N slow query patterns
- raw refs

Do not put large SQL dumps into EvidenceBundle.

## Processlist

Support:

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

Produce:

- active query count
- sleeping connection count
- long-running queries
- top states
- top users
- samples with raw refs

## Metrics

Support Prometheus text format minimally.

Produce:

- metric name
- labels
- samples
- min/max/avg/p95 where possible
- spike windows
- missing metric warnings

---

# 7. Bounded Context Reduction

EvidenceBundle must be bounded and LLM-safe.

Telemetry must include:

```text
raw_bytes
filtered_bytes
compression_ratio
estimated_tokens_before
estimated_tokens_after
discarded_lines
retained_events
dedup_count
top_patterns
```

Large raw evidence must be referenced, not embedded.

---

# 8. Guardrails

Evidence Guardrails must check:

```text
raw evidence exists
raw evidence path allowed
file size within limit
EvidenceBundle size within limit
raw secrets absent/redacted
time-window filtering status recorded
raw_refs valid
EvidenceItem schema valid
```

---

# 9. Artifacts

Required:

```text
.dbkit/artifacts/<request_id>.evidence-bundle.json
.dbkit/artifacts/<request_id>.evidence-processing-telemetry.jsonl
.dbkit/artifacts/evidence/<evidence_id>.json
```

---

# 10. Out of Scope

Do not implement:

- root cause judgment
- findings
- confidence verdict
- remediation recommendations
- additional evidence planning
- new live collectors
- docx/pdf reports

---

# 11. Skill Requirements

`skills/evidence/SKILL.md` must define:

```text
Role
Input Contract
Output Contract
Evidence Types
Processing Rules
Time Window Rules
Deduplication Rules
Aggregation Rules
Raw Reference Rules
LLM-safe Context Rules
Forbidden Behavior
```

Must state:

```text
Do not diagnose root cause.
Do not generate findings.
Do not invent missing raw data.
Always preserve raw_refs.
Prefer summaries and aggregation over raw dumps.
```

---

# 12. CLI Behavior

Expected output:

```text
DBKit 0.1.0
phase=phase-03
status=evidence_bundle_created
evidence_items=4
artifact=.dbkit/artifacts/<request_id>.evidence-bundle.json
```

No root cause summary.

---

# 13. Required Tests

- error log becomes EvidenceItem with raw_refs
- slow log becomes digest/top patterns
- processlist becomes structured counts/states
- metrics becomes metrics summary
- time-window filtering works
- timestamp parse failures are explicit
- large raw evidence is bounded
- EvidenceBundle contains no findings/verdict
- raw secrets absent

---

# 14. Success Criteria

Phase-03 is complete when:

1. RawEvidence becomes EvidenceBundle.
2. EvidenceItems are structured.
3. Error log / slow log / processlist / metrics are minimally supported.
4. Time-window filtering works.
5. Raw refs are preserved.
6. Output is bounded and LLM-safe.
7. Processing telemetry exists.
8. No findings/root cause/verdict are generated.
9. Required tests pass.
10. GitHub CI passes.
