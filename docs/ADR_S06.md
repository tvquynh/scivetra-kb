# ADR S06: Local PDF parsing and structured extraction (0.6.0.dev2)

Status: candidate, submitted for independent architect review. Baseline: the accepted S05 v02 source
(`SCIVETRA_S05_FULLTEXT_ELIGIBILITY_v02.zip`, sha256 `95cff052…e69a`). Contracts: S01 v03 sections 03, 05, 08, 09, 10 and 11;
S06 handoff `SCIVETRA_S05_ACCEPT_S06_HANDOFF_v01`.

## PZ-D1. Scope and authority

`rkb parse` turns ONE registered, rights-gated KB PDF asset into an immutable, versioned, page-anchored **ParseArtifact**
(`parse-artifact/1`). It is extraction only: no identity decision, no eligibility vote, no citation/DOI matching, no
synthesis, no AI, no embeddings, no OCR. No S03 or S05 row is written or changed. Parsed text is untrusted data
(`content_trust = UNTRUSTED_DOCUMENT_TEXT_DATA_NOT_INSTRUCTIONS`), never instructions.

## PZ-D2. Parser library choice

| Candidate | Licence | Footprint | Modern xref / object streams | Text + Unicode | Notes | Decision |
|---|---|---|---|---|---|---|
| **pypdf 6.12.1** | **BSD-3-Clause** | pure Python, no dependency on Python >= 3.11 | yes (strict mode refuses broken tables) | ToUnicode CMaps, encodings, text visitor with matrices | decompression limits are module constants that can be lowered | **chosen** |
| pdfminer.six | MIT | pure Python (+ charset-normalizer, cryptography) | yes | very good layout analysis | heavier, more dependencies; layout analysis is interpretation | used only as an independent dev-time **fixture oracle** (`tools/s06_fixture_oracle.py`) |
| pypdfium2 | Apache-2.0 / BSD-3 (PDFium) | native binary wheel | yes | good | native code in the parser process; wheel pinning per platform | not chosen for v01 (native attack surface, per-platform hashes) |
| pikepdf | MPL-2.0 + qpdf (Apache-2.0) | native | yes (structure only) | no text extraction | would need a second library for text | not chosen |
| PyMuPDF (fitz) | **AGPL-3.0 / commercial** | native | yes | excellent | AGPL/commercial: not embedded without an owner licence decision | **excluded** |
| stdlib-only (existing `rkb-pdf-structure/1`) | project | none | **no** (C17) | partial, latin-1 only | kept as the legacy first stage | kept, extended |

Installation: optional extra `pdf = ["pypdf==6.12.1"]` (`pip install pypdf==6.12.1`, or `pip install -e .[pdf]`). The base
package keeps `dependencies = []`; S02–S05 run unchanged without pypdf.

**Identity and provenance (S06 v02, finding S06-F01).** v01 trusted the dist-info folder name and a self-attested RECORD; a
one-file fake was accepted. v02 separates three things:

1. *Identity*: dist-info folder version, METADATA `Name`/`Version` and the package's own `_version.py` must agree with each other
   (`PARSER_METADATA_MISMATCH`) and with the pin (`PARSER_VERSION_NOT_PINNED`).
2. *Integrity*: RECORD must list exactly the package files and every hash must match (`PARSER_SITE_TAMPERED`). This protects
   against damage only; a forger can write a consistent RECORD.
3. *Provenance*: the trust anchor is the reviewed table `parse/approved_parsers.py` (code, not a local manifest). It holds the
   SHA-256 of the official PyPI file `pypdf-6.12.1-py3-none-any.whl`, `8fa2a232…43b0`, **recorded independently by the
   architect**, and the fingerprint of the 56 package files inside that wheel (`23c6a7c7…4bfd`), derived by
   `tools/s06_parser_provenance.py` only after the wheel's SHA-256 matched. A wheel is accepted only if its SHA-256 equals the
   recorded value (`OFFICIAL_WHEEL_SHA256_VERIFIED`, otherwise `PARSER_WHEEL_NOT_OFFICIAL`); an installed copy only if its files
   reproduce the official fingerprint (`CONTENT_MATCHES_OFFICIAL_WHEEL`, otherwise `PARSER_BUILD_NOT_APPROVED`). Without an
   established fingerprint everything is `PARSER_PROVENANCE_UNVERIFIED`.

At run time the parent copies the verified parser into the private run folder and re-verifies the copy; the child computes the
version and package fingerprint of what it ACTUALLY imported (from inside that copy) and the parent discards any result that
does not match (`PARSER_IDENTITY_MISMATCH`, no artifact). Artifacts record `build_sha256`, `provenance` and the official wheel
SHA-256; runs record the parser mode (wheel or site directory). The official wheel was downloaded once from PyPI with the
owner's approval (no other network use); the local pypdf 6.12.1 used in v01 turned out to be file-for-file identical to it.
The wheel is shipped for private review only under `reference/dependencies/` (BSD-3-Clause, licence inside the wheel), never
vendored into the SciVetra package.

## PZ-D3. C17: modern PDF validation without weakening the legacy checks (`rkb-pdf-structure/2`)

