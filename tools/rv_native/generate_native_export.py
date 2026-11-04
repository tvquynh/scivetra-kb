"""Generate native Reprovane 2.0.1 portable corpus exports from SYNTHETIC fixtures (S03-B item 5).

Run with the interpreter of a Reprovane 2.0.1 environment, pointing at a source tree extracted from the
pinned commit (``git archive 6c4793cda915481a831a96f925f2646d80de6cc3``)::

    <reprovane-venv>/python -I -B generate_native_export.py --reprovane-src <extracted tree> --out <empty dir>

Everything runs in a disposable temporary workspace with Reprovane's own scripted fake HTTP session; an audit
hook refuses any socket/DNS use. No real workspace, network, credentials or human judgments are involved.
The script writes two releases of the same synthetic screening run (labels ``s03-r1`` and ``s03-r2``),
verifies each with ``reprovane.corpus_release.verify_export`` and the native ``verify-export`` CLI, and
writes ``generation_report.json``.
"""
import argparse
import hashlib
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

PINNED_COMMIT = "6c4793cda915481a831a96f925f2646d80de6cc3"


def _guard(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.bind", "urllib.Request"):
        raise RuntimeError("network refused in fixture generation: " + event)


def synthetic_works(work):
    """Synthetic OpenAlex-shaped works (prefix 10.99999/, ids W9xxx). Entity 4 has two provider records."""
    base = dict(cited_by_count=0, referenced_works=[], open_access=dict(is_oa=False, oa_url=None))
    first = [
        work(9001, title="Synthetic alpha study", doi="https://doi.org/10.99999/s03.alpha", type="article", **base),
        work(9002, title="Synthetic study without DOI", doi=None, type="article", **base),
        work(9003, title="Synthetic retracted study", doi="https://doi.org/10.99999/s03.retracted", type="article",
             **(base | dict(is_retracted=True))),
        work(9004, title="Synthetic two-manifestation study", doi="https://doi.org/10.99999/s03.pre", type="preprint", **base),
        work(9005, title="Synthetic excluded study", doi="https://doi.org/10.99999/s03.excluded", type="article", **base),
    ]
    second = [work(9004, title="Synthetic two-manifestation study", doi="https://doi.org/10.99999/s03.jnl", type="article", **base)]
    return first, second


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
    from reprovane.corpus_release import verify_export
    from reprovane.corpus_release.__main__ import _unmanaged_main as corpus_cli
    from corpus_release_fixtures import CorpusCase, plan
    from openalex_acquisition_fixtures import work

    if not Path(reprovane.__file__).resolve().is_relative_to(src):
        raise SystemExit("reprovane was not imported from the pinned source tree")

    class Generator(CorpusCase):
        def runTest(self):  # pragma: no cover - unittest protocol only
            pass

    case = Generator()
    case.setUp()
    report = {"schema": "rkb-rv-native-fixture-report/1", "reprovane_version": reprovane.__version__ if hasattr(reprovane, "__version__") else None,
              "pinned_commit": PINNED_COMMIT, "python": sys.version.split()[0], "releases": []}
    try:
        from reprovane._version import __version__ as rv_version
        report["reprovane_version"] = rv_version
        first, second = synthetic_works(work)
        case.primary(first)
        case.primary(second)
        case.discovery.seal(case.rid)
        result = case.store.create(case.revision, case.rid, __import__("screening_fixtures").plan())
        case.sid = result["run"]["screening_run_id"]
        case.cs = case.store.candidates(case.sid)
        titles = {}
        for index, candidate in enumerate(case.cs):
            evidence = json.dumps(candidate, sort_keys=True, default=str)
            excluded = "Synthetic excluded study" in evidence
            titles[candidate["candidate_id"]] = "EXCLUDE" if excluded else "INCLUDE"
            case.unanimous(index, "SCREEN_EXCLUDE" if excluded else "SCREEN_INCLUDE", **({"reason_codes": ["X"]} if excluded else {}))
        case.store.finalize(case.sid)
        report["screening_candidates"] = len(case.cs)
        report["screening_decisions"] = sorted(titles.values())
        for label in ("s03-r1", "s03-r2"):
            created = case.releases.create(case.revision, case.sid, plan(release_label=label))
            rid = created["release"]["release_id"]
            case.releases.finalize(rid)
            target = out / label
            case.releases.export(rid, target)
            verified = verify_export(target)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = corpus_cli(["verify-export", "--directory", str(target)])
            files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.iterdir())}
            report["releases"].append({"label": label, "release_id": rid, "verify_export": verified, "native_cli_exit_code": code,
                                       "native_cli_output": json.loads(buffer.getvalue()), "file_sha256": files})
    finally:
        case.tearDown()
    (out / "generation_report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "releases": [r["label"] for r in report["releases"]]}))


if __name__ == "__main__":
    main()
