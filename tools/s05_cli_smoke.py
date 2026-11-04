"""Beginner S05 CLI smoke path, executed as separate guarded child processes, with a full transcript.

Each step runs ``rkb`` (``scivetra_kb.cli.main``) in a NEW Python process started through ``tests/netguard.child_command``,
so the outbound-network guard is active before any user code (the child prints GUARD_ACTIVE first). The transcript
records the command line (temporary paths replaced by <WORK>), exit code and stdout JSON for every step.
Data: a synthetic 3-item screened Stage-11 release and one synthetic PDF (tests/fte_helpers.py, tests/pdf_fixtures.py).
Usage:  python -I tools/s05_cli_smoke.py <empty work dir> <transcript.json>
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def main(argv) -> int:
    from tests import netguard
    from tests.fte_helpers import PROTOCOL, RIGHTS, ROSTER, VERSION_EVIDENCE, write_stage11b_export, write_ta_release
    from tests.pdf_fixtures import synthetic_pdf, title_page
    work = Path(argv[0]).resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work dir must be absent or empty")
    work.mkdir(parents=True, exist_ok=True)
    kb, steps = str(work / "kb"), []
    export = work / "rv_release"
    items, sem, verify = write_ta_release(str(export), 3, release_id="crl_s05_cli_smoke")
    files = work / "inputs"
    (files / "pdfs").mkdir(parents=True)
    for name, value in (("protocol.json", PROTOCOL), ("roster.json", ROSTER), ("version_evidence.json", VERSION_EVIDENCE["PUBLISHED"]),
                        ("rights_evidence.json", RIGHTS["PUBLISHED"])):
        (files / name).write_text(json.dumps(value), encoding="utf-8")
    (files / "pdfs" / "study0.pdf").write_bytes(synthetic_pdf([title_page(title="Synthetic full-text study number 0", authors=["First Synthetic"],
                                                                          doi="10.99999/fte.0", body=["Synthetic CLI smoke body"])]))

    def rkb(*args, expect=0):
        code = ("import sys; sys.path.insert(0, %r); from scivetra_kb.cli import main; raise SystemExit(main(%r))" % (str(ROOT / "src"), list(args)))
        proc = subprocess.run(netguard.child_command(code), capture_output=True, text=True, timeout=900, encoding="utf-8")
        lines = proc.stdout.strip().splitlines()
        guard, payload = lines[0], json.loads(lines[-1])
        shown = ["rkb"] + [a.replace(str(work), "<WORK>") for a in args]
        steps.append({"command": " ".join(shown), "exit_code": proc.returncode, "guard": guard,
                      "stdout_json": json.loads(json.dumps(payload).replace(str(work).replace("\\", "\\\\"), "<WORK>"))})
        if proc.returncode != expect:
            raise SystemExit(f"step failed: {shown} -> {proc.returncode}\n{proc.stderr[-3000:]}")
        return payload

    def fte(*args, expect=0):
        return rkb("fte", *args, expect=expect)

    rkb("init", "--data-root", kb)
    rkb("library", "import-reprovane", "--data-root", kb, "--export-dir", str(export), "--workspace-label", "ws", "--operator", "novice")
    source = fte("sources", "--data-root", kb)["eligible_sources"][0]["release_import_id"]
    pid = fte("protocol-create", "--data-root", kb, "--protocol-file", str(files / "protocol.json"), "--operator", "novice")["protocol_id"]
    base = ["--data-root", kb, "--export-dir", str(export), "--expected-manifest-sha256", sem, "--rv-verify-json", verify, "--protocol-id", pid,
            "--roster-file", str(files / "roster.json"), "--label", "CLI smoke run", "--operator", "novice"]
    journal_refusal = fte("create", "--source", "jcv_00000000000000000000000001", *base, expect=2)
    run_id = fte("create", "--source", source, *base)["run_id"]
    again = fte("create", "--source", source, *base)
    c0, c1, c2 = fte("candidates", "--data-root", kb, "--run-id", run_id)["candidates"]
    dv = rkb("asset", "add-version", "--data-root", kb, "--paper-id", c0["paper_id"], "--version-class", "PUBLISHED",
             "--evidence-file", str(files / "version_evidence.json"), "--operator", "novice")["document_version_id"]
    reg = rkb("asset", "register", "--data-root", kb, "--fixture-root", str(files / "pdfs"), "--file", "study0.pdf", "--document-version-id", dv,
              "--operator", "novice")
    unconfirmed = fte("attach-evidence", "--data-root", kb, "--candidate-id", c0["candidate_id"], "--link-id", reg["link_id"], "--operator", "novice",
                      expect=2)
    rkb("asset", "review-link", "--data-root", kb, "--link-id", reg["link_id"], "--decision", "CONFIRM", "--operator", "novice",
        "--reason", "title page and DOI checked by a human")
    rkb("rights", "assess", "--data-root", kb, "--asset", reg["asset_sha256"], "--document-version-id", dv, "--evidence-file",
        str(files / "rights_evidence.json"), "--operator", "novice")
    attached = fte("attach-evidence", "--data-root", kb, "--candidate-id", c0["candidate_id"], "--link-id", reg["link_id"], "--operator", "novice")
    ai = fte("decide", "--data-root", kb, "--candidate-id", c0["candidate_id"], "--reviewer", "gpt.assistant", "--outcome", "FT_INCLUDE", expect=2)
    fte("decide", "--data-root", kb, "--candidate-id", c0["candidate_id"], "--reviewer", "rev.a", "--outcome", "FT_INCLUDE", "--page", "2")
    fte("decide", "--data-root", kb, "--candidate-id", c0["candidate_id"], "--reviewer", "rev.b", "--outcome", "FT_EXCLUDE", "--reason", "WRONG_OUTCOME")
    adj = fte("adjudicate", "--data-root", kb, "--candidate-id", c0["candidate_id"], "--adjudicator", "adj.c", "--outcome", "FT_INCLUDE",
              "--note", "outcome C2 reported in table 2")
    early_nr = fte("decide", "--data-root", kb, "--candidate-id", c1["candidate_id"], "--reviewer", "rev.a", "--outcome", "FT_NOT_RETRIEVED",
                   "--reason", "NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS", expect=2)
    for day, status in (("2026-10-01", "LOGIN_REQUIRED"), ("2026-10-02", "BOT_CHECK")):
        fte("attempt", "--data-root", kb, "--candidate-id", c1["candidate_id"], "--status", status, "--source", "publisher site",
            "--attempted-on", day, "--operator", "novice")
    secret = fte("attempt", "--data-root", kb, "--candidate-id", c2["candidate_id"], "--status", "LOGIN_REQUIRED", "--source",
                 "https://publisher.example/login?token=abc123", "--attempted-on", "2026-10-03", "--operator", "novice", expect=2)
    fte("attempt", "--data-root", kb, "--candidate-id", c2["candidate_id"], "--status", "RETRY_QUEUED", "--source", "repository mirror",
        "--attempted-on", "2026-10-03", "--retry-after", "2026-11-20", "--operator", "novice")
    stage11b = work / "rv_stage11b"
    s11b_digest = write_stage11b_export(str(stage11b), items, release_id="crl_s05_cli_smoke", release_sem=sem,
                                        acquired={c2["corpus_item_id"]: "5" * 64})
    ext_no_digest = fte("attach-evidence", "--data-root", kb, "--candidate-id", c2["candidate_id"], "--rv11b-export-dir", str(stage11b),
                        "--operator", "novice", expect=2)
    ext_wrong_digest = fte("attach-evidence", "--data-root", kb, "--candidate-id", c2["candidate_id"], "--rv11b-export-dir", str(stage11b),
                           "--rv11b-expected-manifest-sha256", "a" * 64, "--operator", "novice", expect=3)
    ext_verified = fte("attach-evidence", "--data-root", kb, "--candidate-id", c2["candidate_id"], "--rv11b-export-dir", str(stage11b),
                       "--rv11b-expected-manifest-sha256", s11b_digest, "--operator", "novice")
    for who in ("rev.a", "rev.b"):
        fte("decide", "--data-root", kb, "--candidate-id", c1["candidate_id"], "--reviewer", who, "--outcome", "FT_NOT_RETRIEVED", "--reason",
            "NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS")
    queue = fte("queue", "--data-root", kb, "--run-id", run_id)
    pending = fte("summary", "--data-root", kb, "--run-id", run_id)
    early_lock = fte("lock", "--data-root", kb, "--run-id", run_id, "--operator", "novice", expect=2)
    provisional = fte("export", "--data-root", kb, "--run-id", run_id, "--out-dir", str(work / "exports"), "--operator", "novice")
    fte("verify", "--export-dir", str(work / "exports" / provisional["folder_name"]))
    fte("attempt", "--data-root", kb, "--candidate-id", c2["candidate_id"], "--status", "NO_ACCESS", "--source", "interlibrary loan",
        "--attempted-on", "2026-11-21", "--operator", "novice")
    for who in ("rev.a", "rev.b"):
        fte("decide", "--data-root", kb, "--candidate-id", c2["candidate_id"], "--reviewer", who, "--outcome", "FT_NOT_RETRIEVED", "--reason",
            "NOT_AVAILABLE_FROM_AUTHORS")
    final = fte("summary", "--data-root", kb, "--run-id", run_id)
    fte("resolve", "--data-root", kb, "--run-id", run_id, "--operator", "novice")
    lock = fte("lock", "--data-root", kb, "--run-id", run_id, "--operator", "novice")
    after_lock = fte("decide", "--data-root", kb, "--candidate-id", c0["candidate_id"], "--reviewer", "rev.a", "--outcome", "FT_UNSURE", "--note", "x",
                     expect=2)
    locked = fte("export", "--data-root", kb, "--run-id", run_id, "--out-dir", str(work / "exports"), "--operator", "novice")
    folder = work / "exports" / locked["folder_name"]
    verified = fte("verify", "--export-dir", str(folder))
    lock_ok = fte("verify", "--data-root", kb, "--run-id", run_id)
    tampered = work / "tampered"
    import shutil
    shutil.copytree(folder, tampered)
    text = (tampered / "decisions.jsonl").read_text(encoding="utf-8").replace("FT_INCLUDE", "FT_EXCLUDE", 1)
    (tampered / "decisions.jsonl").write_text(text, encoding="utf-8")
    tamper = fte("verify", "--export-dir", str(tampered), expect=3)
    rkb("backup", "--data-root", kb, "--target", str(work / "backups"))
    backup = str(next((work / "backups").iterdir()))
    rkb("verify-backup", "--backup", backup)
    rkb("restore", "--backup", backup, "--data-root", str(work / "restored"))
    doctor = rkb("doctor", "--data-root", str(work / "restored"), "--full")
    restored_lock = fte("verify", "--data-root", str(work / "restored"), "--run-id", run_id)
    exported = "".join(p.read_text(encoding="utf-8", errors="replace") for p in folder.iterdir())
    cats = {f["category"] for e in queue["queue"] for f in e["flags"]} if "queue" in queue else set()
    checks = {
        "journal_source_refused": journal_refusal["error"] == "JOURNAL_INVENTORY_NOT_AN_FTE_SOURCE",
        "external_reference_needs_trusted_digest": ext_no_digest["error"] == "EXTERNAL_REFERENCE_INVALID",
        "external_wrong_digest_refused": ext_wrong_digest["error"] == "EXPECTED_MANIFEST_MISMATCH",
        "external_verified_never_ready": ext_verified["evidence_ready"] is False
        and ext_verified["verification_level"] == "RV11B_PORTABLE_EXPORT_VERIFIED_WITH_TRUSTED_DIGEST",
        "create_idempotent": again.get("created") is False,
        "unconfirmed_pdf_refused": unconfirmed["error"] == "EVIDENCE_IDENTITY_NOT_HUMAN_CONFIRMED",
        "confirmed_pdf_ready": attached["evidence_ready"] is True,
        "ai_label_not_a_reviewer": ai["error"] == "UNKNOWN_REVIEWER",
        "adjudicated": adj["candidate_state"]["basis"] == "ADJUDICATION",
        "not_retrieved_needs_attempts": early_nr["error"] == "RETRIEVAL_EFFORT_NOT_DOCUMENTED",
        "secret_url_refused": secret["error"] == "TEXT_LOOKS_LIKE_SECRET",
        "queue_has_retry": "RETRYABLE_ACQUISITION" in cats,
        "pending_counted": (pending["ft_include"], pending["ft_not_retrieved"], pending["ft_pending"]) == (1, 1, 1) and pending["invariant"]["holds"],
        "lock_refused_while_pending": early_lock["error"] == "RUN_NOT_RESOLVED",
        "final_accounting": (final["ft_include"], final["ft_exclude"], final["ft_not_retrieved"], final["ft_pending"]) == (1, 0, 2, 0),
        "locked_non_authoritative": lock["authority"] == "NON_AUTHORITATIVE",
        "no_change_after_lock": after_lock["error"] == "RUN_LOCKED",
        "export_verifies": verified["valid"] is True and lock_ok["valid"] is True,
        "tamper_detected": tamper["valid"] is False,
        "doctor_clean_after_restore": doctor["problems"] == [],
        "restored_lock_verifies": restored_lock["valid"] is True,
        "export_has_no_notes_or_paths": "table 2" not in exported and str(work) not in exported and "study0.pdf" not in exported,
        "guard_active_every_step": all(s["guard"] == "GUARD_ACTIVE" for s in steps),
        "every_fte_output_non_authoritative": all(s["stdout_json"].get("authority") in (None, "NON_AUTHORITATIVE") for s in steps),
    }
    report = {"schema": "rkb-s05-cli-smoke/1", "python": sys.version.split()[0], "data": "SYNTHETIC ONLY", "steps": steps, "checks": checks,
              "ok": all(checks.values())}
    Path(argv[1]).write_text(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"], "checks": checks}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
