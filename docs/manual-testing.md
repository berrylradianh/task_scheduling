# Manual testing tutorial for developers

Use this guide to verify the three project goals through the public Python
interface and the installed CLI. Follow the numbered scenarios and check the
boxes in the final checklist only after comparing your results.

All commands assume a POSIX shell such as bash or zsh and run from the repository
root, the directory containing `pyproject.toml`. No database, API server, HTTP
client, or real target directories are needed. Built-in actions only log
simulated work.

## 1. Prepare the environment — MAN-01

1. Open a normal terminal and change to your checkout's root directory.
2. Confirm Python 3.11 or newer:

   ```sh
   python3 --version
   ```

3. Create a virtual environment if this checkout does not already have one:

   ```sh
   python3 -m venv .venv
   ```

4. Install the project into that environment:

   ```sh
   .venv/bin/python -m pip install -e .
   .venv/bin/python -m pip check
   .venv/bin/task-scheduling --help
   ```

Expected: installation succeeds, `pip check` reports no broken requirements,
and help lists `--config`, `--demo`, `--once`, `--at`, `--async`,
`--max-concurrency`, `--poll-interval`, and `--log-level`.

The app has no third-party runtime dependencies. Installation may download the
setuptools build backend. The OS must supply IANA timezone data. Verify it:

```python
from zoneinfo import ZoneInfo

print(ZoneInfo("UTC").key)
print(ZoneInfo("Asia/Jakarta").key)
```

Expected: `UTC` and `Asia/Jakarta`, with no exception.

### How to run the Python examples

Every `python` block in this guide is a complete, independent example. Run one
block at a time. Either paste it into a Python session started with
`.venv/bin/python`, or wrap the entire block as follows:

```sh
.venv/bin/python - <<'PY'
# Paste one complete Python block here.
PY
```

When pasting into an interactive session, terminate class/function definitions
with a blank line. A heredoc avoids interactive indentation issues. Each example
builds its own service; it does not depend on a previous example's variables.

Do not modify the OS clock. Explicit datetimes below simulate scheduler ticks.
Log timestamps still show the real wall-clock time; the `date=` execution field
and returned `execution_date` correspond to the simulated scheduling date.

## 2. Run the built-in demonstrations — MAN-02

Run synchronous mode:

```sh
.venv/bin/task-scheduling --demo
echo $?
```

Expected exit code: `0`. Look for three summaries in order:

```text
Tick completed success=4 failed=0 quota_exceeded=1
Tick completed success=0 failed=0 quota_exceeded=0
Tick completed success=4 failed=0 quota_exceeded=1
```

The first tick runs Alice's first three tasks and Bob's task, then skips
`alice-over-quota`. A second poll in the same minute runs nothing. The third
tick advances to noon the next day and runs the tasks with fresh quota.

Run asynchronous mode:

```sh
.venv/bin/task-scheduling --demo --async --max-concurrency 2
echo $?
```

Expected: the same three summaries and exit code `0`. Individual execution
logs can interleave. Do not compare durations or log ordering between modes.

Pass criteria: both demos finish without sleeping until noon or the next day;
both enforce quotas and suppress repeated polling.

## 3. Load the original configuration at a fixed time — MAN-03

Use the provided Alice/Bob configuration:

```sh
.venv/bin/task-scheduling --config examples/config.json --once --at '2026-10-09T12:00:00+07:00'
echo $?
```

Expected: `success=3 failed=0 quota_exceeded=0`, exit code `0`, and simulated
execution logs for `alice-sync`, `bob-backup`, and `alice-delete`.

Verify the configured timezone is used by expressing the same instant in UTC:

```sh
.venv/bin/task-scheduling --config examples/config.json --once --at '2026-10-09T05:00:00+00:00'
```

Expected: the same three tasks execute; Jakarta's local time is noon.

Verify nothing is due at another minute:

```sh
.venv/bin/task-scheduling --config examples/config.json --once --at '2026-10-09T11:59:00+07:00'
```

Expected: `success=0 failed=0 quota_exceeded=0`. No action runs.

Important: each CLI invocation starts a new process and creates fresh in-memory
state. Running the noon command twice executes the tasks twice. Duplicate
prevention within one running service is tested in MAN-04.

## 4. Check quotas, multiple tasks, duplicate polls, and the next day — MAN-04

[manual-config.json](manual-config.json) sets Alice's quota to two and Bob's to
one. All four tasks are due at noon UTC; three belong to Alice.

First inspect the CLI behavior:

```sh
.venv/bin/task-scheduling --config docs/manual-config.json --once --at '2026-10-09T12:00:00+00:00'
```

