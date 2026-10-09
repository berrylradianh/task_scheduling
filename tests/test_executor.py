import unittest
from unittest.mock import patch

from task_scheduling import ActionRegistry, ExecutionStatus, Task, TaskExecutor, UserManager
from tests.helpers import NOW, RecordingAction, task_data


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.users = UserManager()
        self.users.register("alice", 1)
        self.actions = ActionRegistry.with_defaults()
        self.executor = TaskExecutor(self.users, self.actions)

    def test_builtins_are_simulated_and_log_context(self) -> None:
        self.users.register("bob", 3)
        with patch("builtins.open") as opening, patch("os.remove") as deleting:
            for action in ["sync", "backup", "delete"]:
                task = Task.from_dict(task_data(action, user="bob", action=action))
                with self.assertLogs("task_scheduling", level="INFO") as captured:
                    result = self.executor.execute(task, NOW.date())
                self.assertEqual(result.status, ExecutionStatus.SUCCESS)
                output = "\n".join(captured.output)
                self.assertIn(f"task_id={action}", output)
                self.assertIn("user=bob", output)
                self.assertIn("target=/data/x", output)
                self.assertIn("status=success", output)
        opening.assert_not_called()
        deleting.assert_not_called()

    def test_quota_exhaustion_does_not_execute_action(self) -> None:
        strategy = RecordingAction()
        self.actions.register("record", strategy)
        task = Task.from_dict(task_data(action="record"))
        self.executor.execute(task, NOW.date())
        with self.assertLogs("task_scheduling.executor", level="WARNING"):
            result = self.executor.execute(task, NOW.date())
        self.assertEqual(result.status, ExecutionStatus.QUOTA_EXCEEDED)
        self.assertEqual(strategy.calls, [task.id])
        self.assertEqual(self.users.quota_status("alice", NOW.date()).used, 1)

    def test_failure_is_logged_and_consumes_quota(self) -> None:
        strategy = RecordingAction(fail=True)
        self.actions.register("fail", strategy)
        task = Task.from_dict(task_data(action="fail"))
        with self.assertLogs("task_scheduling.executor", level="ERROR") as captured:
            result = self.executor.execute(task, NOW.date())
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.error, "simulated action failure")
        self.assertIn("Traceback", "\n".join(captured.output))
        self.assertEqual(self.users.quota_status("alice", NOW.date()).used, 1)

    def test_invalid_direct_execution_does_not_consume_quota(self) -> None:
        invalids = [task_data(user="missing"), task_data(action="missing"),
                    {**task_data(), "params": {}}]
        for data in invalids:
            with self.subTest(data=data), self.assertLogs("task_scheduling.executor", level="ERROR"):
                result = self.executor.execute(Task.from_dict(data), NOW.date())
                self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(self.users.quota_status("alice", NOW.date()).used, 0)
