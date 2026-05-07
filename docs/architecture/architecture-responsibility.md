# DBKit Architecture Responsibility Specification

Version: v0.1

---

# 1. Purpose

This document defines DBKit architectural responsibilities and ownership boundaries.

It is the primary architecture constraint document for coding agents.

---

# 2. Core Principle

```text
Runtime controls execution.
Agents control reasoning.
Skills control methodology.
Tools control deterministic operations.
Guardrails control operational safety.
Validation controls trustworthiness.
```

---

# 3. Runtime Layer

The runtime layer is responsible for:

- orchestration
- routing
- tool execution
- runtime safety
- lifecycle management
- observability
- artifact persistence

Runtime Layer may enforce schemas, safety, routing validity, tool permissions, and artifact lifecycle.

Runtime Layer MUST NOT define business policies.

The runtime layer must NOT perform DBA reasoning.

Business policies include but are not limited to:
- default incident time windows
- evidence selection rules
- domain analysis heuristics
- alert interpretation rules
- DBA troubleshooting SOPs
- confidence interpretation rules

All business policies MUST live in Skills.

Runtime may execute, validate, and enforce Skill-derived outputs, but must not silently redefine them in code.

## Runtime Components

```text
Runtime Layer
  ├── Orchestrator
  ├── Redactor
  ├── Router
  ├── Tool Executor
  ├── Guardrails
  ├── Artifacts
  └── Observability
```

## Orchestrator

The Orchestrator is the runtime lifecycle controller.

Responsibilities:

- stage progression
- runtime state management
- agent lifecycle management
- retry handling
- timeout handling
- validation enforcement
- artifact lifecycle coordination
- final result aggregation

The Orchestrator MUST NOT:

- analyze MySQL
- analyze logs
- generate findings
- decide root causes
- hardcode DBA reasoning flows
- replace agent reasoning

## Redactor

The Redactor is the deterministic secret isolation boundary before any LLM context is created.

Responsibilities:

- redact secrets from user input
- redact secrets from runtime inputs before agent invocation
- support deterministic regex-based redaction
- emit structured redaction telemetry
- prevent secrets from entering LLM context

The Redactor MUST NOT:

- perform free-form reasoning
- persist redaction telemetry as artifacts
- pass raw secrets to agents or tools

## Router

The Router selects domain agents.

Responsibilities:

- receive normalized requests
- determine target domain agents
- determine execution routing

The Router is NOT an AI reasoning layer.

## Tool Executor

The Tool Executor is the deterministic execution bridge.

Responsibilities:

- execute approved tool calls
- pass tool calls through guardrails
- normalize tool responses
- expose execution metadata

## Guardrails

Guardrails are runtime enforcement systems.

Responsibilities:

- secret isolation
- schema validation
- missing field validation
- tool permission checks
- approval checks
- dangerous operation blocking
- runtime policy enforcement
- evidence validation
- confidence enforcement
- verdict enforcement

## Artifacts

Artifacts persist DBKit business outputs.

Responsibilities:

- persist EvidenceBundle
- persist Findings
- persist Verdict
- persist Summary
- support replayability
- support auditability

Artifacts MUST NOT:

- persist runtime telemetry as business artifacts
- mix observability trace events with business outputs
- define Evidence Processing Telemetry as an artifact

## Observability

Observability is a cross-cutting runtime telemetry and explainability system.

Observability exists to make runtime execution, evidence transformation, bounded-context reduction, reasoning support, validation decisions, and operational trustworthiness explainable and auditable.

Responsibilities:

- structured runtime telemetry
- stage lifecycle visibility
- tool execution visibility
- guardrail decision visibility
- evidence processing trace events
- bounded-context reduction visibility
- runtime cost telemetry
- structured analysis telemetry
- validation decision telemetry
- failure visibility
- timing and latency visibility
- telemetry replay visibility

Runtime Cost Telemetry must make the cost and reduction profile visible, including:

- raw_bytes
- filtered_bytes
- compression_ratio
- estimated_tokens
- tool_latency_ms

