"""
ai/triage.py
─────────────
Adaptive task-sizing and emotional friction triage logic.

Addresses gaps identified in the research analysis:
  1. The original white-paper only reduced tasks; never grew them.
     After 5 consecutive completions, we increase scope (up to 30 min cap).
  2. After 3 consecutive reductions (scope_resize_count > 3), we force a
     full "Weekly Review" rather than endlessly shrinking the task.
  3. We distinguish TECHNICAL friction (STUCK) from EMOTIONAL friction (RESIST)
     and route to different Gemma prompts accordingly.

Grounded in:
  - BJ Fogg Behaviour Model: reduce Ability threshold to bootstrap the habit.
  - Lally et al. (2010): one missed day doesn't break habits → gentle reset.
  - Sirois (2019): shame increases procrastination → no guilt nudges.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai import gemma_client
from ai.prompts import (
    RESIST_NUDGE_PROMPT,
    SCOPE_REDUCTION_PROMPT,
    STUCK_NUDGE_PROMPT,
    TIMEOUT_NUDGE_PROMPT,
)


@dataclass
class TriageResult:
    """The output of a triage decision."""
    action: str          # "continue" | "reduce" | "nudge" | "weekly_review"
    new_task: str | None          # Reduced task text (if action == "reduce")
    nudge_message: str | None     # Gemma's nudge (if action == "nudge")
    updated_duration: int | None  # New duration in minutes


def handle_signal(
    *,
    signal: str,                    # "done" | "stuck" | "resist" | "defer" | "timeout"
    current_task: str,
    current_duration: int,
    obstacle: str,
    consecutive_successes: int,
    consecutive_resizes: int,
) -> TriageResult:
    """
    Central routing function for all user signals.

    Called by the Temporal activity layer. Returns a TriageResult
    that tells the workflow what to do next.
    """

    # ── DONE: task completed ───────────────────────────────────────────────────
    if signal == "done":
        new_duration = _maybe_grow_task(current_duration, consecutive_successes)
        return TriageResult(
            action="continue",
            new_task=None,
            nudge_message=None,
            updated_duration=new_duration,
        )

    # ── Guard: too many resizes → force weekly review ─────────────────────────
    if consecutive_resizes >= 3:
        return TriageResult(
            action="weekly_review",
            new_task=None,
            nudge_message=(
                "You've been shrinking this task several times. "
                "Let's take a step back and look at whether this goal "
                "still matters to you right now."
            ),
            updated_duration=None,
        )

    # ── RESIST: emotional avoidance ────────────────────────────────────────────
    if signal == "resist":
        nudge = gemma_client.chat(
            system_prompt="You are a compassionate, evidence-based coach. Never guilt-trip.",
            messages=[{
                "role": "user",
                "content": RESIST_NUDGE_PROMPT.format(task=current_task, obstacle=obstacle),
            }],
            temperature=0.5,
            max_tokens=120,
        )
        reduced = gemma_client.chat(
            system_prompt="Output only a 1-sentence task. No explanation.",
            messages=[{
                "role": "user",
                "content": SCOPE_REDUCTION_PROMPT.format(
                    reason="emotional resistance",
                    task=current_task,
                    obstacle=obstacle,
                ),
            }],
            temperature=0.3,
            max_tokens=60,
        )
        return TriageResult(
            action="reduce",
            new_task=reduced.strip(),
            nudge_message=nudge.strip(),
            updated_duration=2,
        )

    # ── STUCK: technical / cognitive block ─────────────────────────────────────
    if signal == "stuck":
        nudge = gemma_client.chat(
            system_prompt="You are a precise technical coach. Ask one diagnostic question.",
            messages=[{
                "role": "user",
                "content": STUCK_NUDGE_PROMPT.format(task=current_task, obstacle=obstacle),
            }],
            temperature=0.4,
            max_tokens=100,
        )
        return TriageResult(
            action="nudge",
            new_task=None,
            nudge_message=nudge.strip(),
            updated_duration=current_duration,
        )

    # ── TIMEOUT: no response in 45 minutes ────────────────────────────────────
    if signal == "timeout":
        nudge = gemma_client.chat(
            system_prompt="You are a warm, patient coach. Never mention the time elapsed.",
            messages=[{
                "role": "user",
                "content": TIMEOUT_NUDGE_PROMPT.format(task=current_task),
            }],
            temperature=0.5,
            max_tokens=80,
        )
        reduced = gemma_client.chat(
            system_prompt="Output only a 1-sentence task. No explanation.",
            messages=[{
                "role": "user",
                "content": SCOPE_REDUCTION_PROMPT.format(
                    reason="no response for 45 minutes",
                    task=current_task,
                    obstacle=obstacle,
                ),
            }],
            temperature=0.3,
            max_tokens=60,
        )
        return TriageResult(
            action="reduce",
            new_task=reduced.strip(),
            nudge_message=nudge.strip(),
            updated_duration=2,
        )

    # ── DEFER: user wants to reschedule ───────────────────────────────────────
    if signal == "defer":
        return TriageResult(
            action="continue",
            new_task=current_task,
            nudge_message="Task deferred. It will reappear at your next check-in.",
            updated_duration=current_duration,
        )

    # Unknown signal – default to continue
    return TriageResult(
        action="continue",
        new_task=None,
        nudge_message=None,
        updated_duration=current_duration,
    )


# ─────────────────────────── adaptive growth ─────────────────────────────────

def _maybe_grow_task(current_duration: int, consecutive_successes: int) -> int:
    """
    After 5 consecutive successful completions, increase task duration
    by 5 minutes (capped at 30 min). This moves the user from
    'activation inertia breaking' toward genuine deep-work sessions.

    Based on BJ Fogg's Tiny Habits progression principle.
    """
    if consecutive_successes > 0 and consecutive_successes % 5 == 0:
        return min(30, current_duration + 5)
    return current_duration
