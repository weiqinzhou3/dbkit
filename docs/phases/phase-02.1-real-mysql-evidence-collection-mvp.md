# Phase-02.1 — Real MySQL Evidence Collection MVP

Version: v0.1  
Status: Active Planning  
Parent Phase: Phase-02 Evidence Planning & Collection MVP  
Runtime Foundation: DeepAgents SDK

---

# 1. Purpose

Phase-02 implemented the Evidence Planning & Collection framework, but live collectors may still return `not_implemented`.

Phase-02.1 exists to make collection real.

This phase must implement real read-only evidence collection for MySQL incident analysis:

```text
EvidenceRequest
  -> CollectionPlan
  -> Guarded Collector Tools
  -> Real RawEvidence
```

This phase must still NOT perform:

- EvidenceBundle structuring
- root cause analysis
- findings
- validation verdict
- final summary

Those belong to later phases.

---

# 2. Phase Goal

Implement real collector tools for MySQL live collection.

The phase must support collecting raw evidence from:

```text
MySQL service / runtime state
MySQL logs
MySQL metrics
OS / service state through SSH
```

Tool categories must be separated clearly:

```text
service/mysql/*
log/mysql/*
metrics/mysql/*
os/ssh/*
```

Do not implement one giant collector.

Each tool must have a narrow responsibility and return structured `RawEvidence`.

---

# 3. Architecture Rules

This phase must follow:

```text
/docs/master-spec.md
/docs/architecture/architecture-responsibility.md
/docs/phases/phase-02-evidence-planning-collection-mvp.md
AGENTS.md
```

Core rules:

```text
MySQL Analyzer Agent decides what evidence is needed.
Collector Tools execute deterministic collection.
Runtime executes tools through Tool Executor and Guardrails.
Collector tools must be exposed in the MySQL Analyzer skill/tool contract.
The MySQL Analyzer Agent must select collector tools through structured EvidenceRequest.tool_hint or tool calls.
Runtime may validate and execute the selected tools, but must not independently invent collection steps.
All real collection capabilities must be registered as tools.
They must be discoverable by the MySQL Analyzer Agent through skill/tool documentation.
Runtime must not invent collection steps that were not requested by EvidenceRequest or selected via allowed tool hints.
Runtime may normalize aliases and reject invalid tool requests.
Evidence Agent does not decide what to collect.
Evidence Agent does not structure RawEvidence in this phase.
```

Runtime must not hardcode MySQL troubleshooting SOP.

Tools must not generate findings, root cause, verdict, or summary.

---

# 4. In Scope

## 4.1 Real MySQL Service Collection

Implement real MySQL service collectors:

```text
collect_mysql_processlist
collect_mysql_runtime_status
collect_mysql_innodb_status
collect_mysql_variables
collect_mysql_service_metadata
```

## 4.2 Real MySQL Log Discovery and Collection

Implement MySQL log collectors:

```text
discover_mysql_log_paths
collect_mysql_error_log
collect_mysql_slow_log
```

These tools must discover actual log file paths from MySQL variables when possible.

## 4.3 Real MySQL Metrics Collection

Implement MySQL metrics collectors:

```text
collect_mysql_metrics_snapshot
collect_mysql_status_metrics
collect_mysql_variable_metrics
```

If Prometheus / mysqld_exporter endpoint exists, optional support may be added as:

```text
collect_mysqld_exporter_metrics
```

But MySQL-native metrics must work without Prometheus.

## 4.4 SSH / OS Service Collection

Implement SSH-based collectors:

```text
collect_os_service_status
collect_os_cpu_snapshot
collect_os_memory_snapshot
collect_os_disk_snapshot
read_remote_file
```

SSH collectors are used for:

- MySQL log files if logs are on remote host
- OS/service state
- fallback metrics
- system context

---

# 5. Explicitly Out of Scope

Do not implement:

- EvidenceBundle generation
- log parsing / aggregation beyond raw collection
- slow query digest analysis
- root cause findings
- Validation Agent
- Verdict
- Summary
- remediation execution
- kill query / change config
- persistent remote agents
- scheduled collection
- production approval UI

---

# 6. Required Tool Taxonomy

Tools must be organized by capability.

Recommended conceptual structure:

```text
src/dbkit/tools/collectors/
  mysql/
    service.py
    logs.py
    metrics.py
  ssh/
    os.py
    files.py
```

