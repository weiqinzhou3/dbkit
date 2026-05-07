# Phase 01 Runtime Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 01 Runtime + Intake MVP skeleton with DeepAgents SDK wiring, normalized request generation, guardrails, routing, artifact persistence, and structured telemetry.

**Architecture:** Runtime owns orchestration, enforcement, observability, and artifact persistence. Intake owns request normalization only; no MySQL analysis, evidence collection, findings, validation verdicts, or root-cause reasoning are implemented in this phase.

**Tech Stack:** Python 3.11, DeepAgents SDK `deepagents==0.5.7`, standard-library `dataclasses`, `json`, `pathlib`, and `unittest`.

---

### Task 1: Runtime Schemas and Intake Tool

**Files:**
- Create: `src/dbkit/schemas/runtime.py`
- Create: `src/dbkit/tools/normalize_request.py`
- Test: `tests/test_phase01_runtime_intake.py`

- [ ] **Step 1: Write failing schema and normalize_request tests**

Create tests that import `normalize_request`, call it with user input, and assert the returned `NormalizedRequest` has a request id, original input, target domain `mysql`, requested capability `runtime_intake`, missing fields, and phase `phase-01`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m unittest tests/test_phase01_runtime_intake.py`

Expected: failure because `dbkit.tools.normalize_request` and `dbkit.schemas.runtime` do not exist.

- [ ] **Step 3: Implement schemas and normalize_request**

Add dataclasses for `NormalizedRequest`, `RouteDecision`, `TelemetryEvent`, `ArtifactRecord`, and `RuntimeResult`. Implement `normalize_request(user_input: str) -> NormalizedRequest` as a deterministic phase-01 tool.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m unittest tests/test_phase01_runtime_intake.py`

Expected: tests pass.

### Task 2: Skill Loading, Guardrails, Router, Artifacts, and Observability

**Files:**
- Modify: `src/dbkit/agents/intake.py`
- Modify: `src/dbkit/runtime/guardrails.py`
- Modify: `src/dbkit/runtime/router.py`
- Modify: `src/dbkit/runtime/artifacts.py`
- Modify: `src/dbkit/runtime/observability.py`
- Test: `tests/test_phase01_runtime_intake.py`

- [ ] **Step 1: Write failing component tests**

Add tests for `IntakeAgent.load_skill`, `Guardrails.validate_normalized_request`, `Router.route`, `ArtifactStore.persist_request`, and `TelemetryRecorder.emit`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m unittest tests/test_phase01_runtime_intake.py`

Expected: failure because the classes and methods are not implemented.

- [ ] **Step 3: Implement minimal components**

Implement deterministic component classes with focused responsibilities and no DBA reasoning.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m unittest tests/test_phase01_runtime_intake.py`

Expected: tests pass.

### Task 3: DeepAgents Adapter and Orchestrator Flow

**Files:**
- Create: `src/dbkit/runtime/deepagents_runtime.py`
- Modify: `src/dbkit/runtime/orchestrator.py`
- Modify: `src/dbkit/cli.py`
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_phase01_runtime_intake.py`
- Test: `tests/test_main_entrypoint.py`

- [ ] **Step 1: Write failing runtime flow tests**

Add tests that assert `DeepAgentsRuntimeFactory` calls an injected `create_deep_agent` function, `Orchestrator.run` executes the Phase 01 flow, telemetry contains stage events, artifacts contain the normalized request, and CLI prints the route and artifact path.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m unittest tests/test_phase01_runtime_intake.py tests/test_main_entrypoint.py`

Expected: failure because the adapter and orchestrator flow are not implemented.

- [ ] **Step 3: Implement DeepAgents adapter and orchestrator**

Add `deepagents==0.5.7` to project dependencies, raise `requires-python` to `>=3.11`, update CI to Python 3.11, implement adapter construction without invoking an LLM, and wire the orchestrator through redactor, intake agent, normalize_request, guardrails, router, artifacts, and observability.

- [ ] **Step 4: Run focused tests**

Run: `python3.11 -m unittest tests/test_phase01_runtime_intake.py tests/test_main_entrypoint.py`

Expected: tests pass.

- [ ] **Step 5: Run full verification**

Run:

```bash
python3.11 -m unittest discover
python3.11 -m compileall main.py src tests
```

Expected: both commands exit 0.

### Task 4: Closeup, Commit, Push, and CI

**Files:**
- Review all changed files.

- [ ] **Step 1: Inspect diff**

Run: `git diff --stat` and `git diff --name-only`.

- [ ] **Step 2: Check generated file starts**

Run the repository timestamp-start check and confirm no tracked generated file starts with a timestamp.

- [ ] **Step 3: Commit**

Commit message:

```text
phase-01: implement runtime intake skeleton
```

- [ ] **Step 4: Push and watch CI**

Run: `git push`

Then watch the latest `CI` workflow run for `phase-01-runtime-intake`.
