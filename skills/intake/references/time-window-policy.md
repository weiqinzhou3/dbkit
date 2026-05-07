# Time Window Policy

If the user gives `event_time` but no explicit time window:

- `alert_analysis`: before `6h`, after `1h`
- `incident_analysis`: before `6h`, after `1h`

The output must set:

```json
{
  "before": "6h",
  "after": "1h",
  "source": "skill_default_from_event_time"
}
```

If the user provides an explicit time window, preserve it and set `source=user_explicit`.

Runtime tools may compute deterministic `start` and `end` from `event_time`, `before`, and `after`, but must not choose the default `before` or `after` values.