Expected: `success=3 failed=0 quota_exceeded=1`. `alice-over-quota` is skipped.
The `/manual/...` paths need not exist; no files are touched.

Now test repeated ticks in the **same service instance**:

```python
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from task_scheduling import SchedulingService

logging.basicConfig(level=logging.INFO, force=True)
config = json.loads(Path("docs/manual-config.json").read_text())
service = SchedulingService.from_config(config)
now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

def show_tick(label, instant):
    results = service.scheduler.run_pending(instant)
    print(label, [(r.task_id, r.status.value) for r in results])
    for user in ("alice", "bob"):
        status = service.users.quota_status(user, instant.date())
        print(user, "used=", status.used, "remaining=", status.remaining)
    return results

first = show_tick("DAY 1", now)
repeat = show_tick("REPEATED POLL", now + timedelta(seconds=20))
tomorrow = show_tick("DAY 2", now + timedelta(days=1))
print("completed result history:", len(service.scheduler.results))
print("previous day's Alice usage:", service.users.quota_status("alice", now.date()).used)
```

Expected:

- `DAY 1` returns, in registration order: `alice-sync: success`,
  `bob-backup: success`, `alice-delete: success`,
  `alice-over-quota: quota_exceeded`.
- Alice's `used=2`, `remaining=0`; Bob's `used=1`, `remaining=0`.
- `REPEATED POLL` returns `[]`, and usage is unchanged.
- `DAY 2` returns the same four statuses using the next date's quota.
- Completed result history contains `8` results, including the two quota skips.
- Previous-day Alice usage remains `2`; history was not erased to reset quota.

Pass criteria: one user owns multiple due tasks, quotas are independent per
user, skipped tasks consume nothing, duplicate polls consume nothing, and a new
date provides fresh quota.

## 5. Check configurable submissions and rejected input — MAN-05

Run this example to submit a dictionary, confirm the input is copied, and
exercise invalid submissions:

```python
from datetime import date
from task_scheduling import SchedulingService

service = SchedulingService.from_config({
    "users": [{"id": "alice", "quota": 2}], "tasks": [],
})
data = {
    "id": "submitted", "user": "alice", "time": "12:00", "action": "sync",
    "params": {"target": "/original"},
}
registered = service.tasks.submit(data)
data["params"]["target"] = "/changed-after-submission"
print("registered target:", registered.params["target"])

base = {
    "id": "invalid", "user": "alice", "time": "12:00", "action": "sync",
    "params": {"target": "/manual"},
}
cases = [
    ("invalid hour", {**base, "time": "24:00"}, "between"),
    ("invalid format", {**base, "time": "2:00"}, "HH:MM"),
    ("unknown owner", {**base, "user": "missing"}, "unknown user"),
    ("unknown action", {**base, "action": "missing"}, "unknown action"),
    ("missing target", {**base, "params": {}}, "params.target"),
    ("invalid params type", {**base, "params": []}, "dictionary"),
    ("duplicate ID", {**base, "id": "submitted"}, "already exists"),
]
for label, candidate, expected in cases:
    try:
        service.tasks.submit(candidate)
    except ValueError as exc:
        assert expected in str(exc), (label, str(exc))
        print("REJECTED:", label, "->", exc)
    else:
        raise AssertionError(f"unexpected acceptance: {label}")

for quota in (-1, True):
    try:
        service.users.register("bad-quota", quota)
    except ValueError as exc:
        print("REJECTED quota:", repr(quota), "->", exc)
    else:
        raise AssertionError("invalid quota accepted")

print("registered task count:", len(service.tasks.list_tasks()))
print("quota used before execution:", service.users.quota_status("alice", date(2026, 10, 9)).used)
```

Expected: registered target is `/original`, every invalid case prints
`REJECTED`, registered task count is `1`, and usage before execution is `0`.
An `AssertionError` means a case did not behave as expected.

To check zero quota separately:

```python
from datetime import datetime, timezone
from task_scheduling import SchedulingService

service = SchedulingService.from_config({
    "users": [{"id": "alice", "quota": 0}],
    "tasks": [{"id": "blocked", "user": "alice", "time": "12:00",
               "action": "sync", "params": {"target": "/manual"}}],
})
now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
print([r.status.value for r in service.scheduler.run_pending(now)])
print("used:", service.users.quota_status("alice", now.date()).used)
```

Expected: `['quota_exceeded']`, `used: 0`, and no simulated action execution.

## 6. Add strategies and inspect failure isolation — MAN-06

