"""S09 v02 regressions for the independent architectural review (S09-F01, S09-F02, S09-F03).

Every F01 / F03 test drives ONE long-lived ``RetrievalService`` instance on one writer connection: it is warmed with an authorized
call first, the KB / CAS is then changed underneath it, and the same instance must see the change on its next public call.
F02 tests put a scope member behind more out-of-scope matches than the candidate window. Synthetic PDFs only, offline."""
import json
import os
import sqlite3
import stat
import unittest
from pathlib import Path
from unittest import mock as umock

from tests.s09_helpers import OTHER_DOC, RETRIEVAL_DOC, S09Case
from scivetra_kb.eligibility.service import FTEService
from scivetra_kb.retrieval import index as ridx
from scivetra_kb.retrieval import service as rservice
from scivetra_kb.retrieval.service import RetrievalService

PHRASE = "packed samples were excluded from the evaluation"
LOCAL = dict(consumer="LOCAL_CLI", operator="op.reader")


def supersede_rights(kb, asset_sha256, **changes):
    """A newer rights assessment for the asset, written on the service's own connection."""
    old = kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? ORDER BY rowid DESC LIMIT 1", (asset_sha256,))
    row = dict(old, assessment_id=kb.ids.new("rga"), supersedes_assessment_id=old["assessment_id"], assessed_by="OPERATOR:owner",
               recorded_by="OPERATOR:owner", semantic_sha256=kb.ids.new("x").ljust(64, "0")[:64], **changes)
    with kb.transaction():
        kb.insert("rights_assessment", row)


def shown(result) -> str:
    return json.dumps(result)


def cas_file(kb, digest) -> Path:
    path = kb.cas.path_for(digest)
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    return path


def index_rows(root, sql, params=()):
    con = sqlite3.connect(Path(root, "index", "fts.sqlite"))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


