from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RedactionResult:
    raw_text: str
    redacted_text: str
    raw_bytes: int
    filtered_bytes: int
    compression_ratio: float
    estimated_tokens: int
    secret_refs: tuple[str, ...]
    redaction_summary: dict[str, Any]


class Redactor:
    # English keyword assignments: password=val, token: val, api_key：val
    _SECRET_ASSIGNMENT = re.compile(
        r"(?i)\b(password|passwd|pwd|token|secret|api_key)\b(\s*[:=：]\s*)([^\s,;]+)"
    )
    # Chinese password / passphrase keyword assignments: 密码是val, 口令为val
    _CHINESE_PASSWORD = re.compile(
        r"((?:密码|口令)\s*[是为：:]\s*)(\S+)"
    )
    # HTTP Authorization header
    _AUTHORIZATION = re.compile(
        r"(?im)\b(Authorization)(\s*:\s*)([^\r\n]+)"
    )
    # Database connection URIs with embedded credentials: scheme://user:pass@host
    _DATABASE_URI = re.compile(
        r"(?i)((?:mysql|redis|mongodb|postgres(?:ql)?|postgresql)://)([^:@\s,;]*):([^@\s,;]+)@([^\s,;]+)"
    )

    def redact(self, text: str) -> RedactionResult:
        counters: dict[str, int] = {}
        refs: list[str] = []
        patterns: list[str] = []

        def _ref(prefix: str, pattern_name: str) -> str:
            counters[prefix] = counters.get(prefix, 0) + 1
            ref = f"<SECRET_REF:{prefix}_{counters[prefix]:03d}>"
            refs.append(ref)
            if pattern_name not in patterns:
                patterns.append(pattern_name)
            return ref

        def _replace_assignment(m: re.Match) -> str:
            ref = _ref(m.group(1).lower(), "english_assignment")
            return f"{m.group(1)}{m.group(2)}{ref}"

        def _replace_chinese(m: re.Match) -> str:
            ref = _ref("chinese_password", "chinese_password_assignment")
            return f"{m.group(1)}{ref}"

        def _replace_auth(m: re.Match) -> str:
            ref = _ref("auth_token", "authorization_header")
            return f"{m.group(1)}{m.group(2)}{ref}"

        def _replace_uri(m: re.Match) -> str:
            ref = _ref("uri_password", "database_uri")
            return f"{m.group(1)}{m.group(2)}:{ref}@{m.group(4)}"

        redacted = self._SECRET_ASSIGNMENT.sub(_replace_assignment, text)
        redacted = self._CHINESE_PASSWORD.sub(_replace_chinese, redacted)
        redacted = self._AUTHORIZATION.sub(_replace_auth, redacted)
        redacted = self._DATABASE_URI.sub(_replace_uri, redacted)

        raw_bytes = len(text.encode("utf-8"))
        filtered_bytes = len(redacted.encode("utf-8"))

        return RedactionResult(
            raw_text=text,
            redacted_text=redacted,
            raw_bytes=raw_bytes,
            filtered_bytes=filtered_bytes,
            compression_ratio=_ratio(filtered_bytes, raw_bytes),
            estimated_tokens=estimate_tokens(redacted),
            secret_refs=tuple(refs),
            redaction_summary={
                "redacted": len(refs) > 0,
                "secret_refs": list(refs),
                "redacted_patterns": patterns,
            },
        )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)


def estimate_tokens(text: str) -> int:
    return max(1, (len(text.encode("utf-8")) + 3) // 4)
