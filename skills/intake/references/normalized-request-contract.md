# Normalized Request Contract

The Intake Agent must output one JSON object. Runtime will normalize and validate it into `NormalizedRequest`.

Required fields:

- `target_agent`
- `target_domain`
- `task_type`
- `routing_confidence`
- `input_mode`
- `target`
- `ssh_target`
- `provided_evidence`
- `collection_policy`
- `event`
- `evidence_plan`
- `missing_fields`

Allowed route targets:

- `mysql_analyzer`
- `redis_rdb_analyzer`
- `redis_inspector`

System agents are forbidden as route targets:

- `intake_agent`
- `evidence_agent`
- `validation_agent`

Secrets must appear only as `<SECRET_REF:...>` placeholders.
