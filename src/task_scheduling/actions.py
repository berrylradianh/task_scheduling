"""Extensible action strategies. Built-ins simulate work without touching files."""

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Mapping
import logging
from threading import RLock
from typing import Any

from .models import Task, require_name

logger = logging.getLogger(__name__)


class ActionStrategy(ABC):
    """Implement execute; optionally override validation and async execution."""

    def validate(self, params: Mapping[str, Any]) -> None:
        """Reject invalid action parameters before a task is registered."""

    @abstractmethod
    def execute(self, task: Task) -> None:
        """Perform one action; raise an exception to report failure."""

    async def execute_async(self, task: Task) -> None:
        """Adapt a synchronous strategy without blocking the asyncio loop."""
        await asyncio.to_thread(self.execute, task)


class _TargetAction(ActionStrategy):
    def validate(self, params: Mapping[str, Any]) -> None:
        require_name(params.get("target"), "params.target")

    def execute(self, task: Task) -> None:
        logger.info(
            "Simulating %s target=%s user=%s task_id=%s",
            task.action, task.params["target"], task.user, task.id,
        )


class SyncAction(_TargetAction):
    """Simulated synchronization strategy."""


class BackupAction(_TargetAction):
    """Simulated backup strategy."""


class DeleteAction(_TargetAction):
    """Simulated deletion strategy; does not delete anything."""


class ActionRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, ActionStrategy] = {}
        self._lock = RLock()

    def register(self, name: str, strategy: ActionStrategy) -> None:
        require_name(name, "action name")
        if not isinstance(strategy, ActionStrategy):
            raise ValueError("strategy must implement ActionStrategy")
        with self._lock:
            if name in self._strategies:
                raise ValueError(f"action already exists: {name}")
            self._strategies[name] = strategy

    def get(self, name: str) -> ActionStrategy:
        with self._lock:
            try:
                return self._strategies[name]
            except KeyError as exc:
                raise ValueError(f"unknown action: {name}") from exc

    @classmethod
    def with_defaults(cls) -> "ActionRegistry":
        registry = cls()
        registry.register("sync", SyncAction())
        registry.register("backup", BackupAction())
        registry.register("delete", DeleteAction())
        return registry