`AssetService(pdf_validator="2")` / `rkb asset register --pdf-validator 2`:

1. The legacy stdlib validator v1 runs first, unchanged. Its verdict stands for HTML/challenge pages, missing header, missing
   EOF, encryption, zero pages, timeouts and **every structural rejection** except one: "xref stream or invalid xref
   (unsupported)" / "object stream (unsupported)".
2. Only for that modern-syntax rejection does the pinned pypdf child decide, in strict mode: every cross-reference and
   object-stream entry must resolve, the page tree is walked with bounds and must be cycle-, duplicate- and count-consistent,
   every page content stream must decode within limits, and no encryption.
3. A file the legacy validator ACCEPTS must also pass the pypdf strict validation with the same page count.
4. No parser available => `PARSER_SANDBOX_UNAVAILABLE` (fail closed).

The default for `register_asset` stays v1 so that every accepted S03–S05 behaviour is unchanged; v2 is explicit.
`asset_validation_event.validator_version = '2'` records which rule decided (`decided_by`).

## PZ-D4. Gates before any byte is opened

Order: KB `pdf_asset` exists (a Reprovane hash-only reference or a PBD `Completed` row is not one) → link belongs to the
asset → MISMATCH never parsed → `RESEARCH_PARSE` needs `HUMAN_CONFIRMED` + `MAIN_TEXT` → the CURRENT rights assessment of
(asset, document version) allows A2 `local_process` → OS network isolation or explicit operator acceptance of the
language-level guard → verified parser runtime. Every refusal is an audited `parse_run` (`REFUSED`) and creates no artifact,
no CAS object and no text. `IDENTITY_INSPECTION` may parse an unconfirmed or ambiguous link (never MISMATCH); its output is
pinned `NOT_ELIGIBLE_FOR_EVIDENCE` by a CHECK constraint. Migration 0006 triggers repeat the rights, link and identity gates
in SQL (defense in depth).

## PZ-D5. Isolation (honest per platform)

The parent copies the CAS object (re-hashed first) into a private staging folder while hashing, makes it read-only, starts
`python -I -S -B parse_child.py` with an empty environment, closed stdin and a wall-clock timeout, re-hashes the staged copy
and the CAS object afterwards, and deletes the folder. The child applies, BEFORE reading the input:

| Control | Windows | POSIX |
|---|---|---|
| Memory cap | Job Object `ProcessMemoryLimit` (OS) | `RLIMIT_AS` (OS) |
| CPU time | Job Object `PerProcessUserTimeLimit` (OS) | `RLIMIT_CPU` (OS) |
| No child processes | Job Object `ActiveProcessLimit = 1` (OS) | `RLIMIT_NPROC = 0` (OS) — **does not bind root**: a root account is labelled `NOT_ENFORCED_PRIVILEGED_ACCOUNT` and refused `PARSER_PRIVILEGED_ACCOUNT_REFUSED` unless the operator explicitly accepts it for synthetic input (`--allow-privileged-account-for-synthetic-input`, recorded) |
| No file writes | audit hook | `RLIMIT_FSIZE = 0` (OS) + audit hook |
| No further file opens | audit hook (language level) | audit hook |
| Network | **audit hook only (language level)** | audit hook + `unshare(CLONE_NEWUSER|CLONE_NEWNET)` when the kernel allows (OS) |
| Wall clock | parent kill | parent kill |
| Output size | child cap + parent cap | same |

**Windows has no OS-level network isolation here.** A parse therefore refuses with `OS_NETWORK_ISOLATION_UNAVAILABLE` unless
the operator passes `--accept-language-level-network-guard`, which is recorded in the run. This is acceptable only for
synthetic or owner-approved inputs; real untrusted production PDFs on Windows stay behind gate R22 (AppContainer / firewall
rule / VM). Linux network-namespace isolation is implemented but **NOT_TESTED** on this machine. pypdf's own decompression
limits are lowered to `max_stream_bytes`; page count, objects, chunks and characters are bounded.

PDF actions, JavaScript, links, forms and embedded files are never executed or followed; their presence is reported
(`active_content`, flag `ACTIVE_CONTENT_PRESENT_NOT_EXECUTED`).

## PZ-D6. `parse-artifact/1` and anchors

Canonical JSON (sorted keys, UTF-8, trailing LF) stored in CAS; the row stores `artifact_sha256`. Content: asset hash, parser
name/version/build/licence, extractor `rkb-parse/1`, config and `config_sha256`, status (`PARSED_TEXT`, `PARSED_PARTIAL_TEXT`,
`SCANNED_NO_TEXT`), page count, flags, syntax facts, active content, parser warning counts (no messages), and per page:
`page` (1-based), `status`, `text`, `text_sha256`, `char_count`, flags and ordered blocks (`LINE` blocks with
`start`/`end`, `text_sha256`, `origin_cpt` in 1/100 pt, `font_size_cpt`). **Anchor rule:** `start`/`end` are Unicode code
point offsets `[start, end)` into the stored page text; the anchor's `text_sha256` is the SHA-256 of the UTF-8 slice. Bounding
boxes are not available (`BBOX_NOT_AVAILABLE`). No timestamps, paths or secrets are inside the payload; reruns on the same
input, parser build and config give the same bytes (checked by `--recheck`).

