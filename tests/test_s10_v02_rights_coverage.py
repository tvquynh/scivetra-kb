"""S10 v02 (S10-F01): every research-derived content class, METADATA and ANCHOR included, is released to an external AI consumer only
under an OAG-6 approval AND a current A8 decision (plus A7 for the file exchange) for the exact member. Positive and negative paths for
both consumer profiles, every tool, both transports, freshness, metadata-only members, the anchor-only use case and audit failure.

A document-text canary is not enough here (architect review of v01): denied paths also assert that no title, DOI, venue, paper id,
document version id, asset SHA-256 or chunk id of either synthetic paper appears anywhere in the response."""
import json
import unittest
from pathlib import Path
from unittest import mock as umock

from tests.s10_helpers import ALL_CLASSES, ALL_OPS, CANARY_A, CANARY_B, DOC_A, DOC_B, QUERY, S10Case
from tests.s10_mcp_client import StdioMcpClient
from tests.test_s09_v02_regressions import cas_file, supersede_rights
from scivetra_kb.ai import exchange
from scivetra_kb.ai import policy as P
from scivetra_kb.ai.records import OwnerRecords
from scivetra_kb.core.errors import KBError
from scivetra_kb.retrieval.service import RetrievalService

TOOLS_WITH_SCOPE = {"search_papers": {"query": "Synthetic"}, "search_passages": {"query": QUERY}, "search_evidence": {"query": "TPR FPR"},
                    "build_rag_context": {"query": QUERY}}
V01_POLICY_SHA256 = "35b149eb10cf611bbed6c928f9ad039676e2f0a58c29992e595430eee06428e1"
V01_PROTOCOL_SHA256 = "f64da02a112ff99dd42301a192d1dbc3df5cf599939156a5f0c466bcf0662393"


def text(env):
    return json.dumps(env, ensure_ascii=False)


class RightsCoverageCase(S10Case):
    def world_with_rights_a(self, **changes):
        """``ai_world`` with paper A's rights assessment changed BEFORE its FTE attachment, so the member stays current (a later
        reassessment makes the FTE evidence stale, which already blocks everything)."""
        done = []

        def hook(pdf):
            if not done:
                done.append(pdf["asset_sha256"])
                self.supersede_rights(pdf["asset_sha256"], **changes)
        run, items, scope, _ = self.rworld([[DOC_A], [DOC_B]], embedder="mock", before_attach=hook)
        self.run_, self.items, self.scope = run, items, scope
        self.records("register_owner", owner="owner.one", reason="synthetic test owner", operator="op.owner")
        self.profile = self.make_profile()
        self.assertEqual(done, [items[0]["asset_sha256"]])

    def research_markers(self):
        """Identifiers and bibliographic facts of BOTH synthetic papers (what a metadata / anchor leak would carry)."""
        out = ["10.99999/", "Synthetic full-text study", "Synthetic Journal", CANARY_A, CANARY_B]
        for item in self.items:
            out += [item["candidate"]["paper_id"], item["asset_sha256"], item["document_version_id"]]
        out += [h["chunk_id"] for h in self.passages()["hits"]]
        return out

    def assertNoResearchData(self, *envs):
        markers = self.research_markers()
        for env in envs:
            blob = text(env).lower()
            for m in markers:
                self.assertNotIn(m.lower(), blob, f"research-derived data ({m}) released without current A8")

    def chunk_a(self):
        pa = self.items[0]["candidate"]["paper_id"]
        return next(h["chunk_id"] for h in self.passages()["hits"] if h["paper_id"] == pa)

    def every_tool(self, g=None):
        g = g or self.gateway()
        envs = {name: g.handle(name, dict(scope_id=self.scope, **args)) for name, args in TOOLS_WITH_SCOPE.items()}
        envs["get_verified_passage"] = g.handle("get_verified_passage", {"scope_id": self.scope, "chunk_id": self.chunk_a()})
        envs["list_retrieval_scopes"] = g.handle("list_retrieval_scopes", {})
        envs["get_integration_status"] = g.handle("get_integration_status", {})
        return envs


