from datetime import datetime, timezone

from task_scheduling import ActionStrategy, Task

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def task_data(task_id: str = "task-1", *, user: str = "alice", action: str = "sync", time: str = "12:00") -> dict:
    return {
        "id": task_id, "user": user, "time": time, "action": action,
        "params": {"target": "/data/x"},
    }


class RecordingAction(ActionStrategy):
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    def execute(self, task: Task) -> None:
        self.calls.append(task.id)
        if self.fail:
            raise RuntimeError("simulated action failure")
