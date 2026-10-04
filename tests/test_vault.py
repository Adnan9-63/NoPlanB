import uuid
from pathlib import Path
from core import vault_manager
from core.config import settings

def test_vault_creation(tmp_path: Path):
    # Override vault root for testing
    settings.vault_root = tmp_path
    
    task_id = vault_manager.create_task_entry(
        atomic_task="Test Task",
        duration_min=10,
        obstacle="Distraction",
        intention="If distracted, refocus.",
        woop_transcript="User: hi\nInterrogator: hello"
    )
    
    task = vault_manager.get_task(task_id)
    assert task is not None
    assert task["atomic_task"] == "Test Task"
    assert task["status"] == "pending"

    # Check transcript was saved
    crucible_file = settings.crucibles_dir / f"{task_id}.md"
    assert crucible_file.exists()
    assert "User: hi" in crucible_file.read_text(encoding="utf-8")

def test_vault_update(tmp_path: Path):
    settings.vault_root = tmp_path
    task_id = vault_manager.create_task_entry(
        atomic_task="Task 2",
        duration_min=5,
        obstacle="None",
        intention="Do it"
    )
    
    vault_manager.update_task_status(task_id, "done", mood=4, friction_sec=120)
    
    task = vault_manager.get_task(task_id)
    assert task["status"] == "done"
    assert task["mood"] == 4
    assert task["friction_sec"] == 120
