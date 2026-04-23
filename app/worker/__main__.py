"""CLI entrypoint.

Examples:
    python -m app.worker poll_source
    python -m app.worker run_due_tasks
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.config import get_settings
from app.worker.executor import run_due_tasks
from app.worker.poller import poll_source_once


async def _run(command: str) -> int:
    if command == "poll_source":
        created = await poll_source_once()
        print(f"poll_source: new_posts={created}")
        return 0
    if command == "run_due_tasks":
        claimed = await run_due_tasks()
        print(f"run_due_tasks: claimed={claimed}")
        return 0
    if command == "tick":
        created = await poll_source_once()
        claimed = await run_due_tasks()
        print(f"tick: new_posts={created} claimed={claimed}")
        return 0
    raise ValueError(f"unknown command: {command}")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["poll_source", "run_due_tasks", "tick"])
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.command)))


if __name__ == "__main__":
    main()
