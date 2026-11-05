# SciVetra KB — technical overview (developers)

> User guide (step by step): `../guide/00-START-HERE.md`. Front page: `../README.md`.

## Release header

Start with `docs/SCIVETRA_USER_MANUAL.md` (install, pipeline, safety rules) and `docs/OPERATOR_RUNBOOK.md` (backup, upgrade, rollback,
incidents). Every command: `docs/COMMAND_REFERENCE.md`. Exact capability states: `docs/CAPABILITY_MATRIX.md`. Release gates:
`python -I tools/s12_release_readiness.py --work DIR --out FILE.json` (`docs/ADR_S12.md`). Security: `docs/SECURITY_REVIEW_S12.md`.

Not published; not for publication until the owner approves a release (OAG-10). The cumulative history follows.

**License:** Apache License 2.0 (`LICENSE`); copyright 2026 tvquynh (`NOTICE`). The optional pypdf parser is BSD-3-Clause.

# SciVetra KB: S02 metadata + S03 Global Research Library + S04 Download Bridge + S04J Journal DOI Harvesting + S05 Full-text Eligibility candidate workflow + S06 local PDF parsing + S07 AI screening proposals + S08 evidence extraction (release 1.0.2; history below)

Status: **release 1.0.2** (GitHub, not PyPI); current capability states are in `CAPABILITY_MATRIX.md`. The stage notes below are history. Originally a local-only S03 candidate built on the accepted S02 v03. The identifiers are provisional (OAG-10 pending):

- import namespace `scivetra_kb`
- development distribution `scivetra-kb-dev` (classifier `Private :: Do Not Upload`)
- CLI `rkb`

This is a companion knowledge-base component of the SciVetra framework. Reprovane (system of record for human screening and SEALED corpora) and PaperBatchDownloader are separate products and are not modified.

## What it does

- Explicit KB creation (`rkb init`): SQLite + content-addressed store + writer lock. Read commands never create files or migrate.
- DOI parsing per S01 v03 `04` §2: three input contexts and `source_trust`. Trailing suffix spaces are significant, nothing is trimmed silently, and keys use ASCII-only case equivalence.
- DOI Registration Agency routing (doiRA, batches ≤ 20), then Crossref `/works/{doi}` and DataCite `/dois/{doi}` adapters behind an injected transport.
- Immutable provider snapshots (raw bytes in CAS) and source-typed claims. Metadata change events. Idempotent re-import.
- Publication-integrity model per S01 v03 `03` §5.3:
  - affirmative events;
  - coverage, including omission in a later snapshot;
  - human-only, anchored counter-evidence;
  - reviews, and derived views/alerts with no "not retracted" state.
- Read-only import of a Reprovane RV-Stage11 portable corpus export (synthetic fixtures in tests).
- S03: global library with link-policy/1 (no automatic merges; OpenAlex is a signal only), workspace references, identity conflicts and a human review queue.
- S03: DocumentVersions, explicit synthetic PDF intake (sandboxed structural check, CAS, title-page identity check), rights-policy/1 classification and default-deny permission gates.
- S04 (0.4.0.dev1, revised in 0.4.0.dev2): Download Bridge v1 - Reprovane Stage-11B/11 portable export -> PaperBatchDownloader CSV; PBD result CSV + files -> S03 PDF intake, reconciliation, idempotency (`docs/BRIDGE_USER_GUIDE.md`).
- S04J (0.4.1.dev1, revised in 0.4.1.dev2: every verified Crossref ISSN is queried, S04J-F01): Journal DOI Harvesting. A Crossref-first, OpenAlex-enriched, provenance-preserving journal inventory by ISSN:
  resumable cursor crawls, DOI-union reconciliation, conflicts and review tasks, versioned coverage (COMPLETE only relative to
  the recorded queries), and deterministic CSV/JSONL export. It is discovery evidence only, never SLR/screening state.
  Offline simulated providers only (`docs/JOURNAL_HARVESTING_GUIDE.md`, `docs/ADR_S04J.md`).
- S05 (0.5.0.dev2): Full-text Eligibility CANDIDATE workflow (`rkb fte …`, migration 0005). KB-side and **NON_AUTHORITATIVE**: one
  verified, SEALED Reprovane Stage-11 TA release is frozen as the candidate set; human-only reviewers, adjudication, evidence gating
  (HUMAN_CONFIRMED identity, protocol version, rights), acquisition-attempt queue, exact provisional accounting, lock and
  deterministic verifiable export. Never an RV-FTE run, a sealed corpus or official PRISMA counts; journal inventories are
  refused as sources (`docs/FTE_USER_GUIDE.md`, `docs/ADR_S05.md`).
- S06 (0.6.0.dev2): local, rights-gated PDF parsing (`rkb parse …`, migration 0006). Optional pinned parser `pypdf==6.12.1`
  (BSD-3-Clause); a sandboxed child with OS limits; modern xref/object-stream validation (`rkb-pdf-structure/2`, C17);
  immutable, versioned, page-anchored `parse-artifact/1`; current-rights checks on read/export; metadata-only exports by default.
  No OCR, no AI, no network (`docs/PARSE_USER_GUIDE.md`, `docs/ADR_S06.md`).
