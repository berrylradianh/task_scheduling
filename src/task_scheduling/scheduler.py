"""Minute-based daily scheduling with per-task, per-date dispatch claims."""

import asyncio
from collections.abc import Callable
from datetime import date, datetime
import logging
import math
from threading import Event, RLock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .executor import AsyncTaskExecutor, TaskExecutor
from .models import ExecutionResult, Task
from .tasks import TaskManager

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]


def _validate_interval(interval: float) -> None:
    if (
        isinstance(interval, bool)
        or not isinstance(interval, (int, float))
        or not math.isfinite(interval)
        or interval <= 0
    ):
        raise ValueError("poll_interval must be a finite positive number")


class _ScheduleState:
    def __init__(
        self, tasks: TaskManager, *, timezone: str = "UTC", clock: Clock | None = None,
    ) -> None:
        if not isinstance(timezone, str) or not timezone:
            raise ValueError("timezone must be an IANA timezone name")
        try:
            self.timezone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {timezone}") from exc
        self._tasks = tasks
        self._clock = clock if clock is not None else lambda: datetime.now(self.timezone)
        self._claims: set[tuple[str, date]] = set()
        self._results: list[ExecutionResult] = []
        self._lock = RLock()

    @property
    def results(self) -> tuple[ExecutionResult, ...]:
        with self._lock:
            return tuple(self._results)

    def _claim_due(self, now: datetime | None) -> tuple[date, list[Task]]:
        instant = now if now is not None else self._clock()
        if (
            not isinstance(instant, datetime)
            or instant.tzinfo is None
            or instant.utcoffset() is None
        ):
            raise ValueError("scheduler requires a timezone-aware datetime")
        local = instant.astimezone(self.timezone)
        day = local.date()
        due: list[Task] = []
        with self._lock:
            for task in self._tasks.list_tasks():
                key = (task.id, day)
                if (
                    task.scheduled_time.hour == local.hour
                    and task.scheduled_time.minute == local.minute
                    and key not in self._claims
                ):
                    # Claim before dispatch so overlapping ticks cannot duplicate work.
                    self._claims.add(key)
                    due.append(task)
        return day, due

    def _record(self, result: ExecutionResult) -> None:
        with self._lock:
            self._results.append(result)


class Scheduler(_ScheduleState):
    def __init__(
        self, tasks: TaskManager, executor: TaskExecutor, *,
        timezone: str = "UTC", clock: Clock | None = None,
    ) -> None:
        super().__init__(tasks, timezone=timezone, clock=clock)
        self._executor = executor

    def run_pending(self, now: datetime | None = None) -> list[ExecutionResult]:
        day, tasks = self._claim_due(now)
        results = []
        for task in tasks:
            result = self._executor.execute(task, day)
            self._record(result)
            results.append(result)
        return results

    def run_forever(self, *, poll_interval: float = 1.0, stop_event: Event | None = None) -> None:
        _validate_interval(poll_interval)
        stop = stop_event if stop_event is not None else Event()
        logger.info("Scheduler started mode=sync timezone=%s", self.timezone.key)
        while not stop.is_set():
            self.run_pending()
            stop.wait(poll_interval)
        logger.info("Scheduler stopped mode=sync")


class AsyncScheduler(_ScheduleState):
    def __init__(
        self, tasks: TaskManager, executor: AsyncTaskExecutor, *,
        timezone: str = "UTC", clock: Clock | None = None,
    ) -> None:
        super().__init__(tasks, timezone=timezone, clock=clock)
        self._executor = executor

    async def run_pending(self, now: datetime | None = None) -> list[ExecutionResult]:
        day, tasks = self._claim_due(now)

        async def execute_and_record(task: Task) -> ExecutionResult:
            result = await self._executor.execute(task, day)
            self._record(result)
            return result

        return list(await asyncio.gather(*(execute_and_record(task) for task in tasks)))

    async def run_forever(
        self, *, poll_interval: float = 1.0, stop_event: asyncio.Event | None = None,
    ) -> None:
        _validate_interval(poll_interval)
        stop = stop_event if stop_event is not None else asyncio.Event()
        logger.info("Scheduler started mode=async timezone=%s", self.timezone.key)
        while not stop.is_set():
            await self.run_pending()
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except TimeoutError:
                pass
        logger.info("Scheduler stopped mode=async")
