from __future__ import annotations

from dbkit.schemas.runtime import NormalizedRequest, RouteDecision

_ALLOWED_DOMAINS = frozenset({"mysql", "redis"})


class Router:
    def route(self, request: NormalizedRequest) -> RouteDecision:
        if request.target_domain not in _ALLOWED_DOMAINS:
            raise ValueError(f"unsupported target_domain: {request.target_domain}")

        if request.target_domain == "mysql":
            target_agent = request.target_agent or "mysql_analyzer"
        else:
            raise ValueError(f"no router configured for domain: {request.target_domain}")

        return RouteDecision(
            target_agent_name=target_agent,
            target_domain=request.target_domain,
            phase=request.phase,
            reason="Phase 01.1 routes to domain agent; analysis is deferred to Phase 02+.",
        )
