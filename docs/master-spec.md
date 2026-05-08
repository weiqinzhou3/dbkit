# DBKit Master Spec

Version: v0.4
Status: Draft

---

# 1. Project Overview

DBKit is an AI-native DBA analysis framework.

The framework is designed around:

- Prompt-first interaction
- LLM-driven reasoning
- Runtime-controlled orchestration
- Skill-guided methodology
- Deterministic tool execution
- Structured evidence processing
- Validation-driven conclusions
- Full-chain observability

---

# 2. Mandatory Runtime Foundation

DBKit MUST use DeepAgents SDK as the runtime agent framework.

DeepAgents SDK is a mandatory architectural constraint.

Replacing the runtime framework is outside project scope.

The project must not migrate to another runtime orchestration framework unless explicitly approved in a future architecture decision.

---

# 3. Core Architectural Principles

```text
LLM performs reasoning.
Runtime performs orchestration and enforcement.
Tools perform deterministic execution.
Guardrails enforce safety and constraints.
Validation enforces trustworthiness.
```

---

# 4. Architecture Responsibility Specification

The authoritative architecture responsibility document is:

```text
/docs/architecture/architecture-responsibility.md
```

This file defines:

- runtime responsibilities
- agent responsibilities
- skill responsibilities
- tool responsibilities
- guardrails responsibilities
- validation responsibilities
- execution authority matrix
- final architecture flow

All implementation phases MUST follow this architecture document.

---

# 5. Phase Index

Implementation phases are maintained independently.

```text
/docs/phases/
  phase-01-runtime-intake-mvp.md
  phase-01.1-runtime-intake-closure.md
  phase-01.2-intake-ux-runtime-time-context.md
  phase-02-evidence-planning-collection-mvp.md
  phase-03-evidence-structuring-mvp.md
  phase-04-mysql-analyzer-findings-mvp.md
```

Each phase spec must reference and comply with:

```text
/docs/architecture/architecture-responsibility.md
```
