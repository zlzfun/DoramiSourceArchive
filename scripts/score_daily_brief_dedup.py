"""Score three paired replays against the frozen #313 editorial labels. No network or database."""
import argparse
from collections import Counter
import json
from math import sqrt
from pathlib import Path
from statistics import median


# Keys describe the editorial strata. The last two are public development controls.
QUOTAS = {"numeric_change": 15, "stage_change": 15, "same_number": 10, "same_entity": 10,
          "unrelated": 15, "repeat": 15, "paraphrase": 15, "one_of_many": 10, "older_repeat": 5,
          "anchor_ipo": 1, "anchor_financials": 1}
DUPLICATES = {"repeat", "paraphrase", "one_of_many", "older_repeat"}
ACTIONS = {"keep", "followup", "drop"}


def indexed(rows):
    result = {}
    for row in rows:
        key = row["id"]
        if not isinstance(key, str) or not key or key in result:
            raise ValueError(f"empty or duplicate id: {key!r}")
        result[key] = row
    return result


def proportion(count, total):
    """Wilson 95% interval for this labeled stratum in one trial."""
    p = count / total
    z = 1.96
    scale = 1 + z ** 2 / total
    center = (p + z ** 2 / (2 * total)) / scale
    radius = z * sqrt(p * (1 - p) / total + z ** 2 / (4 * total ** 2)) / scale
    return {"count": count, "total": total, "wilson95": [max(0, center - radius), min(1, center + radius)]}


def score(labels, trials):
    gold = indexed(labels)
    if Counter(r["stratum"] for r in labels) != QUOTAS or len(trials) != 3:
        raise ValueError("expected frozen 110-case holdout, two controls and three paired trials")
    for r in labels:
        refs = r["valid_refs"]
        if not isinstance(refs, list) or not all(isinstance(ref, str) and ref for ref in refs):
            raise ValueError(f"invalid editorial references: {r['id']}")
        if r["stratum"] in DUPLICATES and not refs:
            raise ValueError(f"duplicate without a reference: {r['id']}")
    rounds = []
    votes = {"old": Counter(), "new": Counter()}
    for trial in trials:
        totals = {}
        for variant in ("old", "new"):
            rows = indexed(trial[variant])
            if rows.keys() != gold.keys():
                raise ValueError(f"{variant}: missing or unexpected case IDs")
            counts = Counter()
            for key, row in rows.items():
                action = row["action"]
                if action not in ACTIONS:
                    raise ValueError(f"{key}: invalid action {action}")
                dropped = action == "drop"
                kind = gold[key]["stratum"]
                votes[variant][key] += dropped
                if variant == "new":
                    proposed, rejected = row["proposed_drop"], row["validation_rejected"]
                    if type(proposed) is not bool or type(rejected) is not bool:
                        raise ValueError(f"{key}: expected boolean validation flags")
                    if (rejected and (not proposed or dropped)) or (dropped and not proposed):
                        raise ValueError(f"{key}: inconsistent drop proposal")
                if kind.startswith("anchor_"):
                    counts["anchor_dropped"] += dropped
                    continue
                counts["duplicate_dropped" if kind in DUPLICATES else "false_drop"] += dropped
                counts["unrelated_followup"] += kind == "unrelated" and action == "followup"
                if variant == "new":
                    counts["proposed_drop"] += proposed
                    counts["validation_rejected"] += rejected
                    if action in {"drop", "followup"}:
                        counts["cited"] += 1
                        counts["correct_ref"] += row.get("ref") in gold[key]["valid_refs"]
                    if dropped:
                        counts["missing_evidence"] += row.get("evidence_verified") is not True
            totals[variant] = counts
        rounds.append(totals)
    def values(variant, name):
        return [r[variant][name] for r in rounds]

    regressions = [key for key, row in gold.items() if row["stratum"] not in DUPLICATES
                   and not row["stratum"].startswith("anchor_")
                   and votes["old"][key] < 2 <= votes["new"][key]]
    old_dup = median(values("old", "duplicate_dropped"))
    new_dup = median(values("new", "duplicate_dropped"))
    worst_loss = max(values("new", "false_drop"))
    gates = {
        "new_facts": worst_loss <= 2 and worst_loss <= median(values("old", "false_drop")),
        "paired_regressions": len(regressions) <= 1,
        "duplicates": new_dup >= 36 and new_dup >= old_dup - 5,
        "references": all(r["new"]["cited"] and 10*r["new"]["correct_ref"] >= 9*r["new"]["cited"]
                          for r in rounds),
        "unrelated_followup": max(values("new", "unrelated_followup")) <= 2,
        "rejected": all(r["new"]["proposed_drop"] and
                        10*r["new"]["validation_rejected"] <= 3*r["new"]["proposed_drop"] for r in rounds),
        "evidence": not any(values("new", "missing_evidence")),
        "anchors": not any(values("new", "anchor_dropped")),
    }
    flips = {v: [k for k, n in counts.items() if n in (1, 2)] for v, counts in votes.items()}
    rates = [{v: {"false_drop": proportion(c["false_drop"], 65),
                  "duplicate_dropped": proportion(c["duplicate_dropped"], 45)} for v, c in r.items()}
             for r in rounds]
    return {"passed": all(gates.values()), "gates": gates, "rounds": rounds, "primary_rates": rates,
            "regression_ids": regressions, "drop_flip_ids": flips}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path, help="frozen blind labels (JSON array)")
    parser.add_argument("trials", type=Path, help="three paired old/new trials (JSON array)")
    args = parser.parse_args()
    report = score(json.loads(args.labels.read_text()), json.loads(args.trials.read_text()))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
