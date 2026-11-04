"""S06 CLI smoke: every ``rkb`` step runs in a NEW guarded Python process (``tests/netguard.child_command``).

Covers: init, release import, document version, G1 v2 intake of an object-stream PDF (refused by G1 v1 first), human link
confirmation, rights assessment, parse runtime-check, dry-run, the Windows network-isolation refusal, a refused unconfirmed
research parse, run, reuse, status, show (with and without text), verify, verify-anchor (good and bad), metadata export and a
local text export. Paths are replaced by <WORK> / <PARSER_SITE> in the transcript.
Usage: python -I tools/s06_cli_smoke.py <empty work dir> <transcript.json>   (pinned pypdf required)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def main(argv) -> int:
    from tests import netguard
    from tests import s06_pdf_fixtures as F
    from tests.s03_helpers import s03_item, write_export
    from tests.s06_helpers import OPEN_RIGHTS, VERSION_EVIDENCE, title_lines
    from scivetra_kb.parse import runtime
    site = runtime.verify_site(runtime.default_site())["site"]
    work = Path(argv[0]).resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work dir must be absent or empty")
    work.mkdir(parents=True, exist_ok=True)
    kb, steps, checks = str(work / "kb"), [], {}
    export = write_export(str(work / "rv"), [s03_item("cri_c", dois=[("10.99999/s06.c", "10.99999/s06.c")], title="Synthetic parsing paper 0",
                                                      author="First Synthetic")])
    inputs = work / "inputs"
    (inputs / "pdfs").mkdir(parents=True)
    (inputs / "version.json").write_text(json.dumps(VERSION_EVIDENCE), encoding="utf-8")
    (inputs / "rights.json").write_text(json.dumps(OPEN_RIGHTS), encoding="utf-8")
    (inputs / "pdfs" / "modern.pdf").write_bytes(F.build_pdf([F.text_page(title_lines("Synthetic parsing paper 0", "10.99999/s06.c",
                                                                                        ["CLI smoke body line."]))], syntax="object_stream"))

    def rkb(*args, expect=0):
        code = ("import sys; sys.path.insert(0, %r); from scivetra_kb.cli import main; raise SystemExit(main(%r))" % (str(ROOT / "src"), list(args)))
        proc = subprocess.run(netguard.child_command(code), capture_output=True, text=True, timeout=900, encoding="utf-8")
        lines = proc.stdout.strip().splitlines()
        guard, payload = lines[0], json.loads(lines[-1])
        scrub = lambda t: t.replace(str(work), "<WORK>").replace(site, "<PARSER_SITE>")  # noqa: E731
        steps.append({"command": " ".join(["rkb"] + [scrub(a) for a in args]), "exit_code": proc.returncode, "guard": guard,
                      "stdout_json": json.loads(scrub(json.dumps(payload)).replace(str(work).replace("\\", "\\\\"), "<WORK>")
                                                .replace(site.replace("\\", "\\\\"), "<PARSER_SITE>"))})
        if proc.returncode != expect:
            raise SystemExit(f"step failed: {args} -> {proc.returncode}\n{proc.stderr[-3000:]}")
        return payload

    rkb("init", "--data-root", kb)
    rkb("library", "import-reprovane", "--data-root", kb, "--export-dir", export, "--workspace-label", "ws", "--operator", "novice")
    paper = rkb("library", "search", "--data-root", kb, "--doi", "10.99999/s06.c")["papers"][0]["paper_id"]
    dv = rkb("asset", "add-version", "--data-root", kb, "--paper-id", paper, "--version-class", "PUBLISHED", "--evidence-file",
             str(inputs / "version.json"), "--operator", "novice")["document_version_id"]
    reg = ["asset", "register", "--data-root", kb, "--fixture-root", str(inputs / "pdfs"), "--file", "modern.pdf", "--document-version-id", dv,
           "--operator", "novice"]
    v1 = rkb(*reg)
    checks["g1_v1_rejects_object_stream_pdf"] = v1["outcome"] == "REJECTED"
    v2 = rkb(*reg, "--pdf-validator", "2", "--parser-site", site)
    checks["g1_v2_registers_object_stream_pdf"] = v2["outcome"] == "REGISTERED"
    asset, link = v2["asset_sha256"], v2["link_id"]
    run = ["parse", "run", "--data-root", kb, "--asset", asset, "--link-id", link, "--operator", "novice", "--purpose", "RESEARCH_PARSE",
           "--parser-site", site]
    checks["refused_before_human_confirmation"] = set(rkb(*run, "--accept-language-level-network-guard", expect=3)["reasons"]) == {
        "IDENTITY_NOT_HUMAN_CONFIRMED", "RIGHTS_LOCAL_PROCESS_UNDETERMINED"}
    rkb("asset", "review-link", "--data-root", kb, "--link-id", link, "--decision", "CONFIRM", "--operator", "novice", "--reason", "title page checked")
    rkb("rights", "assess", "--data-root", kb, "--asset", asset, "--document-version-id", dv, "--evidence-file", str(inputs / "rights.json"),
        "--operator", "novice")
    rt = rkb("parse", "runtime-check", "--parser-site", site)
    checks["runtime_pinned"] = (rt["version"], rt["license"]) == ("6.12.1", "BSD-3-Clause")
    checks["dry_run_opens_nothing"] = rkb(*run, "--dry-run")["bytes_opened"] is False
    if os.name == "nt":
        checks["windows_refuses_without_guard_acceptance"] = rkb(*run, expect=3)["reasons"] == ["OS_NETWORK_ISOLATION_UNAVAILABLE"]
    produced = rkb(*run, "--accept-language-level-network-guard")
    checks["produced"] = produced["outcome"] == "PRODUCED"
    checks["reused"] = rkb(*run, "--accept-language-level-network-guard")["outcome"] == "REUSED"
    art = produced["artifact_id"]
    checks["status_lists_runs"] = len(rkb("parse", "status", "--data-root", kb, "--asset", asset)["runs"]) >= 3
    plain = rkb("parse", "show", "--data-root", kb, "--artifact-id", art)
    checks["show_without_text_has_no_text"] = "text" not in plain["pages"][0]
    shown = rkb("parse", "show", "--data-root", kb, "--artifact-id", art, "--with-text", "--page", "1")
    checks["show_with_text"] = "CLI smoke body line." in shown["pages"][0]["text"]
    checks["verify"] = rkb("parse", "verify", "--data-root", kb, "--artifact-id", art)["valid"]
    b = shown["pages"][0]["blocks"][0]
    anchor = ["parse", "verify-anchor", "--data-root", kb, "--artifact-id", art, "--page", "1", "--start", str(b["start"]), "--end", str(b["end"])]
    checks["anchor_ok"] = rkb(*anchor, "--text-sha256", b["text_sha256"])["valid"]
    checks["anchor_wrong_hash_exit_3"] = rkb(*anchor, "--text-sha256", "0" * 64, expect=3)["valid"] is False
    meta = rkb("parse", "export", "--data-root", kb, "--artifact-id", art, "--out-dir", str(work / "exports"), "--operator", "novice")
    folder = work / "exports" / meta["folder_name"]
    checks["metadata_export_has_no_text"] = all(b"CLI smoke body line." not in p.read_bytes() for p in folder.iterdir())
    text = rkb("parse", "export", "--data-root", kb, "--artifact-id", art, "--out-dir", str(work / "exports"), "--operator", "novice",
               "--include-text", "--reason", "local reading copy")
    checks["text_export_explicit"] = text["contains_text"] and "page-0001.txt" in text["files"]
    checks["doctor_full_ok"] = rkb("doctor", "--data-root", kb, "--full")["ok"]
    checks["every_step_guarded"] = all(s["guard"] == "GUARD_ACTIVE" for s in steps)
    out = {"schema": "rkb-s06-cli-smoke/1", "steps": steps, "checks": checks, "ok": all(checks.values())}
    Path(argv[1]).write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": out["ok"], "steps": len(steps), "checks": checks}, sort_keys=True))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