Register two strategies without changing the scheduler or executor. A custom
report action uses a `message` parameter instead of `target`; another action
deliberately fails.

```python
from datetime import datetime, timezone
import logging
from task_scheduling import ActionStrategy, SchedulingService

logging.basicConfig(level=logging.INFO, force=True)

class ReportAction(ActionStrategy):
    def validate(self, params):
        message = params.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("params.message is required")

    def execute(self, task):
        logging.getLogger("manual.report").info("message=%s", task.params["message"])

class FailingAction(ActionStrategy):
    def execute(self, task):
        raise RuntimeError("manual action failure")

service = SchedulingService.from_config({
    "users": [{"id": "alice", "quota": 3}], "tasks": [],
})
service.actions.register("report", ReportAction())
service.actions.register("fail", FailingAction())
for task_id, action, params in [
    ("report", "report", {"message": "Developer manual check"}),
    ("failure", "fail", {}),
    ("after-failure", "sync", {"target": "/manual"}),
    ("over-quota", "sync", {"target": "/manual/extra"}),
]:
    service.tasks.submit({"id": task_id, "user": "alice", "time": "12:00",
                          "action": action, "params": params})

now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
for result in service.scheduler.run_pending(now):
    print(result.task_id, result.status.value, "error=", result.error)
print("used:", service.users.quota_status("alice", now.date()).used)
print("retry in same minute:", service.scheduler.run_pending(now))
```

Expected statuses: `success`, `failed`, `success`, `quota_exceeded`.
The failure's error is `manual action failure`. Usage is `3`, since the failed
action consumes a unit. The repeat poll returns `[]`.

Inspect the logs: the failure includes `task_id=failure`, `user=alice`,
`action=fail`, and a traceback. This traceback is intentional; the scenario
passes if the later valid task still executes and the script completes.

## 7. Inspect logging levels — MAN-07

```sh
.venv/bin/task-scheduling --demo --log-level INFO
.venv/bin/task-scheduling --demo --log-level WARNING
```

Expected at `INFO`: execution start, simulated target, completion, tick summaries,
and quota warnings. Expected at `WARNING`: only the two quota warnings from
the two simulated dates. The actions still execute when INFO messages are hidden.

Logs go to **stderr**, so redirect both stdout and stderr when saving evidence.
Save to a new file you choose; redirection replaces an existing file:

```sh
.venv/bin/task-scheduling --demo > /tmp/task-scheduling-manual-demo.log 2>&1
echo $?
```

Expected exit code: `0`. Read the saved file with your editor and compare the
three summaries from MAN-02. Logger timestamps and durations vary between runs.

## 8. Observe bounded async concurrency and overlapping ticks — MAN-08

The simulated built-ins finish too quickly to judge concurrency from duration.
This custom native async strategy waits at an event gate so you can inspect
two tasks while both are active. It does not require network access.

```python
import asyncio
from datetime import datetime, timezone
import logging
from task_scheduling import ActionStrategy, SchedulingService

logging.basicConfig(level=logging.INFO, force=True)

class GatedAction(ActionStrategy):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.peak = 0

    def execute(self, task):
        raise RuntimeError("this manual probe requires async mode")

    async def execute_async(self, task):
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.active == 2:
            self.started.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1

async def check_concurrency():
    service = SchedulingService.from_config({
        "users": [{"id": "alice", "quota": 3}], "tasks": [],
    }, asynchronous=True, max_concurrency=2)
    probe = GatedAction()
    service.actions.register("gated", probe)
    for index in range(4):
        service.tasks.submit({"id": f"gated-{index}", "user": "alice",
                              "time": "12:00", "action": "gated"})
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    running = asyncio.create_task(service.scheduler.run_pending(now))
    try:
        await asyncio.wait_for(probe.started.wait(), timeout=3)
        print("active before release:", probe.active)
        print("used before release:", service.users.quota_status("alice", now.date()).used)
        print("overlapping tick:", await service.scheduler.run_pending(now))
    finally:
        probe.release.set()
        results = await asyncio.wait_for(running, timeout=3)
    print("results:", [(r.task_id, r.status.value) for r in results])
    print("peak active:", probe.peak)
    print("final usage:", service.users.quota_status("alice", now.date()).used)
    assert probe.peak == 2

asyncio.run(check_concurrency())
```

Expected: active and usage before release are both `2`; the overlapping tick
returns `[]`. Final results contain three successes and one quota skip, in task
ID order `gated-0` through `gated-3`. Peak active is `2`, final usage is `3`.
There must be no `TimeoutError` or `AssertionError`.