Structured Analysis Telemetry must describe reasoning support without dumping free-form chain-of-thought.

It may include:

- hypothesis
- supporting evidence references
- contradicting evidence references
- missing evidence
- confidence delta

Observability MUST NOT:

- persist business outputs as telemetry
- create EvidenceProcessingLog artifacts
- dump free-form chain-of-thought
- replace Artifacts
- replace Validation

---

# 4. Agent Responsibilities

Agents are reasoning units.

Agents:

- load skills
- maintain isolated LLM contexts
- perform reasoning
- decide tool usage
- generate structured outputs

Agents are NOT runtime controllers.

## Intake Agent

Skill:

```text
skills/intake/SKILL.md
```

Responsibilities:

- intent understanding
- input mode classification
- alert parsing
- time understanding
- provided evidence extraction
- collection permission extraction
- missing field detection
- normalized request generation

## Evidence Agent

Skill:

```text
skills/evidence/SKILL.md
```

The Evidence Pipeline exists to transform large-scale raw operational data into structured, bounded, trustworthy, LLM-safe evidence representations.

The Evidence Pipeline must expose structured telemetry to Observability describing raw data volume, filtering decisions, deduplication results, aggregation trends, retained evidence, discarded evidence, context reduction ratios, and evidence quality issues.

Responsibilities:

- evidence classification
- evidence normalization
- evidence filtering
- evidence deduplication
- evidence aggregation
- time-window trimming
- structured evidence generation

## Validation Agent

Skill:

```text
skills/validation/SKILL.md
```

Responsibilities:

- evidence mapping
- confidence evaluation
- contradiction detection
- verdict generation

## MySQL Analyzer Agent

Skill:

```text
skills/mysql-analyzer/SKILL.md
```

Responsibilities:

- MySQL incident reasoning
- evidence requirement decisions
- tool selection
- finding generation
- reasoning generation

The MySQL Analyzer Agent controls:

```text
HOW MySQL incidents are analyzed
```

---

# 5. Skill Responsibilities

Skills define reasoning methodology.

Skills:

- guide LLM reasoning
- describe evidence expectations
- describe tool usage guidance
- describe output expectations
- describe validation expectations

Skills MUST NOT:

- execute tools
- replace runtime enforcement
- perform deterministic execution
- mutate runtime state

---

# 6. Tool Responsibilities

Tools are deterministic execution interfaces.

Responsibilities:

- data collection
- normalization
- structuring
- validation
- deterministic transformation

Tools MUST NOT:

- perform uncontrolled autonomous reasoning
- bypass guardrails
- orchestrate runtime stages

---

# 7. Validation Responsibilities

Validation is mandatory.

Validation responsibilities:

- trustworthiness
- evidence mapping
- contradiction detection
- confidence evaluation
- verdict generation

No findings may bypass validation.

---

# 8. Execution Authority Matrix

| Layer | Owns Reasoning | Owns Execution | Owns Safety | Owns Validation | Owns Persistence |
|---|---|---|---|---|---|
| Runtime Layer | No | Yes | Yes | Enforcement Only | Yes |
| Agents | Yes | No | No | Partial Reasoning | No |
| Skills | Guidance Only | No | No | Guidance Only | No |
| Tools | No | Yes | No | Deterministic Only | No |
| Guardrails | No | Enforcement Only | Yes | Enforcement Only | No |
| Validation | Partial Reasoning | No | Enforcement Only | Yes | No |
| Artifacts | No | No | No | No | Yes |
| Observability | No | No | No | No | Telemetry Only |

---

# 9. Final Architecture Flow

```text
User Input
  -> Runtime Redactor
  -> Intake Agent
  -> normalize_request
  -> Guardrails Validation
  -> Router
  -> MySQL Analyzer Agent
  -> Tool Executor
  -> Guardrails
  -> Tools
  -> Evidence Agent
  -> Structured Evidence
  -> Validation Agent
  -> Verdict
  -> Artifacts

Observability cross-cuts all stages as runtime telemetry.
```
