"""
api/server.py
Lightweight FastAPI server for NoPlanB (Obsidian + Todo + YPT hybrid)
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from core.config import settings
from ai.gemma_client import chat

app = FastAPI(title="NoPlanB - The Crucible", version="0.2.0", docs_url=None, redoc_url=None)

VAULT_DIR = Path("./vault")
JOURNALS_DIR = VAULT_DIR / "journals"
TASKS_DIR = VAULT_DIR / "tasks"

for d in [VAULT_DIR, JOURNALS_DIR, TASKS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

def get_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

class JournalData(BaseModel):
    content: str

class TaskData(BaseModel):
    atomic_task: str
    duration_min: int
    obstacle: str
    intention: str
    status: str
    friction_sec: int = 0
    emotion: str = "None"
    distortion: str = "None"

class AskAIRequest(BaseModel):
    journal_dump: str
    question: str

@app.get("/", response_class=HTMLResponse)
async def serve_ui() -> HTMLResponse:
    html_path = Path("./web/index.html")
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<p>UI not found. Run from project root.</p>", status_code=404)

# --- Obsidian-style Vault Routes ---

@app.get("/api/vault/files")
async def get_vault_files() -> JSONResponse:
    files = []
    # Recursively find all markdown files in the vault
    for f in VAULT_DIR.rglob("*.md"):
        rel_path = f.relative_to(VAULT_DIR).as_posix()
        files.append({"name": f.name, "path": rel_path, "type": "task" if "tasks" in rel_path else "journal"})
    
    # Sort files by path alphabetically
    files.sort(key=lambda x: x["path"])
    return JSONResponse({"files": files})

@app.get("/api/stats")
async def get_stats() -> JSONResponse:
    tasks_count = 0
    total_minutes = 0
    
    # Parse all task files to count lines starting with '###'
    for f in TASKS_DIR.rglob("*.md"):
        content = f.read_text(encoding="utf-8")
        for line in content.split("\n"):
            if line.startswith("### "):
                tasks_count += 1
            elif line.startswith("- **Duration:**"):
                # Extract the minutes from "- **Duration:** 25 min"
                try:
                    mins = int(line.split("**Duration:** ")[1].split(" min")[0].strip())
                    total_minutes += mins
                except:
                    pass
                    
    return JSONResponse({"tasks": tasks_count, "minutes": total_minutes})

@app.get("/api/vault/file")
async def get_vault_file(path: str) -> JSONResponse:
    file_path = VAULT_DIR / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    content = file_path.read_text(encoding="utf-8")
    return JSONResponse({"content": content})

@app.post("/api/vault/file")
async def save_vault_file(path: str, data: JournalData) -> JSONResponse:
    file_path = VAULT_DIR / path
    # Ensure all parent directories exist (allows deep nesting)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(data.content, encoding="utf-8")
    return JSONResponse({"status": "saved"})

# --- Old endpoints for backwards compatibility ---
@app.get("/api/journal/today")
async def get_today_journal() -> JSONResponse:
    today = get_today_str()
    file_path = JOURNALS_DIR / f"{today}.md"
    content = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
    return JSONResponse({"date": today, "path": f"journals/{today}.md", "content": content})

@app.post("/api/journal/today")
async def save_today_journal(data: JournalData) -> JSONResponse:
    today = get_today_str()
    file_path = JOURNALS_DIR / f"{today}.md"
    file_path.write_text(data.content, encoding="utf-8")
    return JSONResponse({"status": "saved"})

@app.post("/api/task/save")
async def save_task(data: TaskData) -> JSONResponse:
    today = get_today_str()
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # Save to a running tasks log
    tasks_file = TASKS_DIR / f"{today}_tasks.md"
    
    md_entry = f"### [{timestamp}] {data.status.upper()}: {data.atomic_task}\n"
    md_entry += f"- **Duration:** {data.duration_min} min (Friction: {data.friction_sec}s)\n"
    md_entry += f"- **Emotion:** {data.emotion}\n"
    md_entry += f"- **Cognitive Distortion:** {data.distortion}\n"
    md_entry += f"- **Obstacle:** {data.obstacle}\n"
    md_entry += f"- **Intention:** If {data.obstacle}, then {data.intention}\n\n"
    
    with open(tasks_file, "a", encoding="utf-8") as f:
        f.write(md_entry)
        
    return JSONResponse({"status": "saved"})

# --- Optional Local AI Route ---

@app.post("/api/ai/ask")
async def ask_ai(req: AskAIRequest) -> JSONResponse:
    if not req.journal_dump.strip():
        return JSONResponse({"answer": "Write some thoughts in your journal first!"})
        
    system_prompt = "You are a concise CBT and productivity assistant. Answer the user's specific question based on their journal entry. Keep your answer to 1 or 2 short sentences. Do not use formatting or markdown."
    
    prompt = [
        {"role": "user", "content": f"My Journal:\n{req.journal_dump}\n\nQuestion: {req.question}"}
    ]
    
    loop = asyncio.get_event_loop()
    try:
        response_text = await loop.run_in_executor(
            None, 
            lambda: chat(system_prompt=system_prompt, messages=prompt, temperature=0.3, max_tokens=60)
        )
        return JSONResponse({"answer": response_text.strip()})
    except Exception as e:
        return JSONResponse({"answer": f"AI Error: {str(e)}"})

def main() -> None:
    uvicorn.run("api.server:app", host=settings.api_host, port=settings.api_port, reload=True)

if __name__ == "__main__":
    main()
