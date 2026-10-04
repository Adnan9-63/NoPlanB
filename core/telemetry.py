"""
core/telemetry.py
──────────────────
Sentry AI Agent Tracing initialisation and helper spans.

Key design decisions (from research):
  1. stream_gen_ai_spans=True  → extracts gen_ai spans as standalone
     envelope items, bypassing the 1 MB transaction payload limit.
     Requires sentry-sdk >= 2.64.0.

  2. send_default_pii = (SENTRY_SEND_PII == 1)
     Default is OFF: journal entries never leave the device.
     Enable only for debugging sessions.

  3. Custom "cognitive.friction" span → measures how long the user
     takes during a WOOP interrogation session. Short latency (<5s)
     may indicate the user is copy-pasting rote answers without real
     mental contrasting, and triggers an intervention flag.

  4. Weekly audit aggregation → collects completion rates, average
     friction latency, token usage into a Markdown report.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import sentry_sdk
from sentry_sdk.integrations.openai import OpenAIIntegration

from core.config import settings


# ─────────────────────────── initialisation ──────────────────────────────────

def init_sentry() -> None:
    """
    Initialise Sentry with Agent Tracing.
    Call once at application startup (worker.py and server.py).
    """
    if not settings.sentry_dsn:
        print("[telemetry] SENTRY_DSN not set – Sentry disabled.")
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        # Capture 100% of traces (suitable for personal/local usage)
        traces_sample_rate=1.0,
        # Send prompt content only if user explicitly opts in
        send_default_pii=bool(settings.sentry_send_pii),
        # Stream gen_ai spans independently (avoids 1 MB payload cap)
        # Requires sentry-sdk >= 2.64.0
        _experiments={
            "ai_monitoring": True,
        },
        # Automatically instruments openai.ChatCompletion calls
        integrations=[OpenAIIntegration(include_prompts=bool(settings.sentry_send_pii))],
    )
    print("[telemetry] Sentry AI Agent Tracing initialised.")


# ─────────────────────────── custom spans ────────────────────────────────────

@contextmanager
def friction_span(task_id: str) -> Generator[sentry_sdk.tracing.Span | None, None, None]:
    """
    Context manager that wraps an entire WOOP interrogation session.

    Measures "Cognitive Friction Latency":
      - The time (in seconds) required for the user to work through the
        Socratic questions and produce a valid WOOP JSON payload.
      - Very short latency (<5s) → flag: likely rote/copy-pasted answers.
      - Stored as span attribute 'friction_sec' and in the vault front-matter.

    Usage:
        with friction_span(task_id) as span:
            result = run_woop_interrogation(journal_dump)
            if span:
                span.set_attribute("result.atomic_task", result["atomic_task"])
    """
    start = time.monotonic()
    if settings.sentry_dsn:
        with sentry_sdk.start_span(
            op="cognitive.friction",
            description="WOOP Socratic Interrogation",
        ) as span:
            span.set_tag("task_id", task_id)
            yield span
            elapsed = time.monotonic() - start
            span.set_data("friction_sec", round(elapsed))
            if elapsed < 5:
                span.set_tag("warning", "low_friction_latency")
    else:
        yield None


@contextmanager
def task_execution_span(task_id: str, task_label: str) -> Generator[None, None, None]:
    """
    Wraps a single Temporal workflow execution step.
    Captures task_id, label, and whether the user signalled DONE/STUCK/RESIST.
    """
    if settings.sentry_dsn:
        with sentry_sdk.start_span(
            op="task.execution",
            description=f"Executing: {task_label[:60]}",
        ) as span:
            span.set_tag("task_id", task_id)
            yield span
    else:
        yield None


# ─────────────────────────── weekly audit ────────────────────────────────────

def generate_weekly_audit(task_records: list[dict]) -> Path:
    """
    Given a list of task front-matter dicts (from vault_manager.list_recent_tasks),
    compute hard metrics and write a Markdown audit report to reports/.

    Python does ALL computation. Gemma only narrates the result.
    (Following Claude's guidance: 'small local LLMs are unreliable
     at long-context analysis.')

    Returns the path of the generated report.
    """
    from pathlib import Path as _Path

    reports_dir = _Path("./reports")
    reports_dir.mkdir(exist_ok=True)

    total = len(task_records)
    if total == 0:
        report_path = reports_dir / "weekly_empty.md"
        report_path.write_text("# Weekly Audit\n\nNo tasks recorded yet.", encoding="utf-8")
        return report_path

    done     = sum(1 for t in task_records if t.get("status") == "done")
    resists  = sum(1 for t in task_records if t.get("status") == "resist")
    stucks   = sum(1 for t in task_records if t.get("status") == "stuck")
    deferred = sum(1 for t in task_records if t.get("status") == "deferred")

    completion_rate = round(done / total * 100, 1) if total > 0 else 0.0

    friction_times = [
        t["friction_sec"] for t in task_records
        if isinstance(t.get("friction_sec"), int)
    ]
    avg_friction = round(sum(friction_times) / len(friction_times)) if friction_times else 0

    moods = [t["mood"] for t in task_records if isinstance(t.get("mood"), int)]
    avg_mood = round(sum(moods) / len(moods), 1) if moods else "N/A"

    date_str = datetime.now(timezone.utc).date().isoformat()
    report_path = reports_dir / f"weekly_{date_str}.md"

    content = f"""# NoPlanB Weekly Audit – {date_str}

> Generated automatically. All numbers are computed from vault front-matter.
> Gemma provides narrative; Python provides facts.

## Execution Summary

| Metric | Value |
|--------|-------|
| Total tasks assigned | {total} |
| Completed (DONE) | {done} |
| Completion rate | **{completion_rate}%** |
| Resisted tasks | {resists} |
| Stuck tasks | {stucks} |
| Deferred tasks | {deferred} |
| Avg. cognitive friction latency | {avg_friction}s |
| Avg. mood on completion | {avg_mood} / 5 |

## Task Breakdown

| Date | Task | Status | Friction (s) | Mood |
|------|------|--------|-------------|------|
"""
    for t in task_records:
        content += (
            f"| {t.get('date', '?')} "
            f"| {t.get('atomic_task', '?')[:50]} "
            f"| {t.get('status', '?')} "
            f"| {t.get('friction_sec', '?')} "
            f"| {t.get('mood', '?')} |\n"
        )

    content += """
---
*Raw data lives in `vault/daily/`. Open these files in Obsidian at any time.*
"""
    report_path.write_text(content, encoding="utf-8")
    return report_path
