from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from task_scheduling import ActionRegistry, AsyncScheduler, SchedulingService
from tests.helpers import NOW, RecordingAction, task_data

ROOT = Path(__file__).resolve().parents[1]


def config() -> dict:
    return {"timezone": "UTC", "users": [{"id": "alice", "quota": 3}], "tasks": [task_data()]}


class ServiceTests(unittest.TestCase):
    def test_service_accepts_dictionary_input_and_new_submissions(self) -> None:
        data = config()
        service = SchedulingService.from_config(data, clock=lambda: NOW)
        service.tasks.submit(task_data("another"))
        data["tasks"][0]["params"]["target"] = "/changed"
        self.assertEqual(service.tasks.get("task-1").params["target"], "/data/x")
        self.assertEqual(len(service.scheduler.run_pending()), 2)

    def test_service_supports_custom_registry(self) -> None:
        registry = ActionRegistry()
        recording = RecordingAction()
        registry.register("custom", recording)
        data = config()
        data["tasks"] = [task_data(action="custom")]
        service = SchedulingService.from_config(data, actions=registry)
        service.scheduler.run_pending(NOW)
        self.assertEqual(recording.calls, ["task-1"])

    def test_async_service_composition(self) -> None:
        service = SchedulingService.from_config(config(), asynchronous=True)
        self.assertIsInstance(service.scheduler, AsyncScheduler)

    def test_example_config(self) -> None:
        data = json.loads((ROOT / "examples/config.json").read_text(encoding="utf-8"))
        service = SchedulingService.from_config(data)
        self.assertEqual(len(service.users.list_users()), 2)
        self.assertEqual(len(service.tasks.list_tasks("alice")), 2)
        local = NOW.astimezone(service.scheduler.timezone).replace(hour=12)
        self.assertEqual(len(service.scheduler.run_pending(local)), 3)

    def test_invalid_configuration(self) -> None:
        invalids = [None, {}, [], {**config(), "users": {}}, {**config(), "tasks": {}},
                    {**config(), "extra": True}, {**config(), "timezone": "unknown/zone"},
                    {**config(), "users": [{"id": "alice"}]},
                    {**config(), "users": [{"id": "alice", "quota": 3, "extra": 1}]}]
        for invalid in invalids:
            with self.subTest(config=invalid), self.assertRaises(ValueError):
                SchedulingService.from_config(invalid)
        duplicated = deepcopy(config())
        duplicated["tasks"].append(task_data())
        with self.assertRaises(ValueError):
            SchedulingService.from_config(duplicated)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "task_scheduling", *arguments],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10,
        )

    def test_sync_and_async_demonstrations(self) -> None:
        for extra in [[], ["--async", "--max-concurrency", "2"]]:
            with self.subTest(extra=extra):
                completed = self.run_cli("--demo", *extra)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr.count("success=4 failed=0 quota_exceeded=1"), 2)
                self.assertIn("success=0 failed=0 quota_exceeded=0", completed.stderr)

    def test_configured_once_sync_and_async(self) -> None:
        for extra in [[], ["--async"]]:
            with self.subTest(extra=extra):
                completed = self.run_cli(
                    "--config", "examples/config.json", "--once",
                    "--at", "2026-10-09T12:00:00+07:00", *extra,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("success=3 failed=0 quota_exceeded=0", completed.stderr)

    def test_configured_once_with_no_due_tasks(self) -> None:
        completed = self.run_cli(
            "--config", "examples/config.json", "--once", "--at", "2026-10-09T11:00:00+07:00",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("success=0 failed=0 quota_exceeded=0", completed.stderr)

    def test_invalid_cli_inputs_report_errors(self) -> None:
        invalids = [
            ["--config", "missing-config.json", "--once"],
            ["--config", "examples/config.json", "--once", "--at", "2026-10-09T12:00:00"],
            ["--config", "examples/config.json", "--at", "2026-10-09T12:00:00+07:00"],
            ["--demo", "--max-concurrency", "0"],
            ["--config", "examples/config.json", "--poll-interval", "0"],
        ]
        for arguments in invalids:
            with self.subTest(arguments=arguments):
                completed = self.run_cli(*arguments)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)

    def test_malformed_json_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text("{invalid-json", encoding="utf-8")
            completed = self.run_cli("--config", str(path), "--once")
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("Unable to run service", completed.stderr)