- S07 (0.7.0.dev2): NON_AUTHORITATIVE AI-assisted screening proposals (`rkb screen …`, migration 0007): explicit processing scopes,
  deterministic rights-checked input packs, a tool-less deterministic mock (no real model, no network), strict response
  validation, anchor verification against S06 text, derived staleness, blind-first human review and advisory approvals
  (`docs/SCREENING_USER_GUIDE.md`, `docs/ADR_S07.md`).
- S11 (0.11.0.dev3; v03 fixes S11-F05 export release race; v02 fixes S11-F01..F04: A5 on statements, COUNT ascertainment, visible-statement checks, signed numbers): scientific synthesis DRAFTS and claim-to-citation audit (`rkb synth …`, migration 0011): pinned synthesis scopes over
  human-accepted S08 evidence, typed claims and comparison keys, deterministic support verdicts with visible [UNVERIFIED] / [CONFLICTED]
  markers, contradiction records, numbered references, append-only human review and LOCAL_REVIEW_ONLY exports; always
  DRAFT_NON_AUTHORITATIVE / PUBLICATION_NOT_AUTHORIZED; external sharing (OAG-7) denied (`docs/ADR_S11.md`, `docs/S11_SYNTHESIS_GUIDE.md`).
- S10 (0.10.0.dev2; v02 fixes S10-F01: A8 / A7 for every content class incl. metadata and anchors): read-only, rights-gated AI integration gateway (`rkb ai …`, migration 0010): default deny; per-request
  A2 / A5 / A7 / A8 and owner gate OAG-6 approvals; a standard-library stdio MCP server for Claude Desktop (NOT_TESTED in the app;
  official SDK interop BLOCKED) and a ChatGPT-compatible file exchange with re-validation before any manual upload; remote MCP,
  tunnels and AI APIs disabled (`docs/ADR_S10.md`, `docs/S10_*_GUIDE.md`, `docs/S10_CAPABILITY_MATRIX.md`).
- S09 (0.9.0.dev2; v02: per-call authorization and CAS re-verification, scope-first candidates): local FTS5 / hybrid search with per-hit rights checks and cited, tool-less RAG context (`rkb index`, `rkb search`,
  `rkb rag`; migration 0009; LOCAL_CLI only; see docs/RETRIEVAL_USER_GUIDE.md).
- S08 (0.8.0.dev3; v02: rights-aware text fields, audited export lifecycle; v03: human-typed excerpts under A4 / A5): typed, provenance-preserving EvidenceDraft records (`rkb evidence …`, migration 0008): versioned
  research-domain profiles, scopes bound to S05 FT_INCLUDE candidates and their exact S06 artifacts, rights-checked input packs,
  a deterministic tool-less mock, strict response validation, structural (not semantic) verification of anchors, numbers, units
  and conditions, derived STALE/BLOCKED states, audited reveal, independent human review, correction and adjudication, and
  metadata-only exports. NON_AUTHORITATIVE (`docs/EVIDENCE_USER_GUIDE.md`, `docs/ADR_S08.md`).
- S03 v02 (0.3.0.dev2): complete Creative Commons URI validation (S03-F01) and CAS referential completeness for backup / verify-backup / restore (S03-F02).
- S03: explicit backup-first `rkb upgrade`, `rkb backup` / `verify-backup` / `restore`, `doctor --full`, read-only `reconcile`. See `docs/CLI_GUIDE.md`.

**No live network.** There is no live transport: `--live` returns `LIVE_ACCESS_NOT_AUTHORIZED`. Offline commands need `--transport-fixtures DIR`.

## Layout

```
src/scivetra_kb/core/       db, migrations (0001, 0002, 0003), CAS, lock, ids, canonical JSON, paths, doctor, manifest, backup, upgrade
src/scivetra_kb/metadata/   doi, divergence, ra, crossref, datacite, cn, transport, store, integrity, service, rv_import
src/scivetra_kb/library/    link-policy/1, library service, review queue, search, reconcile
src/scivetra_kb/assets/     DocumentVersion, intake, sandboxed G1 validator, G3 identity
src/scivetra_kb/rights/     rights-policy/1 and action gates
src/scivetra_kb/bridge/     S04 Download Bridge (PBD profile, export/import/status/close)
src/scivetra_kb/eligibility/ S05 full-text eligibility staging (protocol, service, handoff/export/verify)
src/scivetra_kb/cli.py      rkb command line
tests/                      offline unittest suite (network guard), synthetic fixtures
tools/                      run_offline_tests.py, static_checks.py, build_conformance.py, required_ids.json, rv_native/, s02_upgrade_rehearsal.py, pypdf_crosscheck.py, pbd_contract_audit.py, s04_pilot.py, s04_upgrade_rehearsal.py, s04_cli_smoke.py
docs/                       ADR_S03.md, ADR_S04.md, BRIDGE_USER_GUIDE.md, CLI_GUIDE.md, S02_TEST_ADAPTATIONS.md, S04_TEST_ADAPTATIONS.md, DEFERRED_FIELDS.md, OPEN_ISSUES.md, CHANGELOG.md
```

## Run the tests

```
python -I tools/run_offline_tests.py
```

The runner fails on any failure, error or skip. It writes `evidence/test_results.json` and `evidence/test_run.log`.

See the delivery package's `05_LOCAL_INSTALL_AND_SMOKE_GUIDE.md` for PowerShell 7 steps.
