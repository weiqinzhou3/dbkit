# DBKit Evidence Structuring Skill

## Role

The Evidence Structuring stage transforms Phase-02.1 RawEvidence artifacts into structured, bounded, deduplicated, LLM-safe EvidenceItems and an EvidenceBundle.

This stage does not diagnose root cause, produce findings, generate verdicts, or recommend remediation.

## Input Contract

Input is a Phase-02.1 raw evidence index:

- `.dbkit/artifacts/<request_id>.raw-evidence-index.json`
- Each collected raw evidence entry must have `payload.content_ref`.
- Full raw content must be loaded from `payload.content_ref`, not from index preview.
- `collection.status=not_available` entries remain coverage records and must not become EvidenceItems.

## Output Contract

The stage must write:

- `.dbkit/artifacts/<request_id>.evidence-bundle.json`
- `.dbkit/artifacts/evidence/<evidence_id>.json`
- `.dbkit/artifacts/<request_id>.evidence-processing-telemetry.jsonl`

The bundle must contain only structured, bounded, LLM-safe evidence. It must preserve raw references through `raw_refs`.

## Supported Evidence Types

Supported evidence types:

- `mysql.processlist`
- `mysql.runtime_status`
- `mysql.innodb_status`
- `mysql.variables`
- `mysql.service_metadata`
- `mysql.log_paths`
- `mysql.error_log`
- `mysql.slow_log`
- `metrics.os_cpu`
- `metrics.os_memory`
- `metrics.os_disk`
- `os.mysql_service_status`

## Deprecated Evidence Types

Deprecated duplicate MySQL metrics evidence must not produce independent EvidenceItems:

- `metrics.mysql`
- `metrics.mysql_status`
- `metrics.mysql_variables`

Use `mysql.runtime_status` and `mysql.variables` instead.

## Processing Rules

Evidence structuring must:

- Load raw artifacts from `payload.content_ref`.
- Classify raw evidence by `evidence_type`.
- Parse deterministic structures where possible.
- Produce compact summaries and selected counters.
- Keep samples bounded.
- Preserve enough `raw_refs` for audit and replay.
- Record unavailable evidence with explicit reasons.

## Time Window Rules

Log evidence must apply the RawEvidence `metadata.time_window` when timestamps can be parsed.

If timestamps are parsed:

- Keep lines inside the time window.
- Count discarded out-of-window lines.
- Set `timestamp_parse_status=ok`.

If timestamps cannot be parsed:

- Keep bounded evidence.
- Set `timestamp_parse_status=failed`.
- Add a quality flag.

## Deduplication Rules

EvidenceItems must not duplicate the same raw content for the same evidence type. Deduplicate by canonical evidence type and content reference.

Deprecated duplicate MySQL metrics evidence must be skipped or normalized without creating duplicate EvidenceItems.

## Aggregation Rules

Large raw tables and logs must be reduced into bounded structures:

- Count rows and selected variables.
- Keep top counters, top users, top states, top log patterns, and bounded samples.
- Do not embed full SHOW GLOBAL STATUS, SHOW GLOBAL VARIABLES, full processlist, or full logs in EvidenceBundle.

## Raw Reference Rules

Every EvidenceItem must include raw references:

- `raw_evidence_id`
- `content_ref`
- `line_start`
- `line_end`

Raw references are audit pointers. They are not full payload copies.

## Unavailable Evidence Rules

`collection.status=not_available` must be represented in bundle coverage with:

- `raw_evidence_id`
- `evidence_type`
- `status`
- `reason`

It must not become an EvidenceItem.

## Evidence Quality Rules

Evidence quality must explain whether the bundle is:

- `usable`
- `usable_with_warnings`
- `insufficient`

Warnings must be structured and bounded.

## LLM-Safe Context Rules

EvidenceBundle must be safe for LLM input:

- No raw secrets.
- No full large payloads.
- No unbounded logs.
- No full raw tables.
- No free chain-of-thought.
- No diagnosis or verdict fields.

## Forbidden Behavior

Do not output:

- root cause
- findings
- verdict
- final summary
- recommendations
- remediation steps

Do not perform new collection. Do not call MySQL, SSH, metrics, or file discovery tools. Phase-03 only structures already collected RawEvidence.
