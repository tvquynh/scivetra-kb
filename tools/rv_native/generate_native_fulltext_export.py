"""Generate a native Reprovane 2.0.1 Stage-11 corpus export AND the matching Stage-11B full-text export (S04 fixture).

Run with the interpreter of a Reprovane 2.0.1 environment, pointing at a source tree extracted from the pinned
commit (``git archive 6c4793cda915481a831a96f925f2646d80de6cc3``)::

    <reprovane-venv>/python -I -B generate_native_fulltext_export.py --reprovane-src <extracted tree> --out <empty dir>

Everything runs in a disposable temporary workspace using Reprovane's own synthetic test fixtures
(``FullCase``: scripted DNS, scripted HTTP opener, the tiny synthetic PDFs shipped in Reprovane's tests). An audit
hook refuses real socket/DNS use. Only the portable METADATA exports are written (``export``, never
``export-pdfs``), so no PDF bytes leave the temporary workspace. Output: ``corpus/`` (Stage 11) and
``fulltext/`` (Stage 11B), each verified with Reprovane's own ``verify_export`` and native ``verify-export`` CLI,
plus ``generation_report.json``.
"""
import argparse
import hashlib
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

PINNED_COMMIT = "6c4793cda915481a831a96f925f2646d80de6cc3"


def _guard(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.bind", "urllib.Request"):
        raise RuntimeError("network refused in fixture generation: " + event)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--reprovane-src", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    out = args.out
    if out.exists() and any(out.iterdir()):
        raise SystemExit("output directory must be absent or empty")
    out.mkdir(parents=True, exist_ok=True)
    sys.addaudithook(_guard)
    src = args.reprovane_src.resolve()
    sys.path[:0] = [str(src / "tests"), str(src)]
    import reprovane
    from reprovane._version import __version__ as rv_version
    from reprovane.corpus_release import verify_export as corpus_verify
    from reprovane.corpus_release.__main__ import _unmanaged_main as corpus_cli
    from reprovane.fulltext_acquisition import verify_export as fulltext_verify
    from reprovane.fulltext_acquisition.__main__ import _unmanaged_main as fulltext_cli
    from fulltext_acquisition_fixtures import FullCase, Response, location

    if not Path(reprovane.__file__).resolve().is_relative_to(src):
        raise SystemExit("reprovane was not imported from the pinned source tree")

    class Generator(FullCase):
        def runTest(self):  # pragma: no cover - unittest protocol only
            pass

    # Six synthetic works (W1..W6, DOIs 10.1234/testN from Reprovane's fixture helper):
    #  W1 publishedVersion direct PDF -> acquired automatically (ACQUIRED_AUTO, never exported to PBD)
    #  W2 acceptedVersion direct PDF  -> HTTP 403 -> MANUAL_REQUIRED with an ELIGIBLE candidate
    #  W3 submittedVersion            -> ineligible by RV policy -> MANUAL_REQUIRED, candidate eligible=false
    #  W4 landing page only           -> no direct PDF -> MANUAL_REQUIRED, landing_pages populated
    #  W5 publishedVersion, signed-looking query -> HTTP 403 -> MANUAL_REQUIRED (bridge must omit the URL)
    #  W6 no OA location              -> MANUAL_REQUIRED, no candidates
    locations = [
        [location("publishedVersion", "journal", "https://papers.example.org/1.pdf")],
        [location("acceptedVersion", "repository", "https://repository.example.org/2.pdf")],
        [location("submittedVersion", "repository", "https://preprints.example.org/3.pdf")],
        [location("publishedVersion", "journal", None, landing_page_url="https://papers.example.org/landing/4")],
        [location("publishedVersion", "journal", "https://papers.example.org/5.pdf?Expires=1700000000&Signature=synthetic")],
        [],
    ]
    case = Generator()
    case.setUp()
    report = {"schema": "rkb-rv-native-fulltext-fixture-report/1", "reprovane_version": rv_version, "pinned_commit": PINNED_COMMIT,
              "python": sys.version.split()[0], "exports": []}
    try:
        case.setup_run(versions=("publishedVersion",) * 6, locations=locations)
        case.full.discover(case.fid, case.transport())
        case.full.acquire(case.fid, case.transport([Response(), Response(status=403), Response(status=403), Response(status=403)]))
        case.full.finalize(case.fid)
        report["opener_calls"] = [c["url"].split("?")[0] for c in case.opener.calls]
        for label, export, verify, cli, args_ in (
                ("corpus", lambda d: case.releases.export(case.crid, d), corpus_verify, corpus_cli, ["verify-export"]),
                ("fulltext", lambda d: case.full.export(case.fid, d), fulltext_verify, fulltext_cli, ["verify-export"])):
            target = out / label
            export(target)
            verified = verify(target)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cli(args_ + ["--directory", str(target)])
            files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.iterdir())}
            report["exports"].append({"label": label, "verify_export": verified, "native_cli_exit_code": code,
                                      "native_cli_output": json.loads(buffer.getvalue()), "file_sha256": files})
        assert not list(out.rglob("*.pdf")), "PDF bytes must not be exported"
    finally:
        case.tearDown()
    (out / "generation_report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "exports": [e["label"] for e in report["exports"]]}))


if __name__ == "__main__":
    main()
