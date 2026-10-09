"""Dictionary-based task submission and ordered task storage."""

from collections.abc import Mapping
from threading import RLock
from typing import Any

from .actions import ActionRegistry
from .models import Task
from .users import UserManager


class TaskManager:
    def __init__(self, users: UserManager, actions: ActionRegistry) -> None:
        self._users = users
        self._actions = actions
        self._tasks: dict[str, Task] = {}
        self._lock = RLock()

    def submit(self, data: Mapping[str, Any]) -> Task:
        task = Task.from_dict(data)
        self._users.get(task.user)
        self._actions.get(task.action).validate(task.params)
        with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"task already exists: {task.id}")
            self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Task:
        with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError as exc:
                raise ValueError(f"unknown task: {task_id}") from exc

    def list_tasks(self, user_id: str | None = None) -> tuple[Task, ...]:
        if user_id is not None:
            self._users.get(user_id)
        with self._lock:
            return tuple(
                task for task in self._tasks.values()
                if user_id is None or task.user == user_id
            )