Exact module paths may follow project style, but boundaries must remain.

---

# 7. MySQL Service Tools

## 7.1 collect_mysql_processlist

Purpose:

```text
Collect current MySQL processlist.
```

Implementation:

```sql
SHOW FULL PROCESSLIST;
```

RawEvidence:

```text
evidence_type=mysql.processlist
source.kind=mysql
tool_name=collect_mysql_processlist
```

## 7.2 collect_mysql_runtime_status

Implementation:

```sql
SHOW GLOBAL STATUS;
```

RawEvidence:

```text
evidence_type=mysql.runtime_status
```

## 7.3 collect_mysql_innodb_status

Implementation:

```sql
SHOW ENGINE INNODB STATUS;
```

RawEvidence:

```text
evidence_type=mysql.innodb_status
```

## 7.4 collect_mysql_variables

Implementation:

```sql
SHOW GLOBAL VARIABLES;
```

RawEvidence:

```text
evidence_type=mysql.variables
```

## 7.5 collect_mysql_service_metadata

Implementation may include:

```sql
SELECT VERSION();
SELECT @@hostname, @@port, @@datadir, @@log_error, @@slow_query_log_file;
```

RawEvidence:

```text
evidence_type=mysql.service_metadata
```

---

# 8. MySQL Log Discovery and Collection

## 8.1 discover_mysql_log_paths

Purpose:

```text
Discover MySQL error log and slow log paths.
```

Implementation:

```sql
SHOW GLOBAL VARIABLES LIKE 'log_error';
SHOW GLOBAL VARIABLES LIKE 'slow_query_log_file';
SHOW GLOBAL VARIABLES LIKE 'slow_query_log';
SHOW GLOBAL VARIABLES LIKE 'log_output';
SHOW GLOBAL VARIABLES LIKE 'datadir';
```

Expected output:

```json
{
  "error_log_path": "/path/to/error.log",
  "slow_log_path": "/path/to/slow.log",
  "slow_query_log_enabled": true,
  "log_output": "FILE",
  "datadir": "/var/lib/mysql"
}
```

If path is relative, resolve relative to `datadir`.

If `log_output=TABLE`, do not read file; emit:

```text
status=not_available
reason=log_output_table_not_supported_in_phase_02_1
```

## 8.2 collect_mysql_error_log

Collection flow:

```text
discover_mysql_log_paths
  -> error_log_path
  -> read via SSH when ssh_target exists
  -> or read local workspace path if local
```

Required behavior:

- read-only
- bounded max bytes / tail lines
- store raw artifact by content_ref
- do not parse root cause

Config:

```yaml
collection:
  logs:
    max_bytes: 10485760
    tail_lines: 5000
```

RawEvidence:

```text
evidence_type=mysql.error_log
source.kind=ssh_file or local_file
```

## 8.3 collect_mysql_slow_log

Same behavior as error log.

If slow query log is disabled:

```text
status=not_available
reason=slow_query_log_disabled
```

RawEvidence:

```text
evidence_type=mysql.slow_log
```

---

# 9. Metrics Collection

Metrics tools must be separated from service and log collectors.

## 9.1 collect_mysql_metrics_snapshot

Purpose:

```text
Collect MySQL-native metrics snapshot.
```

Implementation may combine:

```sql
SHOW GLOBAL STATUS;
SHOW GLOBAL VARIABLES;
```

RawEvidence:

```text
evidence_type=metrics.mysql
```

## 9.2 collect_mysql_status_metrics

Implementation:

```sql
SHOW GLOBAL STATUS;
```

RawEvidence:

```text
evidence_type=metrics.mysql_status
```

## 9.3 collect_mysql_variable_metrics

Implementation:

```sql
SHOW GLOBAL VARIABLES;
```

RawEvidence:

```text
evidence_type=metrics.mysql_variables
```

## 9.4 collect_mysqld_exporter_metrics Optional

If configured:

```yaml
collection:
  metrics:
    mysqld_exporter_url: "http://host:9104/metrics"
```

If not configured:

```text
status=not_configured
```

---

# 10. SSH / OS Collection

## 10.1 read_remote_file

Purpose:

```text
Read bounded content from a remote file through SSH.
```

Requirements:

- read-only
- max bytes / max lines
- allowed paths guardrail
- no shell injection
- no raw secret logging
- timeout

Allowed command pattern:

```bash
tail -n <N> -- <path>
```

