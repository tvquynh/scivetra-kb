"""S09 rights-adversarial suite: A3 before indexing, A5 / A2 revocation after indexing, stale S05 eligibility and parse artifacts,
forged / foreign / corrupt / lost index, interrupted builds, refused consumers, S08 withheld text and human rationales never indexed,
no writes outside rt_*, no query or document text in the audit. Synthetic PDFs only, offline."""
import json
import os
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest import mock as umock

from tests.s09_helpers import OTHER_DOC, RETRIEVAL_DOC, S09Case
from scivetra_kb.core.backup import create_backup, restore_backup
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import KBError
from scivetra_kb.core.manifest import table_content_sha256
from scivetra_kb.parse.service import ParseService
from scivetra_kb.retrieval import index as ridx
from scivetra_kb.retrieval.service import RetrievalService

PHRASE = "packed samples were excluded from the evaluation"
WORD = "packed"


def index_bytes(root):
    return (Path(root) / "index" / "fts.sqlite").read_bytes()


def index_rows(root, sql, params=()):
    con = sqlite3.connect(Path(root, "index", "fts.sqlite"))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def all_index_text(root):
    con = sqlite3.connect(Path(root, "index", "fts.sqlite"))
    try:
        out = []
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            out.extend(repr(r) for r in con.execute(f"SELECT * FROM \"{name}\""))
        return "\n".join(out)
    finally:
        con.close()


