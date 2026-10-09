"""Command-line entry point for demonstrations and the polling service."""

import argparse
import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path

from .models import ExecutionResult, ExecutionStatus
from .scheduler import AsyncScheduler, Scheduler
from .service import SchedulingService

logger = logging.getLogger(__name__)


def _demo_config() -> dict:
    return {
        "timezone": "Asia/Jakarta",
        "users": [{"id": "alice", "quota": 3}, {"id": "bob", "quota": 5}],
        "tasks": [
            {"id": "alice-sync", "user": "alice", "time": "12:00", "action": "sync",
             "params": {"target": "/data/x"}},
            {"id": "bob-backup", "user": "bob", "time": "12:00", "action": "backup",
             "params": {"target": "/srv/y"}},
            {"id": "alice-delete", "user": "alice", "time": "12:00", "action": "delete",
             "params": {"target": "/tmp/z"}},
            {"id": "alice-backup", "user": "alice", "time": "12:00", "action": "backup",
             "params": {"target": "/data/archive"}},
            {"id": "alice-over-quota", "user": "alice", "time": "12:00", "action": "sync",
             "params": {"target": "/data/extra"}},
        ],
    }


def _summarize(results: list[ExecutionResult]) -> None:
    counts = {status: 0 for status in ExecutionStatus}
    for result in results:
        counts[result.status] += 1
    logger.info(
        "Tick completed success=%d failed=%d quota_exceeded=%d",
        counts[ExecutionStatus.SUCCESS], counts[ExecutionStatus.FAILED],
        counts[ExecutionStatus.QUOTA_EXCEEDED],
    )


def _run_demo(service: SchedulingService) -> int:
    scheduler = service.scheduler
    now = datetime.now(scheduler.timezone).replace(hour=12, minute=0, second=0, microsecond=0)
    instants = [now, now + timedelta(seconds=20), now + timedelta(days=1)]

    async def run_async() -> list[ExecutionResult]:
        assert isinstance(scheduler, AsyncScheduler)
        results = []
        for instant in instants:
            logger.info("Demo tick at=%s", instant.isoformat())
            tick_results = await scheduler.run_pending(instant)
            _summarize(tick_results)
            results.extend(tick_results)
        return results

    if isinstance(scheduler, AsyncScheduler):
        results = asyncio.run(run_async())
    else:
        results = []
        for instant in instants:
            logger.info("Demo tick at=%s", instant.isoformat())
            tick_results = scheduler.run_pending(instant)
            _summarize(tick_results)
            results.extend(tick_results)
    return int(any(result.status == ExecutionStatus.FAILED for result in results))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulated daily task scheduling service")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path, help="JSON configuration file")
    source.add_argument("--demo", action="store_true", help="demonstrate quotas and daily scheduling")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    parser.add_argument("--at", help="timezone-aware ISO datetime; requires --once")
    parser.add_argument("--async", dest="asynchronous", action="store_true", help="use async execution")
    parser.add_argument("--max-concurrency", type=int, default=4, help="async concurrency limit (default: 4)")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="poll interval in seconds (default: 1)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    args = parser.parse_args(argv)
    if args.at and (not args.once or args.demo):
        parser.error("--at requires --config and --once")
    if args.max_concurrency < 1:
        parser.error("--max-concurrency must be positive")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = _demo_config() if args.demo else json.loads(args.config.read_text(encoding="utf-8"))
        service = SchedulingService.from_config(
            config, asynchronous=args.asynchronous, max_concurrency=args.max_concurrency,
        )
        if args.demo:
            return _run_demo(service)

        now = datetime.fromisoformat(args.at) if args.at else None
        scheduler = service.scheduler
        if isinstance(scheduler, AsyncScheduler):
            if args.once:
                results = asyncio.run(scheduler.run_pending(now))
            else:
                asyncio.run(scheduler.run_forever(poll_interval=args.poll_interval))
                return 0
        else:
            assert isinstance(scheduler, Scheduler)
            if args.once:
                results = scheduler.run_pending(now)
            else:
                scheduler.run_forever(poll_interval=args.poll_interval)
                return 0
        _summarize(results)
        return int(any(result.status == ExecutionStatus.FAILED for result in results))
    except (OSError, ValueError) as exc:
        logger.error("Unable to run service: %s", exc)
        return 2
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
        return 0
