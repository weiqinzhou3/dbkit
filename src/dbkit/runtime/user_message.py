from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:password|passwd|pwd|token|secret|api_key)\b\s*[:=：]\s*(?!<SECRET_REF:)\S{3,}"
    r"|(?:密码|口令)\s*[是为：:]\s*(?!<SECRET_REF:)\S{3,}"
)
_FORBIDDEN_TEXT = re.compile(r"```|chain[- ]of[- ]thought|思考过程|推理过程", re.I)


@dataclass(frozen=True)
class UserMessageValidationResult:
    valid: bool
    user_message: dict[str, Any] | None
    errors: tuple[str, ...] = ()


def validate_user_message(
    user_message: object,
    *,
    blocking_issues: tuple[str, ...],
) -> UserMessageValidationResult:
    if not isinstance(user_message, dict):
        return UserMessageValidationResult(False, None, ("user_message is not an object",))

    errors: list[str] = []
    summary = user_message.get("summary")
    missing_items = user_message.get("missing_items")
    retry_example = user_message.get("retry_example", "")

    if not isinstance(summary, str) or not summary.strip():
        errors.append("user_message.summary is required")
    if not isinstance(missing_items, list):
        errors.append("user_message.missing_items must be a list")
        missing_items = []

    allowed_fields = _allowed_fields(blocking_issues)
    normalized_items: list[dict[str, str]] = []
    for item in missing_items:
        if not isinstance(item, dict):
            errors.append("user_message.missing_items item must be an object")
            continue
        normalized = {}
        for field in ("field", "label", "reason", "example"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"user_message.missing_items.{field} is required")
                value = ""
            normalized[field] = value
        if normalized["field"] not in allowed_fields:
            errors.append(
                f"user_message field does not match blocking issue: {normalized['field']}"
            )
        normalized_items.append(normalized)

    candidate = {
        "summary": str(summary or ""),
        "missing_items": normalized_items,
        "retry_example": str(retry_example or ""),
    }
    candidate_text = str(candidate)
    if _FORBIDDEN_TEXT.search(candidate_text):
        errors.append("user_message contains forbidden prose")
    if _SECRET_PATTERN.search(candidate_text):
        errors.append("user_message contains raw secret")

    return UserMessageValidationResult(
        valid=not errors,
        user_message=candidate if not errors else None,
        errors=tuple(errors),
    )


def render_user_message(
    user_message: dict[str, Any] | None,
    blocking_issues: tuple[str, ...],
    *,
    artifact_path: str | None = None,
) -> str:
    if not user_message:
        lines = [
            "DBKit intake blocked.",
            "",
            "缺少必要信息：",
        ]
        fields = sorted(_allowed_fields(blocking_issues)) or list(blocking_issues)
        lines.extend(f"- {field}" for field in fields)
        if artifact_path:
            lines.extend(["", f"artifact={artifact_path}"])
        return "\n".join(lines)

    lines = [
        "DBKit intake blocked.",
        "",
        "原因：",
        str(user_message["summary"]),
        "",
        "缺少信息：",
    ]
    for item in user_message["missing_items"]:
        lines.extend(
            [
                f"- {item['label']}",
                f"  原因：{item['reason']}",
                f"  示例：{item['example']}",
            ]
        )
    retry = str(user_message.get("retry_example") or "").strip()
    if retry:
        lines.extend(["", "你可以这样补充：", retry])
    if artifact_path:
        lines.extend(["", f"artifact={artifact_path}"])
    return "\n".join(lines)


def _allowed_fields(blocking_issues: tuple[str, ...]) -> set[str]:
    fields: set[str] = set()
    for issue in blocking_issues:
        if issue.startswith("missing required field: "):
            fields.add(issue.removeprefix("missing required field: ").strip())
        else:
            fields.add(issue)
    return fields
