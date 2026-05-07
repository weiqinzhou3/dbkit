# Provided Evidence Example

Input:

```text
请帮我分析这个 MySQL，今天17:00触发 mysql cpu usage > 85%，只需要分析本地文件。
```

Output:

```json
{
  "target_agent": "mysql_analyzer",
  "target_domain": "mysql",
  "task_type": "alert_analysis",
  "routing_confidence": 0.9,
  "input_mode": "provided_evidence",
  "target": null,
  "ssh_target": null,
  "provided_evidence": {
    "mode": "local_files",
    "files": [],
    "pasted_text": false,
    "description": "只需要分析本地文件"
  },
  "collection_policy": {
    "allow_live_collection": false,
    "allow_mysql_login": false,
    "allow_ssh": false,
    "allow_metrics_query": false
  },
  "event": {
    "event_time": "2026-05-07T17:00:00+08:00",
    "time_window": {
      "before": "6h",
      "after": "1h",
      "source": "skill_default_from_event_time"
    },
    "alerts": [
      {
        "raw": "mysql cpu usage > 85%",
        "name": "mysql cpu usage",
        "operator": ">",
        "threshold": 85,
        "unit": "percent",
        "semantic_hint": "high_cpu",
        "confidence": 0.8
      }
    ],
    "symptoms": ["high_cpu"]
  },
  "evidence_plan": {
    "required_evidence": ["metrics.cpu", "mysql.processlist"],
    "provided_evidence": [],
    "missing_evidence": ["provided_evidence.files"]
  },
  "missing_fields": ["provided_evidence.files"]
}
```
