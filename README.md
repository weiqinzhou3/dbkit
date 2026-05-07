# DBKit

DBKit is an AI-native DBA analysis framework.

The project focuses on transforming large-scale raw operational data into structured, bounded, trustworthy, LLM-safe evidence for database operations analysis.

## Current Phase

Phase 01: Runtime + Intake MVP.

This phase builds the runnable runtime skeleton, root entrypoint, package structure, DeepAgents SDK adapter, and intake-oriented foundation. It does not perform real DBA analysis.

## Requirements

- Python 3.11+
- A DBKit config file with an OpenAI-compatible model that supports tool calling and multi-turn agent runtime

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
runtime:
  artifact_dir: .dbkit/artifacts
  invoke_llm: true
```

Run the root entrypoint:

```bash
python3.11 main.py --config config/config.yaml "MySQL connection spike on prod-db-1"
```

Run tests:

```bash
python3.11 -m unittest discover
```
