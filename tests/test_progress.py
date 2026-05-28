import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_completed_progress_is_visible_without_internal_timestamp():
    from pipeline.progress import clear_progress, complete_progress, get_all_progress, set_progress

    clear_progress("fetcher_a")

    set_progress("fetcher_a", 1, 5)
    assert get_all_progress()["fetcher_a"] == {
        "current": 1,
        "total": 5,
        "status": "running",
    }

    complete_progress("fetcher_a", 3, 5)
    assert get_all_progress()["fetcher_a"] == {
        "current": 3,
        "total": 5,
        "status": "completed",
    }

    clear_progress("fetcher_a")
