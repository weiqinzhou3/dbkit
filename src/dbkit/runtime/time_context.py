from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RuntimeTimeContext:
    current_datetime: str
    timezone: str
    locale: str

    def to_dict(self) -> dict[str, str]:
        return {
            "current_datetime": self.current_datetime,
            "timezone": self.timezone,
            "locale": self.locale,
        }


class TimeProvider:
    def __init__(self, *, timezone: str = "Asia/Shanghai", locale: str = "zh-CN") -> None:
        self.timezone = timezone
        self.locale = locale

    def runtime_context(self) -> dict[str, str]:
        zone = ZoneInfo(self.timezone)
        return RuntimeTimeContext(
            current_datetime=datetime.now(zone).isoformat(timespec="seconds"),
            timezone=self.timezone,
            locale=self.locale,
        ).to_dict()


@dataclass(frozen=True)
class FixedTimeProvider:
    current_datetime: datetime
    timezone: str = "Asia/Shanghai"
    locale: str = "zh-CN"

    def runtime_context(self) -> dict[str, str]:
        return RuntimeTimeContext(
            current_datetime=self.current_datetime.isoformat(timespec="seconds"),
            timezone=self.timezone,
            locale=self.locale,
        ).to_dict()
