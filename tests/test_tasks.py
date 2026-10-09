from datetime import time, timezone
import unittest

from task_scheduling import ActionRegistry, Task, TaskManager, UserManager
from tests.helpers import RecordingAction, task_data


class TaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.users = UserManager()
        self.users.register("alice", 3)
        self.users.register("bob", 5)
        self.actions = ActionRegistry.with_defaults()
        self.tasks = TaskManager(self.users, self.actions)

    def test_dictionary_submission_and_multiple_tasks(self) -> None:
        first = self.tasks.submit(task_data("first"))
        second = self.tasks.submit(task_data("second"))
        self.tasks.submit(task_data("bob-task", user="bob"))
        self.assertEqual(first.scheduled_time, time(12, 0))
        self.assertEqual(self.tasks.list_tasks("alice"), (first, second))
        self.assertIs(self.tasks.get("first"), first)

    def test_caller_cannot_change_submitted_parameters(self) -> None:
        data = task_data()
        data["params"]["options"] = {"mode": "safe"}
        task = self.tasks.submit(data)
        data["params"]["target"] = "/changed"
        data["params"]["options"]["mode"] = "changed"
        self.assertEqual(task.params["target"], "/data/x")
        self.assertEqual(task.params["options"]["mode"], "safe")
        with self.assertRaises(TypeError):
            task.params["target"] = "/changed"

    def test_time_validation(self) -> None:
        for invalid in ["24:00", "12:60", "2:00", "12:00:00", "noon", 1200]:
            with self.subTest(time=invalid), self.assertRaises(ValueError):
                self.tasks.submit(task_data(time=invalid))
        for valid in ["00:00", "23:59"]:
            self.tasks.submit(task_data(valid, time=valid))

    def test_invalid_task_fields(self) -> None:
        invalids = [None, {}, {**task_data(), "extra": True}, {**task_data(), "params": []},
                    {**task_data(), "params": {1: "bad"}}, {**task_data(), "id": ""}]
        for invalid in invalids:
            with self.subTest(data=invalid), self.assertRaises(ValueError):
                self.tasks.submit(invalid)

    def test_invalid_direct_task_times(self) -> None:
        for invalid in ["12:00", time(12, 0, 1), time(12, 0, tzinfo=timezone.utc)]:
            with self.subTest(time=invalid), self.assertRaises(ValueError):
                Task("direct", "alice", invalid, "sync")

    def test_unknown_user_action_and_missing_target(self) -> None:
        invalids = [task_data(user="unknown"), task_data(action="unknown"),
                    {**task_data(), "params": {}}, {**task_data(), "params": {"target": ""}}]
        for invalid in invalids:
            with self.subTest(data=invalid), self.assertRaises(ValueError):
                self.tasks.submit(invalid)
        self.assertEqual(self.tasks.list_tasks(), ())

    def test_duplicate_and_missing_task(self) -> None:
        self.tasks.submit(task_data())
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.tasks.submit(task_data())
        with self.assertRaisesRegex(ValueError, "unknown task"):
            self.tasks.get("missing")
        with self.assertRaisesRegex(ValueError, "unknown user"):
            self.tasks.list_tasks("missing")

    def test_custom_strategy_registration(self) -> None:
        strategy = RecordingAction()
        self.actions.register("custom", strategy)
        submitted = self.tasks.submit({"id": "custom", "user": "alice", "time": "12:00", "action": "custom"})
        self.assertIs(self.actions.get(submitted.action), strategy)
        self.assertEqual(submitted.params, {})
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.actions.register("custom", strategy)
        with self.assertRaises(ValueError):
            self.actions.register("invalid", object())
