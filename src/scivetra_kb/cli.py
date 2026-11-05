"""``rkb`` command line (local-only S03 candidate).

stdout carries exactly one JSON document; stderr carries short human diagnostics.
There is no implicit network: ``--live`` is refused (LIVE_ACCESS_NOT_AUTHORIZED),
and offline network-shaped commands require ``--transport-fixtures DIR``
(development/testing transport; not a production data source).
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core.canonical import canonical_json, sha256_bytes
from .core.db import init_kb, open_reader, open_writer
from .core.doctor import doctor
from .core.errors import KBError, PolicyRefusal, ValidationError
from .core.paths import safe_input_file
from .metadata.doi import (DOI_IDENTIFIER_FIELD, OPERATOR_OR_TABULAR_INPUT, VALID, parse_doi)
from .metadata.integrity import IntegrityService
from .metadata.rv_import import import_corpus_export
from .metadata.service import MetadataService
from .metadata.store import MetadataStore
from .metadata.transport import FixtureTransport, LiveTransportNotAuthorized


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rkb", description="SciVetra KB (local development candidate, S03)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("init", "doctor"):
        sp = sub.add_parser(name)
        sp.add_argument("--data-root", required=True)
        if name == "doctor":
            sp.add_argument("--full", action="store_true")
    _s03_parsers(sub)
    _s04_parsers(sub)
    _s04j_parsers(sub)
    _s05_parsers(sub)
    _s06_parsers(sub)
    _s07_parsers(sub)
    _s08_parsers(sub)
    _s09_parsers(sub)
    _s10_parsers(sub)
    _s11_parsers(sub)
    _llm_parsers(sub)
    meta = sub.add_parser("metadata").add_subparsers(dest="action", required=True)
    for name in ("route", "fetch"):
        sp = meta.add_parser(name)
        sp.add_argument("--data-root", required=True)
        sp.add_argument("--doi", action="append", default=[])
        sp.add_argument("--file")
        sp.add_argument("--transport-fixtures")
        sp.add_argument("--max-requests", type=int, default=5000)
        if name == "fetch":
            sp.add_argument("--live", action="store_true")
            sp.add_argument("--allow-public-pool", action="store_true")
            sp.add_argument("--allow-cn-probe", action="store_true")
    sp = meta.add_parser("refresh")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--integrity-only", action="store_true")
    sp.add_argument("--older-than", type=int, default=90)
    sp.add_argument("--transport-fixtures")
    sp.add_argument("--live", action="store_true")
    sp = meta.add_parser("show")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--doi", required=True)
    sp = meta.add_parser("import-reprovane")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--export-dir", required=True)
    sp.add_argument("--workspace-label", required=True)
    sp.add_argument("--expected-manifest-sha256")
    integ = sub.add_parser("integrity").add_subparsers(dest="action", required=True)
    sp = integ.add_parser("register-document")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--operator", required=True)
    sp.add_argument("--file", required=True)
    sp.add_argument("--label", required=True)
    sp = integ.add_parser("counter-evidence")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--operator", required=True)
    sp.add_argument("--event-id", required=True)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--primary-document-ref", required=True)
    sp.add_argument("--anchor-json", required=True)
    sp.add_argument("--statement", required=True)
    sp = integ.add_parser("review")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--operator", required=True)
    sp.add_argument("--subject-doi", required=True)
    sp.add_argument("--event-id", action="append", required=True)
    sp.add_argument("--trigger", required=True)
    sp.add_argument("--decision", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--counter-evidence-id", action="append", default=[])
    sp.add_argument("--task-id")
    return p


def _s04_parsers(sub) -> None:
    """S01 v03 06 section 10: rkb bridge export | import | status | close (file exchange with PaperBatchDownloader)."""
    br = sub.add_parser("bridge").add_subparsers(dest="action", required=True)
    sp = br.add_parser("export")
    for flag in ("--data-root", "--source-dir", "--workspace-label", "--out-dir", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--kind", choices=("rv-manual-queue", "rv-corpus"), required=True)
    sp.add_argument("--expected-manifest-sha256")
    sp.add_argument("--rv-verify-json", help="output of Reprovane's own read-only verify-export, cross-checked")
    sp.add_argument("--fulltext-dir", help="rv-corpus only: a Stage-11B export of the same release; ACQUIRED_AUTO items are excluded")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--include-ineligible-candidates", action="store_true")
    sp = br.add_parser("import")
    for flag in ("--data-root", "--batch-id", "--result-csv", "--pdf-folder", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--manifest", help="bridge_manifest.json to compare with the hash recorded at export")
    sp.add_argument("--accept-late-files", action="store_true")
    sp.add_argument("--allow-different-folder", action="store_true")
    sp.add_argument("--pypdf-site")
    sp = br.add_parser("status")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--batch-id", required=True)
    sp = br.add_parser("close")
    for flag in ("--data-root", "--batch-id", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--reason", choices=("OPERATOR_CLOSE_WITH_OPEN_ROWS",))


def _run_bridge(args, kw):
    from .bridge.service import BridgeService
    if args.action == "status":
        with open_reader(args.data_root, **kw) as kb:
            return 0, BridgeService(kb).status(args.batch_id)
    with open_writer(args.data_root, **kw) as kb:
        if args.action == "export":
            return 0, BridgeService(kb).export_batch(source_dir=args.source_dir, kind=args.kind, workspace_label=args.workspace_label,
                                                     out_dir=args.out_dir, operator=args.operator,
                                                     expected_manifest_sha256=args.expected_manifest_sha256, rv_verify_json=args.rv_verify_json,
                                                     fulltext_dir=args.fulltext_dir, limit=args.limit,
                                                     include_ineligible=args.include_ineligible_candidates)
        if args.action == "import":
            return BridgeService(kb, pypdf_site=args.pypdf_site).import_results(
                batch_id=args.batch_id, result_csv=args.result_csv, pdf_folder=args.pdf_folder, operator=args.operator, manifest_file=args.manifest,
                accept_late_files=args.accept_late_files, allow_different_folder=args.allow_different_folder)
        return 0, BridgeService(kb).close(batch_id=args.batch_id, operator=args.operator, reason=args.reason)


def _s04j_parsers(sub) -> None:
    """S04J journal DOI harvesting (offline candidate: simulated providers only; --live is refused)."""
    jr = sub.add_parser("journal").add_subparsers(dest="action", required=True)
    sp = jr.add_parser("register")
    for flag in ("--data-root", "--label", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--issn", action="append", required=True)
    sp.add_argument("--default-export-work-type", action="append")
    sp.add_argument("--owner-count", type=int, help="a count observed earlier (historical note, never an expectation)")
    sp.add_argument("--owner-count-observed-on")
    sp.add_argument("--owner-count-note")
    for name in ("resolve", "harvest", "resume", "harvest-batch"):
        sp = jr.add_parser(name)
        for flag in ("--data-root", "--operator"):
            sp.add_argument(flag, required=True)
        sp.add_argument("--transport-sim", help="simulated provider scenario directory (development/testing transport, not a data source)")
        sp.add_argument("--sim-profile")
        sp.add_argument("--live", action="store_true")
        if name in ("resolve", "harvest"):
            sp.add_argument("--journal-id", required=True)
        if name == "resolve":
            sp.add_argument("--openalex-source-id")
        if name == "harvest":
            sp.add_argument("--provider", choices=("crossref", "openalex", "openalex-alternate"), required=True)
            sp.add_argument("--mode", choices=("full", "incremental"), default="full")
            sp.add_argument("--since")
            sp.add_argument("--page-size", type=int)
            sp.add_argument("--max-pages", type=int)
            sp.add_argument("--max-requests", type=int)
        if name == "resume":
            sp.add_argument("--run-id", required=True)
        if name == "harvest-batch":
            sp.add_argument("--batch-file", required=True)
            sp.add_argument("--no-alternate", action="store_true")
    sp = jr.add_parser("status")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--run-id", required=True)
    sp = jr.add_parser("reconcile")
    for flag in ("--data-root", "--journal-id", "--operator"):
        sp.add_argument(flag, required=True)
    sp = jr.add_parser("coverage")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--version-id", required=True)
    sp = jr.add_parser("export")
    for flag in ("--data-root", "--version-id", "--out-dir", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--work-type", action="append")


def _run_journal(args, kw, *, sleeper=None, jitter=None):
    from .journal.access import Credentials
    from .journal.export import export_version
    from .journal.harvest import JournalHarvester
    from .journal.reconcile import Reconciler
    from .journal.service import run_batch
    from .journal.simulator import SimulatedCrash, SimulatedProviders
    if args.action in ("status", "coverage"):
        with open_reader(args.data_root, **kw) as kb:
            if args.action == "status":
                return 0, JournalHarvester(kb, FixtureTransport(), operator="reader").status(args.run_id)
            row = kb.one("SELECT coverage_json FROM jh_collection_version WHERE version_id=?", (args.version_id,))
            if not row:
                raise ValidationError("Unknown collection version", code="UNKNOWN_VERSION")
            return 0, {"coverage": json.loads(row["coverage_json"])}
    with open_writer(args.data_root, **kw) as kb:
        if args.action == "register":
            harvester = JournalHarvester(kb, FixtureTransport(), operator=args.operator)
            out = harvester.register_journal(args.label, args.issn, work_type_filter=args.default_export_work_type)
            if args.owner_count is not None:
                if not args.owner_count_observed_on or not args.owner_count_note:
                    raise ValidationError("--owner-count needs --owner-count-observed-on and --owner-count-note", code="OWNER_COUNT_INCOMPLETE")
                out["owner_count"] = harvester.record_owner_count(out["journal_id"], count=args.owner_count,
                                                                  observed_on=args.owner_count_observed_on, note=args.owner_count_note)
            return 0, out
        if args.action == "reconcile":
            return 0, Reconciler(kb, operator=args.operator).reconcile(args.journal_id)
        if args.action == "export":
            return 0, export_version(kb, args.version_id, args.out_dir, operator=args.operator, work_types=args.work_type)
        if args.live:
            LiveTransportNotAuthorized()  # raises PolicyRefusal(LIVE_ACCESS_NOT_AUTHORIZED)
        if not args.transport_sim:
            raise PolicyRefusal("No transport configured; S04J performs no network access", code="NO_TRANSPORT_CONFIGURED")
        transport = SimulatedProviders.from_directory(args.transport_sim, args.sim_profile)
        harvester = JournalHarvester(kb, transport, operator=args.operator, credentials=Credentials.from_env(),
                                     **{k: v for k, v in (("sleeper", sleeper), ("jitter", jitter)) if v is not None})
        try:
            if args.action == "resolve":
                return 0, harvester.resolve(args.journal_id, openalex_source_id=args.openalex_source_id)
            if args.action == "resume":
                return 0, harvester.resume(args.run_id)
            if args.action == "harvest-batch":
                entries = _json_file(args.batch_file)
                if not isinstance(entries, list):
                    raise ValidationError("Batch file must be a JSON list of journals", code="BATCH_FILE_INVALID")
                return 0, run_batch(harvester, entries, include_alternate=not args.no_alternate)
            caps = {k: v for k, v in (("max_pages", args.max_pages), ("max_requests", args.max_requests)) if v is not None}
            return 0, harvester.harvest(args.journal_id, args.provider.upper().replace("-", "_"), mode=args.mode.upper(), since=args.since,
                                        page_size=args.page_size, caps=caps)
        except SimulatedCrash:
            # Stands in for a killed process: the page in flight was rolled back; committed pages are resumable.
            return 4, {"ok": False, "error": "SIMULATED_INTERRUPTION", "run_id": harvester.current_run_id,
                       "next_step": "rkb journal resume --run-id <run_id> (committed pages are kept)"}


def _s05_parsers(sub) -> None:
    """S05 full-text eligibility CANDIDATE workflow (KB-side, NON_AUTHORITATIVE; human-only decisions)."""
    ft = sub.add_parser("fte").add_subparsers(dest="action", required=True)
    sp = ft.add_parser("sources")
    sp.add_argument("--data-root", required=True)
    sp = ft.add_parser("protocol-create")
    for flag in ("--data-root", "--protocol-file", "--operator"):
        sp.add_argument(flag, required=True)
    sp = ft.add_parser("create")
    for flag in ("--data-root", "--source", "--export-dir", "--expected-manifest-sha256", "--rv-verify-json", "--protocol-id", "--roster-file",
                 "--label", "--operator"):
        sp.add_argument(flag, required=True)
    for name in ("status", "candidates", "queue", "summary"):
        sp = ft.add_parser(name)
        sp.add_argument("--data-root", required=True)
        sp.add_argument("--run-id", required=True)
    sp = ft.add_parser("attach-evidence")
    for flag in ("--data-root", "--candidate-id", "--operator"):
        sp.add_argument(flag, required=True)
    group = sp.add_mutually_exclusive_group(required=True)
    group.add_argument("--link-id")
    group.add_argument("--rv11b-export-dir")
    group.add_argument("--none", action="store_true")
    for flag in ("--rv11b-expected-manifest-sha256", "--rv11b-verify-json", "--rv11b-expected-artifact-sha256"):
        sp.add_argument(flag)
    sp = ft.add_parser("attempt")
    for flag in ("--data-root", "--candidate-id", "--status", "--source", "--attempted-on", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--retry-after")
    sp.add_argument("--note")
    for name in ("decide", "adjudicate"):
        sp = ft.add_parser(name)
        for flag in ("--data-root", "--candidate-id", "--outcome", "--reviewer" if name == "decide" else "--adjudicator"):
            sp.add_argument(flag, required=True)
        sp.add_argument("--reason", action="append", default=[])
        sp.add_argument("--note", required=name == "adjudicate")
        sp.add_argument("--related-corpus-item-id")
        sp.add_argument("--supersedes")
        if name == "decide":
            sp.add_argument("--page", type=int)
            sp.add_argument("--section")
    for name in ("resolve", "lock"):
        sp = ft.add_parser(name)
        for flag in ("--data-root", "--run-id", "--operator"):
            sp.add_argument(flag, required=True)
    sp = ft.add_parser("export")
    for flag in ("--data-root", "--run-id", "--out-dir", "--operator"):
        sp.add_argument(flag, required=True)
    sp = ft.add_parser("verify")
    sp.add_argument("--export-dir")
    sp.add_argument("--data-root")
    sp.add_argument("--run-id")


def _run_fte(args, kw):
    from .eligibility.handoff import export_run, verify_export, verify_lock
    from .eligibility.service import FTEService
    if args.action == "verify":
        if args.export_dir:
            out = verify_export(args.export_dir)
            return (0 if out["valid"] else 3), out
        if not (args.data_root and args.run_id):
            raise ValidationError("verify needs --export-dir, or --data-root with --run-id", code="VERIFY_TARGET_REQUIRED")
        with open_reader(args.data_root, **kw) as kb:
            out = verify_lock(kb, args.run_id)
        return (0 if out["valid"] else 3), out
    if args.action in ("sources", "status", "candidates", "queue", "summary"):
        with open_reader(args.data_root, **kw) as kb:
            svc = FTEService(kb)
            if args.action == "sources":
                return 0, {"eligible_sources": svc.eligible_sources(), "note": "only S03-imported Reprovane Stage-11 releases; S04J journal "
                                                                                "inventories are never FTE sources"}
            svc.run(args.run_id)
            if args.action == "summary":
                return 0, svc.summary(args.run_id)
            if args.action == "queue":
                return 0, {"authority": "NON_AUTHORITATIVE", "queue": [q for q in svc.queue(args.run_id) if q["flags"]]}
            if args.action == "candidates":
                return 0, {"authority": "NON_AUTHORITATIVE", "candidates": [dict(c, **svc.candidate_state(c)) for c in svc.candidates(args.run_id)]}
            return 0, {"authority": "NON_AUTHORITATIVE", "run": {k: v for k, v in svc.run(args.run_id).items() if k != "source_verification_json"},
                       "state": svc.state(args.run_id), "summary": svc.summary(args.run_id)}
    with open_writer(args.data_root, **kw) as kb:
        svc = FTEService(kb)
        if args.action == "protocol-create":
            return 0, svc.create_protocol(_json_file(args.protocol_file), operator=args.operator)
        if args.action == "create":
            return 0, svc.create_run(source_id=args.source, export_dir=args.export_dir, expected_manifest_sha256=args.expected_manifest_sha256,
                                     rv_verify_json=args.rv_verify_json, protocol_id=args.protocol_id, roster=_json_file(args.roster_file),
                                     label=args.label, operator=args.operator)
        if args.action == "attach-evidence":
            if args.link_id:
                return 0, svc.attach_kb_asset(candidate_id=args.candidate_id, link_id=args.link_id, operator=args.operator)
            if args.rv11b_export_dir:
                ref = {"fulltext_export_dir": args.rv11b_export_dir, "expected_manifest_sha256": args.rv11b_expected_manifest_sha256,
                       "rv_verify_json": args.rv11b_verify_json, "expected_artifact_sha256": args.rv11b_expected_artifact_sha256}
                return 0, svc.attach_external(candidate_id=args.candidate_id, artifact={k: v for k, v in ref.items() if v is not None},
                                              operator=args.operator)
            return 0, svc.attach_none(candidate_id=args.candidate_id, operator=args.operator)
        if args.action == "attempt":
            return 0, svc.record_attempt(candidate_id=args.candidate_id, status=args.status, source_description=args.source,
                                         attempted_on=args.attempted_on, retry_after=args.retry_after, note=args.note, operator=args.operator)
        if args.action == "decide":
            return 0, svc.decide(candidate_id=args.candidate_id, reviewer=args.reviewer, outcome=args.outcome, reasons=args.reason, note=args.note,
                                 page=args.page, section=args.section, related_corpus_item_id=args.related_corpus_item_id, supersedes=args.supersedes)
        if args.action == "adjudicate":
            return 0, svc.adjudicate(candidate_id=args.candidate_id, adjudicator=args.adjudicator, outcome=args.outcome, reasons=args.reason,
                                     note=args.note, related_corpus_item_id=args.related_corpus_item_id, supersedes=args.supersedes)
        if args.action == "resolve":
            return 0, svc.resolve(args.run_id, operator=args.operator)
        if args.action == "lock":
            return 0, svc.lock(args.run_id, operator=args.operator)
        return 0, export_run(kb, args.run_id, args.out_dir, operator=args.operator)


def _s03_parsers(sub) -> None:
    sp = sub.add_parser("backup")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--target", required=True)
    sp = sub.add_parser("verify-backup")
    sp.add_argument("--backup", required=True)
    sp = sub.add_parser("restore")
    sp.add_argument("--backup", required=True)
    sp.add_argument("--data-root", required=True)
    sp = sub.add_parser("upgrade")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--backup-target", required=True)
    sp = sub.add_parser("reconcile")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--workspace-label", required=True)
    sp.add_argument("--export-dir", required=True)
    lib = sub.add_parser("library").add_subparsers(dest="action", required=True)
    sp = lib.add_parser("import-reprovane")
    for flag in ("--data-root", "--export-dir", "--workspace-label", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--expected-manifest-sha256")
    sp = lib.add_parser("review-queue")
    sp.add_argument("--data-root", required=True)
    sp = lib.add_parser("search")
    sp.add_argument("--data-root", required=True)
    sp.add_argument("--doi")
    sp.add_argument("--title")
    sp.add_argument("--paper-id")
    sp = lib.add_parser("decide-link")
    for flag in ("--data-root", "--candidate-id", "--decision", "--operator", "--reason"):
        sp.add_argument(flag, required=True)
    sp = lib.add_parser("resolve-conflict")
    for flag in ("--data-root", "--conflict-id", "--operator", "--reason"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--action", dest="action_name", required=True)
    sp.add_argument("--evidence-ref", action="append", required=True)
    sp.add_argument("--activate-assertion-id", action="append", default=[])
    sp.add_argument("--withdraw-assertion-id", action="append", default=[])
    sp = lib.add_parser("set-kind")
    for flag in ("--data-root", "--paper-id", "--kind", "--operator", "--reason"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--claim-ref", action="append", required=True)
    sp = lib.add_parser("confirm-family-member")
    for flag in ("--data-root", "--member-id", "--confirmation", "--operator", "--reason"):
        sp.add_argument(flag, required=True)
    sp = lib.add_parser("workspace-status")
    for flag in ("--data-root", "--workspace-label", "--availability", "--operator", "--reason"):
        sp.add_argument(flag, required=True)
    asset = sub.add_parser("asset").add_subparsers(dest="action", required=True)
    sp = asset.add_parser("add-version")
    for flag in ("--data-root", "--paper-id", "--version-class", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--evidence-file")
    sp.add_argument("--label")
    sp = asset.add_parser("register")
    for flag in ("--data-root", "--fixture-root", "--file", "--document-version-id", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--role", default="MAIN_TEXT")
    sp.add_argument("--access-route", default="UNKNOWN")
    sp.add_argument("--access-barrier", action="append", default=[])
    sp.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
    sp.add_argument("--parser-timeout", type=float, default=20.0)
    sp.add_argument("--require-human-identity", action="store_true")
    sp.add_argument("--pypdf-site")
    sp.add_argument("--pdf-validator", choices=("1", "2"), default="1", help="2 = S06 parser-backed G1 (modern xref/object streams, C17)")
    sp.add_argument("--parser-site", help="the official pypdf wheel or a site directory holding the pinned pypdf (default: the installed pypdf)")
    sp.add_argument("--allow-privileged-account-for-synthetic-input", action="store_true",
                    help="POSIX root only: RLIMIT_NPROC does not confine root; synthetic inputs only (recorded)")
    sp = asset.add_parser("review-link")
    for flag in ("--data-root", "--link-id", "--decision", "--operator", "--reason"):
        sp.add_argument(flag, required=True)
    rights = sub.add_parser("rights").add_subparsers(dest="action", required=True)
    sp = rights.add_parser("assess")
    for flag in ("--data-root", "--asset", "--document-version-id", "--evidence-file", "--operator"):
        sp.add_argument(flag, required=True)
    sp = rights.add_parser("gate")
    for flag in ("--data-root", "--asset", "--document-version-id"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--action", dest="action_name", required=True)
    sp.add_argument("--quote-chars", type=int)


def _s06_parsers(sub) -> None:
    """S06: rkb parse run | status | show | verify | verify-anchor | export | runtime-check (local, rights-gated PDF parsing)."""
    pr = sub.add_parser("parse").add_subparsers(dest="action", required=True)
    sp = pr.add_parser("run")
    for flag in ("--data-root", "--asset", "--link-id", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--purpose", choices=("RESEARCH_PARSE", "IDENTITY_INSPECTION"), required=True)
    sp.add_argument("--parser-site")
    sp.add_argument("--accept-language-level-network-guard", action="store_true",
                    help="parse although the OS gives no network isolation (Windows); synthetic/owner-approved inputs only")
    sp.add_argument("--allow-privileged-account-for-synthetic-input", action="store_true",
                    help="POSIX root only: RLIMIT_NPROC does not confine root; synthetic inputs only (recorded)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--recheck", action="store_true", help="re-parse an existing artifact and require identical bytes")
    sp.add_argument("--timeout", type=int)
    sp.add_argument("--memory-mb", type=int)
    sp = pr.add_parser("status")
    for flag in ("--data-root", "--asset"):
        sp.add_argument(flag, required=True)
    sp = pr.add_parser("show")
    for flag in ("--data-root", "--artifact-id"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--link-id")
    sp.add_argument("--with-text", action="store_true")
    sp.add_argument("--page", type=int)
    sp = pr.add_parser("verify")
    for flag in ("--data-root", "--artifact-id"):
        sp.add_argument(flag, required=True)
    sp = pr.add_parser("verify-anchor")
    for flag in ("--data-root", "--artifact-id", "--text-sha256"):
        sp.add_argument(flag, required=True)
    for flag in ("--page", "--start", "--end"):
        sp.add_argument(flag, type=int, required=True)
    sp = pr.add_parser("export")
    for flag in ("--data-root", "--artifact-id", "--out-dir", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--link-id")
    sp.add_argument("--include-text", action="store_true")
    sp.add_argument("--reason")
    sp = pr.add_parser("export-status")
    sp.add_argument("--data-root", required=True)
    sp = pr.add_parser("runtime-check")
    sp.add_argument("--parser-site")


def _run_parse(args, kw):
    from .parse import runtime
    from .parse.service import ParseService
    if args.action == "runtime-check":
        info = dict(runtime.verify_site(args.parser_site or runtime.default_site()))
        site = info.pop("site")      # local paths are never printed; only their hash
        return 0, dict(info, pinned=runtime.PINNED_VERSION, site_path_sha256=sha256_bytes(site.encode("utf-8")))
    if args.action == "export-status":
        with open_reader(args.data_root, **kw) as kb:
            return 0, ParseService(kb).export_status()
    if args.action in ("status", "show", "verify", "verify-anchor"):
        with open_reader(args.data_root, **kw) as kb:
            svc = ParseService(kb)
            if args.action == "status":
                return 0, svc.status(asset_sha256=args.asset)
            if args.action == "show":
                return 0, svc.show(artifact_id=args.artifact_id, link_id=args.link_id, with_text=args.with_text, page=args.page)
            if args.action == "verify":
                out = svc.verify(artifact_id=args.artifact_id)
                return (0 if out["valid"] else 3), out
            out = svc.verify_anchor(artifact_id=args.artifact_id, page=args.page, start=args.start, end=args.end, text_sha256=args.text_sha256)
            return (0 if out["valid"] else 3), out
    with open_writer(args.data_root, **kw) as kb:
        if args.action == "export":
            return 0, ParseService(kb).export(artifact_id=args.artifact_id, out_dir=args.out_dir, operator=args.operator,
                                              include_text=args.include_text, reason=args.reason, link_id=args.link_id)
        limits = {}
        if args.timeout:
            limits["timeout_seconds"] = args.timeout
        if args.memory_mb:
            limits["memory_bytes"] = args.memory_mb * 1024 * 1024
        svc = ParseService(kb, parser_site=args.parser_site, limits=limits,
                           allow_language_level_network_guard=args.accept_language_level_network_guard,
                           allow_privileged_account=args.allow_privileged_account_for_synthetic_input)
        out = svc.parse(asset_sha256=args.asset, link_id=args.link_id, purpose=args.purpose, operator=args.operator, dry_run=args.dry_run,
                        recheck=args.recheck)
        return (3 if out.get("outcome") in ("REFUSED", "FAILED") else 0), out


def _s07_parsers(sub) -> None:
    """S07: rkb screen ... (NON_AUTHORITATIVE AI screening proposals; local mock only; human review is advisory)."""
    sc = sub.add_parser("screen").add_subparsers(dest="action", required=True)
    sp = sc.add_parser("criteria-create")
    for flag in ("--data-root", "--task-type", "--criteria-file", "--label", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--supersedes")
    sp = sc.add_parser("scope-create")
    for flag in ("--data-root", "--scope-file", "--operator"):
        sp.add_argument(flag, required=True)
    for name in ("build-packs", "run-mock"):
        sp = sc.add_parser(name)
        for flag in ("--data-root", "--scope-id", "--operator"):
            sp.add_argument(flag, required=True)
    sp = sc.add_parser("import-proposal")
    for flag in ("--data-root", "--pack-id", "--response-file", "--model-file", "--operator"):
        sp.add_argument(flag, required=True)
    sp = sc.add_parser("verify")
    for flag in ("--data-root", "--proposal-id", "--operator"):
        sp.add_argument(flag, required=True)
    for name in ("state", "show"):
        sp = sc.add_parser(name)
        for flag in ("--data-root", "--proposal-id"):
            sp.add_argument(flag, required=True)
    sp = sc.add_parser("reviewer-register")
    for flag in ("--data-root", "--label", "--operator", "--reason"):
        sp.add_argument(flag, required=True)
    sp = sc.add_parser("initial-judgement")
    for flag in ("--data-root", "--scope-id", "--paper-id", "--reviewer", "--outcome"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--note")
    sp = sc.add_parser("reveal")
    for flag in ("--data-root", "--proposal-id", "--reviewer"):
        sp.add_argument(flag, required=True)
    sp = sc.add_parser("approve")
    for flag in ("--data-root", "--proposal-id", "--reviewer", "--decision"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--reason-code", action="append", default=[])
    sp.add_argument("--edited-payload-file")
    sp.add_argument("--linked-decision-file")
    sp = sc.add_parser("export")
    for flag in ("--data-root", "--scope-id", "--out-dir", "--operator"):
        sp.add_argument(flag, required=True)


def _run_screen(args, kw):
    from .screening.service import ScreeningService
    if args.action in ("state", "show"):
        with open_reader(args.data_root, **kw) as kb:
            return 0, getattr(ScreeningService(kb), args.action)(proposal_id=args.proposal_id)
    with open_writer(args.data_root, **kw) as kb:
        svc = ScreeningService(kb)
        if args.action == "criteria-create":
            return 0, svc.create_criteria_set(task_type=args.task_type, criteria=_json_file(args.criteria_file), label=args.label, operator=args.operator,
                                              supersedes=args.supersedes)
        if args.action == "scope-create":
            spec = _json_file(args.scope_file)
            if not isinstance(spec, dict):
                raise ValidationError("scope file must hold a JSON object", code="SCOPE_FILE_INVALID")
            return 0, svc.create_scope(operator=args.operator, **spec)
        if args.action == "build-packs":
            return 0, svc.build_packs(scope_id=args.scope_id, operator=args.operator)
        if args.action == "run-mock":
            return 0, svc.run_mock(scope_id=args.scope_id, operator=args.operator)
        if args.action == "import-proposal":
            raw = safe_input_file(args.response_file, max_bytes=256 * 1024).read_bytes()
            out = svc.import_proposal(pack_id=args.pack_id, response=raw, operator=args.operator, declared_model=_json_file(args.model_file))
            return (3 if out["status"] != "OK" else 0), out
        if args.action == "verify":
            out = svc.verify(proposal_id=args.proposal_id, operator=args.operator)
            return (3 if out["state"] == "FAILED" else 0), out
        if args.action == "reviewer-register":
            return 0, svc.register_reviewer(label=args.label, operator=args.operator, reason=args.reason)
        if args.action == "initial-judgement":
            return 0, svc.record_initial_judgement(scope_id=args.scope_id, paper_id=args.paper_id, reviewer=args.reviewer, outcome=args.outcome, note=args.note)
        if args.action == "reveal":
            return 0, svc.reveal(proposal_id=args.proposal_id, reviewer=args.reviewer)
        if args.action == "approve":
            return 0, svc.approve(proposal_id=args.proposal_id, reviewer=args.reviewer, decision=args.decision, reason_codes=args.reason_code,
                                  edited_payload=_json_file(args.edited_payload_file) if args.edited_payload_file else None,
                                  linked_authoritative_decision=_json_file(args.linked_decision_file) if args.linked_decision_file else None)
        return 0, svc.export(scope_id=args.scope_id, out_dir=args.out_dir, operator=args.operator)


def _s08_parsers(sub) -> None:
    """S08: rkb evidence ... (typed EvidenceDraft records; local mock only; human review; NON_AUTHORITATIVE)."""
    ev = sub.add_parser("evidence").add_subparsers(dest="action", required=True)
    sp = ev.add_parser("profile-register")
    for flag in ("--data-root", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--builtin", choices=("rkb-general-empirical", "rkb-malware-detection"))
    sp.add_argument("--profile-file")
    sp.add_argument("--supersedes")
    sp = ev.add_parser("scope-create")
    for flag in ("--data-root", "--scope-file", "--operator"):
        sp.add_argument(flag, required=True)
    for name in ("build-packs", "run-mock"):
        sp = ev.add_parser(name)
        for flag in ("--data-root", "--scope-id", "--operator"):
            sp.add_argument(flag, required=True)
    sp = ev.add_parser("import")
    for flag in ("--data-root", "--pack-id", "--response-file", "--model-file", "--operator"):
        sp.add_argument(flag, required=True)
    sp = ev.add_parser("verify")
    for flag in ("--data-root", "--draft-id", "--operator"):
        sp.add_argument(flag, required=True)
    for name in ("state", "show", "audit"):
        sp = ev.add_parser(name)
        for flag in ("--data-root", "--draft-id"):
            sp.add_argument(flag, required=True)
    for name in ("summary", "exports"):
        sp = ev.add_parser(name)
        for flag in ("--data-root", "--scope-id"):
            sp.add_argument(flag, required=True)
    sp = ev.add_parser("reveal")
    for flag in ("--data-root", "--draft-id", "--reviewer"):
        sp.add_argument(flag, required=True)
    sp = ev.add_parser("review")
    for flag in ("--data-root", "--draft-id", "--reviewer", "--decision"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--reason-code", action="append", default=[])
    sp.add_argument("--rationale")
    sp.add_argument("--correction-file")
    sp = ev.add_parser("adjudicate")
    for flag in ("--data-root", "--draft-id", "--adjudicator", "--outcome", "--rationale"):
        sp.add_argument(flag, required=True)
    sp = ev.add_parser("export")
    for flag in ("--data-root", "--scope-id", "--out-dir", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--content-level", default="METADATA_ONLY", choices=("METADATA_ONLY", "ACCEPTED_TYPED_VALUES"))


def _run_evidence(args, kw):
    from .evidence.service import EvidenceService
    if args.action in ("state", "show", "audit", "summary", "exports"):
        with open_reader(args.data_root, **kw) as kb:
            svc = EvidenceService(kb)
            if args.action == "summary":
                return 0, svc.scope_summary(scope_id=args.scope_id)
            if args.action == "exports":
                return 0, svc.export_history(scope_id=args.scope_id)
            return 0, getattr(svc, args.action)(draft_id=args.draft_id)
    with open_writer(args.data_root, **kw) as kb:
        svc = EvidenceService(kb)
        if args.action == "profile-register":
            if bool(args.builtin) == bool(args.profile_file):
                raise ValidationError("give exactly one of --builtin or --profile-file", code="PROFILE_SOURCE_INVALID")
            return 0, svc.register_profile(operator=args.operator, builtin_key=args.builtin,
                                           profile=_json_file(args.profile_file) if args.profile_file else None, supersedes=args.supersedes)
        if args.action == "scope-create":
            spec = _json_file(args.scope_file)
            if not isinstance(spec, dict):
                raise ValidationError("scope file must hold a JSON object", code="SCOPE_FILE_INVALID")
            return 0, svc.create_scope(operator=args.operator, **spec)
        if args.action == "build-packs":
            return 0, svc.build_packs(scope_id=args.scope_id, operator=args.operator)
        if args.action == "run-mock":
            return 0, svc.run_mock(scope_id=args.scope_id, operator=args.operator)
        if args.action == "import":
            raw = safe_input_file(args.response_file, max_bytes=512 * 1024).read_bytes()
            out = svc.import_extraction(pack_id=args.pack_id, response=raw, operator=args.operator, declared_model=_json_file(args.model_file))
            return (3 if out["status"] == "SCHEMA_INVALID" else 0), out
        if args.action == "verify":
            out = svc.verify(draft_id=args.draft_id, operator=args.operator)
            return (0 if out["state"] in ("STRUCTURALLY_VERIFIED", "ABSENCE_RECORDED") else 3), out
        if args.action == "reveal":
            return 0, svc.reveal(draft_id=args.draft_id, reviewer=args.reviewer)
        if args.action == "review":
            return 0, svc.review(draft_id=args.draft_id, reviewer=args.reviewer, decision=args.decision, reason_codes=args.reason_code,
                                 rationale=args.rationale, correction=_json_file(args.correction_file) if args.correction_file else None)
        if args.action == "adjudicate":
            return 0, svc.adjudicate(draft_id=args.draft_id, adjudicator=args.adjudicator, outcome=args.outcome, rationale=args.rationale)
        return 0, svc.export(scope_id=args.scope_id, out_dir=args.out_dir, operator=args.operator, content_level=args.content_level)


def _s09_parsers(sub) -> None:
    """S09: rkb retrieval / index / search / passage / rag (local CLI consumer only; NON_AUTHORITATIVE)."""
    rt = sub.add_parser("retrieval").add_subparsers(dest="action", required=True)
    sp = rt.add_parser("scope-create")
    for flag in ("--data-root", "--scope-file", "--operator"):
        sp.add_argument(flag, required=True)
    sp = rt.add_parser("access-log")
    for flag in ("--data-root", "--scope-id"):
        sp.add_argument(flag, required=True)
    ix = sub.add_parser("index").add_subparsers(dest="action", required=True)
    for name in ("build", "rebuild"):
        sp = ix.add_parser(name)
        for flag in ("--data-root", "--operator"):
            sp.add_argument(flag, required=True)
        sp.add_argument("--embedder", default="mock", choices=("mock", "none", "local-model"))
        sp.add_argument("--model-dir")
        sp.add_argument("--model-manifest")
    for name in ("status", "verify", "purge"):
        ix.add_parser(name).add_argument("--data-root", required=True)
    se = sub.add_parser("search").add_subparsers(dest="action", required=True)
    for name in ("passages", "papers", "evidence"):
        sp = se.add_parser(name)
        for flag in ("--data-root", "--scope-id", "--query", "--operator"):
            sp.add_argument(flag, required=True)
        sp.add_argument("--consumer", default="LOCAL_CLI")
        sp.add_argument("--limit", type=int, default=10)
        if name == "passages":
            sp.add_argument("--mode", default="hybrid", choices=("hybrid", "lexical"))
            sp.add_argument("--model-dir")
            sp.add_argument("--model-manifest")
    sp = sub.add_parser("passage").add_subparsers(dest="action", required=True).add_parser("get")
    for flag in ("--data-root", "--scope-id", "--chunk-id", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--consumer", default="LOCAL_CLI")
    sp = sub.add_parser("rag").add_subparsers(dest="action", required=True).add_parser("context")
    for flag in ("--data-root", "--scope-id", "--query", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--consumer", default="LOCAL_CLI")
    sp.add_argument("--max-passages", type=int, default=8)
    sp.add_argument("--max-chars", type=int, default=6000)
    sp.add_argument("--mode", default="hybrid", choices=("hybrid", "lexical"))
    sp.add_argument("--model-dir")
    sp.add_argument("--model-manifest")


def _run_retrieval(args, kw):
    from .retrieval.service import RetrievalService
    model = {k: v for k, v in (("model_dir", getattr(args, "model_dir", None)), ("manifest_file", getattr(args, "model_manifest", None))) if v}
    if args.command == "retrieval" and args.action == "access-log":
        with open_reader(args.data_root, **kw) as kb:
            return 0, RetrievalService(kb).access_log(scope_id=args.scope_id)
    if args.command == "index" and args.action in ("status", "verify"):
        with open_reader(args.data_root, **kw) as kb:
            svc = RetrievalService(kb)
            out = svc.index_status() if args.action == "status" else svc.verify_index()
            return (0 if out.get("state", "PUBLISHED") == "PUBLISHED" and out.get("valid", True) else 3), out
    with open_writer(args.data_root, **kw) as kb:
        svc = RetrievalService(kb)
        if args.command == "retrieval":
            spec = _json_file(args.scope_file)
            if not isinstance(spec, dict):
                raise ValidationError("scope file must hold a JSON object", code="SCOPE_FILE_INVALID")
            return 0, svc.create_scope(operator=args.operator, **spec)
        if args.command == "index":
            if args.action == "purge":
                return 0, svc.purge_index()
            return 0, svc.build_index(operator=args.operator, embedder=args.embedder, embedder_config=model or None)
        if args.command == "search":
            common = dict(scope_id=args.scope_id, query=args.query, consumer=args.consumer, operator=args.operator, limit=args.limit)
            if args.action == "passages":
                return 0, svc.search_passages(mode=args.mode, embedder_config=model or None, **common)
            return 0, (svc.search_papers if args.action == "papers" else svc.search_evidence)(**common)
        if args.command == "passage":
            return 0, svc.get_passage(scope_id=args.scope_id, chunk_id=args.chunk_id, consumer=args.consumer, operator=args.operator)
        return 0, svc.rag_context(scope_id=args.scope_id, query=args.query, consumer=args.consumer, operator=args.operator,
                                  max_passages=args.max_passages, max_chars=args.max_chars, mode=args.mode, embedder_config=model or None)


def _s10_parsers(sub) -> None:
    """S10: rkb ai ... (owner records, read-only AI gateway adapters; NON_AUTHORITATIVE, default deny)."""
    ai = sub.add_parser("ai").add_subparsers(dest="action", required=True)
    sp = ai.add_parser("owner-register")
    for flag in ("--data-root", "--owner", "--reason", "--operator"):
        sp.add_argument(flag, required=True)
    sp = ai.add_parser("profile-create")
    for flag in ("--data-root", "--consumer", "--label", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--scope-id", action="append", required=True)
    sp = ai.add_parser("determine-rights")
    for flag in ("--data-root", "--asset", "--document-version-id", "--basis-ref", "--owner", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--permission", required=True, choices=("external_ai_process", "redistribute"))
    sp.add_argument("--valid-hours", type=int, required=True)
    sp = ai.add_parser("approve")
    for flag in ("--data-root", "--profile-id", "--scope-id", "--reason", "--owner", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--operation", action="append", required=True)
    sp.add_argument("--content-class", action="append", required=True)
    sp.add_argument("--valid-hours", type=int, required=True)
    sp.add_argument("--paper-id", action="append")
    sp = ai.add_parser("revoke")
    for flag in ("--data-root", "--reason"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--target-id")
    sp.add_argument("--operator")
    sp.add_argument("--token-file", help="file holding a revocation token (keeps it out of the process list and shell history)")
    for name in ("status", "access-log"):
        ai.add_parser(name).add_argument("--data-root", required=True)
    for name in ("mcp-serve", "claude-desktop-config"):
        sp = ai.add_parser(name)
        for flag in ("--data-root", "--profile-id"):
            sp.add_argument(flag, required=True)
    sp = ai.add_parser("exchange-export")
    for flag in ("--data-root", "--profile-id", "--scope-id", "--query", "--out-dir", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--valid-hours", type=int, default=1)
    sp.add_argument("--max-passages", type=int, default=6)
    sp.add_argument("--max-chars", type=int, default=4000)
    sp = ai.add_parser("exchange-verify")
    for flag in ("--data-root", "--bundle-dir"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--purge-if-invalid", action="store_true")


def _s11_parsers(sub) -> None:
    """S11: rkb synth ... (scientific synthesis DRAFTS and citation audit; NON_AUTHORITATIVE, local review only)."""
    sy = sub.add_parser("synth").add_subparsers(dest="action", required=True)
    sp = sy.add_parser("scope-create")
    for flag in ("--data-root", "--evidence-scope-id", "--label", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--paper-id", action="append")
    sp.add_argument("--review-policy", choices=("DUAL_INDEPENDENT", "SINGLE_HUMAN"), default="DUAL_INDEPENDENT")
    sp.add_argument("--review-policy-reason")
    sp = sy.add_parser("claim-add")
    for flag in ("--data-root", "--scope-id", "--claim-key", "--spec-file", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--cite", action="append", default=[])
    sp.add_argument("--statement")
    sp = sy.add_parser("audit")
    for flag in ("--data-root", "--scope-id", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--full-report", action="store_true")
    sp = sy.add_parser("review")
    for flag in ("--data-root", "--scope-id", "--audit-id", "--reviewer", "--decision"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--claim-key")
    sp.add_argument("--reason-code", action="append", default=[])
    sp.add_argument("--rationale")
    for name in ("status", "reviews"):
        sp = sy.add_parser(name)
        sp.add_argument("--data-root", required=True)
        sp.add_argument("--scope-id", required=True)
    sp = sy.add_parser("export")
    for flag in ("--data-root", "--scope-id", "--out-dir", "--operator"):
        sp.add_argument(flag, required=True)
    sp = sy.add_parser("share")
    for flag in ("--data-root", "--scope-id", "--destination-class", "--operator"):
        sp.add_argument(flag, required=True)


def _run_synth(args, kw):
    from .synthesis.service import SynthesisService
    with open_writer(args.data_root, **kw) as kb:
        svc = SynthesisService(kb)
        if args.action == "scope-create":
            return 0, svc.create_scope(ev_scope_id=args.evidence_scope_id, operator=args.operator, label=args.label, paper_ids=args.paper_id,
                                       review_policy=args.review_policy, review_policy_reason=args.review_policy_reason)
        if args.action == "claim-add":
            try:
                spec = json.loads(safe_input_file(args.spec_file, max_bytes=64 * 1024).read_text(encoding="utf-8"))
            except ValueError:
                raise ValidationError("spec file must be JSON", code="CLAIM_SPEC_INVALID") from None
            return 0, svc.add_claim(scope_id=args.scope_id, claim_key=args.claim_key, spec=spec, cites=args.cite, operator=args.operator,
                                    statement=args.statement)
        if args.action == "audit":
            out = svc.audit(scope_id=args.scope_id, operator=args.operator)
            if not args.full_report:
                out["report"] = {"claims": [{k: c[k] for k in ("claim_key", "version", "verdict", "marker", "references", "reasons")}
                                            for c in out["report"]["claims"]], "citation_audit": out["report"]["citation_audit"]}
            return 0, out
        if args.action == "review":
            return 0, svc.review(scope_id=args.scope_id, audit_id=args.audit_id, reviewer=args.reviewer, decision=args.decision,
                                 claim_key=args.claim_key, reason_codes=args.reason_code, rationale=args.rationale)
        if args.action == "status":
            return 0, svc.status(scope_id=args.scope_id)
        if args.action == "reviews":
            return 0, svc.reviews(scope_id=args.scope_id)
        if args.action == "export":
            return 0, svc.export(scope_id=args.scope_id, out_dir=args.out_dir, operator=args.operator)
        return 0, svc.share(scope_id=args.scope_id, destination_class=args.destination_class, operator=args.operator)


def _llm_parsers(sub) -> None:
    """ADR_LLM L1: rkb llm ... (provider config check, routing plan, offline simulation; no network, no key is ever printed)."""
    ll = sub.add_parser("llm").add_subparsers(dest="action", required=True)
    sp = ll.add_parser("check-config")
    sp.add_argument("--config", required=True)
    sp = ll.add_parser("plan")
    sp.add_argument("--config", required=True)
    sp.add_argument("--data-class", required=True, choices=("LOCAL_ONLY", "EXTERNAL_NO_TRAINING", "EXTERNAL_ANY"))
    sp = ll.add_parser("simulate")
    sp.add_argument("--config", required=True)
    sp.add_argument("--scenario", choices=("rotation", "breaker", "consensus"), default="rotation")
    sp.add_argument("--data-class", choices=("LOCAL_ONLY", "EXTERNAL_NO_TRAINING", "EXTERNAL_ANY"), default="EXTERNAL_ANY")
    sp = ll.add_parser("ping")
    sp.add_argument("--config", required=True)
    sp = ll.add_parser("extract-evidence")
    for flag in ("--data-root", "--scope-id", "--config", "--operator"):
        sp.add_argument(flag, required=True)
    sp.add_argument("--consensus", action="store_true")
    sp.add_argument("--log")


def _run_llm(args, kw):
    import os
    from .llm import simulate as sim
    from .llm.config import load_config
    from .llm.router import FakeTransport, Router
    config = load_config(args.config)
    if args.action == "check-config":
        return 0, sim.check(config, os.environ)
    if args.action == "plan":
        return 0, {"data_class": args.data_class, "plan": Router(config, FakeTransport({})).plan(args.data_class)}
    if args.action == "simulate":
        return 0, sim.simulate(config, args.scenario, args.data_class)
    from .llm.router import json_object
    from scivetra_llm_transport import LoopbackTransport
    if args.action == "ping":
        rows = []
        for p in config["providers"]:
            if p["data_policy"] != "LOCAL" or not p["enabled"]:
                rows.append({"provider": p["name"], "status": "NOT_TRIED_REMOTE_PROVIDERS_ARE_STAGE_L3" if p["data_policy"] != "LOCAL" else "DISABLED"})
                continue
            one = Router({"providers": [p]}, LoopbackTransport(max_tokens=64))
            out = one.complete("llm.ping", [{"role": "user", "content": 'Reply with exactly this JSON object and nothing else: {"ok": true}'}],
                               "LOCAL_ONLY", validate=json_object)
            row = {"provider": p["name"], "model": p["model"], "status": out["status"], "attempts": out["attempts"]}
            if out["status"] != "OK":
                row["hint"] = ("Is the local model server running and is the model downloaded? Ollama: 'ollama serve' and 'ollama pull "
                               + p["model"] + "'; LM Studio: start its local server.")
            rows.append(row)
        return 0, {"providers": rows, "network": "LOOPBACK_ONLY"}
    from .llm.evidence_extract import extract_scope
    router = Router(config, LoopbackTransport(), log_path=args.log)
    with open_writer(args.data_root, **kw) as kb:
        return 0, extract_scope(kb, scope_id=args.scope_id, operator=args.operator, router=router, consensus=args.consensus)


def _run_ai(args, kw):
    from .ai import exchange
    from .ai.records import OwnerRecords
    from .core.db import KB
    def open_kb():
        return KB(args.data_root, readonly=False, **kw)
    if args.action == "claude-desktop-config":
        with open_reader(args.data_root, **kw) as kb:
            profile, why = OwnerRecords(kb).profile(args.profile_id)
        if profile is None or profile["transport"] != "MCP_STDIO":
            raise PolicyRefusal("not an active CLAUDE_DESKTOP_LOCAL_MCP profile", code=why or "PROFILE_TRANSPORT_MISMATCH")
        return 0, {"claude_desktop_config_mcpServers_entry": {"scivetra-kb": {
            "command": sys.executable, "args": ["-I", "-m", "scivetra_kb", "ai", "mcp-serve", "--data-root", args.data_root, "--profile-id", args.profile_id]}},
            "state": "NOT_TESTED_IN_CLAUDE_DESKTOP", "note": "template only; follow docs/S10_CLAUDE_DESKTOP_GUIDE.md. Content stays denied until an owner approval exists."}
    if args.action == "exchange-export":
        return 0, exchange.export_bundle(open_kb, profile_id=args.profile_id, scope_id=args.scope_id, query=args.query, out_dir=args.out_dir,
                                         operator=args.operator, valid_hours=args.valid_hours, max_passages=args.max_passages, max_chars=args.max_chars)
    if args.action == "exchange-verify":
        out = exchange.verify_bundle(open_kb, bundle_dir=args.bundle_dir, purge=args.purge_if_invalid)
        return (0 if out["verdict"] == "RELEASE_STILL_AUTHORIZED" else 3), out
    if args.action in ("status", "access-log"):
        with open_reader(args.data_root, **kw) as kb:
            if args.action == "status":
                return 0, OwnerRecords(kb).status()
            rows = kb.all("SELECT event_id, request_id, profile_id, consumer, transport, tool, scope_id, approval_id, status, decision, reason_codes_json, "
                          "content_revealed, returned_json, result_sha256, created_at_utc FROM aig_access_event ORDER BY rowid")
            for r in rows:
                r["reason_codes"], r["returned"] = json.loads(r.pop("reason_codes_json")), json.loads(r.pop("returned_json"))
            return 0, {"events": rows, "contains_query_text": False, "contains_document_text": False}
    with open_writer(args.data_root, **kw) as kb:
        rec = OwnerRecords(kb)
        if args.action == "owner-register":
            return 0, rec.register_owner(owner=args.owner, reason=args.reason, operator=args.operator)
        if args.action == "profile-create":
            return 0, rec.create_profile(consumer=args.consumer, scope_ids=args.scope_id, label=args.label, operator=args.operator)
        if args.action == "determine-rights":
            return 0, rec.determine_rights(asset_sha256=args.asset, document_version_id=args.document_version_id, permission=args.permission,
                                           basis_ref=args.basis_ref, valid_hours=args.valid_hours, owner=args.owner, operator=args.operator)
        if args.action == "approve":
            return 0, rec.approve(profile_id=args.profile_id, scope_id=args.scope_id, operations=args.operation, content_classes=args.content_class,
                                  valid_hours=args.valid_hours, reason=args.reason, owner=args.owner, operator=args.operator, paper_ids=args.paper_id)
        if args.token_file:
            token = safe_input_file(args.token_file, max_bytes=1024).read_text(encoding="utf-8").strip()
            return 0, rec.revoke(token=token, reason=args.reason)
        return 0, rec.revoke(target_id=args.target_id, reason=args.reason, operator=args.operator)


def _json_file(path_text: str):
    from .metadata.strict_json import UnsafeJson, decode
    try:
        return decode(safe_input_file(path_text, max_bytes=1024 * 1024).read_bytes())
    except UnsafeJson as exc:
        raise ValidationError("Evidence file is not strict JSON: " + exc.reason, code="EVIDENCE_FILE_INVALID") from None


def _run_s03(args, kw):
    from .assets.service import AssetService
    from .core.backup import create_backup, restore_backup, verify_backup
    from .core.upgrade import upgrade_kb
    from .library import queries
    from .library.service import LibraryService
    if args.command == "backup":
        return 0, create_backup(args.data_root, args.target, clock=kw.get("clock"))
    if args.command == "verify-backup":
        return 0, verify_backup(args.backup)
    if args.command == "restore":
        return 0, restore_backup(args.backup, args.data_root)
    if args.command == "upgrade":
        return 0, upgrade_kb(args.data_root, args.backup_target, **kw)
    if args.command == "reconcile":
        with open_reader(args.data_root, **kw) as kb:
            return 0, queries.reconcile(kb, workspace_label=args.workspace_label, export_dir=args.export_dir)
    read_only = {("library", "review-queue"), ("library", "search"), ("rights", "gate")}
    if (args.command, args.action) in read_only:
        with open_reader(args.data_root, **kw) as kb:
            if args.command == "rights":
                return 0, AssetService(kb).gate(asset_sha256=args.asset, document_version_id=args.document_version_id, action=args.action_name,
                                                quote_chars=args.quote_chars)
            if args.action == "review-queue":
                return 0, queries.review_queue(kb)
            return 0, queries.search(kb, doi=args.doi, title=args.title, paper_id=args.paper_id)
    with open_writer(args.data_root, **kw) as kb:
        if args.command == "library":
            service = LibraryService(kb)
            if args.action == "import-reprovane":
                return 0, service.import_release(args.export_dir, workspace_label=args.workspace_label, operator=args.operator,
                                                 expected_manifest_sha256=args.expected_manifest_sha256)
            if args.action == "decide-link":
                return 0, service.decide_link(link_candidate_id=args.candidate_id, decision=args.decision, operator=args.operator, reason=args.reason)
            if args.action == "resolve-conflict":
                return 0, service.resolve_conflict(conflict_id=args.conflict_id, action=args.action_name, operator=args.operator, reason=args.reason,
                                                   evidence_refs=args.evidence_ref, activate=args.activate_assertion_id,
                                                   withdraw=args.withdraw_assertion_id)
            if args.action == "set-kind":
                return 0, service.set_manifestation_kind(paper_id=args.paper_id, kind=args.kind, operator=args.operator, reason=args.reason,
                                                         claim_refs=args.claim_ref)
            if args.action == "confirm-family-member":
                return 0, service.confirm_family_member(member_id=args.member_id, confirmation=args.confirmation, operator=args.operator,
                                                        reason=args.reason)
            return 0, service.set_workspace_availability(workspace_label=args.workspace_label, availability=args.availability,
                                                         operator=args.operator, reason=args.reason)
        service = AssetService(kb, pypdf_site=getattr(args, "pypdf_site", None), pdf_validator=getattr(args, "pdf_validator", "1"),
                               parser_site=getattr(args, "parser_site", None),
                               parser_allow_privileged=getattr(args, "allow_privileged_account_for_synthetic_input", False))
        if args.command == "asset":
            if args.action == "add-version":
                evidence = _json_file(args.evidence_file) if args.evidence_file else []
                return 0, service.add_document_version(paper_id=args.paper_id, version_class=args.version_class, evidence=evidence,
                                                       operator=args.operator, version_label=args.label)
            if args.action == "register":
                return 0, service.register_asset(fixture_root=args.fixture_root, relative_path=args.file, document_version_id=args.document_version_id,
                                                 operator=args.operator, role=args.role, access_route=args.access_route,
                                                 access_barriers=args.access_barrier, max_bytes=args.max_bytes, parser_timeout=args.parser_timeout,
                                                 require_human_identity=args.require_human_identity)
            return 0, service.review_link(link_id=args.link_id, decision=args.decision, operator=args.operator, reason=args.reason)
        return 0, service.assess_rights(asset_sha256=args.asset, document_version_id=args.document_version_id, evidence=_json_file(args.evidence_file),
                                        operator=args.operator)


def _inputs(args) -> list[str]:
    values = list(args.doi)
    if args.file:
        path = safe_input_file(args.file, max_bytes=8 * 1024 * 1024)
        values += [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line != ""]
    if not values:
        raise ValidationError("Provide --doi or --file", code="NO_INPUT")
    return values


def _transport(args):
    if getattr(args, "live", False):
        LiveTransportNotAuthorized()  # raises PolicyRefusal(LIVE_ACCESS_NOT_AUTHORIZED)
    if not args.transport_fixtures:
        raise PolicyRefusal("No transport configured; S02 performs no network access", code="NO_TRANSPORT_CONFIGURED")
    return FixtureTransport.from_directory(args.transport_fixtures)


def _show(data_root: str, doi_text: str, *, clock=None, ids=None) -> dict:
    parse = parse_doi(doi_text, DOI_IDENTIFIER_FIELD, OPERATOR_OR_TABULAR_INPUT)
    out = {"input": doi_text, "parse": parse.as_dict()}
    if parse.status != VALID:
        out["note"] = "No lookup for non-VALID input; candidates are shown for human review only"
        return out
    key = parse.comparison_key
    with open_reader(data_root, clock=clock, ids=ids) as kb:
        store = MetadataStore(kb)
        integrity = IntegrityService(store)
        out["papers"] = store.papers_for_doi_key(key)
        out["assertions"] = kb.all("SELECT assertion_id, paper_id, namespace, value_original, value_normalized, status, asserted_by "
                                   "FROM identity_assertion WHERE namespace='doi' AND value_normalized=?", (key,))
        out["registration_agency"] = store.latest_ra_claim(key)
        snapshots = kb.all("SELECT snapshot_id, provider, http_status, outcome, outcome_reason, retrieved_at_utc, response_bytes_sha256 "
                           "FROM provider_snapshot WHERE json_extract(request_identity_json,'$.doi_key')=? ORDER BY rowid", (key,))
        out["snapshots"] = snapshots
        latest_found = {}
        for s in snapshots:
            if s["outcome"] == "FOUND":
                latest_found[s["provider"]] = s["snapshot_id"]
        out["claims"] = {provider: [dict(field=r["field"], value=json.loads(r["value_json"]), path=r["source_field_path"], licence=r["licence_class"])
                                    for r in kb.all("SELECT * FROM metadata_claim WHERE snapshot_id=? ORDER BY rowid", (sid,))]
                         for provider, sid in latest_found.items()}
        out["metadata_changes"] = kb.all("SELECT change_id, provider, prior_snapshot_id, new_snapshot_id, diff_json FROM metadata_change_event "
                                         "WHERE new_snapshot_id IN (SELECT snapshot_id FROM provider_snapshot WHERE json_extract(request_identity_json,'$.doi_key')=?)", (key,))
        out["integrity"] = integrity.view("doi", key)
        out["integrity_events"] = kb.all("SELECT event_id, event_type, event_subtype, notice_doi, reported_by, record_id, first_snapshot_id, "
                                         "source_field_path FROM integrity_event WHERE subject_namespace='doi' AND subject_value=? ORDER BY rowid", (key,))
        out["review_tasks"] = kb.all("SELECT task_id, trigger, event_ids_json, context_json FROM review_task WHERE subject_namespace='doi' AND subject_value=?", (key,))
        out["warnings"] = kb.all("SELECT kind, key_a, key_b FROM identifier_warning WHERE key_a=? OR key_b=?", (key, key))
        out["normalization_divergence"] = kb.all("SELECT source_ref, rv_value_original, rv_value_normalized, kb_status, kb_comparison_key, "
                                                 "divergence_kind FROM normalization_divergence WHERE kb_comparison_key=?", (key,))
    return out


def run(argv=None, *, clock=None, ids=None, sleeper=None, jitter=None) -> tuple[int, dict]:
    args = _parser().parse_args(argv)
    kw = dict(clock=clock, ids=ids)
    if args.command == "init":
        return 0, init_kb(args.data_root, **kw)
    if args.command == "doctor":
        report = doctor(args.data_root, full=args.full)
        return (0 if report["ok"] else 3), report
    if args.command == "bridge":
        return _run_bridge(args, kw)
    if args.command == "journal":
        return _run_journal(args, kw, sleeper=sleeper, jitter=jitter)
    if args.command == "fte":
        return _run_fte(args, kw)
    if args.command == "parse":
        return _run_parse(args, kw)
    if args.command == "screen":
        return _run_screen(args, kw)
    if args.command == "evidence":
        return _run_evidence(args, kw)
    if args.command in ("retrieval", "index", "search", "passage", "rag"):
        return _run_retrieval(args, kw)
    if args.command == "ai":
        return _run_ai(args, kw)
    if args.command == "synth":
        return _run_synth(args, kw)
    if args.command == "llm":
        return _run_llm(args, kw)
    if args.command in ("backup", "verify-backup", "restore", "upgrade", "reconcile", "library", "asset", "rights"):
        return _run_s03(args, kw)
    if args.command == "metadata":
        if args.action == "show":
            return 0, _show(args.data_root, args.doi, **kw)
        if args.action == "import-reprovane":
            with open_writer(args.data_root, **kw) as kb:
                return 0, import_corpus_export(kb, args.export_dir, workspace_label=args.workspace_label,
                                               expected_manifest_sha256=args.expected_manifest_sha256)
        transport = _transport(args)
        with open_writer(args.data_root, **kw) as kb:
            service = MetadataService(kb, transport, command="metadata " + args.action, sleeper=sleeper, jitter=jitter,
                                      allow_cn_probe=getattr(args, "allow_cn_probe", False),
                                      **({"max_requests": args.max_requests} if hasattr(args, "max_requests") else {}))
            if args.action == "refresh":
                return 0, {"run_id": service.run_id, "refresh": service.refresh(older_than_days=args.older_than, integrity_only=args.integrity_only)}
            parses = service.parse_inputs(_inputs(args), OPERATOR_OR_TABULAR_INPUT)
            results = service.route(parses) if args.action == "route" else service.fetch(parses)
            out = {"run_id": service.run_id, "results": results}
            if getattr(args, "allow_public_pool", False):
                out["note"] = "--allow-public-pool has no effect without authorized live access"
            return 0, out
    if args.command == "integrity":
        with open_writer(args.data_root, **kw) as kb:
            service = IntegrityService(MetadataStore(kb))
            with kb.transaction():
                if args.action == "register-document":
                    data = safe_input_file(args.file, max_bytes=4 * 1024 * 1024).read_bytes()
                    return 0, service.register_document(args.operator, data, args.label)
                if args.action == "counter-evidence":
                    return 0, service.record_counter_evidence(operator=args.operator, opposes_event_id=args.event_id, kind=args.kind,
                                                              primary_document_ref=args.primary_document_ref,
                                                              evidence_anchor=json.loads(args.anchor_json), statement_summary=args.statement)
                parse = parse_doi(args.subject_doi, DOI_IDENTIFIER_FIELD, OPERATOR_OR_TABULAR_INPUT)
                if parse.status != VALID:
                    raise ValidationError("Review subject DOI must parse as VALID", code="SUBJECT_DOI_INVALID")
                return 0, service.record_review(operator=args.operator, subject_namespace="doi", subject_value=parse.comparison_key,
                                                event_ids=args.event_id, trigger=args.trigger, decision=args.decision, reason=args.reason,
                                                counter_evidence_ids=args.counter_evidence_id, task_id=args.task_id)
    raise ValidationError("Unknown command")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:2] == ["ai", "mcp-serve"]:              # stdio MCP server: stdout carries protocol messages only, never a CLI JSON line
        from .ai.mcp_stdio import serve
        try:
            args = _parser().parse_args(argv)
        except SystemExit as exc:
            return int(exc.code or 2)
        return serve(args.data_root, args.profile_id)
    try:
        code, payload = run(argv)
        payload = {"ok": code == 0, **payload} if isinstance(payload, dict) and "ok" not in payload else payload
    except KBError as exc:
        code, payload = exc.exit_code, {"ok": False, "error": exc.code, "message": str(exc)}
        print(f"rkb: {exc.code}", file=sys.stderr)
    except (json.JSONDecodeError, ValueError) as exc:
        code, payload = 2, {"ok": False, "error": "INVALID_ARGUMENT", "message": exc.__class__.__name__}
        print("rkb: INVALID_ARGUMENT", file=sys.stderr)
    sys.stdout.write(canonical_json(payload) + "\n")
    return code