class F01LongLivedAuthorization(S09Case):
    def test_rights_revocation_reaches_a_warmed_instance(self):
        """IDs: S09-V02-F01-A5, S09-V02-F01-A2. One instance returns PHRASE (search, passage, context); store-locally is then withdrawn
        (A5) and the SAME instance returns no text anywhere (the member is withheld or excluded with counted reasons); local processing
        is withdrawn (A2) and the member is BLOCKED, status counts it stale and member_problems reports it; the audit shows content
        revealed only before the revocation."""
        run, items, scope, _ = self.rworld()
        asset = items[0]["asset_sha256"]
        with self.writer() as kb:
            svc = RetrievalService(kb)
            first = svc.search_passages(scope_id=scope, query=PHRASE, **LOCAL)
            hit = next(h for h in first["hits"] if PHRASE in h.get("text", ""))
            self.assertEqual(hit["rights_view"], "FULL_EXCERPT")
            self.assertEqual(svc.rag_context(scope_id=scope, query=PHRASE, **LOCAL)["outcome"], "CONTEXT_READY")
            self.assertEqual(svc.get_passage(scope_id=scope, chunk_id=hit["chunk_id"], **LOCAL)["hit"]["rights_view"], "FULL_EXCERPT")
            self.assertEqual(svc.index_status()["stale_artifacts"], 0)

            supersede_rights(kb, asset, store_locally="DENY")
            res = svc.search_passages(scope_id=scope, query=PHRASE, **LOCAL)
            self.assertNotIn(PHRASE, shown(res))
            self.assertTrue(res["excluded_by_reason"])
            self.assertFalse(any(h["paper_id"] == items[0]["candidate"]["paper_id"] and h["rights_view"] == "FULL_EXCERPT" for h in res["hits"]))
            ctx = svc.rag_context(scope_id=scope, query=PHRASE, **LOCAL)
            self.assertNotIn(PHRASE, shown(ctx))
            self.assertEqual(ctx["outcome"], "NO_AUTHORIZED_EVIDENCE")
            got = svc.get_passage(scope_id=scope, chunk_id=hit["chunk_id"], **LOCAL)["hit"]
            self.assertTrue(got is None or (got["rights_view"] == "ANCHOR_ONLY" and "text" not in got))

            supersede_rights(kb, asset, store_locally="ALLOW", local_process="DENY")
            res = svc.search_passages(scope_id=scope, query=PHRASE, **LOCAL)
            self.assertNotIn(PHRASE, shown(res))
            self.assertFalse(any(h["paper_id"] == items[0]["candidate"]["paper_id"] for h in res["hits"]))
            self.assertTrue(any(k.startswith("BLOCKED:RIGHTS_") for k in res["excluded_by_reason"]))
            self.assertIsNone(svc.get_passage(scope_id=scope, chunk_id=hit["chunk_id"], **LOCAL)["hit"])
            self.assertEqual(svc.index_status()["stale_artifacts"], 1)
            member = next(m for m in svc._scope(scope)["members"] if m["paper_id"] == items[0]["candidate"]["paper_id"])
            self.assertTrue(any(p.startswith("BLOCKED:RIGHTS_") for p in svc.member_problems(member)))
            events = kb.all("SELECT content_revealed, returned_json FROM rt_access_event WHERE scope_id=? ORDER BY rowid", (scope,))
            self.assertEqual([e["content_revealed"] for e in events[:3]], [1, 1, 1])
            for e in events[3:]:
                self.assertFalse(any(r["rights_view"] == "FULL_EXCERPT" and r["chunk_id"] == hit["chunk_id"] for r in json.loads(e["returned_json"])))

    def test_s05_eligibility_change_reaches_a_warmed_instance(self):
        """IDs: S09-V02-F01-S05, S09-V02-F01-REINSTATE. A FINAL FT_INCLUDE member is returned, a superseding human decision makes it
        FT_EXCLUDE, and the same instance excludes it as STALE:ELIGIBILITY_CHANGED in search, passage, context and status; when the
        reviewer supersedes again with FT_INCLUDE the same instance returns it again (no sticky cache in either direction)."""
        run, items, scope, _ = self.rworld()
        cand = items[1]["candidate"]
        query = "AndroZoo hold-out accuracy"
        with self.writer() as kb:
            svc = RetrievalService(kb)
            hit = next(h for h in svc.search_passages(scope_id=scope, query=query, **LOCAL)["hits"] if h["paper_id"] == cand["paper_id"])
            self.assertEqual(hit["rights_view"], "FULL_EXCERPT")
            prior = kb.one("SELECT d.decision_id FROM fte_decision d JOIN fte_reviewer v USING (reviewer_id) WHERE d.candidate_id=? "
                           "AND v.label='OPERATOR:rev.a'", (cand["candidate_id"],))["decision_id"]
            FTEService(kb).decide(candidate_id=cand["candidate_id"], reviewer="rev.a", outcome="FT_EXCLUDE", reasons=["WRONG_STUDY_DESIGN"],
                                  supersedes=prior)
            res = svc.search_passages(scope_id=scope, query=query, **LOCAL)
            self.assertFalse(any(h["paper_id"] == cand["paper_id"] for h in res["hits"]))
            self.assertTrue(any(k.startswith("STALE:ELIGIBILITY_CHANGED") for k in res["excluded_by_reason"]))
            self.assertIsNone(svc.get_passage(scope_id=scope, chunk_id=hit["chunk_id"], **LOCAL)["hit"])
            ctx = svc.rag_context(scope_id=scope, query=query, **LOCAL)
            self.assertFalse(any(p["anchor"]["paper_id"] == cand["paper_id"] for p in ctx["passages"]))
            self.assertTrue(any(k.startswith("STALE:ELIGIBILITY_CHANGED") for k in svc.index_status()["stale_reasons"]))
            exclude = kb.one("SELECT d.decision_id FROM fte_decision d JOIN fte_reviewer v USING (reviewer_id) WHERE d.candidate_id=? "
                             "AND v.label='OPERATOR:rev.a' ORDER BY d.rowid DESC LIMIT 1", (cand["candidate_id"],))["decision_id"]
            FTEService(kb).decide(candidate_id=cand["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE", supersedes=exclude)
            again = svc.get_passage(scope_id=scope, chunk_id=hit["chunk_id"], **LOCAL)["hit"]
            self.assertEqual(again["rights_view"], "FULL_EXCERPT")
            self.assertEqual(svc.index_status()["stale_artifacts"], 0)


class F02ScopeBeforeCandidateLimit(S09Case):
    def test_passage_search_reaches_a_member_behind_1000_out_of_scope_matches(self):
        """IDs: S09-V02-F02-PASSAGES. Another scope's paper has 1,080 chunks that all outrank the target chunk for the query (the
        target is ranked beyond 1,000 globally, verified on the index file); the one-paper scope still finds it in lexical and
        hybrid search and in the context, and no out-of-scope chunk is ever counted or returned."""
        distractor = [[f"Packed samples excluded evaluation; packed samples excluded evaluation {p}.{i}." for i in range(45)] for p in range(24)]
        run, items = self.ev_world([[RETRIEVAL_DOC], distractor])
        target_scope = self.rscope(run, items[:1])
        self.rscope(run, items[1:])
        self.rcall("build_index", operator="op.index", embedder="mock")
        target_paper = items[0]["candidate"]["paper_id"]
        ranked = index_rows(self.root, "SELECT c.paper_id FROM chunk_fts JOIN chunk c ON c.rid = chunk_fts.rowid WHERE chunk_fts MATCH ? "
                                       "ORDER BY bm25(chunk_fts), chunk_fts.rowid", ('"packed" "samples" "excluded" "evaluation"',))
        position = [p for (p,) in ranked].index(target_paper) + 1
        self.assertGreater(position, 1000)
        for mode in ("lexical", "hybrid"):
            res = self.passages(target_scope, PHRASE, mode=mode)
            self.assertTrue(res["hits"], mode)
            self.assertEqual({h["paper_id"] for h in res["hits"]}, {target_paper})
            self.assertIn(PHRASE, shown(res))
            self.assertNotIn("NOT_IN_SCOPE", res["excluded_by_reason"])
        ctx = self.rcall("rag_context", scope_id=target_scope, query=PHRASE, **LOCAL)
        self.assertEqual(ctx["outcome"], "CONTEXT_READY")
        self.assertIn(PHRASE, ctx["passages"][0]["text"])

    def test_paper_search_reaches_a_member_behind_200_out_of_scope_matches(self):
        """IDs: S09-V02-F02-PAPERS, S09-V02-F02-LARGE-SCOPE. 202 release papers match the query equally; the target is ranked last
        globally (202nd); a one-paper scope still finds it. The 202-member scope is searched with SQLite's bound-variable limit
        lowered to 4, so scope filtering never expands into one SQL variable per member."""
        run = self.make_run(202, release_id="crl_v02_papers")
        everyone = [{"paper_id": c["paper_id"]} for c in run["candidates"]]
        big = self.rcall("create_scope", basis_kind="RELEASE_IMPORT", basis_id=run["source"], operator="op.owner", label="all release papers",
                         members=everyone)["scope_id"]
        self.rcall("build_index", operator="op.index", embedder=None)
        ranked = [p for (p,) in index_rows(self.root, "SELECT p.paper_id FROM paper_fts JOIN paper p ON p.rid = paper_fts.rowid "
                                                      "WHERE paper_fts MATCH ? ORDER BY bm25(paper_fts), paper_fts.rowid", ('"synthetic"',))]
        self.assertEqual(len(ranked), 202)
        target = ranked[-1]
        small = self.rcall("create_scope", basis_kind="RELEASE_IMPORT", basis_id=run["source"], operator="op.owner", label="one release paper",
                           members=[{"paper_id": target}])["scope_id"]
        self.rcall("build_index", operator="op.index", embedder=None)
        hits = self.rcall("search_papers", scope_id=small, query="Synthetic", **LOCAL)["hits"]
        self.assertEqual([h["paper_id"] for h in hits], [target])

        real_open = ridx.open_readonly

        def low_variable_limit(path):
            con, meta = real_open(path)
            con.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 4)
            return con, meta
        with umock.patch.object(ridx, "open_readonly", low_variable_limit):
            res = self.rcall("search_papers", scope_id=big, query="Synthetic", limit=50, **LOCAL)
            self.assertEqual(len(res["hits"]), 50)
            self.assertEqual(self.rcall("search_papers", scope_id=small, query="Synthetic", **LOCAL)["hits"][0]["paper_id"], target)

    def test_evidence_search_applies_scope_before_the_window(self):
        """IDs: S09-V02-F02-EVIDENCE. Two papers each have one accepted S08 draft matching the query; with the candidate window set to 1
        the draft ranked second globally is still found in its own one-paper scope."""
        run, items = self.ev_world([[RETRIEVAL_DOC], [RETRIEVAL_DOC]])
        ev_scope = self.ev_scope(run, items)
        self.ecall("build_packs", scope_id=ev_scope, operator="op.owner")
        self.ecall("run_mock", scope_id=ev_scope, operator="op.runner")
        self.roster("rev.one")
        for d in self.drafts(ev_scope):
            if d["evidence_class"] == "NUMERICAL_RESULT" and "93.1" in d["typed_fields_json"]:
                self.ecall("verify", draft_id=d["draft_id"], operator="op.v")
                self.ecall("reveal", draft_id=d["draft_id"], reviewer="rev.one")
                self.ecall("review", draft_id=d["draft_id"], reviewer="rev.one", decision="ACCEPT")
        scopes = {it["candidate"]["paper_id"]: self.rscope(run, [it]) for it in items}
        self.assertEqual(self.rcall("build_index", operator="op.index", embedder=None)["counts"]["evidence"], 2)
        ranked = [p for (p,) in index_rows(self.root, "SELECT e.paper_id FROM evidence_fts JOIN evidence e ON e.rid = evidence_fts.rowid "
                                                      "WHERE evidence_fts MATCH ? ORDER BY bm25(evidence_fts), evidence_fts.rowid",
                                           ('"TPR" "EMBER"',))]
        self.assertEqual(len(ranked), 2)
        with umock.patch.object(rservice, "CANDIDATES", 1):
            hits = self.rcall("search_evidence", scope_id=scopes[ranked[1]], query="TPR EMBER", **LOCAL)["hits"]
            self.assertEqual([h["paper_id"] for h in hits], [ranked[1]])
            res = self.passages(scopes[ranked[1]], "TPR EMBER test split", mode="lexical")
            self.assertEqual({h["paper_id"] for h in res["hits"]}, {ranked[1]})


