"""
ai/woop_session.py
───────────────────
Manages a multi-turn WOOP interrogation session.

The session accumulates conversation history and calls Gemma iteratively
until the model emits a valid JSON payload (max MAX_TURNS turns).

This is intentionally NOT a Temporal activity because it is a short,
synchronous interaction (< 5 minutes). Temporal activities are reserved
for long-running human-in-the-loop steps spanning hours or days.
"""

from __future__ import annotations

from ai.gemma_client import chat, extract_woop_json
from ai.prompts import INTERROGATOR_OPENING, WOOP_SYSTEM_PROMPT

MAX_TURNS = 10  # Abort after 10 turns to prevent infinite loops


class WoopSession:
    """
    Stateful multi-turn WOOP interrogation session.

    Usage:
        session = WoopSession()
        opening = session.start()          # Returns the interrogator's opening line
        response = session.reply(user_msg) # Returns the model's next question or the JSON payload
        if session.complete:
            payload = session.payload      # The final WOOP JSON
    """

    def __init__(self) -> None:
        self.history: list[dict[str, str]] = []
        self.complete: bool = False
        self.payload: dict | None = None
        self.turn_count: int = 0
        self.full_transcript: str = ""

    def start(self) -> str:
        """Return the interrogator's opening message."""
        self.history.append({"role": "assistant", "content": INTERROGATOR_OPENING})
        self.full_transcript += f"**Interrogator:** {INTERROGATOR_OPENING}\n\n"
        return INTERROGATOR_OPENING

    def reply(self, user_message: str) -> str:
        """
        Submit a user message and get the next model response.
        If the model returns valid JSON, sets self.complete = True.
        """
        if self.complete:
            return "Session already complete."

        self.turn_count += 1
        self.history.append({"role": "user", "content": user_message})
        self.full_transcript += f"**You:** {user_message}\n\n"

        if self.turn_count > MAX_TURNS:
            return (
                "We've been going back and forth for a while. "
                "Let's try a different approach — what's the absolute smallest "
                "physical action you could take in the next 60 seconds?"
            )

        model_response = chat(
            system_prompt=WOOP_SYSTEM_PROMPT,
            messages=self.history,
            temperature=0.3,
            max_tokens=512,
        )

        self.history.append({"role": "assistant", "content": model_response})
        self.full_transcript += f"**Interrogator:** {model_response}\n\n"

        # Check if the model produced a valid WOOP payload
        woop = extract_woop_json(model_response)
        if woop is not None:
            self.complete = True
            self.payload = woop

        return model_response

    def get_transcript(self) -> str:
        """Return the full conversation as a Markdown string for vault storage."""
        return self.full_transcript
