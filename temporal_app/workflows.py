"""
temporal_app/workflows.py
──────────────────────────
NoPlanB Execution Engine – the durable state machine.

Architecture decisions (grounded in research):

1. SINGLE-TASK DRIP (Anti-Overwhelm)
   The user only ever sees one task. The next task is hidden until DONE
   is received. This is the core "Anti-Overwhelm Lens" from the Gemini analysis.

2. DURABLE WAIT (workflow.wait_condition)
   The workflow pauses at each task step, surviving:
   - Laptop sleep/restart
   - Worker process crashes
   - Days-long pauses (for multi-day goals)
   No CPU consumed during the wait.

3. HUMAN SIGNALS (@workflow.signal)
   User actions (DONE, STUCK, RESIST, DEFER) are injected as Temporal Signals.
   The workflow catches them and routes to the triage engine.

4. TIMEOUT INTERVENTION (45 minutes)
   If no signal arrives in 45 minutes, the workflow executes a scope-reduction
   intervention via Gemma, then re-presents a smaller task.

5. ADAPTIVE COUNTERS
   - consecutive_successes: triggers task-size growth after 5 completions
   - consecutive_resizes: triggers forced weekly review after 3 reductions

6. INFINITE LIFECYCLE (continue_as_new)
   Temporal caps event histories at ~50 k events. For a year-long daily habit
   this would be hit quickly. We call continue_as_new when suggested,
   carrying forward only the essential state dict.

DETERMINISM RULE:
   No I/O, no time.time(), no random, no external calls in workflow code.
   All of that lives in activities.py.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temporal_app.activities import (
        notify_user_task,
        record_task_completion,
        run_triage,
        run_woop_interrogation,
        send_weekly_audit,
    )


# ─────────────────────────── Retry policy ────────────────────────────────────
# Gemma can occasionally return invalid JSON or stall.
# This retry policy retries activities up to 3 times with exponential backoff.
_ACTIVITY_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
)

_SHORT_TIMEOUT  = timedelta(seconds=15)
_MEDIUM_TIMEOUT = timedelta(minutes=5)
_LONG_TIMEOUT   = timedelta(minutes=1)


# ─────────────────────────── Main Workflow ────────────────────────────────────

@workflow.defn
class NoPlanBExecutionEngine:
    """
    Durable execution engine for a single WOOP task lifecycle.

    Accepts a journal_dump string, runs the Socratic interrogation,
    then drip-feeds atomic micro-tasks one at a time until completion.

    State is durable: survives laptop restarts, crashes, and multi-day pauses.
    """

    def __init__(self) -> None:
        # Signal state
        self._signal: str | None = None
        self._mood: int | None = None
        self._friction_start: float | None = None  # NOT real clock – use for counting turns

        # Counters (carried via continue_as_new)
        self.consecutive_successes: int = 0
        self.consecutive_resizes: int = 0
        self.total_completed: int = 0

    # ── Signals (called from the FastAPI server when user clicks a button) ────

    @workflow.signal
    def user_action(self, signal: str, mood: int = 0) -> None:
        """
        Receive user interaction.
        signal: "done" | "stuck" | "resist" | "defer"
        mood:   1-5 self-rating (0 = not provided)
        """
        self._signal = signal.lower()
        self._mood = mood if mood > 0 else None

    # ── Queries (read-only, safe to call any time) ────────────────────────────

    @workflow.query
    def get_state(self) -> dict:
        return {
            "consecutive_successes": self.consecutive_successes,
            "consecutive_resizes":   self.consecutive_resizes,
            "total_completed":       self.total_completed,
            "last_signal":           self._signal,
        }

    # ── Main run ──────────────────────────────────────────────────────────────

    @workflow.run
    async def run(self, journal_dump: str, carry_state: dict | None = None) -> dict:
        """
        Main workflow execution.

        Args:
            journal_dump: The user's raw journal/goal text.
            carry_state:  State dict from a previous continue_as_new call.
        """
        # Restore counters from previous run (if continue_as_new was used)
        if carry_state:
            self.consecutive_successes = carry_state.get("consecutive_successes", 0)
            self.consecutive_resizes   = carry_state.get("consecutive_resizes", 0)
            self.total_completed       = carry_state.get("total_completed", 0)

        # ── Step 1: Run WOOP interrogation to get the execution payload ───────
        payload: dict = await workflow.execute_activity(
            run_woop_interrogation,
            journal_dump,
            start_to_close_timeout=_MEDIUM_TIMEOUT,
            retry_policy=_ACTIVITY_RETRY,
        )

        task_id: str = payload.get("task_id", "unknown")

        # ── Step 2: Notify the UI with the first micro-task ───────────────────
        await workflow.execute_activity(
            notify_user_task,
            payload,
            start_to_close_timeout=_SHORT_TIMEOUT,
        )

        # ── Step 3: Durable wait loop ─────────────────────────────────────────
        task_done = False
        while not task_done:
            # self._signal = None  # Reset signal before each wait (removed to fix race condition)

            try:
                # Wait up to 45 minutes for a user signal
                await workflow.wait_condition(
                    lambda: self._signal is not None,
                    timeout=timedelta(minutes=45),
                )
            except asyncio.TimeoutError:
                # ── No signal in 45 min → timeout intervention ────────────────
                triage_result = await workflow.execute_activity(
                    run_triage,
                    args=["timeout", payload, self.consecutive_successes, self.consecutive_resizes],
                    start_to_close_timeout=_MEDIUM_TIMEOUT,
                    retry_policy=_ACTIVITY_RETRY,
                )
                payload = {**payload, **triage_result}
                self.consecutive_resizes += 1

                if triage_result.get("triage_action") == "weekly_review":
                    return await self._weekly_review(task_id, payload)

                # Re-notify with the reduced task
                await workflow.execute_activity(
                    notify_user_task,
                    payload,
                    start_to_close_timeout=_SHORT_TIMEOUT,
                )
                continue

            # ── Signal received ────────────────────────────────────────────────
            signal = self._signal

            if signal == "done":
                # ── Task completed ────────────────────────────────────────────
                task_done = True
                self.total_completed += 1
                self.consecutive_successes += 1
                self.consecutive_resizes = 0  # Reset resize streak

                await workflow.execute_activity(
                    record_task_completion,
                    args=[task_id, "done", 0, self._mood],
                    start_to_close_timeout=_SHORT_TIMEOUT,
                )
                await workflow.execute_activity(
                    "clear_user_task",
                    start_to_close_timeout=_SHORT_TIMEOUT,
                )

            elif signal in ("stuck", "resist"):
                # ── Emotional or technical friction ────────────────────────────
                triage_result = await workflow.execute_activity(
                    run_triage,
                    args=[signal, payload, self.consecutive_successes, self.consecutive_resizes],
                    start_to_close_timeout=_MEDIUM_TIMEOUT,
                    retry_policy=_ACTIVITY_RETRY,
                )
                payload = {**payload, **triage_result}
                self.consecutive_resizes += 1
                self.consecutive_successes = 0  # Reset success streak on friction

                if triage_result.get("triage_action") == "weekly_review":
                    return await self._weekly_review(task_id, payload)

                # Re-notify with nudge/reduced task
                await workflow.execute_activity(
                    notify_user_task,
                    payload,
                    start_to_close_timeout=_SHORT_TIMEOUT,
                )

            elif signal == "defer":
                # ── User defers to later ───────────────────────────────────────
                await workflow.execute_activity(
                    record_task_completion,
                    args=[task_id, "deferred", 0, None],
                    start_to_close_timeout=_SHORT_TIMEOUT,
                )
                task_done = True  # End this workflow run; new one starts when they return

            self._signal = None  # Reset signal at the end of the loop

        # ── Step 4: Infinite lifecycle management ─────────────────────────────
        # Prevent event history bloat over months of daily use.
        if workflow.info().is_continue_as_new_suggested():
            workflow.continue_as_new(
                args=[
                    journal_dump,
                    {
                        "consecutive_successes": self.consecutive_successes,
                        "consecutive_resizes":   self.consecutive_resizes,
                        "total_completed":       self.total_completed,
                    },
                ]
            )

        return {
            "status":                "completed",
            "task_id":               task_id,
            "total_completed":       self.total_completed,
            "consecutive_successes": self.consecutive_successes,
        }

    # ─────────────────────────── helpers ─────────────────────────────────────

    async def _weekly_review(self, task_id: str, payload: dict) -> dict:
        """
        Trigger the weekly review when too many resize cycles have occurred.
        Records the task as deferred and generates the audit report.
        """
        await workflow.execute_activity(
            record_task_completion,
            args=[task_id, "deferred", 0, None],
            start_to_close_timeout=timedelta(seconds=15),
        )
        report_path = await workflow.execute_activity(
            send_weekly_audit,
            None,
            start_to_close_timeout=timedelta(minutes=2),
        )
        return {
            "status":      "weekly_review_triggered",
            "report_path": report_path,
            "task_id":     task_id,
        }
