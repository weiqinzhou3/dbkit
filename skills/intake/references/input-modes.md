# Input Modes

## live_collection

Use when the user wants DBKit to actively connect to MySQL, SSH, or metrics sources.

Required policy:

- `input_mode=live_collection`
- `collection_policy.allow_live_collection=true`
- `collection_policy.allow_mysql_login=true` when MySQL login is requested or required
- `collection_policy.allow_ssh=true` only when SSH or host logs are requested
- `collection_policy.allow_metrics_query=true` only when metrics queries are requested

Missing fields may include live target fields such as `target.host`, `target.username`, and `target.password_ref`.

## provided_evidence

Use when the user wants DBKit to analyze already-collected evidence only.

Required policy:

- `input_mode=provided_evidence`
- `target=null` unless explicitly provided as metadata
- `ssh_target=null` unless explicitly provided as metadata
- all collection permissions false

Do not require `target.host`, `target.username`, `ssh_target.host`, or `ssh_target.username`.

If no file, attachment, or pasted text is present, require `provided_evidence.files` or `provided_evidence.pasted_text`.

## hybrid

Use when the user provides evidence and also allows live supplement.

Required policy:

- `input_mode=hybrid`
- record provided evidence
- set only explicitly allowed collection permissions to true
- require live target fields only for the allowed live collection methods
