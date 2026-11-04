# rkb parse: local PDF parsing (S06, 0.6.0.dev2)

`rkb parse` reads a PDF that is already registered in your KB and stores its text as a **ParseArtifact**: page-by-page text
with exact anchors you can verify later. It works only on your machine, only with synthetic or owner-approved PDFs, and only
when the rights record allows local processing. It never decides paper identity or eligibility.

Use a practice folder under `E:\ResearchPlatformDev` (for example `E:\ResearchPlatformDev\SciVetra\S06_practice\kb`).
Never point `--data-root` at `E:\ResearchKB`: production data is not approved (gate OAG-2).

## 1. Install the parser (once)

The parser is an optional, exactly pinned extra:

Install it with its official hash (the file is `pypdf-6.12.1-py3-none-any.whl`; for review it is shipped in
`reference/dependencies/`):

```
pip install --no-index --find-links FOLDER_WITH_THE_WHEEL --require-hashes -r requirements-pdf.txt
```

where `requirements-pdf.txt` contains one line:
`pypdf==6.12.1 --hash=sha256:8fa2a2321cf16247ed848bd7c97f193a60c08670d04abed5b0138327e51c43b0`. Check it:

```
rkb parse runtime-check
```

It must print `"version": "6.12.1"` and `"provenance": "CONTENT_MATCHES_OFFICIAL_WHEEL"` (or
`OFFICIAL_WHEEL_SHA256_VERIFIED` when `--parser-site` points at the wheel file itself). Anything else is refused:
`PARSER_METADATA_MISMATCH` (folder, METADATA and `_version.py` disagree), `PARSER_VERSION_NOT_PINNED`, `PARSER_SITE_TAMPERED`
(RECORD and files differ), `PARSER_BUILD_NOT_APPROVED` (files differ from the official wheel), `PARSER_WHEEL_NOT_OFFICIAL`,
`PARSER_NOT_INSTALLED`. The parser can also be given with `--parser-site DIR_OR_WHEEL` or `SCIVETRA_PYPDF_SITE`.

## 2. Register modern PDFs (C17)

Many valid PDFs use cross-reference streams or object streams, which the legacy validator rejects. Use validator 2:

```
rkb asset register --data-root KB --fixture-root FOLDER --file paper.pdf --document-version-id dv_... --operator you --pdf-validator 2
```

All old safety checks still apply (HTML pages, broken header/EOF, encryption, corrupt structure).

## 3. Before parsing: identity and rights

- Confirm the link by a human: `rkb asset review-link --link-id pal_... --decision CONFIRM ...`.
- Record a rights assessment that allows local processing (for example a verified CC BY licence): `rkb rights assess ...`.

## 4. Parse

Dry run first (opens no bytes, writes nothing):

```
rkb parse run --data-root KB --asset SHA256 --link-id pal_... --purpose RESEARCH_PARSE --operator you --dry-run
```

Then run it. On **Windows** you must accept that only a language-level network guard exists:

```
rkb parse run --data-root KB --asset SHA256 --link-id pal_... --purpose RESEARCH_PARSE --operator you --accept-language-level-network-guard
```

Without that flag the run is refused with `OS_NETWORK_ISOLATION_UNAVAILABLE`. Use the flag only for synthetic or
owner-approved files.

`--purpose IDENTITY_INSPECTION` parses an unconfirmed link only to help a human check identity; its output is always
`NOT_ELIGIBLE_FOR_EVIDENCE`. Optional: `--timeout SECONDS`, `--memory-mb N`, `--recheck` (re-parse and require identical bytes).

Outcomes: `PRODUCED`, `REUSED` (same input, parser and config), `REFUSED` (a gate said no; nothing was opened), `FAILED`
(the sandbox rejected the file). Exit code 3 for REFUSED/FAILED.

## 5. Look at and verify results

```
rkb parse status --data-root KB --asset SHA256
rkb parse show   --data-root KB --artifact-id pza_...                      (metadata, flags, anchors; no text)
rkb parse show   --data-root KB --artifact-id pza_... --with-text --page 1 (text, only while rights allow)
rkb parse verify --data-root KB --artifact-id pza_...                      (re-hash + every anchor)
rkb parse verify-anchor --data-root KB --artifact-id pza_... --page 1 --start 0 --end 20 --text-sha256 HEX
```

