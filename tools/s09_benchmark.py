"""S09 offline retrieval benchmark (synthetic): 120 generated papers (synthetic PDFs parsed by the official S06 parser, ~1,400 chunks),
72 questions written from the generator's fact table with gold chunk ids, rights / staleness variants, two independent builds.

Questions are specified from facts (codenames, values, dataset / year, limitation types, operating points), NOT copied from chunk
text, in categories of increasing difficulty: EXACT (rare codename), KEYWORD (a unique value), OPERATING_POINT (TPR at 0.1% FPR for
a dataset / year; the 1% passages are distractors), PARAPHRASE (synonyms absent from the text), MORPHOLOGY (other word forms),
DUPLICATE (identical paragraph in two papers), rights / staleness (A3 denied before build, A5 revoked after build, S05 decision
changed after build: the gold must never be shown) and NO_ANSWER (absent concepts). Gold chunk ids are fixed by where the generator
placed each fact (page, line), resolved after the build from the index's page-ordered anchors.

Metrics per mode (lexical BM25, mock vector branch, hybrid RRF): recall@10 = |gold in top 10| / min(|gold|, 10), MRR, per category,
denominators, eligible / excluded counts, mean and p95 query time, failure categories, deterministic hashes and rebuild identity.
The mock embedder is NOT a semantic model and the corpus is generated: these numbers describe this fixture only and say nothing
about real papers or real semantic retrieval.
Usage: python -I tools/s09_benchmark.py --out-dir DIR   (the official pypdf wheel/site must be available, see the guide)
"""
import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from tests.s09_helpers import S09Case  # noqa: E402
from scivetra_kb.retrieval.service import RetrievalService  # noqa: E402

N = 120
SYL_A = ("Quill", "Bras", "Tor", "Vel", "Mir", "Kast", "Lun", "Ferr", "Gal", "Dov", "Pell", "Run")
SYL_B = ("fern", "moth", "wick", "dane", "holt", "sorrow", "brook", "mire", "stag", "vane")
DOMAINS = (("Windows PE malware", "static byte histogram features", ("EMBER", "SOREL", "BODMAS")),
           ("Android malware", "permission graph features", ("AndroZoo", "Drebin", "AMD")),
           ("phishing websites", "URL lexical features", ("PhishTank", "OpenPhish", "ISCX-URL")),
           ("network intrusion", "flow statistics features", ("CICIDS", "UNSW-NB15", "KDD")))
MODELS = ("gradient boosted tree", "random forest", "convolutional network", "logistic regression")
LIMITS = (("packed samples were excluded from the evaluation", "which studies left compressed executables out of testing", "packing"),
          ("obfuscated applications were not studied", "which work ignored disguised apps", "obfuscation"),
          ("encrypted traffic was removed before training", "where was ciphered network data discarded", "encryption"),
          ("non-English pages were discarded", "which study dropped foreign language web pages", "language"),
          ("adversarial evasion attacks were out of scope", "which detectors did not consider attackers dodging detection", "evasion"),
          ("label noise was not corrected", "who left wrong annotations unfixed", "labelling"))
HARDWARE = ("workstation", "laptop", "GPU server", "cluster node")
ABSENT = ("quantum annealing for genome assembly", "medieval tax records of Flanders", "coral reef bleaching in 1998", "opera ticket pricing",
          "glacier melt velocity measurements", "honeybee waggle dance decoding", "volcanic ash aviation warnings", "sourdough fermentation kinetics")
A3_DENIED, A5_REVOKED, S05_CHANGED = (7, 31, 55, 79), (11, 35, 59, 83), (15, 39, 63)
DUP_PAIRS = ((2, 62), (14, 74), (26, 86), (38, 98), (50, 110))


class Bench(S09Case):
    def runTest(self):
        pass


