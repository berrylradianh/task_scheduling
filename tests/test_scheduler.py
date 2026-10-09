from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
import unittest
from zoneinfo import ZoneInfo

from task_scheduling import (
    ActionRegistry, ExecutionStatus, Scheduler, TaskExecutor, TaskManager, UserManager,
)
from tests.helpers import NOW, RecordingAction, task_data


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.users = UserManager()
        self.users.register("alice", 3)
        self.users.register("bob", 5)
        self.actions = ActionRegistry.with_defaults()
        self.recording = RecordingAction()
        self.actions.register("record", self.recording)
        self.tasks = TaskManager(self.users, self.actions)
        self.executor = TaskExecutor(self.users, self.actions)
        self.scheduler = Scheduler(self.tasks, self.executor)

    def submit(self, task_id: str, **kwargs) -> None:
        self.tasks.submit(task_data(task_id, action="record", **kwargs))

    def test_runs_only_matching_time(self) -> None:
        self.submit("due")
        self.submit("later", time="12:01")
        self.assertEqual(self.scheduler.run_pending(NOW - timedelta(minutes=1)), [])
        self.assertEqual([r.task_id for r in self.scheduler.run_pending(NOW)], ["due"])
        self.assertEqual([r.task_id for r in self.scheduler.run_pending(NOW + timedelta(minutes=1))], ["later"])

    def test_multiple_users_and_tasks_preserve_registration_order(self) -> None:
        self.submit("alice-first")
        self.submit("bob-first", user="bob")
        self.submit("alice-second")
        results = self.scheduler.run_pending(NOW)
        self.assertEqual([r.task_id for r in results], ["alice-first", "bob-first", "alice-second"])
        self.assertTrue(all(r.status == ExecutionStatus.SUCCESS for r in results))
        self.assertEqual(self.users.quota_status("alice", NOW.date()).used, 2)
        self.assertEqual(self.users.quota_status("bob", NOW.date()).used, 1)
        self.assertEqual(self.scheduler.results, tuple(results))

    def test_repeated_polls_do_not_repeat_tasks(self) -> None:
        self.submit("once")
        self.scheduler.run_pending(NOW)
        self.assertEqual(self.scheduler.run_pending(NOW + timedelta(seconds=30)), [])
        self.assertEqual(self.recording.calls, ["once"])

    def test_overlapping_threaded_ticks_do_not_duplicate_dispatch(self) -> None:
        for index in range(3):
            self.submit(f"task-{index}")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.scheduler.run_pending(NOW), range(20)))
        self.assertEqual(sum(len(result) for result in results), 3)
        self.assertCountEqual(self.recording.calls, ["task-0", "task-1", "task-2"])
        self.assertEqual(len(self.scheduler.results), 3)

    def test_next_day_repeats_and_has_fresh_quota(self) -> None:
        for index in range(4):
            self.submit(f"task-{index}")
        with self.assertLogs("task_scheduling", level="WARNING"):
            first = self.scheduler.run_pending(NOW)
            second = self.scheduler.run_pending(NOW + timedelta(days=1))
        expected = [ExecutionStatus.SUCCESS] * 3 + [ExecutionStatus.QUOTA_EXCEEDED]
        self.assertEqual([r.status for r in first], expected)
        self.assertEqual([r.status for r in second], expected)
        self.assertEqual(self.scheduler.run_pending(NOW), [])

    def test_failure_does_not_stop_other_tasks_or_retry(self) -> None:
        self.actions.register("fail", RecordingAction(fail=True))
        self.tasks.submit(task_data("failure", action="fail"))
        self.submit("success")
        with self.assertLogs("task_scheduling", level="ERROR"):
            results = self.scheduler.run_pending(NOW)
        self.assertEqual([r.status for r in results], [ExecutionStatus.FAILED, ExecutionStatus.SUCCESS])
        self.assertEqual(self.scheduler.run_pending(NOW), [])
        self.assertEqual(self.users.quota_status("alice", NOW.date()).used, 2)

    def test_new_task_submitted_during_due_minute_can_run(self) -> None:
        self.submit("first")
        self.scheduler.run_pending(NOW)
        self.submit("new")
        results = self.scheduler.run_pending(NOW + timedelta(seconds=10))
        self.assertEqual([r.task_id for r in results], ["new"])

    def test_missed_minutes_are_skipped(self) -> None:
        self.submit("missed")
        self.assertEqual(self.scheduler.run_pending(NOW + timedelta(minutes=1)), [])
        self.assertEqual(len(self.scheduler.run_pending(NOW + timedelta(days=1))), 1)

    def test_timezone_conversion_uses_local_date_and_time(self) -> None:
        self.submit("midnight", time="00:00")
        scheduler = Scheduler(self.tasks, self.executor, timezone="Asia/Jakarta")
        instant = datetime(2026, 10, 9, 17, 0, tzinfo=timezone.utc)
        results = scheduler.run_pending(instant)
        self.assertEqual(results[0].execution_date.isoformat(), "2026-10-10")
        self.assertEqual(self.users.quota_status("alice", instant.date()).used, 0)
        self.assertEqual(self.users.quota_status("alice", results[0].execution_date).used, 1)

    def test_repeated_dst_minute_runs_once_per_date(self) -> None:
        self.submit("dst", time="01:30")
        scheduler = Scheduler(self.tasks, self.executor, timezone="America/New_York")
        zone = ZoneInfo("America/New_York")
        first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
        repeated = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
        self.assertEqual(len(scheduler.run_pending(first)), 1)
        self.assertEqual(scheduler.run_pending(repeated), [])

    def test_injected_clock(self) -> None:
        self.submit("clock")
        scheduler = Scheduler(self.tasks, self.executor, clock=lambda: NOW)
        self.assertEqual(len(scheduler.run_pending()), 1)

    def test_invalid_time_timezone_and_interval(self) -> None:
        for instant in [NOW.replace(tzinfo=None), "12:00"]:
            with self.subTest(instant=instant), self.assertRaises(ValueError):
                self.scheduler.run_pending(instant)
        with self.assertRaises(ValueError):
            Scheduler(self.tasks, self.executor, timezone="not/a-timezone")
        for interval in [0, -1, float("nan"), float("inf"), True]:
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.scheduler.run_forever(poll_interval=interval)

    def test_polling_loop_can_stop_after_a_tick(self) -> None:
        stop = Event()

        def clock() -> datetime:
            stop.set()
            return NOW

        self.submit("loop")
        scheduler = Scheduler(self.tasks, self.executor, clock=clock)
        scheduler.run_forever(stop_event=stop)
        self.assertEqual(self.recording.calls, ["loop"])