Pass criteria: work really overlaps, the concurrency limit is respected, the
shared user quota is not overspent, and overlapping polls do not duplicate work.

## 9. Inspect async cancellation — MAN-09

This scenario cancels a native async action after it has started and reserved
quota. It deliberately uses no thread-backed work.

```python
import asyncio
from datetime import datetime, timezone
import logging
from task_scheduling import ActionStrategy, SchedulingService

logging.basicConfig(level=logging.INFO, force=True)

class WaitingAction(ActionStrategy):
    def __init__(self):
        self.started = asyncio.Event()
        self.cleaned_up = False

    def execute(self, task):
        raise RuntimeError("this manual probe requires async mode")

    async def execute_async(self, task):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleaned_up = True

async def check_cancellation():
    service = SchedulingService.from_config({
        "users": [{"id": "alice", "quota": 1}], "tasks": [],
    }, asynchronous=True)
    probe = WaitingAction()
    service.actions.register("wait", probe)
    service.tasks.submit({"id": "cancel-me", "user": "alice", "time": "12:00",
                          "action": "wait"})
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    running = asyncio.create_task(service.scheduler.run_pending(now))
    try:
        await asyncio.wait_for(probe.started.wait(), timeout=3)
    finally:
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            print("cancellation propagated")
    print("cleaned up:", probe.cleaned_up)
    print("used:", service.users.quota_status("alice", now.date()).used)
    print("retry:", await service.scheduler.run_pending(now))
    print("completed result count:", len(service.scheduler.results))

asyncio.run(check_cancellation())
```

Expected: cancellation propagates, cleaned up is `True`, used is `1`, retry is
`[]`, and completed result count is `0`. A warning identifies `cancel-me` and
`quota_retained=true`.

Cancellation retains quota and dispatch claims. This does not demonstrate
stopping a running thread: a synchronous action adapted with `to_thread` may
continue after cancellation. See [notes and future work](notes-and-future-work.md).

## 10. Check timezone boundaries and missed minutes — MAN-10

```python
from datetime import datetime, timedelta, timezone
from task_scheduling import SchedulingService

service = SchedulingService.from_config({
    "timezone": "Asia/Jakarta",
    "users": [{"id": "alice", "quota": 1}],
    "tasks": [{"id": "midnight", "user": "alice", "time": "00:00",
               "action": "sync", "params": {"target": "/manual"}}],
})
before = datetime(2026, 10, 9, 16, 59, tzinfo=timezone.utc)
midnight = datetime(2026, 10, 9, 17, 0, tzinfo=timezone.utc)
print("before local midnight:", service.scheduler.run_pending(before))
results = service.scheduler.run_pending(midnight)
print("execution date:", results[0].execution_date.isoformat())
print("UTC-date usage:", service.users.quota_status("alice", midnight.date()).used)
print("local-date usage:", service.users.quota_status("alice", results[0].execution_date).used)

missed = SchedulingService.from_config({
    "users": [{"id": "alice", "quota": 1}],
    "tasks": [{"id": "noon", "user": "alice", "time": "12:00",
               "action": "sync", "params": {"target": "/manual"}}],
})
noon = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
print("missed minute:", missed.scheduler.run_pending(noon + timedelta(minutes=1)))
print("following day:", [r.status.value for r in missed.scheduler.run_pending(noon + timedelta(days=1))])
```

Expected: before-midnight results are `[]`; execution date is `2026-10-10`;
UTC-date usage is `0` and local-date usage is `1`. The missed minute returns
`[]`; the following day returns `['success']`.

This confirms the service uses the configured local date for quota accounting
and does not backfill a task missed at noon.

## 11. Watch the actual polling loop — MAN-11

Schedule one task for the current UTC minute, poll every quarter-second, and
stop automatically after two seconds:

```python
from datetime import datetime, timezone
import logging
from threading import Event, Timer
from task_scheduling import SchedulingService

logging.basicConfig(level=logging.INFO, force=True)
now = datetime.now(timezone.utc)
service = SchedulingService.from_config({
    "users": [{"id": "alice", "quota": 1}],
    "tasks": [{"id": "live-poll", "user": "alice", "time": now.strftime("%H:%M"),
               "action": "sync", "params": {"target": "/manual"}}],
})
stop = Event()
stopper = Timer(2, stop.set)
stopper.start()
try:
    service.scheduler.run_forever(poll_interval=0.25, stop_event=stop)
finally:
    stopper.cancel()
print("completed results:", len(service.scheduler.results))
print("quota used:", service.users.quota_status("alice", now.date()).used)
```