class RightsAtIndexAndQuery(S09Case):
    def test_a3_denied_before_indexing_stores_no_text(self):
        """IDs: S09-A3-BEFORE-INDEX, S09-A3-NO-VECTORS, S09-ANCHOR-ONLY-VIEW. When gate A3 denies storing excerpts before the build, the
        member's chunks are recorded with anchors and SHA-256 only: no FTS row, no vector, and no word of the text anywhere in the index
        file bytes or tables; text search cannot find it; get_passage returns ANCHOR_ONLY without text; the context abstains."""
        def deny(pdf):
            self.supersede_rights(pdf["asset_sha256"], store_locally="DENY", local_process="ALLOW")
        run, items, scope, built = self.rworld([[RETRIEVAL_DOC]], before_attach=deny)
        self.assertEqual((built["counts"]["chunks_text_indexed"], built["counts"]["chunks_anchor_only"]), (0, 8))
        self.assertEqual(built["counts"]["vectors_computed"], 0)
        self.assertNotIn(WORD.encode(), index_bytes(self.root))
        self.assertNotIn(WORD, all_index_text(self.root).lower())
        res = self.passages(scope, PHRASE)
        self.assertFalse(any("text" in h for h in res["hits"]))
        chunk = index_rows(self.root, "SELECT chunk_id FROM chunk LIMIT 1")[0][0]
        got = self.rcall("get_passage", scope_id=scope, chunk_id=chunk, consumer="LOCAL_CLI", operator="op.r")
        self.assertEqual(got["hit"]["rights_view"], "ANCHOR_ONLY")
        self.assertNotIn("text", got["hit"])
        ctx = self.rcall("rag_context", scope_id=scope, query=PHRASE, consumer="LOCAL_CLI", operator="op.r")
        self.assertEqual(ctx["passages"], [])
        self.assertIn(ctx["outcome"], ("NO_MATCH", "NO_AUTHORIZED_EVIDENCE"))

    def test_a5_revoked_after_indexing_never_returns_old_text(self):
        """IDs: S09-A5-REVOKE-STALE-INDEX, S09-PER-HIT-RECHECK, S09-RAG-NO-AUTHORIZED. Built while A5 allowed, then the store-locally permission
        is withdrawn: the old index still matches, but no search, passage, evidence or context call returns text (ANCHOR_ONLY or excluded
        with counted reasons), the context is NO_AUTHORIZED_EVIDENCE, the audit records content_revealed = 0, and a rebuild removes the
        text from the file."""
        run, items, scope, _ = self.rworld()
        self.assertIn(PHRASE, json.dumps(self.passages(scope, PHRASE)))
        self.supersede_rights(items[0]["asset_sha256"], store_locally="DENY")
        res = self.passages(scope, PHRASE)
        self.assertNotIn(PHRASE, json.dumps(res))
        self.assertTrue(res["excluded_by_reason"])
        ctx = self.rcall("rag_context", scope_id=scope, query=PHRASE, consumer="LOCAL_CLI", operator="op.r")
        self.assertNotIn(PHRASE, json.dumps(ctx))
        self.assertTrue(all(p["anchor"]["paper_id"] != items[0]["candidate"]["paper_id"] for p in ctx["passages"]))
        revoked = items[0]["candidate"]["paper_id"]
        self.assertFalse(any(h["rights_view"] == "FULL_EXCERPT" and h["paper_id"] == revoked for h in res["hits"]))
        log = self.rread("access_log", scope_id=scope)["events"]
        revoked_chunks = {r[0] for r in index_rows(self.root, "SELECT chunk_id FROM chunk WHERE paper_id=?", (revoked,))}
        for e in log[1:]:
            self.assertFalse(any(r["rights_view"] == "FULL_EXCERPT" and r["chunk_id"] in revoked_chunks for r in e["returned"]))
        self.assertNotIn(PHRASE, json.dumps(log))
        rebuilt = self.rcall("build_index", operator="op.index", embedder="mock")
        self.assertGreater(sum(rebuilt["excluded_by_reason"].values()), 0)
        self.assertNotIn(WORD.encode(), index_bytes(self.root))

    def test_a2_denied_and_s05_eligibility_change_exclude_hits(self):
        """IDs: S09-A2-REVOKE, S09-S05-STALE, S09-EXCLUDED-COUNTS. Local processing withdrawn (A2) blocks the member entirely; a changed S05
        decision makes the member STALE; both are excluded at query time with counted reasons, without rebuilding."""
        run, items, scope, _ = self.rworld()
        self.supersede_rights(items[0]["asset_sha256"], local_process="DENY")
        res = self.passages(scope, "EMBER test split TPR")
        self.assertFalse(any(h["paper_id"] == items[0]["candidate"]["paper_id"] for h in res["hits"]))
        self.assertTrue(any(k.startswith("BLOCKED:RIGHTS_") for k in res["excluded_by_reason"]))
        cand = items[1]["candidate"]
        with self.reader() as kb:
            prior = kb.one("SELECT d.decision_id FROM fte_decision d JOIN fte_reviewer v USING (reviewer_id) WHERE d.candidate_id=? AND v.label='OPERATOR:rev.a'",
                           (cand["candidate_id"],))["decision_id"]
        self.svc_call("decide", candidate_id=cand["candidate_id"], reviewer="rev.a", outcome="FT_EXCLUDE", reasons=["WRONG_STUDY_DESIGN"], supersedes=prior)
        res = self.passages(scope, "AndroZoo hold-out accuracy")
        self.assertFalse(any(h["paper_id"] == cand["paper_id"] for h in res["hits"]))
        self.assertTrue(any(k.startswith("STALE:ELIGIBILITY_CHANGED") for k in res["excluded_by_reason"]))

    def test_superseded_artifact_and_link_mismatch_are_not_citable(self):
        """IDs: S09-ARTIFACT-SUPERSEDED, S09-IDENTITY-MISMATCH. A newer parse artifact for the asset makes old chunks STALE; a human REJECT of
        the link makes them BLOCKED; neither is returned or cited, and the context abstains for that paper."""
        run, items, scope, _ = self.rworld()
        with self.writer() as kb:
            ParseService(kb, parser_site=self.site, allow_language_level_network_guard=True, limits={"max_chars_per_page": 300_000}).parse(
                asset_sha256=items[0]["asset_sha256"], link_id=items[0]["link_id"], purpose="RESEARCH_PARSE", operator="op.parse", recheck=True)
        res = self.passages(scope, PHRASE)
        self.assertIn("STALE:PARSE_ARTIFACT_SUPERSEDED", res["excluded_by_reason"])
        self.assertNotIn(PHRASE, json.dumps(res))
        from scivetra_kb.assets.service import AssetService
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=items[1]["link_id"], decision="REJECT", operator="op.lib", reason="wrong paper after all")
        res = self.passages(scope, "AndroZoo hold-out accuracy")
        self.assertIn("BLOCKED:IDENTITY_NOW_MISMATCH", res["excluded_by_reason"])
        self.assertEqual(res["hits"], [])

    def test_forged_chunk_and_cross_version_substitution_fail_anchor_checks(self):
        """IDs: S09-ANCHOR-TAMPER, S09-CROSS-VERSION, S09-INDEX-TAMPER. A chunk row with altered offsets fails CAS anchor verification; a row
        claiming another member's artifact is STALE:CHUNK_NOT_FOR_MEMBER_ARTIFACT; editing the published index file breaks its manifest
        (INDEX_CORRUPT) and a self-consistent forged file still differs from the recorded build (INDEX_CORRUPT / INDEX_FOREIGN)."""
        run, items, scope, _ = self.rworld()
        with self.writer() as kb:
            svc = RetrievalService(kb)
            sc = svc._scope(scope)
            members = {m["paper_id"]: m for m in sc["members"]}
            con = sqlite3.connect(Path(self.root, "index", "fts.sqlite"))
            cur = con.execute("SELECT * FROM chunk WHERE paper_id=? LIMIT 1", (items[0]["candidate"]["paper_id"],))
            row = dict(zip([c[0] for c in cur.description], cur.fetchone()))
            con.close()
            excluded = {}
            self.assertIsNone(svc._authorize_chunk(dict(row, char_end=row["char_end"] - 1), members, excluded))
            other = members[items[1]["candidate"]["paper_id"]]
            self.assertIsNone(svc._authorize_chunk(dict(row, paper_id=other["paper_id"]), members, excluded))
            self.assertEqual(excluded, {"ANCHOR_VERIFY_FAILED": 1, "STALE:CHUNK_NOT_FOR_MEMBER_ARTIFACT": 1})
        path = Path(self.root, "index", "fts.sqlite")
        con = sqlite3.connect(path)
        con.execute("UPDATE chunk SET char_start = char_start + 1 WHERE rid = 1")
        con.commit()
        con.close()
        with self.assertRaises(KBError) as ctx:
            self.passages(scope, PHRASE)
        self.assertEqual(ctx.exception.code, "INDEX_CORRUPT")
        con = sqlite3.connect(path)
        con.execute("UPDATE meta SET value=? WHERE key='manifest_sha256'", (ridx.manifest(con),))
        con.commit()
        con.close()
        with self.assertRaises(KBError) as ctx:
            self.passages(scope, PHRASE)
        self.assertIn(ctx.exception.code, ("INDEX_CORRUPT", "INDEX_FOREIGN"))
        self.assertFalse(self.rread("verify_index")["valid"])

    def test_foreign_corrupt_lost_index_and_restore(self):
        """IDs: S09-INDEX-FOREIGN, S09-INDEX-CORRUPT, S09-INDEX-LOST, S09-BACKUP-NO-INDEX, S09-REBUILD-AFTER-RESTORE. An index copied from
        another KB is INDEX_FOREIGN; garbage bytes are INDEX_CORRUPT; a deleted index is INDEX_NOT_BUILT; none of them returns anything.
        Backups do not carry the index; after restore, status is INDEX_NOT_BUILT and a rebuild reproduces the same manifest."""
        run, items, scope, built = self.rworld()
        index = Path(self.root, "index", "fts.sqlite")
        saved = index.read_bytes()
        backup = create_backup(self.root, os.path.join(self.tmp, "backups"), clock=self.clock)
        restored = os.path.join(self.tmp, "restored")
        restore_backup(os.path.join(self.tmp, "backups", backup["backup_name"]), restored)
        self.assertFalse(Path(restored, "index").exists())
        with self.writer() as kb:
            pass
        from scivetra_kb.core.db import open_writer
        with open_writer(restored) as kb:
            svc = RetrievalService(kb)
            self.assertEqual(svc.index_status()["state"], "INDEX_NOT_BUILT")
            rebuilt = svc.build_index(operator="op.index", embedder="mock")
        self.assertEqual(rebuilt["manifest_sha256"], built["manifest_sha256"])
        other = os.path.join(self.tmp, "other_kb")
        from scivetra_kb.core.db import init_kb
        init_kb(other, clock=self.clock, ids=self.ids)
        Path(other, "index").mkdir()
        Path(other, "index", "fts.sqlite").write_bytes(saved)
        with open_writer(other) as kb:
            self.assertEqual(RetrievalService(kb).index_status()["state"], "INDEX_FOREIGN")
        index.write_bytes(b"SQLite format 3\x00" + os.urandom(4096))
        with self.assertRaises(KBError) as ctx:
            self.passages(scope, PHRASE)
        self.assertEqual(ctx.exception.code, "INDEX_CORRUPT")
        index.unlink()
        with self.assertRaises(KBError) as ctx:
            self.passages(scope, PHRASE)
        self.assertEqual(ctx.exception.code, "INDEX_NOT_BUILT")
        self.rcall("build_index", operator="op.index", embedder="mock")
        self.assertEqual(self.passages(scope, PHRASE)["hits"][0]["rights_view"], "FULL_EXCERPT")

    def test_interrupted_builds_never_publish(self):
        """IDs: S09-BUILD-INTERRUPTED, S09-BUILD-ATOMIC, S09-BUILD-RESUME. A crash while chunking, before recording or before the atomic
        rename leaves the previous published index serving queries and records nothing (or only an unpublished build); a kept partial file
        is reported as an incomplete build, never read, and removed by the next build, which publishes the same manifest as before."""
        run, items, scope, built = self.rworld()
        before = index_bytes(self.root)
        for fault in ("after_chunks", "before_record", "before_publish"):
            with self.writer() as kb:
                with self.assertRaises(RuntimeError):
                    RetrievalService(kb, faults={fault}).build_index(operator="op.index", embedder="mock")
            self.assertEqual(index_bytes(self.root), before, fault)
            self.assertEqual(self.passages(scope, PHRASE)["index_build_id"], built["build_id"])
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM rt_index_build")["n"], 2)       # the before_publish attempt was recorded, never published
        with self.writer() as kb:
            with self.assertRaises(RuntimeError):
                RetrievalService(kb, faults={"before_publish", "keep_partial"}).build_index(operator="op.index", embedder="mock")
        st = self.rread("index_status")
        self.assertEqual((st["state"], st["build_id"], len(st["incomplete_builds"])), ("PUBLISHED", built["build_id"], 1))
        again = self.rcall("build_index", operator="op.index", embedder="mock")
        self.assertEqual((again["manifest_sha256"], again["leftover_builds_removed"]), (built["manifest_sha256"], 1))
        self.assertEqual(self.rread("index_status")["incomplete_builds"], [])

    def test_fts5_missing_is_not_supported(self):
        """IDs: S09-FTS5-DETECTION. Without FTS5 the build and every search fail with FTS5_NOT_SUPPORTED instead of a false success."""
        run, items, scope, _ = self.rworld()
        with umock.patch.object(ridx, "fts5_available", return_value=False):
            for call in (lambda: self.rcall("build_index", operator="op.index"), lambda: self.passages(scope, PHRASE)):
                with self.assertRaises(KBError) as ctx:
                    call()
                self.assertEqual(ctx.exception.code, "FTS5_NOT_SUPPORTED")


