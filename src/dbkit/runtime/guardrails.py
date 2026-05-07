from __future__ import annotations

from dbkit.schemas.runtime import NormalizedRequest


class Guardrails:
    def validate_normalized_request(self, request: NormalizedRequest) -> NormalizedRequest:
        if not request.request_id:
            raise ValueError("request_id is required")
        if not request.original_input:
            raise ValueError("original_input is required")
        if request.target_domain != "mysql":
            raise ValueError(f"unsupported target_domain: {request.target_domain}")
        if request.requested_capability != "runtime_intake":
            raise ValueError(
                f"unsupported requested_capability: {request.requested_capability}"
            )
        if request.phase != "phase-01":
            raise ValueError(f"unsupported phase: {request.phase}")
        return request
