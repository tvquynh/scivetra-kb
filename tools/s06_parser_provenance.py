"""S06-F01 parser provenance tool: derive the reviewed package fingerprint from the OFFICIAL wheel and compare a local copy.

1. The wheel's SHA-256 must equal ``approved_parsers.APPROVED[PINNED]["official_wheel_sha256"]`` (recorded independently by
   the architect from PyPI; never taken from the wheel). Otherwise nothing is derived.
2. From that verified wheel: dist-info name, METADATA Name/Version/License-Expression, ``pypdf/_version.py``, the wheel's own
   RECORD (every entry must match the wheel bytes), and the package-file fingerprint (``runtime.fingerprint``).
3. Optionally compares an installed site directory file-by-file with the wheel's package files.
The printed ``package_files_sha256`` / ``package_file_count`` are what ``approved_parsers.py`` records after review.
Usage: python -I tools/s06_parser_provenance.py --wheel pypdf-6.12.1-py3-none-any.whl [--site DIR] [--out FILE.json]
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]


def main(argv=None):
    from scivetra_kb.parse import runtime
    from scivetra_kb.parse.approved_parsers import APPROVED, PINNED
    p = argparse.ArgumentParser()
    p.add_argument("--wheel", required=True)
    p.add_argument("--site")
    p.add_argument("--out")
    args = p.parse_args(argv)
    approved = APPROVED[PINNED]
    data = Path(args.wheel).read_bytes()
    report = {"schema": "rkb-s06-parser-provenance/1", "pin": PINNED, "wheel_file": Path(args.wheel).name, "wheel_bytes": len(data),
              "wheel_sha256": hashlib.sha256(data).hexdigest(), "expected_official_wheel_sha256": approved["official_wheel_sha256"],
              "expected_source": approved["official_wheel_sha256_source"]}
    report["wheel_sha256_matches_official"] = report["wheel_sha256"] == approved["official_wheel_sha256"]
    if not report["wheel_sha256_matches_official"]:
        report["ok"] = False
        print(json.dumps(report, indent=1))
        return 1
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    dist = sorted({n.split("/")[0] for n in names if n.split("/")[0].endswith(".dist-info")})
    meta = Parser().parsestr(z.read(f"{dist[0]}/METADATA").decode("utf-8"))
    record = [r for r in csv.reader(io.StringIO(z.read(f"{dist[0]}/RECORD").decode("utf-8"))) if r]
    record_ok = all((not r[1]) or base64.urlsafe_b64encode(hashlib.new(r[1].split("=")[0], z.read(r[0])).digest()).rstrip(b"=").decode() == r[1].split("=", 1)[1]
                    for r in record)
    entries = [(n, hashlib.sha256(z.read(n)).hexdigest()) for n in names if n.startswith("pypdf/") and not n.endswith("/")]
    report.update(dist_info=dist, metadata={"Name": meta.get("Name"), "Version": meta.get("Version"), "License-Expression": meta.get("License-Expression"),
                                            "Requires-Python": meta.get("Requires-Python"),
                                            "Requires-Dist": sorted(meta.get_all("Requires-Dist") or [])},
                  version_py=z.read("pypdf/_version.py").decode("utf-8").strip(), wheel_record_matches_bytes=record_ok,
                  package_file_count=len(entries), package_files_sha256=runtime.fingerprint(entries),
                  license_file_present=any(n.endswith("/LICENSE") for n in names))
    if args.site:
        site = Path(args.site)
        local = {p.relative_to(site).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (site / "pypdf").rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        wheel_files = dict(entries)
        report["site_comparison"] = {"same_file_set": set(local) == set(wheel_files),
                                     "differing_files": sorted(k for k in wheel_files if local.get(k) != wheel_files[k]),
                                     "extra_local_files": sorted(set(local) - set(wheel_files)),
                                     "local_fingerprint": runtime.fingerprint(local.items())}
        report["site_comparison"]["identical_to_official_wheel"] = (report["site_comparison"]["same_file_set"]
                                                                     and not report["site_comparison"]["differing_files"])
    report["recorded_in_approved_parsers"] = (approved["package_files_sha256"] == report["package_files_sha256"]
                                              and approved["package_file_count"] == report["package_file_count"])
    report["ok"] = bool(report["wheel_record_matches_bytes"] and report["metadata"]["Name"] == "pypdf" and report["metadata"]["Version"] == approved["version"]
                        and approved["version"] in report["version_py"] and (not args.site or report["site_comparison"]["identical_to_official_wheel"]))
    text = json.dumps(report, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
