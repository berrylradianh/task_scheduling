"""Quota-aware synchronous and asynchronous task execution."""

import asyncio
from datetime import date
import logging
from time import perf_counter

from .actions import ActionRegistry, ActionStrategy
from .models import ExecutionResult, ExecutionStatus, Task, require_date
from .users import UserManager

logger = logging.getLogger(__name__)


class _ExecutorBase:
    def __init__(self, users: UserManager, actions: ActionRegistry) -> None:
        self._users = users
        self._actions = actions

    def _prepare(self, task: Task, day: date) -> ActionStrategy | ExecutionResult:
        require_date(day)
        try:
            self._users.get(task.user)
            strategy = self._actions.get(task.action)
            strategy.validate(task.params)
        except Exception as exc:
            return self._failure(task, day, perf_counter(), exc)

        if not self._users.try_consume(task.user, day):
            logger.warning(
                "Task skipped status=quota_exceeded task_id=%s user=%s action=%s date=%s",
                task.id, task.user, task.action, day,
            )
            return ExecutionResult(
                task.id, task.user, task.action, day, ExecutionStatus.QUOTA_EXCEEDED,
            )
        logger.info(
            "Task starting task_id=%s user=%s action=%s date=%s",
            task.id, task.user, task.action, day,
        )
        return strategy

    @staticmethod
    def _success(task: Task, day: date, started: float) -> ExecutionResult:
        duration = perf_counter() - started
        logger.info(
            "Task finished status=success task_id=%s user=%s action=%s date=%s duration=%.6fs",
            task.id, task.user, task.action, day, duration,
        )
        return ExecutionResult(
            task.id, task.user, task.action, day, ExecutionStatus.SUCCESS, duration,
        )

    @staticmethod
    def _failure(task: Task, day: date, started: float, exc: Exception) -> ExecutionResult:
        duration = perf_counter() - started
        logger.exception(
            "Task failed status=failed task_id=%s user=%s action=%s date=%s error=%s",
            task.id, task.user, task.action, day, exc,
        )
        return ExecutionResult(
            task.id, task.user, task.action, day, ExecutionStatus.FAILED, duration, str(exc),
        )


class TaskExecutor(_ExecutorBase):
    def execute(self, task: Task, day: date) -> ExecutionResult:
        prepared = self._prepare(task, day)
        if isinstance(prepared, ExecutionResult):
            return prepared
        started = perf_counter()
        try:
            prepared.execute(task)
        except Exception as exc:
            return self._failure(task, day, started, exc)
        return self._success(task, day, started)


class AsyncTaskExecutor(_ExecutorBase):
    def __init__(
        self, users: UserManager, actions: ActionRegistry, *, max_concurrency: int = 4,
    ) -> None:
        super().__init__(users, actions)
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency < 1
        ):
            raise ValueError("max_concurrency must be a positive integer")
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def execute(self, task: Task, day: date) -> ExecutionResult:
        async with self._semaphore:
            prepared = self._prepare(task, day)
            if isinstance(prepared, ExecutionResult):
                return prepared
            started = perf_counter()
            try:
                await prepared.execute_async(task)
            except asyncio.CancelledError:
                # A thread-backed action may still finish after cancellation.
                # Preserve its quota reservation instead of risking overspending.
                logger.warning(
                    "Task cancelled task_id=%s user=%s action=%s date=%s quota_retained=true",
                    task.id, task.user, task.action, day,
                )
                raise
            except Exception as exc:
                return self._failure(task, day, started, exc)
            return self._success(task, day, started)
