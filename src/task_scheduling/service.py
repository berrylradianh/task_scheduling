"""Compose the service from dictionary configuration."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .actions import ActionRegistry
from .executor import AsyncTaskExecutor, TaskExecutor
from .scheduler import AsyncScheduler, Clock, Scheduler
from .tasks import TaskManager
from .users import UserManager


@dataclass(frozen=True, slots=True)
class SchedulingService:
    users: UserManager
    tasks: TaskManager
    actions: ActionRegistry
    executor: TaskExecutor | AsyncTaskExecutor
    scheduler: Scheduler | AsyncScheduler

    @classmethod
    def from_config(
        cls, config: Mapping[str, Any], *, asynchronous: bool = False,
        max_concurrency: int = 4, actions: ActionRegistry | None = None,
        clock: Clock | None = None,
    ) -> "SchedulingService":
        if not isinstance(config, Mapping):
            raise ValueError("configuration must be a dictionary")
        unknown = config.keys() - {"timezone", "users", "tasks"}
        if unknown:
            raise ValueError(f"unknown configuration fields: {', '.join(sorted(map(str, unknown)))}")
        if not isinstance(config.get("users"), list):
            raise ValueError("users must be a list")
        if not isinstance(config.get("tasks"), list):
            raise ValueError("tasks must be a list")

        users = UserManager()
        registry = actions if actions is not None else ActionRegistry.with_defaults()
        tasks = TaskManager(users, registry)
        for user in config["users"]:
            if not isinstance(user, Mapping) or set(user) != {"id", "quota"}:
                raise ValueError("each user must contain exactly id and quota")
            users.register(user["id"], user["quota"])
        for task in config["tasks"]:
            tasks.submit(task)

        timezone = config.get("timezone", "UTC")
        if asynchronous:
            executor = AsyncTaskExecutor(users, registry, max_concurrency=max_concurrency)
            scheduler = AsyncScheduler(tasks, executor, timezone=timezone, clock=clock)
        else:
            executor = TaskExecutor(users, registry)
            scheduler = Scheduler(tasks, executor, timezone=timezone, clock=clock)
        return cls(users, tasks, registry, executor, scheduler)