class EveryClassNeedsA8(RightsCoverageCase):
    def check_no_a8_matrix(self, consumer):
        self.ai_world(consumer=consumer)
        self.approve(ops=ALL_OPS, classes=ALL_CLASSES, paper_ids=[self.items[0]["candidate"]["paper_id"]])
        envs = self.every_tool()
        for name in list(TOOLS_WITH_SCOPE) + ["get_verified_passage"]:
            env = envs[name]
            self.assertIn(env["status"], ("NO_AUTHORIZED_EVIDENCE", "NO_MATCH") if name == "search_evidence" else ("NO_AUTHORIZED_EVIDENCE",), name)
            self.assertEqual(env["authorization"]["decision"], "DENY", name)
            self.assertFalse(any(r.startswith("RELEASED") for r in env["authorization"]["reason_codes"]), name)
            self.assertFalse(env["content_revealed"], name)
            self.assertFalse(env["results"].get("hits") or env["results"].get("passages"), name)
            for r in env["rights"]:
                self.assertEqual(r["A8"]["decision"], "DENY")
                self.assertEqual(r["member"], "WITHHELD_NOTHING_RELEASED")
                self.assertNotIn("RELEASED", r["classes"].values())
        self.assertIn("METADATA_NOT_RELEASED:A8_NOT_AUTHORIZED:NO_OWNER_DETERMINATION", envs["search_papers"]["excluded_by_reason"])
        self.assertIn("ANCHOR_NOT_RELEASED:A8_NOT_AUTHORIZED:NO_OWNER_DETERMINATION", envs["search_passages"]["excluded_by_reason"])
        self.assertEqual(envs["build_rag_context"]["results"]["anchor_only_hits"], 0)
        self.assertEqual(envs["get_verified_passage"]["authorization"]["gates_for_every_content_class"],
                         P.CONSUMERS[consumer]["release_gates"] + ["OAG-6"])
        self.assertNoResearchData(*envs.values())
        self.assertNoCanary(self.ai_tables_text())
        return envs

    def test_claude_desktop_no_a8_releases_no_class_on_any_tool(self):
        """IDs: S10-SC-63, S10-SC-64. Claude Desktop profile, OAG-6 approval for every operation and class of paper A, no A8 decision
        (external_ai_process UNDETERMINED): all content tools and get_verified_passage release nothing; METADATA and ANCHOR are counted as
        A8_NOT_AUTHORIZED; no title, DOI, venue, paper / version / asset id, chunk id or text appears in any of the seven tool results;
        rights entries keep the DENY but withhold the member's identifiers."""
        self.check_no_a8_matrix("CLAUDE_DESKTOP_LOCAL_MCP")

    def test_chatgpt_file_exchange_no_a8_and_a8_without_a7(self):
        """IDs: S10-SC-65, S10-SC-66. ChatGPT file-exchange profile: without A8 nothing of any class is released on any tool; with an A8
        owner determination but no A7 (redistribute) METADATA and ANCHOR are still denied (A7_NOT_AUTHORIZED); with A7 too, paper A's
        metadata is released (A8 and A7 ALLOW recorded) and paper B, not approved, stays hidden."""
        self.check_no_a8_matrix("CHATGPT_FILE_EXCHANGE")
        self.determine(self.items[0])
        papers = self.tool("search_papers", scope_id=self.scope, query="Synthetic")
        anchors = self.tool("search_passages", scope_id=self.scope, query=QUERY)
        self.assertIn("METADATA_NOT_RELEASED:A7_NOT_AUTHORIZED:NO_OWNER_DETERMINATION", papers["excluded_by_reason"])
        self.assertTrue(any(k.startswith("ANCHOR_NOT_RELEASED:A7_NOT_AUTHORIZED") for k in anchors["excluded_by_reason"]))
        self.assertNoResearchData(papers, anchors)
        self.determine(self.items[0], "redistribute")
        papers = self.tool("search_papers", scope_id=self.scope, query="Synthetic")
        self.assertEqual(papers["status"], "OK")
        self.assertEqual([h["paper_id"] for h in papers["results"]["hits"]], [self.items[0]["candidate"]["paper_id"]])
        self.assertEqual(papers["authorization"]["reason_codes"], ["RELEASED_UNDER_OAG6_APPROVAL_AND_CURRENT_A8_A7"])
        entry = papers["rights"][0]
        self.assertEqual((entry["A8"]["decision"], entry["A7"]["decision"], entry["classes"]), ("ALLOW", "ALLOW", {"METADATA": "RELEASED"}))
        self.assertNotIn(self.items[1]["candidate"]["paper_id"], text(papers))

    def test_explicit_a8_deny_blocks_metadata_and_anchor_and_cannot_be_widened(self):
        """IDs: S10-SC-67. Paper A's CURRENT rights assessment says external_ai_process DENY (member otherwise current): an approved
        METADATA + ANCHOR release returns neither metadata nor anchors (A8_NOT_AUTHORIZED:EXTERNAL_AI_PROCESS_DENY) and the owner cannot
        determine A8 for it."""
        self.world_with_rights_a(external_ai_process="DENY")
        with self.assertRaises(KBError):
            self.determine(self.items[0])
        self.approve(ops=["search_papers", "search_passages"], classes=["METADATA", "ANCHOR"], paper_ids=[self.items[0]["candidate"]["paper_id"]])
        papers = self.tool("search_papers", scope_id=self.scope, query="Synthetic")
        anchors = self.tool("search_passages", scope_id=self.scope, query=QUERY)
        self.assertEqual((papers["status"], anchors["status"]), ("NO_AUTHORIZED_EVIDENCE", "NO_AUTHORIZED_EVIDENCE"))
        self.assertIn("METADATA_NOT_RELEASED:A8_NOT_AUTHORIZED:EXTERNAL_AI_PROCESS_DENY", papers["excluded_by_reason"])
        self.assertIn("ANCHOR_NOT_RELEASED:A8_NOT_AUTHORIZED:EXTERNAL_AI_PROCESS_DENY", anchors["excluded_by_reason"])
        self.assertEqual(papers["rights"][0]["A8"]["basis"], "EXTERNAL_AI_PROCESS_DENY")
        self.assertNoResearchData(papers, anchors)

    def test_reassessment_to_a8_deny_stops_release_on_the_next_request(self):
        """IDs: S10-SC-76. After released METADATA and ANCHOR calls, paper A is reassessed with external_ai_process DENY: the next
        requests on the same long-lived gateway return no metadata and no anchor (the old determination is not carried over)."""
        self.ai_world()
        self.determine(self.items[0])
        self.approve(ops=["search_papers", "search_passages"], classes=["METADATA", "ANCHOR"], paper_ids=[self.items[0]["candidate"]["paper_id"]])
        g = self.gateway()
        self.assertEqual(g.handle("search_papers", {"scope_id": self.scope, "query": "Synthetic"})["status"], "OK")
        self.assertEqual(g.handle("search_passages", {"scope_id": self.scope, "query": QUERY})["status"], "OK")
        with self.writer() as kb:
            supersede_rights(kb, self.items[0]["asset_sha256"], external_ai_process="DENY")
        papers = g.handle("search_papers", {"scope_id": self.scope, "query": "Synthetic"})
        anchors = g.handle("search_passages", {"scope_id": self.scope, "query": QUERY})
        self.assertEqual((papers["status"], anchors["status"]), ("NO_AUTHORIZED_EVIDENCE", "NO_AUTHORIZED_EVIDENCE"))
        self.assertNoResearchData(papers, anchors)


