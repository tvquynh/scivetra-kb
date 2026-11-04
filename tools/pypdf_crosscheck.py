"""Optional G1 second opinion: run the sandboxed validator WITH an isolated pypdf copy on synthetic PDFs.

Not part of the required offline suite (pypdf is not a project dependency). Usage::

    python -I tools/pypdf_crosscheck.py --pypdf-site <dir containing pypdf/ and pypdf-*.dist-info/> --out <file.json>

The pypdf copy is verified file-by-file against its dist-info RECORD hashes before use (integrity of the copy, not
authenticity). The child imports pypdf before installing its audit hook; network, process creation and further
file opens stay refused.
"""
import argparse
import base64
import csv
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_record(site: Path) -> dict:
    dist = sorted(site.glob("pypdf-*.dist-info"))
    if len(dist) != 1:
        raise SystemExit("exactly one pypdf dist-info expected")
    checked = 0
    with open(dist[0] / "RECORD", encoding="utf-8", newline="") as fh:
        for name, digest, _size in csv.reader(fh):
            if not digest or not name.startswith("pypdf/"):
                continue
            algo, value = digest.split("=", 1)
            actual = base64.urlsafe_b64encode(hashlib.new(algo, (site / name).read_bytes()).digest()).rstrip(b"=").decode()
            if actual != value:
                raise SystemExit("pypdf copy differs from RECORD: " + name)
            checked += 1
    return {"dist_info": dist[0].name, "record_files_verified": checked}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--pypdf-site", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from scivetra_kb.assets.sandbox import validate_file
    from tests.pdf_fixtures import html_challenge, malformed_xref, synthetic_pdf, title_page, truncated
    site = Path(args.pypdf_site).resolve()
    report = {"schema": "rkb-pypdf-crosscheck/1", "pypdf": verify_record(site), "cases": []}
    good = synthetic_pdf([title_page(title="Synthetic cross-check", authors=["Ada Synthetic"], doi="10.99999/x.1")])
    cases = {"valid": good, "valid_flate": synthetic_pdf([["compressed synthetic page"]], compress=True), "two_pages": synthetic_pdf([["a"], ["b"]]),
             "html": html_challenge(), "truncated": truncated(good), "bad_xref": malformed_xref(good), "encrypted": synthetic_pdf([["x"]], encrypt=True),
             "zero_pages": synthetic_pdf([])}
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in cases.items():
            path = Path(tmp, name + ".bin")
            path.write_bytes(data)
            plain, with_pypdf = validate_file(path), validate_file(path, pypdf_site=str(site))
            report["cases"].append({"case": name, "stdlib_validator": plain["reasons"] or ["STRUCTURALLY_VALID"],
                                    "with_pypdf": with_pypdf["reasons"] or ["STRUCTURALLY_VALID"],
                                    "pypdf_open_ok": with_pypdf["result"].get("pypdf_open_ok"),
                                    "pypdf_page_count": with_pypdf["result"].get("pypdf_page_count")})
    report["agree"] = all(c["stdlib_validator"] == c["with_pypdf"] for c in report["cases"])
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"agree": report["agree"], "cases": len(report["cases"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
