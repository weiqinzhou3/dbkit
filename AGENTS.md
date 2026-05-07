# DBKit Agent Instructions

This file is repo-level operating guidance for coding agents working on DBKit.

## Mandatory Reading

Before implementation work, read the relevant docs:

- `docs/master-spec.md`
- `docs/architecture/architecture-responsibility.md`
- the current phase spec under `docs/phases/`

Do not infer architecture boundaries from code alone.

## Project Mission

DBKit is an AI-native DBA analysis framework.

The core problem is not just MySQL analysis. The hard problem is:

```text
Raw Operational Data
  -> Structured / Bounded / Trustworthy LLM Evidence
```

MySQL analysis is the first domain analyzer that validates this framework.

## Runtime Constraint

DBKit MUST use DeepAgents SDK as the runtime agent framework.

Do not replace DeepAgents SDK with another runtime or orchestration framework unless a future architecture decision explicitly approves it.

## Architecture Boundaries

Follow the authority model in `docs/architecture/architecture-responsibility.md`.

- Runtime controls execution, orchestration, enforcement, lifecycle, persistence, and observability.
- Agents control reasoning.
- Skills control methodology.
- Tools perform deterministic operations.
- Guardrails control operational safety.
- Validation controls trustworthiness.
- Artifacts persist business outputs.
- Observability emits runtime telemetry.

The Runtime and Orchestrator MUST NOT perform DBA reasoning, analyze MySQL, decide root causes, generate findings, or hardcode domain reasoning flows.

## Evidence Pipeline

The Evidence Pipeline exists to transform large-scale raw operational data into structured, bounded, trustworthy, LLM-safe evidence representations.

It is not a simple log formatter.

Evidence Pipeline work must preserve visibility into:

- raw data volume
- filtering decisions
- deduplication results
- aggregation trends
- retained evidence
- discarded evidence
- context reduction ratios
- evidence quality issues

Trend and aggregation preprocessing belongs in the Evidence stage, not as ad hoc Analyzer behavior over raw logs.

## Observability

DBKit Observability is a cross-cutting runtime telemetry and explainability system.

It must make runtime execution, evidence transformation, bounded-context reduction, reasoning support, validation decisions, and operational trustworthiness explainable and auditable.

Observability is not just printing logs.

Observability events include runtime telemetry such as:

- `stage_started`
- `stage_completed`
- `stage_failed`
- `tool_called`
- `tool_completed`
- `guardrail_blocked`
- `evidence_filtered`
- `dedup_completed`
- `aggregation_completed`
- `validation_failed`
- `verdict_generated`

Runtime Cost Telemetry must make reduction and cost visible, including raw bytes, post-filter bytes, compression ratio, evidence shrink ratio, estimated LLM context tokens, tool latency, and stage latency.

## Artifacts vs Telemetry

Keep Artifacts and Observability strictly separated.

Artifacts are business outputs:

- `EvidenceBundle`
- `Findings`
- `Verdict`
- `Summary`

Observability is runtime telemetry:

- stage events
- tool events
- guardrail events
- evidence processing trace events
- runtime cost telemetry
- validation decision telemetry

Do not create `EvidenceProcessingLog` as a business artifact. Use Evidence Processing Telemetry or Evidence Processing Trace Events.

## Analysis Telemetry

Analyzer observability must be structured.

Allowed structured fields include:

- hypothesis
- supporting evidence references
- contradicting evidence references
- missing evidence
- confidence delta

Do not dump free-form chain-of-thought into logs, traces, artifacts, or telemetry.

## Validation

Validation is mandatory.

Validation is responsible for:

- trustworthiness
- evidence mapping
- contradiction detection
- confidence evaluation
- verdict generation

No finding may bypass Validation.

Validation telemetry must explain why a verdict is `pass`, `retry`, `human_review`, or `failed`.

## Phase Discipline

Respect phase scope.

- Phase 01 builds the runnable Runtime + Intake skeleton. It does not perform real DBA analysis.
- Phase 02 builds the Evidence Pipeline. It does not generate final DBA findings.
- Phase 03 builds the MySQL Analyzer and Validation workflow.
- Phase 04 hardens runtime policies, approvals, artifact lifecycle, cleanup, and observability.

Do not pull later-phase behavior into earlier phases unless the user explicitly changes the phase spec.

## GitHub Workflow

Use one branch per phase.

Recommended branch names:

- `phase-01-runtime-intake`
- `phase-02-evidence-pipeline`
- `phase-03-mysql-analyzer`
- `phase-04-runtime-hardening`

Do not mix unrelated phase work in the same branch.

Before committing:

- inspect the diff
- verify the phase scope
- confirm generated files do not start with timestamps
- run the relevant checks for the changed scope

Commit messages must clearly state what changed and why.

Use phase-prefixed commit messages when possible, for example:

```text
phase-02: add evidence reduction telemetry schema
```

Pull requests must describe:

- phase scope
- key implementation changes
- checks run
- known limitations
- follow-up work

## Check Round Closeup

After every review or check round, perform an automatic closeup.

A closeup must record or report:

- what was checked
- what passed
- what failed
- what changed as a result
- remaining risks or open questions
- next action

If a check fails, fix within the current phase scope before widening scope.

Do not claim work is complete without verification evidence.

## Documentation Rules

Generated files must not begin with a timestamp.

Keep architecture docs concise and authoritative. Do not duplicate full specs into generated guidance files.

When architecture docs and implementation convenience conflict, follow the architecture docs.
