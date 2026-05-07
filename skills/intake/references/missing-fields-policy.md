# Missing Fields Policy

`missing_fields` must contain only fields required for the selected input mode.

## provided_evidence

Do not include:

- `target.host`
- `target.username`
- `ssh_target.host`
- `ssh_target.username`

Require one evidence source:

- `provided_evidence.files`
- `provided_evidence.pasted_text`
- registered CLI or attachment input files

## live_collection

When MySQL live collection is allowed, missing fields may include:

- `target.host`
- `target.username`
- `target.password_ref`

When SSH collection is allowed, missing fields may include:

- `ssh_target.host`
- `ssh_target.username`
- `ssh_target.password_ref`

## hybrid

Require only the live target fields for collection paths the user allows.

Do not require fields for collection paths the user does not allow.

## event time

For `alert_analysis` and `incident_analysis`, require `event.event_time` if it cannot be parsed.

Do not require `time_window` when `event.event_time` exists and skill defaults apply.