Do not allow arbitrary user-provided shell commands.

## 10.2 collect_os_service_status

Allowed read-only commands:

```bash
systemctl status mysqld --no-pager
systemctl status mysql --no-pager
ps -ef | grep -E 'mysqld|mysql' | grep -v grep
```

If systemctl unavailable, fallback to ps.

RawEvidence:

```text
evidence_type=os.mysql_service_status
```

## 10.3 collect_os_cpu_snapshot

Allowed commands:

```bash
uptime
top -b -n 1 | head -50
vmstat 1 3
```

RawEvidence:

```text
evidence_type=metrics.os_cpu
```

## 10.4 collect_os_memory_snapshot

Allowed commands:

```bash
free -m
vmstat 1 3
```

RawEvidence:

```text
evidence_type=metrics.os_memory
```

## 10.5 collect_os_disk_snapshot

Allowed commands:

```bash
df -h
du -sh <mysql datadir>
```

Only run `du` if datadir is known and allowed.

RawEvidence:

```text
evidence_type=metrics.os_disk
```

---

# 11. Connection Handling

## 11.1 MySQL Connection

Preferred implementation:

```text
PyMySQL or mysqlclient
```

Connection input comes from `NormalizedRequest.target`.

Secrets come only from `secret_ref`.

Runtime / secret store resolves secret_ref internally.

Raw password must not be logged or passed to LLM.

Config:

```yaml
collection:
  mysql:
    connect_timeout_seconds: 5
    read_timeout_seconds: 30
```

Runtime must run a dependency preflight before executing live MySQL collectors.
If `pymysql` is unavailable, collection must block once with
`reason=missing_collection_dependencies` instead of letting each collector fail.

## 11.2 SSH Connection

Preferred implementation:

```text
paramiko
```

Input comes from `NormalizedRequest.ssh_target`.

Secrets come only from `secret_ref`.

Config:

```yaml
collection:
  ssh:
    connect_timeout_seconds: 5
    command_timeout_seconds: 30
```

Runtime must run a dependency preflight before executing SSH collectors. If
`paramiko` is unavailable, collection must block once with
`reason=missing_collection_dependencies` instead of letting each collector fail.

---

# 12. RawEvidence Requirements

Every collector must return `RawEvidence`.

Allowed statuses:

```text
collected
partial
failed
blocked
not_available
not_configured
not_implemented
```

For Phase-02.1, core tools must not return `not_implemented`.

Core tools:

```text
collect_mysql_processlist
collect_mysql_runtime_status
collect_mysql_innodb_status
collect_mysql_variables
collect_mysql_service_metadata
discover_mysql_log_paths
collect_mysql_error_log
collect_mysql_slow_log
collect_mysql_metrics_snapshot
collect_os_service_status
collect_os_cpu_snapshot
read_remote_file
```

---

# 13. Collection Summary

Raw evidence index must include summary counts:

```json
{
  "raw_evidence_count": 6,
  "collected_count": 4,
  "partial_count": 1,
  "failed_count": 0,
  "blocked_count": 0,
  "not_available_count": 1,
  "not_implemented_count": 0
}
```

CLI status rules:

```text
raw_evidence_collected:
  collected_count > 0 and no warnings

collection_completed_with_warnings:
  collected_count > 0 and failed/not_available/partial exists

collection_failed:
  collected_count == 0 and failed_count > 0

collection_not_implemented:
  collected_count == 0 and not_implemented_count > 0

collection_blocked:
  blocked_count > 0 and collected_count == 0
```

Do not print `status=raw_evidence_collected` if all collectors are not implemented / failed / blocked.

---

# 14. Guardrails

## 14.1 MySQL Guardrails

Allowed SQL only:

```sql
SHOW FULL PROCESSLIST;
SHOW GLOBAL STATUS;
SHOW GLOBAL VARIABLES;
SHOW ENGINE INNODB STATUS;
SELECT VERSION();
SELECT @@hostname, @@port, @@datadir, @@log_error, @@slow_query_log_file;
```

Block:

```text
DML
DDL
KILL
SET GLOBAL
FLUSH
ALTER
DROP
DELETE
UPDATE
INSERT
```

## 14.2 SSH Guardrails

Block:

```text
arbitrary shell execution
write commands
service restart
file modification
package installation
```

Allow only explicit read-only command templates.

## 14.3 Path Guardrails

Remote file read must check:

