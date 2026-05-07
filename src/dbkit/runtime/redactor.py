from __future__ import annotations

import re
from dataclasses import dataclass

REDACTED = "<REDACTED>"


@dataclass(frozen=True)
class RedactionResult:
    raw_text: str
    redacted_text: str
    raw_bytes: int
    filtered_bytes: int
    compression_ratio: float
    estimated_tokens: int


class Redactor:
    _SECRET_ASSIGNMENT = re.compile(
        r"(?i)\b(password|passwd|pwd|token|secret|api_key)\b(\s*[:=：]\s*)([^\s,;]+)"
    )
    _AUTHORIZATION = re.compile(
        r"(?im)\b(Authorization)(\s*:\s*)([^\r\n]+)"
    )
    _DATABASE_URI = re.compile(
        r"(?i)\b(mysql|redis|mongodb|postgres)://[^\s,;]+"
    )

    def redact(self, text: str) -> RedactionResult:
        redacted = self._SECRET_ASSIGNMENT.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
            text,
        )
        redacted = self._AUTHORIZATION.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
            redacted,
        )
        redacted = self._DATABASE_URI.sub(
            lambda match: f"{match.group(1)}://{REDACTED}",
            redacted,
        )
        raw_bytes = len(text.encode("utf-8"))
        filtered_bytes = len(redacted.encode("utf-8"))

        return RedactionResult(
            raw_text=text,
            redacted_text=redacted,
            raw_bytes=raw_bytes,
            filtered_bytes=filtered_bytes,
            compression_ratio=_ratio(filtered_bytes, raw_bytes),
            estimated_tokens=estimate_tokens(redacted),
        )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)


def estimate_tokens(text: str) -> int:
    return max(1, (len(text.encode("utf-8")) + 3) // 4)