Expected: scheduler startup/shutdown logs, one successful execution, completed
results `1`, and quota used `1`. Repeated polls must not execute it again.
If the initial tick crosses a minute boundary during setup, the task can be
missed; rerun the example well inside a minute.

To inspect CLI startup and Ctrl+C shutdown separately:

```sh
.venv/bin/task-scheduling --config docs/manual-config.json --poll-interval 1
```

Observe `Scheduler started`, then press Ctrl+C. Expected:
`Scheduler stopped by user` and exit code `0`. Configured tasks run only at noon
UTC, so no action logs are expected at other times. Do not leave this command
running when proceeding to the next scenario.

## 12. Verify invalid CLI input and exit codes — MAN-12

Run each command separately and immediately inspect its exit code with
`echo $?`:

```sh
.venv/bin/task-scheduling --config examples/config.json --once --at '2026-10-09T12:00:00'
echo $?
```

Expected: a timezone-aware datetime error and exit code `2`.

```sh
.venv/bin/task-scheduling --config examples/config.json --at '2026-10-09T12:00:00+07:00'
echo $?
```

Expected: `--at requires --config and --once`, exit code `2`.

```sh
.venv/bin/task-scheduling --demo --async --max-concurrency 0
echo $?
```

Expected: `--max-concurrency must be positive`, exit code `2`.

```sh
.venv/bin/task-scheduling --config examples/config.json --poll-interval 0
echo $?
```

Expected: finite positive polling-interval error, exit code `2`. All four invalid
CLI scenarios should report a useful error without an unhandled traceback.

Exit-code policy: `0` means normal completion, including quota skips; `1` means
an action failed during a CLI one-tick run or demo; `2` means invalid input or
configuration. The deliberate failure in MAN-06 returns a failed Python result;
that script catches action failures through the executor and does not itself
test the CLI's exit code `1`.

## Troubleshooting and test boundaries

| Symptom | Check / expected explanation |
| --- | --- |
| `ModuleNotFoundError: task_scheduling` | Use `.venv/bin/python` after installing the project, or `PYTHONPATH=src python3` from the repository root. |
| Config file not found | Run commands from the repository root; the fixture path is `docs/manual-config.json`. |
| Timezone lookup fails | Ensure OS IANA timezone data is installed. |
| No task runs in the live CLI | Compare current time in the configured timezone with the task's `HH:MM`; fixed-time checks use `--once --at`. |
| Repeating a CLI command executes tasks again | Each process has fresh state; use one service instance to test deduplication. |
| INFO logs are missing | Confirm the log level and inspect stderr. |
| A deliberate action failure prints a traceback | Expected in MAN-06; check that the later task still succeeds. |
| Async execution stalls only in a restricted sandbox | Reproduce in a normal terminal. Earlier sandbox runs stalled while outside-sandbox verification passed; the exact restriction was not identified. |

These checks verify a simulated service. They do not prove real file operations,
restart durability, coordination between service instances, or measured
CPU/memory limits. Read [notes and future work](notes-and-future-work.md) before
interpreting those behaviors as missing test results.

## Developer sign-off checklist

These boxes are intentionally unchecked so each developer can record their own
manual run. Keep your results separately from the project's completion report.

- [ ] MAN-01: Python environment, package installation, help, and timezone data.
- [ ] MAN-02: Both demo modes give the three expected summaries.
- [ ] MAN-03: Original configuration, UTC conversion, and no-due-task minute.
- [ ] MAN-04: Multiple tasks, independent quotas, repeat polls, and next day.
- [ ] MAN-05: Configurable input, copied parameters, rejected input, zero quota.
- [ ] MAN-06: Custom strategies, failure logging, continued execution, failure quota.
- [ ] MAN-07: Logging levels and stderr evidence capture.
- [ ] MAN-08: Two concurrent actions, bounded peak, overlapping tick, atomic quota.
- [ ] MAN-09: Cancellation propagation, cleanup, retained quota/claim, no result.
- [ ] MAN-10: Local midnight accounting and skipped missed minute.
- [ ] MAN-11: Real polling loop, repeat suppression, graceful stop.
- [ ] MAN-12: Useful CLI errors and exit code `2`.

For each scenario, record: tester name, date, OS/Python version, scenario ID,
command/example, observed result, PASS/FAIL, and any relevant log excerpt.
Do not mark the scenario passed solely because a command exits; compare its
behavior with the expected results above.

Optional automated cross-check:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Expected for the current implementation: `Ran 51 tests` and `OK`. Automated
verification details are in [verification-report.md](verification-report.md).