class F03SourceIntegrity(S09Case):
    def _warm(self, svc, scope):
        hit = next(h for h in svc.search_passages(scope_id=scope, query=PHRASE, **LOCAL)["hits"] if PHRASE in h.get("text", ""))
        self.assertEqual(svc.rag_context(scope_id=scope, query=PHRASE, **LOCAL)["outcome"], "CONTEXT_READY")
        return hit

    def _assert_fail_closed(self, svc, scope, hit, reason_prefix):
        res = svc.search_passages(scope_id=scope, query=PHRASE, **LOCAL)
        self.assertNotIn(PHRASE, shown(res))
        self.assertTrue(any(k.startswith(reason_prefix) for k in res["excluded_by_reason"]), res["excluded_by_reason"])
        got = svc.get_passage(scope_id=scope, chunk_id=hit["chunk_id"], **LOCAL)
        self.assertIsNone(got["hit"])
        self.assertTrue(any(k.startswith(reason_prefix) for k in got["excluded_by_reason"]))
        ctx = svc.rag_context(scope_id=scope, query=PHRASE, **LOCAL)
        self.assertNotIn(PHRASE, shown(ctx))
        self.assertEqual(ctx["outcome"], "NO_AUTHORIZED_EVIDENCE")
        self.assertEqual(svc.index_status()["stale_artifacts"], 1)
        self.assertFalse(svc.verify_index()["valid"])

    def test_removed_parse_artifact_fails_closed_on_a_warmed_instance(self):
        """IDs: S09-V02-F03-REMOVED, S09-V02-F03-REBUILD. The S06 artifact CAS object is deleted after one instance served its text: the
        same instance returns no text in search, passage or context (BLOCKED:SOURCE_INTEGRITY:PARSE_ARTIFACT:CAS_MISSING), status
        counts it, verify fails, and a rebuild on that instance excludes the member instead of crashing; restoring the bytes
        restores service on the same instance."""
        run, items, scope, _ = self.rworld()
        with self.writer() as kb:
            svc = RetrievalService(kb)
            hit = self._warm(svc, scope)
            path = cas_file(kb, hit["artifact_sha256"])
            saved = path.read_bytes()
            path.unlink()
            self._assert_fail_closed(svc, scope, hit, "BLOCKED:SOURCE_INTEGRITY:PARSE_ARTIFACT:CAS_MISSING")
            built = svc.build_index(operator="op.index", embedder="mock")
            self.assertIn("BLOCKED:SOURCE_INTEGRITY:PARSE_ARTIFACT:CAS_MISSING", built["excluded_by_reason"])
            self.assertEqual(built["counts"]["artifacts_indexed"], 1)
            path.write_bytes(saved)
            svc.build_index(operator="op.index", embedder="mock")
            self.assertIn(PHRASE, shown(svc.search_passages(scope_id=scope, query=PHRASE, **LOCAL)))

    def test_corrupted_parse_artifact_fails_closed_on_a_warmed_instance(self):
        """IDs: S09-V02-F03-CORRUPT. The S06 artifact bytes are altered in place (same length) after one instance served its text: the
        same instance fails closed with BLOCKED:SOURCE_INTEGRITY:PARSE_ARTIFACT:CAS_HASH_MISMATCH and recovers when the bytes are
        restored."""
        run, items, scope, _ = self.rworld()
        with self.writer() as kb:
            svc = RetrievalService(kb)
            hit = self._warm(svc, scope)
            path = cas_file(kb, hit["artifact_sha256"])
            saved = path.read_bytes()
            path.write_bytes(saved.replace(b"packed", b"PACKED"))
            self._assert_fail_closed(svc, scope, hit, "BLOCKED:SOURCE_INTEGRITY:PARSE_ARTIFACT:CAS_HASH_MISMATCH")
            path.write_bytes(saved)
            self.assertIn(PHRASE, shown(svc.search_passages(scope_id=scope, query=PHRASE, **LOCAL)))

    def test_removed_or_corrupted_pdf_asset_fails_closed_on_a_warmed_instance(self):
        """IDs: S09-V02-F03-PDF. The source PDF asset object behind the artifact is corrupted, then removed: the same instance excludes
        the member (BLOCKED:SOURCE_INTEGRITY:PDF_ASSET:*) although the parse artifact itself is intact."""
        run, items, scope, _ = self.rworld()
        with self.writer() as kb:
            svc = RetrievalService(kb)
            hit = self._warm(svc, scope)
            path = cas_file(kb, hit["asset_sha256"])
            saved = path.read_bytes()
            path.write_bytes(saved[:-1] + bytes([saved[-1] ^ 1]))
            self._assert_fail_closed(svc, scope, hit, "BLOCKED:SOURCE_INTEGRITY:PDF_ASSET:CAS_HASH_MISMATCH")
            path.unlink()
            self._assert_fail_closed(svc, scope, hit, "BLOCKED:SOURCE_INTEGRITY:PDF_ASSET:CAS_MISSING")
            path.write_bytes(saved)
            self.assertIn(PHRASE, shown(svc.search_passages(scope_id=scope, query=PHRASE, **LOCAL)))


if __name__ == "__main__":
    unittest.main()
