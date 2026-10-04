"""
temporal_app/worker.py
───────────────────────
Temporal Worker entry point.

Registers all workflows and activities, connects to the Temporal server,
and starts polling the task queue.

Run with:
    python -m temporal_app.worker
or via the project script:
    noplanb-worker
"""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from core.config import settings
from core.telemetry import init_sentry
from temporal_app.activities import (
    notify_user_task,
    record_task_completion,
    run_create_task_entry,
    run_triage,
    run_woop_interrogation,
    send_weekly_audit,
    clear_user_task,
)
from temporal_app.workflows import NoPlanBExecutionEngine


async def _run_worker() -> None:
    # Initialise Sentry (no-op if DSN not set)
    init_sentry()

    # Connect to local Temporal server
    client = await Client.connect(
        settings.temporal_host,
        namespace=settings.temporal_namespace,
    )

    # Start worker
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[NoPlanBExecutionEngine],
        activities=[
            run_woop_interrogation,
            run_create_task_entry,
            notify_user_task,
            run_triage,
            record_task_completion,
            send_weekly_audit,
            clear_user_task,
        ],
    )

    print(f"[worker] NoPlanB worker started. Queue: {settings.temporal_task_queue}")
    print("[worker] Press Ctrl+C to stop.")
    await worker.run()


def main() -> None:
    asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
