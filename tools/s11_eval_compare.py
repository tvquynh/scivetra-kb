"""Row-level comparison of two S11 synthetic evaluation runs (``s11_evaluation_cases.json``), e.g. v01 (rubric /1) against v02
(rubric /2). Every case present in both is compared on oracle verdict, actual verdict and reasons; new and removed cases are listed.

Usage: python -I tools/s11_eval_compare.py --old OLD_cases.json --new NEW_cases.json [--out OUT.json]
"""
import argparse
import json
from pathlib import Path


def compare(old: list, new: list) -> dict:
    a, b = {r["case_id"]: r for r in old}, {r["case_id"]: r for r in new}
    changed = []
    for k in sorted(set(a) & set(b)):
        x, y = a[k], b[k]
        diff = {f: [x[f], y[f]] for f in ("oracle_verdict", "actual_verdict", "reasons", "references") if x[f] != y[f]}
        if diff:
            changed.append({"case_id": k, "kind": y["kind"], **diff})
    return {"old_cases": len(a), "new_cases": len(b), "common": len(set(a) & set(b)), "added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
            "verdict_changes": [c for c in changed if "actual_verdict" in c or "oracle_verdict" in c],
            "reason_only_changes": [c for c in changed if "actual_verdict" not in c and "oracle_verdict" not in c]}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--out")
    args = p.parse_args(argv)
    out = compare(json.loads(Path(args.old).read_text(encoding="utf-8")), json.loads(Path(args.new).read_text(encoding="utf-8")))
    text = json.dumps(out, indent=1, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
