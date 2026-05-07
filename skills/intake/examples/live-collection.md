# Live Collection Example

Input:

```text
请连接 192.168.1.1 的 MySQL 分析今天17:00的 CPU 告警，用户 root。
```

Output:

```json
{
  "target_agent": "mysql_analyzer",
  "target_domain": "mysql",
  "task_type": "alert_analysis",
  "routing_confidence": 0.92,
  "input_mode": "live_collection",
  "target": {
    "type": "mysql",
    "host": "192.168.1.1",
    "port": 3306,
    "username": "root",
    "password_ref": null
  },
  "ssh_target": null,
  "provided_evidence": {
    "mode": "none",
    "files": [],
    "pasted_text": false,
    "description": ""
  },
  "collection_policy": {
    "allow_live_collection": true,
    "allow_mysql_login": true,
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
    "alerts": [],
    "symptoms": ["high_cpu"]
  },
  "evidence_plan": {
    "required_evidence": ["mysql.runtime_status", "mysql.processlist", "metrics.cpu"],
    "provided_evidence": [],
    "missing_evidence": ["target.password_ref"]
  },
  "missing_fields": ["target.password_ref"]
}
```