class PositiveRelease(RightsCoverageCase):
    def test_metadata_and_anchor_released_under_current_a8(self):
        """IDs: S10-SC-68. With an A8 owner determination and an OAG-6 approval for METADATA + ANCHOR of paper A: search_papers returns
        paper A's facts only; search_passages and get_verified_passage return ANCHOR_ONLY hits for paper A without text; the rights entry
        records the A8 basis, determination id and each released class; the reason names the gates actually checked."""
        self.ai_world()
        det = self.determine(self.items[0])
        pa = self.items[0]["candidate"]["paper_id"]
        self.approve(ops=["search_papers", "search_passages", "get_verified_passage"], classes=["METADATA", "ANCHOR"], paper_ids=[pa])
        papers = self.tool("search_papers", scope_id=self.scope, query="Synthetic")
        self.assertEqual(papers["status"], "OK")
        self.assertEqual([h["paper_id"] for h in papers["results"]["hits"]], [pa])
        self.assertEqual(papers["authorization"]["reason_codes"], ["RELEASED_UNDER_OAG6_APPROVAL_AND_CURRENT_A8"])
        self.assertEqual(papers["rights"][0]["A8"], {"decision": "ALLOW", "basis": "OWNER_DETERMINATION", "determination_id": det["determination_id"]})
        self.assertEqual(papers["rights"][0]["classes"], {"METADATA": "RELEASED"})
        self.assertIn("METADATA_NOT_RELEASED:APPROVAL_DOES_NOT_COVER_MEMBER", papers["excluded_by_reason"])
        anchors = self.tool("search_passages", scope_id=self.scope, query=QUERY)
        self.assertEqual(anchors["status"], "OK")
        self.assertTrue(anchors["results"]["hits"])
        self.assertTrue(all(h["paper_id"] == pa and h["rights_view"] == "ANCHOR_ONLY" and h["text"] is None for h in anchors["results"]["hits"]))
        self.assertEqual(anchors["rights"][0]["classes"]["ANCHOR"], "RELEASED")
        one = self.tool("get_verified_passage", scope_id=self.scope, chunk_id=self.chunk_a())
        self.assertEqual((one["status"], one["results"]["hits"][0]["rights_view"]), ("OK", "ANCHOR_ONLY"))
        self.assertNoCanary(papers, anchors, one)
        self.assertNotIn(self.items[1]["candidate"]["paper_id"], text(papers) + text(anchors))

    def test_anchor_only_when_a5_denies_text_but_a8_allows(self):
        """IDs: S10-SC-69. Gate A5 denies showing text for paper A and an ANCHOR + PASSAGE_EXCERPT approval exists: without an A8
        decision no anchor is returned at all; with a current A8 owner determination the valid anchor-only use case remains (ANCHOR_ONLY
        hits for paper A, no text, TEXT_WITHHELD_GATE_A5, rights entry A5 DENY). The A5 DENY is injected at the rights gate: the synthetic
        pipeline cannot produce a CURRENT member with store_locally DENY (such an asset is not indexed, and a later reassessment makes
        the member's FTE evidence stale, which blocks everything)."""
        from scivetra_kb.rights import policy as rights_policy
        real_gate = rights_policy.gate

        def a5_denied(action, assessment, **kw):
            if action == "A5" and assessment and assessment["asset_sha256"] == self.items[0]["asset_sha256"]:
                return {"action": "A5", "decision": "DENY", "reason": "STORE_LOCALLY_DENY", "label": "synthetic"}
            return real_gate(action, assessment, **kw)
        self.ai_world()
        self.approve(ops=["search_passages"], classes=["ANCHOR", "PASSAGE_EXCERPT"], paper_ids=[self.items[0]["candidate"]["paper_id"]])
        with umock.patch.object(rights_policy, "gate", a5_denied):
            env = self.tool("search_passages", scope_id=self.scope, query=QUERY)
        self.assertEqual(env["status"], "NO_AUTHORIZED_EVIDENCE")
        self.assertIn("ANCHOR_NOT_RELEASED:A8_NOT_AUTHORIZED:NO_OWNER_DETERMINATION", env["excluded_by_reason"])
        self.assertNoResearchData(env)
        self.determine(self.items[0])
        with umock.patch.object(rights_policy, "gate", a5_denied):
            env = self.tool("search_passages", scope_id=self.scope, query=QUERY)
        self.assertEqual(env["status"], "OK")
        self.assertIn("TEXT_WITHHELD_GATE_A5", env["excluded_by_reason"])
        self.assertTrue(env["results"]["hits"] and all(h["rights_view"] == "ANCHOR_ONLY" and h["text"] is None for h in env["results"]["hits"]))
        self.assertEqual(env["rights"][0]["A5"], "DENY")
        self.assertFalse(env["document_content_revealed"])
        self.assertNoCanary(env)


