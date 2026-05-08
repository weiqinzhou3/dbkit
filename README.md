# DBKit

DBKit is an AI-native DBA analysis framework.

The project focuses on transforming large-scale raw operational data into structured, bounded, trustworthy, LLM-safe evidence for database operations analysis.

## Current Phase

Phase 02: Evidence Planning & Collection MVP.

This phase connects a Phase-01 `NormalizedRequest` to MySQL Analyzer evidence
planning, guarded collection planning, and deterministic RawEvidence collection.
It does not produce findings, root cause, validation verdicts, or summaries.

## Requirements

- Python 3.11+
- A DBKit config file with an OpenAI-compatible model that supports tool calling and multi-turn agent runtime

## Installation

Install DBKit from the repo root:

```bash
python3.11 -m pip install -e .
```

The default install includes Phase-02.1 live collection dependencies:

- `PyMySQL` for MySQL read-only collection
- `paramiko` for SSH read-only collection

The same dependencies are also exposed as the `collection` extra, so this is
equivalent and explicit for collection environments:

```bash
python3.11 -m pip install -e ".[collection]"
```

If live collection dependencies are missing, DBKit blocks before executing
collectors and prints:

```text
status=blocked
reason=missing_collection_dependencies
missing_dependencies=pymysql,paramiko
install_hint=pip install -e ".[collection]"
```

## Entry Point

Create a local config from the example:

```bash
cp config/config.example.yaml config/config.yaml
```

Edit `config/config.yaml` with your model name, base URL, and API key.

Example DeepSeek V4 settings:

```yaml
model:
  provider_kind: openai_compatible
  model_name: deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key: replace-with-your-deepseek-api-key
  temperature: 0.0
  reasoning_effort: high
  extra_body:
    thinking:
      type: enabled
agent:
  tool_calling: true
  tool_calling_thinking_type: disabled
runtime:
  artifact_dir: .dbkit/artifacts
  invoke_llm: true
  interactive: false
  timezone: Asia/Shanghai
  locale: zh-CN
  repo_dir: .
  workspace_dir: .
  skills_dir: skills
  agents_dir: agents
```

`model` is the default OpenAI-compatible connection config and is passed through
as configured. `agent.tool_calling_thinking_type` controls the DeepAgents
tool-calling runtime. It defaults to `disabled` when a `thinking` body is present,
because DeepSeek V4 thinking-mode tool calls require `reasoning_content` to be
passed back in later turns, and the current LangChain/DeepAgents path does not
preserve that provider-specific field.

Runtime directories are explicit config values:

- `repo_dir`: DBKit repository root exposed to the agent as `/repo/`
- `workspace_dir`: user evidence workspace exposed as `/workspace/`
- `skills_dir`: skill directory exposed as `/skills/`
- `agents_dir`: system prompt directory exposed as `/agents/`

If local evidence is stored in `/tmp/mysql_conn_full_mock`, set
`runtime.workspace_dir: /tmp/mysql_conn_full_mock` and refer to it through the
agent-visible `/workspace/` path. Host absolute paths such as `/tmp/...` are not
the same as DeepAgents virtual filesystem paths.

If `runtime.workspace_dir: /`, host path `/tmp/mysql_conn_full_mock/` maps to
`/workspace/tmp/mysql_conn_full_mock/`.

Run the root entrypoint:

```bash
python3.11 main.py --config config/config.yaml "请帮我分析这个 MySQL，今天17:00触发 mysql cpu usage > 85%，只需要分析本地文件，文件在/tmp/mysql_conn_full_mock/。"
```

Run with interactive intake supplement:

```bash
python3.11 main.py --config config/config.yaml --interactive "请帮我分析这个 MySQL，账号 root"
```

Run tests:

```bash
python3.11 -m unittest discover
```