```text
path discovered from MySQL variables or explicitly allowed
no path traversal
max bytes / max lines enforced
```

---

# 15. Artifacts

Required artifacts:

```text
.dbkit/artifacts/<request_id>.evidence-request.json
.dbkit/artifacts/<request_id>.collection-plan.json
.dbkit/artifacts/<request_id>.raw-evidence-index.json
.dbkit/artifacts/raw/<raw_evidence_id>.json
.dbkit/artifacts/raw/<raw_evidence_id>.txt
.dbkit/artifacts/<request_id>.collection-telemetry.jsonl
```

No raw secrets.

JSON artifacts:

```python
ensure_ascii=False
indent=2
sort_keys=True
```

---

# 16. Telemetry

Required events:

```text
mysql_connection_started
mysql_connection_completed
mysql_connection_failed
ssh_connection_started
ssh_connection_completed
ssh_connection_failed
collector_started
collector_completed
collector_failed
collector_blocked
mysql_log_paths_discovered
remote_file_read_started
remote_file_read_completed
raw_evidence_written
collection_summary_created
```

Telemetry must include:

```text
duration_ms
tool_name
evidence_type
status
bytes
line_count
```

Telemetry must not contain raw secrets.

---

# 17. CLI Behavior

## 17.1 MySQL-only Collection

Expected:

```text
DBKit 0.1.0
phase=phase-02.1
status=raw_evidence_collected
input_mode=live_collection
raw_evidence_count=4
collected=4
failed=0
not_implemented=0
artifact=.dbkit/artifacts/<request_id>.raw-evidence-index.json
```

## 17.2 MySQL + SSH + Logs + OS Metrics

Expected:

```text
DBKit 0.1.0
phase=phase-02.1
status=collection_completed_with_warnings
input_mode=live_collection
raw_evidence_count=...
collected=...
not_available=...
failed=0
not_implemented=0
artifact=.dbkit/artifacts/<request_id>.raw-evidence-index.json
```

`not_available` is acceptable when:

- slow query log is disabled
- log path is empty
- systemctl unavailable but ps fallback works

---

# 18. Required Tests

- MySQL processlist collector returns collected.
- MySQL runtime status collector returns collected.
- InnoDB status collector returns collected.
- Variables collector returns collected.
- Log path discovery resolves absolute and relative paths.
- Error log collection through SSH returns collected or explicit not_available.
- Slow log disabled returns not_available.
- OS CPU snapshot uses allowlisted commands.
- Dangerous SQL is blocked.
- Dangerous SSH command is blocked.
- Raw evidence index includes status summary counts.
- CLI does not fake success when collectors are not implemented.
- No raw password in artifacts, telemetry, logs.

---

# 19. Manual Acceptance Test

Before DBKit:

```bash
mysql -h192.168.23.176 -P3306 -uroot -p -e "select version();"
ssh root@192.168.23.176 "hostname && date"
```

Run MySQL-only collection and verify:

```text
mysql.processlist collected
mysql.runtime_status collected
mysql.innodb_status collected
mysql.variables collected
```

Run MySQL + SSH and verify:

```text
log paths discovered
error log collected or not_available with reason
slow log collected or not_available with reason
OS CPU snapshot collected
service status collected
```

---

# 20. Success Criteria

Phase-02.1 is complete when:

1. Core MySQL service collectors execute real read-only SQL.
2. MySQL log paths are discovered from MySQL variables.
3. Error log and slow log collection work through SSH or return explicit not_available reason.
4. MySQL metrics snapshot works through MySQL-native status/variables.
5. OS/service collectors work through SSH.
6. RawEvidence contains real payload references for successful collectors.
7. CLI summary accurately reports collected/failed/not_available/not_implemented counts.
8. No fake success when collectors are not implemented.
9. Guardrails block unsafe SQL and SSH commands.
10. Raw secrets do not appear in artifacts, telemetry, logs, or LLM traces.
11. Required tests pass.
12. GitHub CI passes.

---

# 21. Closeout Requirements

Closeout must report:

```text
Branch
Commit
Tests run
CI URL/status
Manual MySQL-only command
Manual MySQL+SSH command
RawEvidence index artifact
CollectionPlan artifact
Telemetry artifact
Collected count
Failed count
Not available count
Not implemented count
Known limitations
Remaining risks
```

Do not mark Phase-02.1 complete if core collectors still return `not_implemented`.