Anchors: page numbers start at 1; `start`/`end` count Unicode characters in the stored page text; the hash is SHA-256 of the
UTF-8 slice. If the rights record later stops allowing local processing, `show` withholds the text and reports
`stale_derived_content`.

## 6. Export

The default export is **metadata-only** (no text):

```
rkb parse export --data-root KB --artifact-id pza_... --out-dir E:\ResearchPlatformDev\SciVetra\S06_practice\exports --operator you
```

A local text copy needs `--include-text --reason "why"`, current rights that allow local processing and local storage, and a
link that is not rejected. The KB records the authorization BEFORE any text file is written and the completion after; if
anything fails, no text folder is left behind. `rkb parse export-status --data-root KB` lists interrupted exports
(`recovery_required`); running the same export again reconciles them. Do not share or upload exported text.

## 7. Flags you will see

`READING_ORDER_IS_CONTENT_STREAM_ORDER`, `BBOX_NOT_AVAILABLE`, `CITATIONS_NOT_PARSED` (always); `MULTI_COLUMN_SUSPECTED`,
`TABLE_LAYOUT_SUSPECTED`, `READING_ORDER_UNCERTAIN`, `FORMULA_CONTENT_UNCERTAIN`, `FONT_DECODE_UNCERTAIN`,
`SMALL_TEXT_PRESENT`, `NO_TEXT_LAYER`, `IMAGE_ONLY_PAGE`, `OCR_NOT_PERFORMED`, `MIXED_TEXT_AND_NO_TEXT_PAGES`,
`INCREMENTAL_UPDATES_PRESENT`, `ACTIVE_CONTENT_PRESENT_NOT_EXECUTED`, `PARSER_WARNINGS_PRESENT`. They are heuristics: check
the PDF yourself before relying on tables, formulas or reading order.

## 8. Error codes

Refusals: `ASSET_NOT_IN_KB`, `LINK_NOT_FOR_ASSET`, `IDENTITY_MISMATCH`, `IDENTITY_NOT_HUMAN_CONFIRMED`,
`LINK_ROLE_NOT_MAIN_TEXT`, `RIGHTS_NO_RIGHTS_ASSESSMENT`, `RIGHTS_LOCAL_PROCESS_UNDETERMINED`, `RIGHTS_LOCAL_PROCESS_DENY`,
`RIGHTS_ASSESSMENT_STALE`, `OS_NETWORK_ISOLATION_UNAVAILABLE`, `PARSER_NOT_INSTALLED`, `PARSER_VERSION_NOT_PINNED`,
`PARSER_SITE_TAMPERED`, `PARSER_SANDBOX_UNAVAILABLE`.
Failures: `HTML_OR_CHALLENGE`, `NOT_PDF_HEADER`, `TRUNCATED_NO_EOF`, `ENCRYPTED`, `ZERO_PAGES`, `PARSER_REJECTED`,
`PAGE_LIMIT_EXCEEDED`, `OUTPUT_LIMIT_EXCEEDED`, `DECOMPRESSION_LIMIT_EXCEEDED`, `RESOURCE_LIMIT_EXCEEDED`, `PARSER_TIMEOUT`,
`SANDBOX_REFUSED_EVENT`, `PARSER_CHILD_FAILED`, `CAS_MISSING`, `CAS_HASH_MISMATCH`, `STAGED_COPY_MISMATCH`,
`STAGED_COPY_CHANGED`, `CAS_CHANGED_DURING_PARSE`, `PARSE_NONDETERMINISTIC`.
Exports: `TEXT_EXPORT_REFUSED`, `OUT_DIR_INVALID`, `EXPORT_FOLDER_EXISTS_DIFFERENT`, `EXPORT_FOLDER_UNAUDITED`,
`EXPORT_FAILED_ROLLED_BACK`. Parser identity: `PARSER_METADATA_MISMATCH`, `PARSER_BUILD_NOT_APPROVED`, `PARSER_WHEEL_NOT_OFFICIAL`,
`PARSER_PROVENANCE_UNVERIFIED`, `PARSER_IDENTITY_MISMATCH`. POSIX root: `PARSER_PRIVILEGED_ACCOUNT_REFUSED`.

## 9. Limits

No OCR (scanned pages stay `NO_TEXT_LAYER`); no bounding boxes; reading order, tables and formulas are flagged, not
reconstructed; encrypted PDFs are refused; Windows has no OS network isolation; the Linux network namespace is untested here.
