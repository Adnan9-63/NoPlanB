"""
temporal_app/activities.py
───────────────────────────
All Temporal activities for NoPlanB.

KEY RULE (Temporal determinism constraint):
  Workflow code must be 100% deterministic.
  ALL non-deterministic operations (LLM calls, file I/O, HTTP requests,
  time.time()) MUST live in activities, never in workflow code.

Activities defined here:
  1. run_woop_interrogation  – conducts the Socratic WOOP session with Gemma
  2. notify_user_task        – writes the active task to a state file the UI polls
  3. run_triage              – handles STUCK/RESIST/TIMEOUT → Gemma response + reduced task
  4. record_task_completion  – writes the final status to the vault
  5. send_weekly_audit       – generates the audit report and optionally sends a Telegram nudge
  6. send_telegram_nudge     – sends a text-only nudge via Telegram (optional)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from temporalio import activity

from ai.triage import TriageResult, handle_signal
from ai.woop_session import WoopSession
from core.config import settings
from core.telemetry import friction_span, generate_weekly_audit
from core.vault_manager import (
    create_task_entry,
    list_recent_tasks,
    save_crucible_transcript,
    update_task_status,
)

# ── State file: the UI polls this to know what task to show ───────────────────
_STATE_FILE = Path("./state.json")


def _write_state(data: dict) -> None:
    existing = _read_state()
    if "workflow_id" in existing and "workflow_id" not in data:
        data["workflow_id"] = existing["workflow_id"]
    _STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_state() -> dict:
    if _STATE_FILE.exists():
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    return {}


# ─────────────────────────── Activity 1 ──────────────────────────────────────

@activity.defn
async def run_woop_interrogation(journal_dump: str) -> dict:
    """
    Conduct a multi-turn Socratic WOOP session with the local Gemma model.

    Input:  The user's raw journal dump (free-form text).
    Output: The structured execution payload dict:
            {atomic_task, duration_minutes, anticipated_obstacle, implementation_intention}

    Sentry 'cognitive.friction' span wraps the full session.
    """
    task_id_placeholder = f"woop_{int(time.time())}"

    with friction_span(task_id_placeholder):
        session = WoopSession()
        session.start()

        # Inject the journal dump as the first user message
        session.reply(journal_dump)

        # If the first reply didn't complete the session, we force it to conclude.
        fallback_turns = 0
        while not session.complete and fallback_turns < 2:
            auto_response = (
                "Assume my internal obstacle is procrastination. Pick ONE small task from my list. "
                "You MUST output exactly and only this JSON format, no markdown, no other text:\n"
                '{\n  "atomic_task": "the task",\n  "duration_minutes": 15,\n  "anticipated_obstacle": "procrastination",\n  "implementation_intention": "If procrastination arises, then I will start."\n}'
            )
            session.reply(auto_response)
            fallback_turns += 1

    payload = session.payload or {
        "atomic_task": journal_dump[:100],
        "duration_minutes": 15,
        "anticipated_obstacle": "Unknown",
        "implementation_intention": "If resistance arises, then I will open the file and read 1 line.",
    }

    # Save the interrogation transcript to vault/crucibles/
    task_id = await run_create_task_entry(payload, session.get_transcript())
    payload["task_id"] = task_id
    return payload


# ─────────────────────────── Activity 2 ──────────────────────────────────────

@activity.defn
async def run_create_task_entry(payload: dict, transcript: str = "") -> str:
    """Create the markdown task log in the vault and return the task_id."""
    task_id = create_task_entry(
        atomic_task=payload["atomic_task"],
        duration_min=payload["duration_minutes"],
        obstacle=payload["anticipated_obstacle"],
        intention=payload["implementation_intention"],
        woop_transcript=transcript,
    )
    if transcript:
        save_crucible_transcript(task_id, transcript)
    return task_id


# ─────────────────────────── Activity 3 ──────────────────────────────────────

@activity.defn
async def notify_user_task(payload: dict) -> None:
    """
    Write the active task to state.json so the FastAPI UI can display it.
    Also optionally send a Telegram message.

    This is deliberately minimal — the UI design principle is:
    'Show ONE task. Nothing else.'
    """
    state = {
        "task_id":        payload.get("task_id", ""),
        "atomic_task":    payload["atomic_task"],
        "duration_min":   payload["duration_minutes"],
        "obstacle":       payload["anticipated_obstacle"],
        "intention":      payload["implementation_intention"],
        "assigned_at":    datetime.now(timezone.utc).isoformat(),
        "status":         "pending",
    }
    _write_state(state)

    # Optional Telegram nudge (sends only the task text, never journal content)
    if settings.telegram_bot_token and settings.telegram_chat_id:
        await _send_telegram(
            f"⚡ NoPlanB\n\n*{payload['atomic_task']}*\n"
            f"Duration: {payload['duration_minutes']} min\n"
            f"If resistance: {payload['implementation_intention']}"
        )


# ─────────────────────────── Activity 4 ──────────────────────────────────────

@activity.defn
async def run_triage(
    signal: str,
    payload: dict,
    consecutive_successes: int,
    consecutive_resizes: int,
) -> dict:
    """
    Handle a user signal (STUCK/RESIST/TIMEOUT/DEFER) and return an updated payload.
    Calls into ai/triage.py which may call Gemma for nudges.
    """
    result: TriageResult = handle_signal(
        signal=signal,
        current_task=payload["atomic_task"],
        current_duration=payload["duration_minutes"],
        obstacle=payload["anticipated_obstacle"],
        consecutive_successes=consecutive_successes,
        consecutive_resizes=consecutive_resizes,
    )

    # Update state.json with the nudge message so UI can display it
    state = _read_state()
    if result.nudge_message:
        state["nudge"] = result.nudge_message
    if result.new_task:
        state["atomic_task"] = result.new_task
        state["duration_min"] = result.updated_duration
        payload["atomic_task"] = result.new_task
        payload["duration_minutes"] = result.updated_duration or payload["duration_minutes"]
    _write_state(state)

    return {
        **payload,
        "triage_action": result.action,
        "nudge": result.nudge_message,
    }


# ─────────────────────────── Activity 5 ──────────────────────────────────────

@activity.defn
async def record_task_completion(
    task_id: str,
    status: str,
    friction_sec: int,
    mood: int | None,
) -> None:
    """Write the final task status back to the vault markdown file."""
    update_task_status(
        task_id=task_id,
        status=status,
        mood=mood,
        friction_sec=friction_sec,
    )
    # Update state.json
    state = _read_state()
    state["status"] = status
    _write_state(state)


# ─────────────────────────── Activity 6 ──────────────────────────────────────

@activity.defn
async def send_weekly_audit(_: None = None) -> str:
    """
    Generate and save the weekly audit report.
    Returns the path of the generated report file.
    """
    tasks = list_recent_tasks(n=50)
    report_path = generate_weekly_audit(tasks)
    return str(report_path)


# ─────────────────────────── Helpers ─────────────────────────────────────────

async def _send_telegram(text: str) -> None:
    url = (
        f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        f"/sendMessage"
    )
    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "chat_id": settings.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })

@activity.defn
async def clear_user_task() -> None:
    "Clear the active task from state.json when completed."
    _write_state({"status": "done"})

