"""Re-verify the frozen native fixture with Reprovane 2.0.1's own verify-export (run with a Reprovane interpreter).

    <reprovane-venv>/python -I -B verify_native_fixture.py --reprovane-src <pinned tree> --fixture <tests/fixtures/rv_native_2_0_1> --out <file.json>

Copies each release to a temporary folder first (the fixture is never touched), runs the native CLI
``verify-export`` and compares file hashes with generation_report.json. An audit hook refuses network use.
"""
import argparse
import hashlib
import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path


def _guard(event, args):
    if event in ("socket.connect", "socket.getaddrinfo"):
        raise RuntimeError("network refused")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--reprovane-src", required=True, type=Path)
    p.add_argument("--fixture", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)
    sys.addaudithook(_guard)
    sys.path[:0] = [str(args.reprovane_src.resolve())]
    import reprovane
    from reprovane._version import __version__
    from reprovane.corpus_release.__main__ import _unmanaged_main as corpus_cli
    if not Path(reprovane.__file__).resolve().is_relative_to(args.reprovane_src.resolve()):
        raise SystemExit("reprovane not imported from the pinned tree")
    report = json.loads((args.fixture / "generation_report.json").read_text(encoding="utf-8"))
    out = {"schema": "rkb-rv-native-verify/1", "reprovane_version": __version__, "pinned_commit": report["pinned_commit"], "releases": []}
    with tempfile.TemporaryDirectory() as tmp:
        for release in report["releases"]:
            copy = Path(tmp, release["label"])
            shutil.copytree(args.fixture / release["label"], copy)
            hashes = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(copy.iterdir())}
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = corpus_cli(["verify-export", "--directory", str(copy), "--expected-manifest-sha256", release["verify_export"]["release_manifest_sha256"]])
            result = json.loads(buffer.getvalue())
            out["releases"].append({"label": release["label"], "release_id": release["release_id"], "native_cli_exit_code": code,
                                    "valid": result.get("valid"), "corpus_item_count": result.get("corpus_item_count"),
                                    "release_manifest_sha256": result.get("release_manifest_sha256"),
                                    "file_hashes_match_generation_report": hashes == release["file_sha256"]})
    out["ok"] = all(r["native_cli_exit_code"] == 0 and r["valid"] and r["file_hashes_match_generation_report"] for r in out["releases"])
    args.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": out["ok"]}))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