def facts(i):
    domain, features, datasets = DOMAINS[i % len(DOMAINS)]
    ds, year = datasets[(i // len(DOMAINS)) % 3], 2015 + (i * 7) % 8
    return {"i": i, "code": SYL_A[i % 12] + SYL_B[(i // 12) % 10], "domain": domain, "features": features, "ds": ds, "year": year,
            "n": 20_000 + (i * 7919) % 900_000, "model": MODELS[i % 4], "trees": 100 + 25 * (i % 9), "lr": f"0.{1 + i % 9}",
            "v1": f"{80 + (i * 7) % 19}.{i % 10}", "v2": f"{55 + (i * 11) % 23}.{(i + 3) % 10}", "auc": f"{900 + (i * 37) % 99}",
            "limit": i % len(LIMITS), "hours": 2 + i % 30, "hw": HARDWARE[i % 4]}


def pages(f, shared=None):
    p1 = [f"Objective: evaluate the {f['code']} detector for {f['domain']} using {f['features']}.",
          f"The {f['ds']} corpus contains {f['n']:,} samples collected in {f['year']}.",
          f"Method: a {f['model']} classifier with {f['trees']} trees and a learning rate of {f['lr']}.",
          f"TPR of {f['v1']}% at 1% FPR on the {f['ds']} {f['year']} test split.",
          f"TPR of {f['v2']}% at 0.1% FPR on the {f['ds']} {f['year']} test split."]
    p2 = [f"ROC-AUC of 0.{f['auc']} on the {f['ds']} {f['year']} test split.",
          f"Temporal holdout: trained on samples up to {f['year'] - 1} and tested on samples from {f['year']}.",
          f"Limitation: {LIMITS[f['limit']][0]}.",
          f"Training took {f['hours']} hours on a single {f['hw']} machine.",
          f"Ethics: the {f['ds']} data is publicly released under a research licence."]
    if shared:
        p2.append(shared)
    return [p1, p2]


def shared_text(k):
    return f"Shared note: results were reproduced with seed {4100 + k} by an independent replication team."


def questions(F):
    """(qid, category, text, gold [(paper index, page, line)], expectation)."""
    q = []
    for i in range(0, 120, 12)[:10]:
        q.append((f"EX{i}", "EXACT", f"{F[i]['code']} detector", [(i, 1, 0)], "FOUND"))
    for i in range(5, 120, 12)[:10]:
        q.append((f"KW{i}", "KEYWORD", f"which detector reached ROC-AUC 0.{F[i]['auc']}", [(i, 2, 0)], "FOUND"))
    for k, i in enumerate(range(3, 120, 12)[:10]):
        f = F[i]
        gold = [(j, 1, 4) for j in range(N) if F[j]["ds"] == f["ds"] and F[j]["year"] == f["year"] and j not in A3_DENIED + A5_REVOKED + S05_CHANGED]
        text = (f"TPR at 0.1% FPR on {f['ds']} {f['year']}" if k % 2 == 0 else
                f"true positive rate at a 0.1 percent false positive rate for {f['ds']} in {f['year']}")
        q.append((f"OP{i}", "OPERATING_POINT", text, gold, "FOUND"))
    for k in range(len(LIMITS)):
        gold = [(j, 2, 2) for j in range(N) if F[j]["limit"] == k and j not in A3_DENIED + A5_REVOKED + S05_CHANGED]
        q.append((f"PA{k}", "PARAPHRASE", LIMITS[k][1], gold, "FOUND"))
        q.append((f"MO{k}", "MORPHOLOGY", f"{LIMITS[k][2]} limitation", gold, "FOUND"))
    for extra, i in enumerate((8, 20, 44, 92)):
        q.append((f"PB{i}", "PARAPHRASE", f"how long did model fitting last for the {F[i]['code']} study on a {F[i]['hw']}", [(i, 2, 3)], "FOUND"))
    for k, (a, b) in enumerate(DUP_PAIRS):
        q.append((f"DU{k}", "DUPLICATE", f"reproduced with seed {4100 + k} independent replication", [(a, 2, 5), (b, 2, 5)], "FOUND"))
    for i in A3_DENIED:
        q.append((f"A3{i}", "RIGHTS_A3_DENIED_BEFORE_BUILD", f"{F[i]['code']} detector", [(i, 1, 0)], "NEVER_SHOWN"))
    for i in A5_REVOKED:
        q.append((f"A5{i}", "RIGHTS_A5_REVOKED_AFTER_BUILD", f"{F[i]['code']} detector", [(i, 1, 0)], "NEVER_SHOWN"))
    for i in S05_CHANGED:
        q.append((f"S5{i}", "STALE_S05_DECISION_CHANGED", f"{F[i]['code']} detector", [(i, 1, 0)], "NEVER_SHOWN"))
    for k, text in enumerate(ABSENT):
        q.append((f"NA{k}", "NO_ANSWER", text, [], "NO_MATCH"))
    return q


def build(tag):
    case = Bench()
    case.setUp()
    try:
        F = [facts(i) for i in range(N)]
        shared = {i: shared_text(k) for k, pair in enumerate(DUP_PAIRS) for i in pair}
        run = case.make_run(N, release_id="crl_s09_bench")
        items = []
        for i, cand in enumerate(run["candidates"]):
            pg = pages(F[i], shared.get(i))
            pg[-1].append(f"Synthetic benchmark paper {i}.")
            pdf = case.ev_pdf(cand, pg, name=f"bench_{i:03d}.pdf")
            if i in A3_DENIED:
                case.supersede_rights(pdf["asset_sha256"], store_locally="DENY", local_process="ALLOW")
            case.svc_call("attach_kb_asset", candidate_id=cand["candidate_id"], link_id=pdf["link_id"], operator="op.fte")
            case.both(cand, "FT_INCLUDE")
            items.append(dict(pdf, candidate=cand, i=i))
        scope = case.rscope(run, items)
        t0 = time.perf_counter()
        built = case.rcall("build_index", operator="op.index", embedder="mock")
        build_seconds = time.perf_counter() - t0
        rebuilt = case.rcall("build_index", operator="op.index", embedder="mock")
        case.rcall("purge_index")
        full = case.rcall("build_index", operator="op.index", embedder="mock")
        rebuild = {"first": built["manifest_sha256"], "incremental_equal": rebuilt["manifest_sha256"] == built["manifest_sha256"],
                   "after_purge_equal": full["manifest_sha256"] == built["manifest_sha256"], "vectors_reused_incremental": rebuilt["counts"]["vectors_reused"]}
        # resolve gold (paper, page, line) -> chunk id from the index's page-ordered anchors
        with case.writer() as kb:
            svc = RetrievalService(kb)
            con, meta = svc._open_index()
            try:
                rows = list(con.execute("SELECT paper_id, page, char_start, chunk_id FROM chunk ORDER BY paper_id, page, char_start"))
            finally:
                con.close()
        by_paper = {}
        for pid, page, _, cid in rows:
            by_paper.setdefault(pid, {}).setdefault(page, []).append(cid)
        paper_of = {it["i"]: it["candidate"]["paper_id"] for it in items}
        layout_ok = all(len(by_paper.get(paper_of[i], {}).get(1, [])) == 5 for i in range(N) if i not in A3_DENIED)
        # adverse events after the build: A5 revoked, S05 decision changed
        for i in A5_REVOKED:
            case.supersede_rights(items[i]["asset_sha256"], store_locally="DENY")
        for i in S05_CHANGED:
            cand = items[i]["candidate"]
            with case.reader() as kb:
                prior = kb.one("SELECT d.decision_id FROM fte_decision d JOIN fte_reviewer v USING (reviewer_id) WHERE d.candidate_id=? "
                               "AND v.label='OPERATOR:rev.a'", (cand["candidate_id"],))["decision_id"]
            case.svc_call("decide", candidate_id=cand["candidate_id"], reviewer="rev.a", outcome="FT_EXCLUDE", reasons=["WRONG_STUDY_DESIGN"], supersedes=prior)
        Q = questions(F)
        results, times, per_query = {}, [], []
        for mode in ("lexical", "hybrid", "vector"):
            rows_m = []
            for qid, cat, text, gold_spec, expect in Q:
                gold = {by_paper[paper_of[i]][page][line] for i, page, line in gold_spec if paper_of[i] in by_paper and page in by_paper[paper_of[i]]
                        and line < len(by_paper[paper_of[i]][page])}
                if mode == "vector":
                    with case.writer() as kb:
                        svc = RetrievalService(kb)
                        con, meta = svc._open_index()
                        try:
                            ranked = svc._ranked_chunks(con, meta, svc._scope(scope), text, "hybrid", None, None).get("vector", [])[:10]
                        finally:
                            con.close()
                    shown, elapsed = [(c, "UNAUTHORIZED_BRANCH_ONLY") for c in ranked], None
                    outcome = None
                else:
                    t = time.perf_counter()
                    res = case.passages(scope, text, mode=mode, limit=10)
                    elapsed = (time.perf_counter() - t) * 1000
                    times.append(elapsed)
                    shown = [(h["chunk_id"], h["rights_view"]) for h in res["hits"]]
                    outcome = None
                    if cat == "NO_ANSWER" or expect == "NEVER_SHOWN":
                        outcome = case.rcall("rag_context", scope_id=scope, query=text, consumer="LOCAL_CLI", operator="op.bench", mode=mode)["outcome"]
                ids = [c for c, _ in shown]
                hit_ranks = [r for r, c in enumerate(ids, start=1) if c in gold]
                row = {"qid": qid, "category": cat, "mode": mode, "gold": sorted(gold), "top10": ids, "views": [v for _, v in shown]}
                if expect == "FOUND":
                    row["recall_at_10"] = round(len(set(ids) & gold) / min(len(gold), 10), 6) if gold else None
                    row["reciprocal_rank"] = round(1 / hit_ranks[0], 6) if hit_ranks else 0.0
                    row["failure"] = None if hit_ranks else ("GOLD_NOT_IN_TOP10" if gold else "NO_GOLD")
                    if cat == "OPERATING_POINT" and mode != "vector":
                        distractors = {by_paper[paper_of[j]][1][3] for j in range(N) if paper_of[j] in by_paper and 1 in by_paper[paper_of[j]]
                                       and F[j]["ds"] == F[int(qid[2:])]["ds"] and F[j]["year"] == F[int(qid[2:])]["year"]}
                        first = hit_ranks[0] if hit_ranks else 11
                        row["distractor_1pct_ranked_above_first_gold"] = sum(1 for r, c in enumerate(ids, start=1) if c in distractors and r < first)
                elif expect == "NEVER_SHOWN":
                    leaked = any(c in gold and v == "FULL_EXCERPT" for c, v in shown)
                    row["rights_expectation_met"] = (not leaked) if mode != "vector" else None
                    row["context_outcome"] = outcome
                    row["failure"] = "RIGHTS_LEAK" if leaked and mode != "vector" else None
                else:
                    row["context_outcome"] = outcome
                    row["failure"] = None if mode == "vector" or outcome == "NO_MATCH" else "NO_ANSWER_RETURNED_" + str(outcome)
                row["elapsed_ms"] = elapsed
                rows_m.append(row)
            results[mode] = rows_m
            per_query.extend(rows_m)

        def summary(rows_m):
            found = [r for r in rows_m if "recall_at_10" in r and r["recall_at_10"] is not None]
            cats = sorted({r["category"] for r in found})
            out = {"answerable_questions": len(found), "recall_at_10": round(statistics.mean(r["recall_at_10"] for r in found), 4),
                   "mrr": round(statistics.mean(r["reciprocal_rank"] for r in found), 4),
                   "per_category": {c: {"n": sum(1 for r in found if r["category"] == c),
                                        "recall_at_10": round(statistics.mean(r["recall_at_10"] for r in found if r["category"] == c), 4),
                                        "mrr": round(statistics.mean(r["reciprocal_rank"] for r in found if r["category"] == c), 4)} for c in cats},
                   "failures": dict(sorted(Counter(r["failure"] for r in rows_m if r["failure"]).items()))}
            rights = [r for r in rows_m if r.get("rights_expectation_met") is not None]
            if rights:
                out["rights_questions"] = len(rights)
                out["rights_expectations_met"] = sum(1 for r in rights if r["rights_expectation_met"])
                out["rights_context_outcomes"] = dict(sorted(Counter(r["context_outcome"] for r in rights).items()))
            na = [r for r in rows_m if r["category"] == "NO_ANSWER" and r["mode"] != "vector"]
            if na:
                out["no_answer_questions"] = len(na)
                out["no_answer_context_outcomes"] = dict(sorted(Counter(r["context_outcome"] for r in na).items()))
            op = [r for r in rows_m if "distractor_1pct_ranked_above_first_gold" in r]
            if op:
                out["operating_point_distractors_above_gold"] = sum(r["distractor_1pct_ranked_above_first_gold"] for r in op)
            return out

        status = case.rread("index_status")
        report = {"papers": N, "questions": len(Q), "question_categories": dict(sorted(Counter(c for _, c, _, _, _ in Q).items())),
                  "chunks": built["counts"]["chunks"], "chunks_text_indexed": built["counts"]["chunks_text_indexed"],
                  "chunks_anchor_only_a3": built["counts"]["chunks_anchor_only"], "build_excluded_by_reason": built["excluded_by_reason"],
                  "index_status_after_adverse_events": {"stale_artifacts": status["stale_artifacts"], "stale_reasons": status["stale_reasons"]},
                  "layout_ok": layout_ok, "rebuild": rebuild, "modes": {m: summary(r) for m, r in results.items()},
                  "query_ms": {"n": len(times), "mean": round(statistics.mean(times), 2), "p95": round(sorted(times)[int(0.95 * (len(times) - 1))], 2)},
                  "build_seconds": round(build_seconds, 1), "embedder": built["embedder"], "semantic_model": "NOT_CONFIGURED",
                  "hashes": {"index_manifest": built["manifest_sha256"],
                             "results": hashlib.sha256(json.dumps([{k: v for k, v in r.items() if k != "elapsed_ms"} for r in per_query],
                                                                  sort_keys=True).encode()).hexdigest()}}
        gold_file = [{"qid": qid, "category": cat, "question": text, "gold": [{"paper_index": i, "page": p, "line": ln} for i, p, ln in g],
                      "expectation": e} for qid, cat, text, g, e in Q]
        timing_free = json.loads(json.dumps(report))
        for k in ("query_ms", "build_seconds"):
            timing_free.pop(k)
        return report, timing_free, per_query, gold_file
    finally:
        case.tearDown()


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", required=True)
    args = p.parse_args(argv)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    first, first_cmp, rows, gold = build(1)
    second, second_cmp, _, _ = build(2)
    report = {"schema": "rkb-s09-benchmark/1", "embedder": "MOCK_LOCAL_RETRIEVAL (deterministic hashing; NOT a semantic model)",
              "validity": "generated corpus and questions; numbers describe this fixture only, not real papers or real semantic retrieval",
              **first, "repeat_identical": first_cmp == second_cmp, "python": sys.version.split()[0]}
    text = json.dumps(report, indent=1, sort_keys=True) + "\n"
    (out / "benchmark_report.json").write_text(text, encoding="utf-8")
    (out / "benchmark_queries.json").write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "benchmark_ground_truth.json").write_text(json.dumps(gold, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    lex, hyb = first["modes"]["lexical"], first["modes"]["hybrid"]
    ok = (report["repeat_identical"] and first["layout_ok"] and first["questions"] >= 50 and first["chunks"] >= 1000
          and first["rebuild"]["incremental_equal"] and first["rebuild"]["after_purge_equal"]
          and lex["rights_expectations_met"] == lex["rights_questions"] and hyb["rights_expectations_met"] == hyb["rights_questions"])
    print(json.dumps({"ok": ok, "report_sha256": hashlib.sha256(text.encode()).hexdigest(), "chunks": first["chunks"], "questions": first["questions"],
                      "lexical": {"recall_at_10": lex["recall_at_10"], "mrr": lex["mrr"]}, "hybrid": {"recall_at_10": hyb["recall_at_10"], "mrr": hyb["mrr"]},
                      "rights_met": [lex["rights_expectations_met"], hyb["rights_expectations_met"], lex["rights_questions"]],
                      "repeat_identical": report["repeat_identical"]}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
