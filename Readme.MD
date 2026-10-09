# Simulated Strategy-Based Task Scheduling and Resource Limitation System

A Python refactoring of the original daily scheduling script. Users can submit
multiple configurable tasks, actions run through extensible strategy objects,
and each user has an independently enforced daily execution quota. Both
synchronous and asynchronous execution are supported.

For the requirement-by-requirement completion checklist, verification results,
and remaining limitations, see the [project reports](docs/README.md).

The runtime and tests use Python's standard library. Python 3.11 or newer is
required. The built-in `sync`, `backup`, and `delete` strategies only log simulated
work; they do not read, copy, or delete files.

## Quick start

Run from the repository root without installing anything:

```sh
PYTHONPATH=src python3 -m task_scheduling --demo
PYTHONPATH=src python3 -m task_scheduling --demo --async --max-concurrency 2
```

The demo advances a simulated clock through three ticks without waiting:

1. At noon, Alice's first three tasks and Bob's task succeed. Alice's fourth task
   is skipped because her daily quota is three.
2. A second poll in the same minute runs no tasks.
3. At noon the next day, tasks run again with fresh daily quota.

Each day's summary is `success=4 failed=0 quota_exceeded=1`. The repeated poll
reports `success=0 failed=0 quota_exceeded=0`.

For an installed command and an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/task-scheduling --demo
```

Installation downloads the setuptools build backend if needed; the application
has no third-party runtime dependencies. IANA timezone data must be available
on the host (normally provided by the OS on Linux and macOS).

## Run the service

`examples/config.json` contains the original Alice and Bob scenario. Start the
polling service with:

```sh
PYTHONPATH=src python3 -m task_scheduling --config examples/config.json
```

It checks for due tasks every second. Stop it with Ctrl+C. Async mode executes
tasks concurrently up to the configured limit:

```sh
PYTHONPATH=src python3 -m task_scheduling --config examples/config.json --async --max-concurrency 4
```

Run a single tick at a specified instant:

```sh
PYTHONPATH=src python3 -m task_scheduling --config examples/config.json --once --at '2026-10-09T12:00:00+07:00'
```

`--at` requires `--config` and `--once`, and its datetime must include a UTC
offset. Without `--at`, the service uses the current time. `--poll-interval`
sets the polling frequency; `--log-level` controls logging verbosity. Logs go to
stderr. Exit codes are 0 for normal completion (including quota skips), 1 when
an action fails in a single tick or demo, and 2 for invalid configuration or
arguments.

## Configuration and submission

```json
{
  "timezone": "Asia/Jakarta",
  "users": [
    {"id": "alice", "quota": 3},
    {"id": "bob", "quota": 5}
  ],
  "tasks": [
    {
      "id": "alice-sync",
      "user": "alice",
      "time": "12:00",
      "action": "sync",
      "params": {"target": "/data/x"}
    }
  ]
}
```

- `timezone` is an IANA timezone name; the default is `UTC`.
- User IDs and task IDs must be unique within their respective collections.
- Quotas are non-negative integers; zero disables execution for that user.
- Tasks repeat daily at a minute-precision `HH:MM` local time.
- The owner and action must already be registered.
- `params` is an action-specific dictionary. Built-in actions require a
  non-empty string `target`; a custom strategy can define other parameters.
- Task parameters are copied at submission so later changes to the input
  dictionary cannot change the registered task.

User submissions are accepted through the Python service interface:

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from task_scheduling import SchedulingService

service = SchedulingService.from_config({
    "timezone": "Asia/Jakarta",
    "users": [{"id": "alice", "quota": 3}],
    "tasks": [],
})

service.tasks.submit({
    "id": "alice-sync",
    "user": "alice",
    "time": "12:00",
    "action": "sync",
    "params": {"target": "/data/x"},
})

now = datetime(2026, 10, 9, 12, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
results = service.scheduler.run_pending(now)
status = service.users.quota_status("alice", now.date())
assert status.used == 1
assert status.remaining == 2
```

Configure application logging when embedding the service:

```python
import logging

logging.basicConfig(level=logging.INFO)
```

The library uses named loggers and leaves logging configuration to its caller.
An HTTP adapter can call the same registration and submission methods; an HTTP
server, authentication, and a database are outside this simulation's scope.

## Design

```text
Dictionary / JSON configuration
           |
    SchedulingService
           |
    TaskManager ----- UserManager
           |               |
     Scheduler             | atomic daily quota reservation
           |               |
      TaskExecutor --------+
           |
     ActionRegistry
           |
     ActionStrategy
       /    |    \
     sync backup delete
```

