# Phase-01 — Runtime + Intake MVP

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

Build the first runnable DBKit runtime skeleton.

This phase validates:

- DeepAgents SDK integration
- Runtime Layer structure
- Agent lifecycle model
- Skill loading model
- Tool registration model
- Intake flow
- NormalizedRequest generation
- Guardrails foundation
- Artifact foundation
- Observability foundation

This phase does NOT perform real DBA analysis.

---

# 2. Required Runtime Flow

```text
User Input
  -> Redactor
  -> Intake Agent
  -> normalize_request
  -> Guardrails Validation
  -> Router
  -> Artifacts
```

---

# 3. Included Scope

- Orchestrator skeleton
- Router skeleton
- Tool Executor skeleton
- Guardrails foundation
- Artifacts foundation
- Observability foundation
- Intake Agent
- Intake Skill
- normalize_request tool

---

# 4. Explicitly Out of Scope

- MySQL evidence collection
- MySQL reasoning
- Findings generation
- Evidence Agent
- Validation Agent
- Verdict generation

---

# 5. Success Criteria

- DeepAgents SDK runtime is wired
- Intake Agent can load its skill
- normalize_request tool works
- Guardrails validate NormalizedRequest
- Router can select target agent name
- Artifacts are persisted
- Logs/traces exist
