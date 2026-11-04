"""Beginner CLI smoke path, executed as separate guarded child processes, with a full transcript.

Each step runs ``rkb`` (``scivetra_kb.cli.main``) in a NEW Python process started through ``tests/netguard.child_command``,
so the outbound-network guard is active before any user code (the child prints GUARD_ACTIVE first). The transcript
records the command line (temporary paths replaced by <WORK>), exit code and stdout JSON for every step.
Usage:  python -I tools/s04j_cli_smoke.py <empty work dir> <transcript.json>
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
SIM = ROOT / "tests" / "fixtures" / "journal_sim" / "jisa_shaped"
TEST_KEY = "SYNTHETIC-OA-TEST-KEY-not-a-real-credential"


def main(argv) -> int:
    from tests import netguard
    work = Path(argv[0]).resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work dir must be absent or empty")
    work.mkdir(parents=True, exist_ok=True)
    kb, steps, ctx = str(work / "kb"), [], {}

    def rkb(*args, env_key=False, expect=0):
        code = ("import sys; sys.path.insert(0, %r); from scivetra_kb.cli import main; raise SystemExit(main(%r))" % (str(ROOT / "src"), list(args)))
        env = dict(os.environ)
        env.pop("SCIVETRA_OPENALEX_API_KEY", None)
        if env_key:
            env["SCIVETRA_OPENALEX_API_KEY"] = TEST_KEY
        proc = subprocess.run(netguard.child_command(code), capture_output=True, text=True, timeout=900, env=env, encoding="utf-8")
        lines = proc.stdout.strip().splitlines()
        guard, payload = lines[0], json.loads(lines[-1])
        shown = ["rkb"] + [a.replace(str(work), "<WORK>").replace(str(SIM), "<SIM>/jisa_shaped") for a in args]
        steps.append({"command": " ".join(shown), "env": {"SCIVETRA_OPENALEX_API_KEY": "<synthetic test key>"} if env_key else {},
                      "exit_code": proc.returncode, "guard": guard, "stdout_json": json.loads(json.dumps(payload).replace(str(work).replace("\\", "\\\\"), "<WORK>"))})
        if proc.returncode != expect:
            raise SystemExit(f"step failed: {shown} -> {proc.returncode}\n{proc.stderr[-3000:]}")
        return payload

    rkb("init", "--data-root", kb)
    ctx["jid"] = rkb("journal", "register", "--data-root", kb, "--label", "JISA-shaped synthetic", "--operator", "novice", "--issn", "2214-2126",
                     "--issn", "2214-2134", "--owner-count", "2151", "--owner-count-observed-on", "2026-11-01",
                     "--owner-count-note", "Owner-observed Crossref rows=0 total-results; historical only")["journal_id"]
    rkb("journal", "resolve", "--data-root", kb, "--journal-id", ctx["jid"], "--operator", "novice", "--transport-sim", str(SIM))
    run_id = rkb("journal", "harvest", "--data-root", kb, "--journal-id", ctx["jid"], "--operator", "novice", "--provider", "crossref",
                 "--page-size", "200", "--transport-sim", str(SIM), "--sim-profile", "interrupt_once", expect=4)["run_id"]
    rkb("journal", "status", "--data-root", kb, "--run-id", run_id)
    rkb("journal", "resume", "--data-root", kb, "--run-id", run_id, "--operator", "novice", "--transport-sim", str(SIM), "--sim-profile", "after_restart")
    rkb("journal", "harvest", "--data-root", kb, "--journal-id", ctx["jid"], "--operator", "novice", "--provider", "openalex", "--transport-sim", str(SIM))
    rkb("journal", "harvest", "--data-root", kb, "--journal-id", ctx["jid"], "--operator", "novice", "--provider", "openalex", "--transport-sim", str(SIM),
        env_key=True)
    rkb("journal", "harvest", "--data-root", kb, "--journal-id", ctx["jid"], "--operator", "novice", "--provider", "openalex-alternate",
        "--transport-sim", str(SIM), env_key=True)
    version = rkb("journal", "reconcile", "--data-root", kb, "--journal-id", ctx["jid"], "--operator", "novice")["version_id"]
    rkb("journal", "export", "--data-root", kb, "--version-id", version, "--out-dir", str(work / "exports"), "--operator", "novice")
    rkb("journal", "export", "--data-root", kb, "--version-id", version, "--out-dir", str(work / "exports"), "--operator", "novice")
    rkb("journal", "coverage", "--data-root", kb, "--version-id", version)
    rkb("journal", "harvest", "--data-root", kb, "--journal-id", ctx["jid"], "--operator", "novice", "--provider", "crossref", "--live", expect=2)
    rkb("backup", "--data-root", kb, "--target", str(work / "backups"))
    backup = str(next((work / "backups").iterdir()))
    rkb("verify-backup", "--backup", backup)
    rkb("restore", "--backup", backup, "--data-root", str(work / "restored"))
    rkb("doctor", "--data-root", str(work / "restored"), "--full")
    checks = {
        "interrupted_exit_4": steps[3]["exit_code"] == 4 and steps[3]["stdout_json"]["error"] == "SIMULATED_INTERRUPTION",
        "status_unfinished": steps[4]["stdout_json"]["state"] == "UNFINISHED",
        "resume_complete_with_replay": steps[5]["stdout_json"]["state"] == "COMPLETE" and steps[5]["stdout_json"]["counters"]["replays"] == 1,
        "openalex_without_key_blocked": steps[6]["stdout_json"]["stop_reason"] == "API_KEY_REQUIRED_FOR_DEEP_PAGING",
        "openalex_with_key_complete": steps[7]["stdout_json"]["state"] == "COMPLETE",
        "reconcile_complete_relative": steps[9]["stdout_json"]["collection_state"] == "COMPLETE_RELATIVE_TO_RECORDED_QUERIES",
        "second_export_noop": steps[11]["stdout_json"]["code"] == "EXPORT_ALREADY_PRESENT_IDENTICAL",
        "live_refused": steps[13]["stdout_json"]["error"] == "LIVE_ACCESS_NOT_AUTHORIZED",
        "verify_valid": steps[15]["stdout_json"]["valid"] is True,
        "doctor_clean": steps[17]["stdout_json"]["problems"] == [],
        "guard_active_every_step": all(s["guard"] == "GUARD_ACTIVE" for s in steps),
        "key_never_in_transcript": TEST_KEY not in json.dumps(steps),
    }
    report = {"schema": "rkb-s04j-cli-smoke/1", "python": sys.version.split()[0], "steps": steps, "checks": checks, "ok": all(checks.values())}
    Path(argv[1]).write_text(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"], "checks": checks}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
