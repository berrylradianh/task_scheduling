# Notes and future-work checklist

Report date: 2026-10-09.

The three requested goals are complete. The notes below describe the current
behavior; unchecked items are optional extensions that have not been built.

## Current behavior and limitations

- [x] **Document simulated actions.** `sync`, `backup`, and `delete` log their
  intended operation. They do not manipulate the target filesystem. This
  matches the simulated project scope.

- [x] **Document quota meaning.** One unit is one started action attempt.
  A failed action consumes a unit. Invalid tasks and quota skips consume none.
  Quota is not a measurement or limit of CPU time, memory, or disk usage.

- [x] **Document daily accounting.** Quota usage is keyed by the scheduler's
  local dispatch date. Queued async work continues to use that dispatch date
  even if execution starts after midnight. There is no midnight reset job.

- [x] **Document same-time execution.** Multiple tasks can be due in the same
  minute. Sync execution is sequential; async execution overlaps work up to
  the coroutine concurrency limit. Async result order follows registration
  order, while completion order can differ.

- [x] **Document in-memory state.** Registered users/tasks, quota history,
  dispatch claims, and completed results are stored in memory. Restarting
  clears them; the CLI reloads configured users/tasks. Restarting during a due
  minute can therefore execute a previously completed task again.

- [x] **Document instance boundaries.** Locks and dispatch claims protect one
  service instance. Separate instances/processes do not share quotas or claims.

- [x] **Document polling semantics.** Only tasks matching the current local
  minute are selected. Missed minutes are skipped, including those missed while
  stopped or busy executing long tasks. Failed and quota-skipped dispatches are
  not retried that day. DST fallback minutes are deduplicated per date;
  nonexistent spring-forward minutes are skipped.

- [x] **Document submission interface.** Configuration and task submission are
  available through JSON loading and Python methods. No HTTP API, authentication,
  task-edit/delete endpoint, or user administration UI is included.

- [x] **Document async strategy adaptation.** Built-ins execute through
  `asyncio.to_thread`; a custom strategy may override `execute_async()` for
  native async I/O. Shared synchronous strategies must support concurrent
  calls. Use each async service within one event loop.

- [x] **Document cancellation.** Cancellation retains consumed quota and daily
  dispatch claims, including queued claims. Cancelled work has a cancellation
  log rather than a completed `ExecutionResult`. A running thread-backed action
  can outlive coroutine cancellation; the semaphore bounds active coroutines,
  not such surviving threads.

- [x] **Document parameter mutability.** Submitted parameters are deep-copied
  and the top-level mapping is read-only. Nested containers are not recursively
  frozen. Custom strategies should treat task parameters as read-only so their
  changes do not affect future daily executions.

- [x] **Document retained history.** Usage, claim, and result history accumulate
  for the lifetime of the process. No history pruning or retention limit is
  implemented.

- [x] **Document environment requirements.** Python 3.11+ and IANA timezone data
  are required. The verified interpreter was Python 3.14.8. Async verification
  needed a run outside the restricted execution sandbox; see the
  [verification report](verification-report.md).

## Optional future work — not required for the three goals

- [ ] Add persistent task, quota, claim, and execution-result storage if restart
  durability is required.
- [ ] Add transactional shared quota reservations and distributed dispatch
  coordination before running multiple service instances.
- [ ] Add HTTP submission and authentication if clients must submit tasks over
  a network instead of using the Python interface.
- [ ] Implement real filesystem action strategies if simulation is no longer
  sufficient, with explicit target rules and failure handling.
- [ ] Add weighted costs or measured CPU/memory/storage limits if resource quota
  must represent more than an execution count.
- [ ] Add an explicit retry and missed-task recovery policy if skipped/failed
  runs need recovery, including how retries consume quota.
- [ ] Add task update/removal and user quota administration if required.
- [ ] Add bounded history retention for a long-running service.
- [ ] Add recursively immutable parameters if strategies must be prevented from
  changing nested configuration.
- [ ] Add stronger cancellation/result tracking if cancelled and queued work
  must have durable outcomes or thread completion must stay bounded.
- [ ] Add CI across the declared Python versions and performance checks if
  broader compatibility or production capacity must be demonstrated.

## Required work still unfinished

None. The unchecked items above are future extensions, not missing parts of
the requested class-based refactor, required behavior, or optional async/OOP
extensions.
