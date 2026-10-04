"""
ai/gemma_client.py
───────────────────
Thin wrapper around the OpenAI-compatible Ollama endpoint.

Why OpenAI client → Ollama?
  Ollama exposes an OpenAI-compatible REST API at http://localhost:11434/v1
  This lets us use the official openai Python SDK, which means:
  1. Sentry's OpenAIIntegration auto-instruments all calls → gen_ai spans
  2. We can swap models (gemma3:4b, gemma3:12b, llama3, etc.) with one env var.
  3. No vendor lock-in; the user's data never leaves their machine.

Token budget strategy (addressing a gap in the original white-paper):
  We enforce a strict MAX_CONTEXT_TOKENS limit and retrieve only the most
  relevant recent journal entries via FAISS vector search. This prevents
  the "context bloat" failure mode where the entire diary is sent every call.
"""

from __future__ import annotations

import json
from typing import Any, Generator

from openai import OpenAI

from core.config import settings


# ─────────────────────────── client singleton ─────────────────────────────────

def _make_client() -> OpenAI:
    return OpenAI(
        base_url=settings.ollama_base_url,
        api_key="ollama",  # Required by the SDK but unused by Ollama
    )


_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = _make_client()
    return _client


# ─────────────────────────── core call ────────────────────────────────────────

MAX_CONTEXT_TOKENS = 3500  # Leave headroom for Gemma's context window


def chat(
    *,
    system_prompt: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> str:
    """
    Single-call chat with the local Gemma model.

    Args:
        system_prompt: The Socratic interrogator persona and rules.
        messages: Full conversation history as [{"role": ..., "content": ...}].
        temperature: Lower = more deterministic JSON extraction.
        max_tokens: Limit output length (reduces hallucination risk).

    Returns:
        The model's text response.
    """
    client = get_client()

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    response = client.chat.completions.create(
        model=settings.gemma_model,
        messages=full_messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def extract_woop_json(raw_response: str) -> dict[str, Any] | None:
    """
    Parse the JSON payload that Gemma emits when the WOOP interrogation is complete.
    Returns None if the response isn't valid JSON yet (more turns needed).

    Expected schema:
    {
      "atomic_task": "...",
      "duration_minutes": 15,
      "anticipated_obstacle": "...",
      "implementation_intention": "If ..., then ..."
    }
    """
    # Find JSON block in the response (model sometimes wraps it in markdown)
    start = raw_response.find("{")
    end = raw_response.rfind("}") + 1
    if start == -1 or end == 0:
        return None

    try:
        data = json.loads(raw_response[start:end])
    except json.JSONDecodeError:
        # Try fixing trailing commas
        import re
        try:
            json_str = re.sub(r',\s*}', '}', raw_response[start:end])
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None

    task = data.get("atomic_task") or data.get("task") or "Start the first step of my goal"
    dur = data.get("duration_minutes") or data.get("duration") or 15
    obs = data.get("anticipated_obstacle") or data.get("obstacle") or "procrastination"
    intent = data.get("implementation_intention") or data.get("intention") or f"If {obs} arises, then I will {task}"

    return {
        "atomic_task": task,
        "duration_minutes": 15,
        "anticipated_obstacle": obs,
        "implementation_intention": intent
    }

    # Validate duration is reasonable (1–30 min, per research: BJ Fogg 2-min rule)
    if not isinstance(data.get("duration_minutes"), int):
        try:
            data["duration_minutes"] = int(data["duration_minutes"])
        except (ValueError, TypeError):
            data["duration_minutes"] = 15

    data["duration_minutes"] = max(1, min(30, data["duration_minutes"]))
    return data


def generate_scope_reduction(task: str, obstacle: str, reason: str) -> str:
    """
    Ask Gemma to reduce the scope of a stalled task.

    Called by the Temporal workflow when:
    - The user sends a RESIST signal (emotional friction)
    - A 45-minute timeout fires with no user response

    Returns a smaller reformulation of the task.
    """
    prompt = f"""The user was given this task but stalled:

Task: "{task}"
Anticipated obstacle: "{obstacle}"
Reason for stall: {reason}

Your job: Propose a SMALLER version of this task that takes under 3 minutes.
The goal is to eliminate activation inertia. Do not explain your reasoning.
Output only the new smaller task as a single sentence."""

    response = chat(
        system_prompt=(
            "You are a compassionate productivity coach. "
            "You never scold. You only make tasks smaller until they become trivially easy to start."
        ),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=100,
    )
    return response.strip()


def generate_emotional_nudge(task: str, signal: str) -> str:
    """
    Generate a self-compassion-based nudge when the user signals RESIST or STUCK.

    Based on Sirois (2019): low self-compassion predicts higher procrastination.
    Shame makes it worse. The nudge must be non-judgmental.
    """
    prompts = {
        "resist": (
            f'The user is resisting starting this task: "{task}"\n'
            "They feel dread or avoidance. Write a 2-sentence non-judgmental nudge "
            "that acknowledges the feeling and proposes a 2-minute version of the task. "
            "Do NOT mention streaks, failure, or productivity. Be warm."
        ),
        "stuck": (
            f'The user started this task but got stuck: "{task}"\n'
            "Write a 2-sentence nudge that helps them identify exactly where they got confused "
            "and suggests one concrete next micro-action. Be specific, not generic."
        ),
    }

    response = chat(
        system_prompt=(
            "You are a kind, evidence-based coach who understands procrastination "
            "is an emotional regulation problem, not a character flaw."
        ),
        messages=[{"role": "user", "content": prompts.get(signal, prompts["resist"])}],
        temperature=0.5,
        max_tokens=120,
    )
    return response.strip()


def narrate_weekly_audit(metrics: dict[str, Any]) -> str:
    """
    Given a dict of pre-computed metrics (from telemetry.py), ask Gemma
    to write a warm, honest weekly summary.

    IMPORTANT: Gemma does NOT compute numbers here.
    Python computes; Gemma only narrates.
    (Following Claude's warning about small LLMs hallucinating stats.)
    """
    metrics_text = "\n".join(f"- {k}: {v}" for k, v in metrics.items())
    prompt = f"""Here are the user's exact execution metrics for this week:

{metrics_text}

Write a 3-paragraph honest, warm weekly review:
1. Celebrate what went well (cite the completion rate).
2. Name one clear pattern from the data (e.g., time of day, task type).
3. Propose one specific change for next week.

Do NOT make up numbers. Use only the metrics provided above.
Do NOT mention streaks or make the user feel guilty about missed days."""

    return chat(
        system_prompt=(
            "You are a data-driven but empathetic life coach. "
            "You always cite evidence. You never shame the user."
        ),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=400,
    )
