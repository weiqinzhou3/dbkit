# Hybrid Example

Input:

```text
我有慢日志文件 /tmp/mysql-slow.log，也可以连数据库补充看 processlist。
```

Output:

```json
{
  "target_agent": "mysql_analyzer",
  "target_domain": "mysql",
  "task_type": "incident_analysis",
  "routing_confidence": 0.88,
  "input_mode": "hybrid",
  "target": null,
  "ssh_target": null,
  "provided_evidence": {
    "mode": "local_files",
    "files": ["/tmp/mysql-slow.log"],
    "pasted_text": false,
    "description": "我有慢日志文件，也可以连数据库补充看 processlist"
  },
  "collection_policy": {
    "allow_live_collection": true,
    "allow_mysql_login": true,
    "allow_ssh": false,
    "allow_metrics_query": false
  },
  "event": null,
  "evidence_plan": {
    "required_evidence": ["mysql.slow_log", "mysql.processlist"],
    "provided_evidence": ["/tmp/mysql-slow.log"],
    "missing_evidence": ["target.host", "target.username"]
  },
  "missing_fields": ["target.host", "target.username"]
}
```
