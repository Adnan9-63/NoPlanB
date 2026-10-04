from ai.triage import handle_signal, TriageResult

def test_triage_done_growth():
    result = handle_signal(
        signal="done",
        current_task="Read 1 page",
        current_duration=10,
        obstacle="Boredom",
        consecutive_successes=5, # Hits the multiple of 5 condition
        consecutive_resizes=0
    )
    assert result.action == "continue"
    # Should grow by 5 min
    assert result.updated_duration == 15

def test_triage_forced_weekly_review():
    result = handle_signal(
        signal="resist",
        current_task="Write essay",
        current_duration=15,
        obstacle="Fear of failure",
        consecutive_successes=0,
        consecutive_resizes=3 # 3 consecutive resizes triggers review
    )
    assert result.action == "weekly_review"