class ConsumersAndLeaks(S09Case):
    def test_remote_and_desktop_consumers_are_refused(self):
        """IDs: S09-CONSUMER-LOCAL-ONLY, S09-NO-DOWNGRADE, S09-A8-OAG6-PRESERVED. claude-desktop, chatgpt-desktop, MCP, remote API, export and any
        unknown or differently-cased consumer are refused with CONSUMER_NOT_PERMITTED on every entry point (no text-free downgrade), and
        nothing is audited for them."""
        run, items, scope, _ = self.rworld()
        calls = {"search_passages": {"query": PHRASE}, "search_papers": {"query": "Synthetic"}, "search_evidence": {"query": "TPR"},
                 "rag_context": {"query": PHRASE}, "get_passage": {"chunk_id": "rch_x"}}
        for consumer in ("claude-desktop", "chatgpt-desktop", "mcp", "remote-api", "desktop-export", "local_cli", "LOCAL_CLI ", "", None):
            for method, kw in calls.items():
                with self.assertRaises(KBError, msg=(consumer, method)) as ctx:
                    self.rcall(method, scope_id=scope, consumer=consumer, operator="op.r", **kw)
                self.assertEqual(ctx.exception.code, "CONSUMER_NOT_PERMITTED")
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM rt_access_event")["n"], 0)

    def test_s08_withheld_text_and_human_rationales_never_indexed(self):
        """IDs: S09-S08-WITHHELD-TEXT, S09-NO-RATIONALE-INDEX, S09-NO-SCOPE-TEXT-INDEX. A3-withheld S08 text fields (WITHHELD_GATE_A3_DENIED)
        and S08 reviewer / adjudicator rationales, reason codes and scope labels never enter the index or any search result, even when
        the rationale was legitimately stored."""
        marker = "zebracornflakequasar"
        run, items = self.ev_world([[RETRIEVAL_DOC]])
        ev_scope = self.ev_scope(run, items)
        self.ecall("build_packs", scope_id=ev_scope, operator="op.owner")
        self.ecall("run_mock", scope_id=ev_scope, operator="op.runner")
        self.roster("rev.one")
        for d in self.drafts(ev_scope):
            self.ecall("verify", draft_id=d["draft_id"], operator="op.v")
            self.ecall("reveal", draft_id=d["draft_id"], reviewer="rev.one")
            self.ecall("review", draft_id=d["draft_id"], reviewer="rev.one", decision="ACCEPT", rationale="reviewed " + marker,
                       reason_codes=["ZEBRACORNFLAKEQUASAR"])
        scope = self.rscope(run, items, label="retrieval " + marker)
        built = self.rcall("build_index", operator="op.index", embedder="mock")
        self.assertGreater(built["counts"]["evidence"], 0)
        self.assertNotIn(marker.encode(), index_bytes(self.root))
        for method in ("search_passages", "search_evidence", "search_papers", "rag_context"):
            self.assertNotIn(marker, json.dumps(self.rcall(method, scope_id=scope, query=marker, consumer="LOCAL_CLI", operator="op.r")).lower())
        # A3 denied at extraction: the S08 text value is withheld, its drafts are never accepted, and the index holds no trace of it
        def deny(pdf):
            self.supersede_rights(pdf["asset_sha256"], store_locally="DENY", local_process="ALLOW")
        run2, items2 = self.ev_world([[RETRIEVAL_DOC]] * 3, before_attach=deny)
        ev2 = self.ev_scope(run2, items2[2:])
        self.ecall("build_packs", scope_id=ev2, operator="op.owner")
        self.ecall("run_mock", scope_id=ev2, operator="op.runner")
        lim = [d for d in self.drafts(ev2) if d["evidence_class"] == "LIMITATION"][0]
        self.assertEqual(lim["text_storage"], "WITHHELD_GATE_A3_DENIED")
        scope2 = self.rscope(run2, items2[2:])
        self.rcall("build_index", operator="op.index", embedder="mock")
        res = self.rcall("search_evidence", scope_id=scope2, query=PHRASE, consumer="LOCAL_CLI", operator="op.r")
        self.assertEqual(res["hits"], [])
        self.assertNotIn(PHRASE, json.dumps(self.passages(scope2, PHRASE)))

    def test_no_writes_outside_rt_and_no_text_in_audit(self):
        """IDs: S09-NO-AUTHORITY-WRITES, S09-AUDIT-NO-TEXT, S09-DOCTOR. A full S09 cycle leaves every S05, S07, S08, release-import and other
        non-rt table byte-identical; the access log records operation, consumer, mode, build, query SHA-256, returned ids, views and
        exclusions but never query or document text; doctor --full is clean with zero retrieval invariant violations."""
        run, items = self.ev_world([[RETRIEVAL_DOC], [OTHER_DOC]])
        with self.reader() as kb:
            names = [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT GLOB 'rt_*'")]
            before = {t: table_content_sha256(kb.connection, t) for t in names}
        scope = self.rscope(run, items)
        self.rcall("build_index", operator="op.index", embedder="mock")
        q = "packed samples TPR EMBER"
        res = self.passages(scope, q)
        self.rcall("search_papers", scope_id=scope, query=q, consumer="LOCAL_CLI", operator="op.r")
        self.rcall("search_evidence", scope_id=scope, query=q, consumer="LOCAL_CLI", operator="op.r")
        self.rcall("get_passage", scope_id=scope, chunk_id=res["hits"][0]["chunk_id"], consumer="LOCAL_CLI", operator="op.r")
        self.rcall("rag_context", scope_id=scope, query=q, consumer="LOCAL_CLI", operator="op.r")
        with self.reader() as kb:
            after = {t: table_content_sha256(kb.connection, t) for t in names}
            events = json.dumps(kb.all("SELECT * FROM rt_access_event"))
        self.assertEqual(sorted(t for t in names if before[t] != after[t]), [])
        self.assertNotIn(q, events)
        for line in RETRIEVAL_DOC + OTHER_DOC:
            self.assertNotIn(line, events)
        self.assertIn('"content_revealed": 1', events)
        report = doctor(self.root, full=True)
        self.assertEqual(report["problems"], [])
        self.assertEqual(set(report["retrieval_invariants"].values()), {0})


if __name__ == "__main__":
    unittest.main()