class Freshness(RightsCoverageCase):
    def released(self, g):
        return bool(g.handle("search_papers", {"scope_id": self.scope, "query": "Synthetic"})["results"].get("hits"))

    def test_metadata_follows_a8_revocation_expiry_and_reassessment(self):
        """IDs: S10-SC-70, S10-SC-71, S10-SC-72. One long-lived gateway with a METADATA approval: revoking the A8 determination stops
        metadata release on the next request (DETERMINATION_REVOKED) and a new determination reinstates it; an expired determination
        stops it (DETERMINATION_EXPIRED, the newest determination's reason); an approval revocation stops it; a superseding rights
        assessment withholds it and makes the determination stale (DETERMINATION_FOR_OTHER_RIGHTS_ASSESSMENT)."""
        self.ai_world()
        self.determine(self.items[0])
        pa = self.items[0]["candidate"]["paper_id"]
        approval = self.approve(ops=["search_papers"], classes=["METADATA"], hours=48, paper_ids=[pa])
        g = self.gateway()
        self.assertTrue(self.released(g))
        with self.reader() as kb:
            det = kb.one("SELECT determination_id FROM aig_rights_determination")["determination_id"]
        self.records("revoke", target_id=det, reason="licence questioned", operator="op.owner")
        env = g.handle("search_papers", {"scope_id": self.scope, "query": "Synthetic"})
        self.assertIn("METADATA_NOT_RELEASED:A8_NOT_AUTHORIZED:DETERMINATION_REVOKED", env["excluded_by_reason"])
        self.assertNoResearchData(env)
        self.clock.advance(seconds=1)
        self.determine(self.items[0], hours=1)
        self.assertTrue(self.released(g))
        self.clock.advance(hours=2)
        env = g.handle("search_papers", {"scope_id": self.scope, "query": "Synthetic"})
        self.assertIn("METADATA_NOT_RELEASED:A8_NOT_AUTHORIZED:DETERMINATION_EXPIRED", env["excluded_by_reason"])
        self.assertNoResearchData(env)
        self.determine(self.items[0])
        self.assertTrue(self.released(g))
        self.records("revoke", target_id=approval["approval_id"], reason="stop", operator="op.owner")
        env = g.handle("search_papers", {"scope_id": self.scope, "query": "Synthetic"})
        self.assertEqual(env["status"], "DENIED")
        self.assertNoResearchData(env)
        self.approve(ops=["search_papers"], classes=["METADATA"], paper_ids=[pa])
        self.assertTrue(self.released(g))
        with self.writer() as kb:
            supersede_rights(kb, self.items[0]["asset_sha256"], redistribute="UNDETERMINED")
        env = g.handle("search_papers", {"scope_id": self.scope, "query": "Synthetic"})
        self.assertEqual(env["status"], "NO_AUTHORIZED_EVIDENCE")
        self.assertNoResearchData(env)
        with self.reader() as kb:
            a = kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? ORDER BY rowid DESC LIMIT 1", (self.items[0]["asset_sha256"],))
            self.assertEqual(OwnerRecords(kb).determination("external_ai_process", a)[1], "DETERMINATION_FOR_OTHER_RIGHTS_ASSESSMENT")

    def test_metadata_requires_a_current_member_source(self):
        """IDs: S10-SC-73. With A8 and a METADATA approval, a corrupted CAS parse artifact makes the member not current: its metadata is
        withheld (METADATA_NOT_RELEASED:MEMBER_NOT_CURRENT:BLOCKED:SOURCE_INTEGRITY...) instead of riding on an assessment of a source that
        can no longer be verified; once restored, it is released again."""
        self.ai_world()
        self.determine(self.items[0])
        self.approve(ops=["search_papers"], classes=["METADATA"], paper_ids=[self.items[0]["candidate"]["paper_id"]])
        with self.reader() as kb:
            art = kb.one("SELECT artifact_sha256 FROM parse_artifact WHERE asset_sha256=?", (self.items[0]["asset_sha256"],))["artifact_sha256"]
            path = cas_file(kb, art)
        original = path.read_bytes()
        path.write_bytes(original[:-5] + b"XXXXX")
        try:
            env = self.tool("search_papers", scope_id=self.scope, query="Synthetic")
        finally:
            path.write_bytes(original)
        self.assertTrue(any(k.startswith("METADATA_NOT_RELEASED:MEMBER_NOT_CURRENT:BLOCKED:SOURCE_INTEGRITY") for k in env["excluded_by_reason"]))
        self.assertNoResearchData(env)
        self.assertEqual(self.tool("search_papers", scope_id=self.scope, query="Synthetic")["status"], "OK")