| Module | Responsibility |
| --- | --- |
| `models.py` | Validated user/task models, quota snapshots, execution results |
| `users.py` | User registration and quota accounting keyed by user and date |
| `tasks.py` | Validated dictionary submission and ordered task storage |
| `actions.py` | Strategy interface, registry, simulated built-in actions |
| `executor.py` | Validation, atomic quota reservation, action execution, logging |
| `scheduler.py` | Timezone conversion, due-task selection, dispatch claims, polling |
| `service.py` | Dependency composition from configuration |
| `main.py` | CLI, logging setup, and deterministic demo |

The scheduler decides when tasks are due; the executor enforces quota and
invokes the strategy. Actions contain no scheduling or quota logic. The clock
can be injected into a scheduler for deterministic testing.

### Scheduling and quota policy

- One unit means one started action attempt. Failed actions consume quota;
  invalid submissions, invalid direct executions, and quota skips do not.
- Daily usage is keyed by the scheduler's local dispatch date. A new date has
  independent quota without needing a midnight reset job. Historical usage is
  retained during the process lifetime.
- A scheduler claims each due task once per local date, before executing it.
  Repeated or overlapping polls cannot duplicate a dispatch. Failures and quota
  skips are not retried that day.
- Multiple tasks in the same minute are supported. Synchronous execution uses
  registration order. Async results are returned in registration order, while
  completion order can differ; concurrent quota reservations are atomic.
- Matching uses the current local minute. Missed minutes are skipped, including
  times missed while the service is stopped or busy executing a long task.
  A newly submitted task can run during its scheduled minute.
- During a daylight-saving fallback, a repeated minute runs a task at most once
  on that date. A nonexistent minute during a spring-forward transition is
  skipped.
- Scheduler state, results, and quota history are in memory. Restarting clears
  them. Claims and quota locks are local to one service instance, not shared
  across processes. Production durability would require persistent storage and
  transactional coordination.

The executor returns `ExecutionResult` with `success`, `failed`, or
`quota_exceeded`, task context, execution date, duration, and any error.
`scheduler.results` exposes completed results. Logs include task ID, user,
action, status, target for built-ins, and exception tracebacks for failures.

## Add an action strategy

```python
import logging
from task_scheduling import ActionStrategy, SchedulingService

class ReportAction(ActionStrategy):
    def validate(self, params):
        message = params.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("params.message must be a non-empty string")

    def execute(self, task):
        logging.getLogger(__name__).info("Report: %s", task.params["message"])

service = SchedulingService.from_config({
    "users": [{"id": "alice", "quota": 3}],
    "tasks": [],
})
service.actions.register("report", ReportAction())
service.tasks.submit({
    "id": "alice-report", "user": "alice", "time": "12:00",
    "action": "report", "params": {"message": "Daily summary"},
})
```

No scheduler or executor changes are needed. Exceptions from an action are
logged and converted into a failed result; other tasks continue.

## Async execution

Use `SchedulingService.from_config(config, asynchronous=True,
max_concurrency=4)` and await `service.scheduler.run_pending(now)` or
`service.scheduler.run_forever()` inside one event loop.

The async executor uses a semaphore to bound active execution coroutines and
reserves quota before an action starts. Synchronous strategies are adapted
with `asyncio.to_thread`, keeping the event loop responsive. Strategies may
override `execute_async(self, task)` to await native async I/O directly while
retaining `execute` for synchronous operation. Shared synchronous strategy
instances must be safe for concurrent calls when used in async mode.

Cancellation propagates to the caller, retains any consumed quota, and retains
the daily dispatch claim. Queued claims are also retained if a tick is
cancelled. A thread-backed action may continue after cancellation; Python cannot
forcibly stop that thread. Cancelled executions have a cancellation log and no
completed result. Native async strategies should clean up in `finally` blocks.

## Tests

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The suite covers input validation, multiple users and tasks, custom strategies,
daily quota boundaries, atomic concurrent quota reservations, failure isolation,
execution logging, duplicate and overlapping polls, timezone conversion, DST
fallback, missed tasks, injected clocks, synchronous and asynchronous loops,
bounded async concurrency, cancellation, configuration, and CLI demonstrations.

## Refactoring decisions

The original script mixed global state, scheduling, quota checks, and output.
The refactor replaces those globals with composed objects, validates submitted
data before storing it, and uses `logging` instead of `print`. Two behavior bugs
are addressed explicitly: quota availability returns on each new local date,
and polling repeatedly in one minute no longer repeats the same task.

The first version intentionally uses in-memory storage and count-based quotas
to keep the interview project runnable and explainable. Persistent storage,
HTTP submission, retries, weighted resource costs, and distributed execution
can be added behind these component boundaries when required.
