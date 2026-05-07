from __future__ import annotations

from hashlib import sha256

from dbkit.schemas.runtime import NormalizedRequest


def normalize_request(user_input: str) -> NormalizedRequest:
    text = user_input.strip()
    if not text:
        raise ValueError("user_input is required")

    target_domain = "mysql" if "mysql" in text.lower() else "mysql"
    missing_fields = _detect_missing_fields(text)
    request_id = _request_id(text)

    return NormalizedRequest(
        request_id=request_id,
        original_input=text,
        redacted_input=text,
        target_domain=target_domain,
        requested_capability="runtime_intake",
        missing_fields=missing_fields,
        metadata={"normalizer": "phase-01-deterministic"},
    )


def _request_id(text: str) -> str:
    digest = sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"req_{digest}"


def _detect_missing_fields(text: str) -> tuple[str, ...]:
    missing: list[str] = []
    lower_text = text.lower()

    if not any(marker in lower_text for marker in ("last ", "since ", "between ", "from ")):
        missing.append("time_window")

    return tuple(missing)


def normalize_request_tool(user_input: str) -> dict[str, object]:
    """Normalize a DBKit Phase 01 intake request into structured fields."""
    return normalize_request(user_input).to_dict()
