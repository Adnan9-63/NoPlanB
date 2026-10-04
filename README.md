# NoPlanB

**NoPlanB** is a blazing-fast, local-first AI execution engine designed to cure "productive procrastination." Instead of letting you endlessly organize goals in Obsidian, it forces you to face them using local AI (Gemma via Ollama), Cognitive Behavioral Therapy (CBT), and an intense execution mode.

Built for the **Hacktoberfest 2026 Weekend Challenge: Build for a Friend**.

## Why It Exists (The Problem)

Traditional tools like Obsidian or Yeolpumta (YPT) often fail because they enable *productive procrastination*. Writing a massive goal for 2026 feels good, but staring at a 50-item checklist triggers cognitive overwhelm and emotional avoidance. 

I built this for a friend who has pages of 2026 goals in a diary, but endlessly organizes their notes instead of actually writing code. 

NoPlanB fixes this by replacing the passive list with a **Socratic WOOP Interrogator** and an **Intense Execution Dashboard**.

## Architecture & Features

1. **The Anti-Procrastination Crucible**
   - Before you can start a task, you must define the exact emotion making you avoid it, the "Planning Trap" you are falling into, the atomic action, and a survival plan.
   - **Local AI (Gemma 3):** If you are too overwhelmed to figure it out, click the `✨ Ask AI` button. Gemma reads your journal and calls out your cognitive distortions directly.
2. **Infinite Obsidian-Style Vault**
   - Data isn't locked in a database. Everything is saved instantly to local Markdown files in the `vault/` directory.
   - Features a recursive, deeply nestable folder tree UI that perfectly mimics Obsidian.
   - Built-in Markdown Preview engine with support for `#tags` and bidirectional `[[linking]]`.
3. **Blazing Fast, Zero-Lag Execution Mode**
   - Stripped down from heavy architectures to run perfectly on older i3/i5 laptops.
   - Pure FastAPI backend and Vanilla JS frontend. No heavy frameworks.
   - Features an inline YPT-style visual countdown timer and daily focus stats tracker.

## Setup Instructions

### Prerequisites
1. **Python 3.11+**
2. **Ollama**: Install [Ollama](https://ollama.ai/) and pull the Gemma model:
   ```bash
   ollama pull gemma
   ```

### Installation
1. Clone the repository and install dependencies:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install fastapi uvicorn httpx pydantic
   ```

### Running the System
Run the lightweight FastAPI backend:
```bash
python -m api.server
```

Open `http://127.0.0.1:8000` in your browser.

## License
Apache 2.0
