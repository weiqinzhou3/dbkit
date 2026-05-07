# Phase-02 — Evidence Pipeline MVP

Version: v0.4
Status: Active Planning

---

# Architecture Dependency

This phase MUST comply with:

```text
/docs/architecture/architecture-responsibility.md
```

The phase must not redefine architectural ownership. It may only implement the responsibilities defined by the architecture document.

---


# 1. Phase Goal

Build the unified evidence ingestion and evidence structuring pipeline.

This phase validates:

- live evidence collection
- user-provided evidence ingestion
- Evidence Agent lifecycle
- EvidenceBundle schema
- evidence normalization
- log filtering
- evidence deduplication
- evidence aggregation
- evidence time-window trimming
- structured evidence persistence

This phase still does NOT perform final DBA findings generation.

---

# 2. Required Evidence Flow

```text
Raw Evidence
  -> Evidence Agent
  -> Evidence Tools
  -> Filtering
  -> Deduplication
  -> Aggregation
  -> Time-window Trimming
  -> Structured EvidenceBundle
  -> Artifacts
```

---

# 3. Included Scope

- Evidence Agent
- Evidence Skill
- evidence ingestion tools
- log normalization tools
- deduplication tools
- aggregation tools
- EvidenceBundle persistence
- evidence observability

---

# 4. Evidence Sources

Live sources:

- MySQL
- SSH
- Prometheus-compatible APIs
- local log files

User-provided sources:

- pasted logs
- uploaded text files
- uploaded SQL outputs
- uploaded processlist outputs
- uploaded InnoDB status outputs

---

# 5. Guardrails Requirements

- maximum evidence size
- maximum log window
- forbidden path checks
- secret isolation
- unsupported evidence rejection
- malformed evidence rejection

---

# 6. Explicitly Out of Scope

- final DBA findings
- root cause analysis
- verdict generation
- confidence scoring
- production approval workflows

---

# 7. Success Criteria

- evidence can be collected or ingested
- raw evidence is structured
- logs can be filtered and deduplicated
- EvidenceBundle is persisted
- evidence observability works
