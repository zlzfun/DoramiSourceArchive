from copy import deepcopy

import pytest
from scripts.score_daily_brief_dedup import DUPLICATES, QUOTAS, score


def test_score_rejects_all_keep_missing_rows_and_anchor_regression():
    labels, rows = [], []
    for kind, count in QUOTAS.items():
        for i in range(count):
            key = f"{kind}-{i}"
            duplicate = kind in DUPLICATES
            labels.append({"id": key, "stratum": kind, "valid_refs": ["p1"] if duplicate else []})
            rows.append({"id": key, "action": "drop" if duplicate else "keep", "ref": "p1",
                         "proposed_drop": duplicate, "validation_rejected": False, "evidence_verified": True})
    trials = [{"old": rows, "new": deepcopy(rows)} for _ in range(3)]
    assert score(labels, trials)["passed"]
    trials[0]["new"][-1]["action"] = "drop"
    with pytest.raises(ValueError, match="inconsistent"):
        score(labels, trials)
    trials[0]["new"][-1]["proposed_drop"] = True
    assert not score(labels, trials)["gates"]["anchors"]
    for trial in trials:
        for row in trial["new"]:
            row.update(action="keep", proposed_drop=False)
    assert not score(labels, trials)["gates"]["duplicates"]
    trials[0]["new"].pop()
    with pytest.raises(ValueError, match="missing"):
        score(labels, trials)
