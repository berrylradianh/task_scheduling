import asyncio
from datetime import timedelta
import unittest

from task_scheduling import (
    ActionRegistry, ActionStrategy, AsyncScheduler, AsyncTaskExecutor,
    ExecutionStatus, Task, TaskManager, UserManager,
)
from tests.helpers import NOW, RecordingAction, task_data


class GatedAsyncAction(ActionStrategy):
    def __init__(self, expected_active: int) -> None:
        self.expected_active = expected_active
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.peak = 0
        self.calls: list[str] = []

    def execute(self, task: Task) -> None:
        raise AssertionError("the async executor should use execute_async")

    async def execute_async(self, task: Task) -> None:
        self.calls.append(task.id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.active == self.expected_active:
            self.started.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.users = UserManager()
        self.users.register("alice", 3)
        self.users.register("bob", 5)
        self.actions = ActionRegistry.with_defaults()
        self.tasks = TaskManager(self.users, self.actions)
        self.executor = AsyncTaskExecutor(self.users, self.actions, max_concurrency=2)
        self.scheduler = AsyncScheduler(self.tasks, self.executor)

    async def test_default_strategies_execute_asynchronously(self) -> None:
        for action in ["sync", "backup", "delete"]:
            self.tasks.submit(task_data(action, action=action))
        with self.assertLogs("task_scheduling", level="INFO"):
            results = await self.scheduler.run_pending(NOW)
        self.assertEqual([result.status for result in results], [ExecutionStatus.SUCCESS] * 3)

    async def test_concurrency_is_real_and_bounded(self) -> None:
        probe = GatedAsyncAction(expected_active=2)
        self.actions.register("probe", probe)
        for index in range(5):
            self.tasks.submit(task_data(f"task-{index}", user="bob", action="probe"))
        running = asyncio.create_task(self.scheduler.run_pending(NOW))
        try:
            await asyncio.wait_for(probe.started.wait(), timeout=2)
            self.assertEqual(probe.active, 2)
            self.assertEqual(self.users.quota_status("bob", NOW.date()).used, 2)
        finally:
            probe.release.set()
            results = await asyncio.wait_for(running, timeout=2)
        self.assertEqual(probe.peak, 2)
        self.assertEqual([result.task_id for result in results], [f"task-{index}" for index in range(5)])
        self.assertTrue(all(result.status == ExecutionStatus.SUCCESS for result in results))

    async def test_concurrent_tasks_do_not_overspend_quota(self) -> None:
        strategy = RecordingAction()
        self.actions.register("record", strategy)
        for index in range(10):
            self.tasks.submit(task_data(f"task-{index}", action="record"))
        with self.assertLogs("task_scheduling", level="WARNING"):
            results = await self.scheduler.run_pending(NOW)
        self.assertEqual(sum(result.status == ExecutionStatus.SUCCESS for result in results), 3)
        self.assertEqual(sum(result.status == ExecutionStatus.QUOTA_EXCEEDED for result in results), 7)
        self.assertEqual(len(strategy.calls), 3)
        self.assertEqual(self.users.quota_status("alice", NOW.date()).used, 3)

    async def test_overlapping_ticks_claim_each_task_once(self) -> None:
        probe = GatedAsyncAction(expected_active=2)
        self.actions.register("probe", probe)
        for index in range(2):
            self.tasks.submit(task_data(f"task-{index}", action="probe"))
        running = asyncio.create_task(self.scheduler.run_pending(NOW))
        try:
            await asyncio.wait_for(probe.started.wait(), timeout=2)
            self.assertEqual(await self.scheduler.run_pending(NOW), [])
        finally:
            probe.release.set()
            await asyncio.wait_for(running, timeout=2)
        self.assertCountEqual(probe.calls, ["task-0", "task-1"])
        self.assertEqual(len(self.scheduler.results), 2)

    async def test_failures_are_isolated_and_not_retried(self) -> None:
        failing = RecordingAction(fail=True)
        self.actions.register("fail", failing)
        self.tasks.submit(task_data("failure", action="fail"))
        self.tasks.submit(task_data("success"))
        with self.assertLogs("task_scheduling", level="ERROR"):
            results = await self.scheduler.run_pending(NOW)
        self.assertEqual([result.status for result in results], [ExecutionStatus.FAILED, ExecutionStatus.SUCCESS])
        self.assertEqual(await self.scheduler.run_pending(NOW), [])
        self.assertEqual(self.users.quota_status("alice", NOW.date()).used, 2)

    async def test_next_day_runs_again_with_fresh_quota(self) -> None:
        self.tasks.submit(task_data())
        self.assertEqual(len(await self.scheduler.run_pending(NOW)), 1)
        tomorrow = NOW + timedelta(days=1)
        self.assertEqual(len(await self.scheduler.run_pending(tomorrow)), 1)
        self.assertEqual(self.users.quota_status("alice", tomorrow.date()).used, 1)

    async def test_cancellation_retains_quota_and_dispatch_claim(self) -> None:
        probe = GatedAsyncAction(expected_active=1)
        self.actions.register("probe", probe)
        self.tasks.submit(task_data(action="probe"))
        running = asyncio.create_task(self.scheduler.run_pending(NOW))
        await asyncio.wait_for(probe.started.wait(), timeout=2)
        with self.assertLogs("task_scheduling", level="WARNING"):
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
        self.assertEqual(self.users.quota_status("alice", NOW.date()).used, 1)
        self.assertEqual(await self.scheduler.run_pending(NOW), [])
        self.assertEqual(probe.active, 0)

    async def test_async_polling_loop_can_stop_after_a_tick(self) -> None:
        stop = asyncio.Event()

        def clock():
            stop.set()
            return NOW

        self.tasks.submit(task_data())
        scheduler = AsyncScheduler(self.tasks, self.executor, clock=clock)
        await asyncio.wait_for(scheduler.run_forever(stop_event=stop), timeout=2)
        self.assertEqual(len(scheduler.results), 1)

    async def test_invalid_concurrency_and_poll_interval(self) -> None:
        for limit in [0, -1, True, 1.5]:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                AsyncTaskExecutor(self.users, self.actions, max_concurrency=limit)
        for interval in [0, -1, float("nan")]:
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                await self.scheduler.run_forever(poll_interval=interval)
