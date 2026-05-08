from __future__ import annotations

import json
from typing import Any


def extract_json_from_invoke_result(invoke_result: object) -> dict[str, Any] | None:
    direct_content = _message_content(invoke_result)
    if direct_content:
        parsed = parse_json_object(direct_content)
        if parsed is not None:
            return parsed

    messages = _messages_from_result(invoke_result)
    if messages is None:
        return None

    for message in reversed(messages):
        role = _message_role(message)
        if role and role not in {"assistant", "ai"}:
            continue
        content = _message_content(message)
        if not content:
            continue
        parsed = parse_json_object(content)
        if parsed is not None:
            return parsed
    return None


def parse_json_object(content: str) -> dict[str, Any] | None:
    for candidate in _json_candidates(content):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _messages_from_result(invoke_result: object) -> list[object] | None:
    if isinstance(invoke_result, dict):
        messages = invoke_result.get("messages")
    else:
        messages = getattr(invoke_result, "messages", None)
    if isinstance(messages, tuple):
        return list(messages)
    if isinstance(messages, list):
        return messages
    return None


def _message_role(message: object) -> str | None:
    if isinstance(message, dict):
        role = message.get("role") or message.get("type")
    else:
        role = getattr(message, "role", None) or getattr(message, "type", None)
    return str(role).lower() if role else None


def _message_content(message: object) -> str:
    if isinstance(message, dict):
        content = message.get("content") or ""
    else:
        content = getattr(message, "content", "") or ""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
                else:
                    item_content = getattr(item, "content", None)
                    if isinstance(item_content, str):
                        parts.append(item_content)
        return "\n".join(parts)
    return str(content)


def _json_candidates(content: str) -> list[str]:
    candidates = [content.strip()]
    candidates.extend(_extract_fenced_json(content))
    candidates.extend(_extract_balanced_json_objects(content))
    return [candidate for candidate in candidates if candidate]


def _extract_fenced_json(content: str) -> list[str]:
    candidates: list[str] = []
    marker = "```"
    cursor = 0
    while True:
        start = content.find(marker, cursor)
        if start < 0:
            return candidates
        body_start = content.find("\n", start + len(marker))
        if body_start < 0:
            return candidates
        end = content.find(marker, body_start + 1)
        if end < 0:
            return candidates
        candidates.append(content[body_start + 1 : end].strip())
        cursor = end + len(marker)


def _extract_balanced_json_objects(content: str) -> list[str]:
    candidates: list[str] = []
    for start, char in enumerate(content):
        if char != "{":
            continue
        candidate = _balanced_json_from(content, start)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _balanced_json_from(content: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]
    return None
