"""Reproducible synthetic end-to-end demonstration of S10 (offline; no AI client, account or network).

Usage: python tools/s10_demo.py --out-dir <new empty folder>

1. builds a synthetic two-paper KB (generated PDFs parsed by the pinned S06 parser), an S09 index, an owner and two profiles;
2. Claude Desktop path: launches ``rkb ai mcp-serve`` as a child process and drives it with the independent mock stdio client:
   default deny -> owner A8 determination + OAG-6 approval for paper A -> released context -> revocation -> denied again;
3. ChatGPT path: A7 + A8 + approval -> ``exchange-export`` -> ``exchange-verify`` (RELEASE_STILL_AUTHORIZED) -> revocation ->
   verify (DO_NOT_UPLOAD) and purge;
4. writes ``demo_transcript.json`` (statuses, reason codes, hashes) and keeps one released bundle under ``bundle_released/`` as a
   sample. All content is synthetic.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from tests.s10_helpers import QUERY, S10Case  # noqa: E402
from tests.s10_mcp_client import StdioMcpClient  # noqa: E402
from scivetra_kb.ai import exchange  # noqa: E402


class Demo(S10Case):
    def runTest(self):
        pass


def brief(env):
    return {k: env[k] for k in ("status", "consumer", "transport", "transport_state", "model_location", "content_revealed", "results_sha256")} | {
        "reason_codes": env["authorization"]["reason_codes"], "approval_id": env["authorization"]["oag6_approval_id"],
        "passages": len(env["results"].get("passages", [])) if isinstance(env.get("results"), dict) else 0}


def main(argv):
    out = Path(argv[argv.index("--out-dir") + 1])
    out.mkdir(parents=True, exist_ok=False)
    case = Demo()
    case.setUp()
    steps = []
    try:
        case.ai_world()
        c = StdioMcpClient(case.root, case.profile)
        init = c.initialize()["result"]
        steps.append({"step": "mcp_initialize", "protocol": init["protocolVersion"], "capabilities": init["capabilities"]})
        steps.append({"step": "mcp_tools_list", "tools": [t["name"] for t in c.request("tools/list")["result"]["tools"]]})
        args = {"scope_id": case.scope, "query": QUERY}
        steps.append({"step": "mcp_default_deny", **brief(c.call("build_rag_context", args)["result"]["structuredContent"])})
        approval = case.full_release(real_clock=True)
        steps.append({"step": "owner_a8_determination_and_oag6_approval_for_paper_A", "approval_id": approval["approval_id"]})
        released = c.call("build_rag_context", args)["result"]["structuredContent"]
        steps.append({"step": "mcp_released_context", **brief(released),
                      "citations": [p["citation"] for p in released["results"]["passages"]],
                      "excerpt_sha256": [p["anchor"]["excerpt_sha256"] for p in released["results"]["passages"]]})
        case.records("revoke", target_id=approval["approval_id"], reason="demo revocation", operator="op.owner")
        steps.append({"step": "mcp_after_revocation_same_process", **brief(c.call("build_rag_context", args)["result"]["structuredContent"])})
        code, _, err = c.close()
        steps.append({"step": "mcp_shutdown", "exit_code": code, "stderr_bytes": len(err)})

        prof = case.make_profile("CHATGPT_FILE_EXCHANGE")
        case.profile = prof
        case.determine(case.items[0], "redistribute")
        appr = case.approve(paper_ids=[case.items[0]["candidate"]["paper_id"]])
        work = out / "work"
        work.mkdir()
        exp = exchange.export_bundle(case.open_kb, profile_id=prof, scope_id=case.scope, query=QUERY, out_dir=str(work / "bundle"), operator="op.exporter")
        steps.append({"step": "chatgpt_export", "published": exp["published"], "bundle_sha256": exp["bundle_sha256"], "files": exp["files"]})
        shutil.copytree(work / "bundle", out / "bundle_released")
        steps.append({"step": "chatgpt_verify_before_upload", **exchange.verify_bundle(case.open_kb, bundle_dir=str(work / "bundle"))})
        case.records("revoke", target_id=appr["approval_id"], reason="demo revocation", operator="op.owner")
        steps.append({"step": "chatgpt_verify_after_revocation_and_purge", **exchange.verify_bundle(case.open_kb, bundle_dir=str(work / "bundle"), purge=True)})
        shutil.rmtree(work, ignore_errors=True)
        with case.reader() as kb:
            steps.append({"step": "audit", "ai_access_events": kb.one("SELECT count(*) n FROM aig_access_event")["n"],
                          "content_revealed_events": kb.one("SELECT count(*) n FROM aig_access_event WHERE content_revealed=1")["n"]})
    finally:
        case.tearDown()
    expected = ["DENIED", "OK", "DENIED"]
    got = [s["status"] for s in steps if s["step"].startswith("mcp_") and "status" in s]
    ok = got == expected and steps[-3]["verdict"] == "RELEASE_STILL_AUTHORIZED" and steps[-2]["verdict"] == "DO_NOT_UPLOAD"
    report = {"schema": "scivetra-s10-demo/1", "synthetic": True, "network": "none", "ai_client": "offline mock stdio client (not Claude Desktop)",
              "ok": ok, "steps": steps}
    (out / "demo_transcript.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "steps": len(steps)}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
