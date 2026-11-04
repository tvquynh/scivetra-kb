"""Build the contract conformance matrix (CSV) from tools/required_ids.json and evidence/test_results.json.

A required contract ID is PASS only if at least one test carries it and every test carrying it passed.
A required ID with no test is reported as MISSING_TEST (blocking), never PASS. Rows from tools/documented_gaps.json
(NOT_IMPLEMENTED_PENDING, NOT_RUN, DEFERRED_STAGE) are appended for transparency; they are not counted as PASS.
Usage: python -I tools/build_conformance.py <out.csv>
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv):
    required = json.loads((ROOT / "tools" / "required_ids.json").read_text(encoding="utf-8"))["required"]
    results = json.loads((ROOT / "evidence" / "test_results.json").read_text(encoding="utf-8"))["tests"]
    by_id = {}
    for record in results:
        for cid in record["contract_ids"]:
            by_id.setdefault(cid, []).append(record)
    rows, blocking = [], 0
    for item in required:
        tests = by_id.get(item["id"], [])
        if not tests:
            outcome = "MISSING_TEST"
            blocking += 1
        elif all(t["outcome"] == "PASS" for t in tests):
            outcome = "PASS"
        else:
            outcome = "FAIL"
            blocking += 1
        kinds = {"STATIC" if t["test"].startswith(("tests.test_static.", "tests.test_s03_static.")) else "EXECUTED" for t in tests}
        rows.append({"contract_id": item["id"], "stage": item.get("stage", "S02"),
                     "contract_reference": item.get("contract", "S01 v03 04 section 10 / 11 section 2"),
                     "test_ids": ";".join(sorted(t["test"].replace("tests.", "") for t in tests)), "test_count": len(tests),
                     "evidence_kind": "+".join(sorted(kinds)) if tests else "NO_TEST_YET", "outcome": outcome,
                     "evidence_file": "evidence/test_results.json; evidence/test_run.log"})
    gaps_path = ROOT / "tools" / "documented_gaps.json"
    if gaps_path.exists():
        for gap in json.loads(gaps_path.read_text(encoding="utf-8"))["gaps"]:
            rows.append({"contract_id": gap["id"], "stage": "S03", "contract_reference": gap["contract"], "test_ids": "", "test_count": 0,
                         "evidence_kind": gap["kind"], "outcome": gap["kind"], "evidence_file": gap["where"]})
    with open(argv[1], "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"required": len(required), "pass": sum(r["outcome"] == "PASS" for r in rows), "blocking": blocking,
                      "documented_gaps": len(rows) - len(required)}))
    return 0 if blocking == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
