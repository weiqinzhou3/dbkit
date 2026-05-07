from __future__ import annotations

from dbkit.schemas.runtime import NormalizedRequest, RouteDecision


class Router:
    def route(self, request: NormalizedRequest) -> RouteDecision:
        if request.target_domain != "mysql":
            raise ValueError(f"unsupported target_domain: {request.target_domain}")

        return RouteDecision(
            target_agent_name="mysql_analyzer",
            target_domain=request.target_domain,
            phase=request.phase,
            reason="Phase 01 only selects the target agent name; it does not run analysis.",
        )