class PolicyAuditAndTransports(RightsCoverageCase):
    def test_v01_policy_records_are_void_under_policy_2(self):
        """IDs: S10-SC-74. The policy meaning changed (S10-F01), so its id is scivetra-ai-consumer-policy/2 with a new SHA-256 that says
        there is no metadata exemption; a profile and an OAG-6 approval recorded under the v01 policy / protocol hashes no longer authorize
        anything (PROFILE_POLICY_CHANGED, APPROVAL_POLICY_CHANGED, APPROVAL_PROTOCOL_CHANGED); nothing silently carries over."""
        doc = P.policy_document()
        self.assertEqual(P.POLICY_ID, "scivetra-ai-consumer-policy/2")
        self.assertNotEqual(P.POLICY_SHA256, V01_POLICY_SHA256)
        self.assertNotEqual(P.PROTOCOL_SHA256, V01_PROTOCOL_SHA256)
        self.assertEqual((doc["metadata_exemption"], doc["member_without_current_rights_assessment"]), ("NONE", "DENY"))
        self.assertEqual(doc["research_derived"], ALL_CLASSES)
        self.ai_world()
        self.determine(self.items[0])
        with umock.patch.object(P, "POLICY_SHA256", V01_POLICY_SHA256), umock.patch.object(P, "PROTOCOL_SHA256", V01_PROTOCOL_SHA256):
            old_profile = self.make_profile(label="v01 profile")
            old = self.approve(profile=old_profile, ops=["search_papers"], classes=["METADATA"])
        env = self.tool("search_papers", profile=old_profile, scope_id=self.scope, query="Synthetic")
        self.assertEqual((env["status"], env["authorization"]["reason_codes"]), ("DENIED", ["PROFILE_POLICY_CHANGED"]))
        self.assertNoResearchData(env)
        with self.reader() as kb:
            row = kb.one("SELECT * FROM aig_consumer_profile WHERE profile_id=?", (old_profile,))
            approval = kb.one("SELECT * FROM aig_release_approval WHERE approval_id=?", (old["approval_id"],))
            problems = OwnerRecords(kb).approval_problems(approval, dict(row), RetrievalService(kb)._scope(self.scope))
        self.assertIn("APPROVAL_POLICY_CHANGED", problems)
        self.assertIn("APPROVAL_PROTOCOL_CHANGED", problems)

    def test_audit_failure_releases_no_metadata(self):
        """IDs: S10-SC-75. With A8 and a METADATA approval, an injected audit I/O failure, disk full or commit failure returns ERROR
        AUDIT_UNAVAILABLE with no metadata, ids or scope, and leaves no audit row; a healthy call then releases paper A's metadata, whose
        audit row keeps the full rights entry."""
        self.ai_world()
        self.determine(self.items[0])
        self.approve(ops=["search_papers"], classes=["METADATA"], paper_ids=[self.items[0]["candidate"]["paper_id"]])
        for fault in ("audit_unavailable", "audit_disk_full", "fail_after_audit_insert"):
            env = self.gateway(faults={fault}).handle("search_papers", {"scope_id": self.scope, "query": "Synthetic"})
            self.assertEqual((env["status"], env["results"], env["scope_id"]), ("ERROR", {}, None))
            self.assertNoResearchData(env)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.tool("search_papers", scope_id=self.scope, query="Synthetic")["status"], "OK")
        rights = json.loads(self.events()[-1]["rights_json"])
        self.assertEqual((rights[0]["paper_id"], rights[0]["classes"]), (self.items[0]["candidate"]["paper_id"], {"METADATA": "RELEASED"}))

    def test_mcp_stdio_child_process_enforces_a8_for_metadata(self):
        """IDs: S10-MCP-13. Over a real stdio child process: an approved METADATA release without A8 returns no hits and no paper
        identifiers; after the owner's A8 determination (recorded while the server runs) the next call returns paper A's metadata."""
        self.ai_world()
        self.approve(ops=["search_papers"], classes=["METADATA"], paper_ids=[self.items[0]["candidate"]["paper_id"]], real_clock=True)
        c = StdioMcpClient(self.root, self.profile)
        self.addCleanup(lambda: c.proc.poll() is None and c.proc.kill())
        c.initialize()
        denied = c.call("search_papers", {"scope_id": self.scope, "query": "Synthetic"})["result"]
        self.assertEqual(denied["structuredContent"]["status"], "NO_AUTHORIZED_EVIDENCE")
        self.assertNoResearchData(denied)
        self.determine(self.items[0], real_clock=True)
        ok = c.call("search_papers", {"scope_id": self.scope, "query": "Synthetic"})["result"]["structuredContent"]
        self.assertEqual([h["paper_id"] for h in ok["results"]["hits"]], [self.items[0]["candidate"]["paper_id"]])
        code, _, err = c.close()
        self.assertEqual((code, err), (0, b""))

    def test_file_exchange_bundle_needs_a8_and_a7(self):
        """IDs: S10-XCH-15. A ChatGPT bundle export with an approval for every class but no A8 / A7 writes nothing; with A8 only it is
        still refused; with A8 and A7 the bundle is written and verifies RELEASE_STILL_AUTHORIZED."""
        self.ai_world(consumer="CHATGPT_FILE_EXCHANGE")
        self.approve(paper_ids=[self.items[0]["candidate"]["paper_id"]])
        out = Path(self.tmp, "exports")
        out.mkdir()

        def export(name):
            return exchange.export_bundle(self.open_kb, profile_id=self.profile, scope_id=self.scope, query=QUERY, out_dir=str(out / name),
                                          operator="op.owner")
        for name, step in (("none", None), ("a8", "external_ai_process")):
            if step:
                self.determine(self.items[0], step)
            res = export(name)
            self.assertEqual((res["published"], res["files"], res["gateway"]["status"]), (False, {}, "NO_AUTHORIZED_EVIDENCE"))
            self.assertFalse((out / name).exists() and any((out / name).iterdir()))
            self.assertNoResearchData(res)
        self.determine(self.items[0], "redistribute")
        self.assertTrue(export("ok")["published"])
        self.assertEqual(exchange.verify_bundle(self.open_kb, bundle_dir=str(out / "ok"))["verdict"], "RELEASE_STILL_AUTHORIZED")


if __name__ == "__main__":
    unittest.main()
