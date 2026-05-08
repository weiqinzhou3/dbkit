from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SecretStore:
    _values: dict[str, str] = field(default_factory=dict)

    def put(self, secret_ref: str, value: str) -> None:
        if secret_ref and value:
            self._values[secret_ref] = value

    def get(self, secret_ref: str | None) -> str | None:
        if not secret_ref:
            return None
        return self._values.get(secret_ref)

    @classmethod
    def from_pairs(cls, pairs: dict[str, str]) -> "SecretStore":
        return cls(dict(pairs))