Text source: pypdf `extract_text` (plain mode) visitor chunks, concatenated; normalization only CRLF/CR → LF and NUL removed.
Reading order is the content-stream order (`READING_ORDER_IS_CONTENT_STREAM_ORDER`). Heuristic flags (`rkb-quality-flags/1`):
`MULTI_COLUMN_SUSPECTED`, `TABLE_LAYOUT_SUSPECTED`, `READING_ORDER_UNCERTAIN`, `FORMULA_CONTENT_UNCERTAIN`,
`FONT_DECODE_UNCERTAIN`, `SMALL_TEXT_PRESENT`, `NO_TEXT_LAYER`, `IMAGE_ONLY_PAGE`, `MIXED_TEXT_AND_NO_TEXT_PAGES`,
`OCR_NOT_PERFORMED`, `INCREMENTAL_UPDATES_PRESENT`, `PARSER_WARNINGS_PRESENT`, `CITATIONS_NOT_PARSED`. Nothing is invented: an
image-only page has empty text.

## PZ-D7. Storage, idempotency, versioning (migration 0006, additive)

`parse_artifact` (unique per asset × parser × version × build × extractor × config), `parse_run` (every request), and
`parse_export_event` (explicit local text exports). All append-only (no-update/no-delete triggers). Same key → `REUSED`
(after re-hashing the CAS object); `--recheck` re-parses and requires identical bytes (`PARSE_NONDETERMINISTIC` otherwise);
a different version/config creates a new artifact and the old one stays. CAS first, then one transaction. The derived
object is a schema-6 CAS reference (`CAS_REFERENCE_COLUMNS`), so `doctor --full`, backup, verify-backup and restore cover it.
Migrations 0001–0005 are byte-identical to S05 v02.

## PZ-D8. Rights revocation and reads

Reads (`show --with-text`) and exports re-evaluate the CURRENT assessment. When `local_process` is no longer ALLOW the text
is withheld (`DERIVED_CONTENT_WITHHELD:…`, `stale_derived_content = true`); the immutable artifact and its CAS bytes remain for
audit and still verify. `evidence_eligible_now` additionally needs a RESEARCH_PARSE run on a currently HUMAN_CONFIRMED
MAIN_TEXT link. Locked S05 snapshots contain no parse text and are not touched; any future consumer (S07+) must call the same
current-access check.

## PZ-D9. Exports (state machine, S06 v02 finding S06-F02)

Default `rkb parse export` is **metadata-only** (counts, flags, hashes, anchor ranges; no text). `--include-text` needs
`--reason`, current `local_process` AND `store_locally` = ALLOW and a link that is not MISMATCH; it writes a LOCAL-ONLY notice.

v01 renamed the plaintext folder into place and only then wrote the audit row, so a failed write left unaudited plaintext.
v02 makes the export one append-only state machine in `parse_export_event` (migration 0006, still the unaccepted S06
migration; 0001–0005 unchanged):

1. `REFUSED` — gates failed; no file is written.
2. `AUTHORIZED` — committed **before any plaintext byte is written** outside the KB. If this commit fails, the call raises and
   nothing exists on disk.
3. Files are written into a hidden, exclusively created staging folder `.<export>.partial-<random>` (each file fsynced); rights
   and identity are re-checked; the staging folder is atomically renamed to the export folder.
4. `COMPLETED` — exactly one terminal event per authorization (UNIQUE + trigger that it matches artifact, manifest, target and
   folder). If it cannot be committed, the folder this call made visible is withdrawn.
5. `FAILED` — any failure in 3–4 removes the staging folder and any folder this call made visible, then appends FAILED when the
   database allows it. If the database itself is down, the authorization stays open: `RECOVERY_REQUIRED`
   (`rkb parse export-status`, `doctor --full` warning `PARSE_EXPORT_RECOVERY_REQUIRED`).

Reconciliation on the next identical export: an identical folder with a COMPLETED event is reported, not rewritten; an identical
folder with only an open AUTHORIZED event (a crash after the rename) is COMPLETED as recovered; leftover rkb-named staging
folders of an open authorization (a crash before the rename) are removed and the authorization closed FAILED
(`INTERRUPTED_BEFORE_COMPLETION`); an identical folder with no audit at all is `EXPORT_FOLDER_UNAUDITED` and left untouched; a
different folder is `EXPORT_FOLDER_EXISTS_DIFFERENT` and left untouched. Only rkb-created staging folders and the folder made
visible by the same call are ever deleted; caller-owned files are never touched. The target path is stored only as a hash.

## PZ-D10. Deferred / out of scope

OCR (contract only: `docs/OCR_ADAPTER_CONTRACT.md`), the manual full-text inbox (reuses `rkb-pdf-structure/2` and the parser
child unchanged; `docs/MANUAL_INBOX_CONTRACT.md`), S07 AI screening, S08 extraction, S09 RAG, S10 desktop, live providers,
PBD browsing, Reprovane authority changes, GitHub/PyPI publication, `E:\ResearchKB`.
