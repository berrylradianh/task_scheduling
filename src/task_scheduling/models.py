"""Validated data models shared by scheduling, quota control, and execution."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum
import re
from types import MappingProxyType
from typing import Any


def require_name(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain leading or trailing whitespace")
    return value


def parse_time(value: object) -> time:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{2}:[0-9]{2}", value) is None:
        raise ValueError("time must use HH:MM format")
    try:
        hour, minute = map(int, value.split(":"))
        return time(hour, minute)
    except ValueError as exc:
        raise ValueError("time must be between 00:00 and 23:59") from exc


def require_date(day: object) -> date:
    if not isinstance(day, date) or isinstance(day, datetime):
        raise ValueError("quota day must be a datetime.date")
    return day


@dataclass(frozen=True, slots=True)
class User:
    id: str
    quota: int

    def __post_init__(self) -> None:
        require_name(self.id, "user id")
        if isinstance(self.quota, bool) or not isinstance(self.quota, int) or self.quota < 0:
            raise ValueError("quota must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    user: str
    scheduled_time: time
    action: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_name(self.id, "task id")
        require_name(self.user, "task user")
        require_name(self.action, "task action")
        if not isinstance(self.scheduled_time, time):
            raise ValueError("scheduled_time must be a datetime.time")
        if self.scheduled_time.tzinfo is not None:
            raise ValueError("task time must be a local time without a timezone")
        if self.scheduled_time.second or self.scheduled_time.microsecond:
            raise ValueError("task time must have minute precision")
        if not isinstance(self.params, Mapping):
            raise ValueError("params must be a dictionary")
        if any(not isinstance(key, str) for key in self.params):
            raise ValueError("parameter names must be strings")
        # Detach submitted parameters from the caller's mutable dictionary.
        object.__setattr__(self, "params", MappingProxyType(deepcopy(dict(self.params))))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Task":
        if not isinstance(data, Mapping):
            raise ValueError("task must be a dictionary")
        required = {"id", "user", "time", "action"}
        missing = required - data.keys()
        if missing:
            raise ValueError(f"missing task fields: {', '.join(sorted(missing))}")
        unknown = data.keys() - (required | {"params"})
        if unknown:
            raise ValueError(f"unknown task fields: {', '.join(sorted(map(str, unknown)))}")
        return cls(
            id=data["id"],
            user=data["user"],
            scheduled_time=parse_time(data["time"]),
            action=data["action"],
            params=data.get("params", {}),
        )


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    user: str
    date: date
    quota: int
    used: int

    @property
    def remaining(self) -> int:
        return self.quota - self.used


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    QUOTA_EXCEEDED = "quota_exceeded"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    task_id: str
    user: str
    action: str
    execution_date: date
    status: ExecutionStatus
    duration_seconds: float = 0.0
    error: str | None = None
