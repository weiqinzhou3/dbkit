# Evidence Plan Policy

The evidence plan is a structured intake output describing what evidence the later pipeline needs. It is not analysis and must not contain root-cause conclusions.

Fields:

- `required_evidence`: evidence types needed for the requested task
- `provided_evidence`: evidence already provided by path, paste, or attachment
- `missing_evidence`: evidence still needed before analysis can be reliable

For `provided_evidence`, missing evidence should point to evidence sources, not live credentials.

For `live_collection`, required evidence may include runtime status, processlist, logs, and metrics if the user permits those sources.

For `hybrid`, separate already-provided evidence from live supplement evidence.
