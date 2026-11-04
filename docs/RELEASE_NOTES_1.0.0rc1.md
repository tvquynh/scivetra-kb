# SciVetra KB 1.0.0rc1: release notes (local release candidate)

**Status:** READY_FOR_OWNER_RELEASE_DECISION when `evidence/release_readiness.json` says so. **Not published.**

**Package name:** `scivetra-kb-dev` (provisional, OAG-10), classifier `Private :: Do Not Upload`.

## What is in this release

The complete S02–S11 companion Research KB, offline and local:

| Stage | Content |
|---|---|
| S02 | Metadata and publication integrity |
| S03 | Global library, assets and rights |
| S04 | PaperBatchDownloader bridge |
| S04J | Journal DOI harvesting |
| S05 | Full-text eligibility staging |
| S06 | Sandboxed PDF parsing |
| S07 | AI screening proposals (mock) |
| S08 | Evidence records |
| S09 | Rights-checked search and cited context |
| S10 | Gated AI integration gateway |
| S11 | Scientific synthesis drafts and citation audit |

S12 adds:
- the release readiness tool (`tools/s12_release_readiness.py`, gates G1–G8);
- the generated command reference (`tools/s12_command_reference.py`);
- the user manual, operator runbook, security review and capability matrix;
- an offline wheel and sdist.

## Changes since S11 v03 (0.11.0.dev3)

- **Version** `1.0.0rc1` (marked version lines in tests updated); package description covers S02–S11.
- **No schema or behaviour change:** migrations 0001–0011 byte-identical; no `src/` change other than the version.
- **New:** S12 docs, tools, `tools/s12_migration_hashes.json`, `tests/test_s12_release.py`.

## Install

See `SCIVETRA_USER_MANUAL.md` section 2. Offline install: `pip install --no-index --no-deps scivetra_kb_dev-1.0.0rc1-py3-none-any.whl`. Python 3.11 or newer. No runtime dependency. Optional parser: `pypdf==6.12.1`, from the shipped official wheel.

## Upgrade

`rkb upgrade --data-root R --backup-target T` from any earlier schema (1–10). The schema stays 11 from S11, so S11 KBs need no upgrade. See `OPERATOR_RUNBOOK.md`.

## Known limitations

See `CAPABILITY_MATRIX.md`, `SECURITY_REVIEW_S12.md` section 4 and `OPEN_ISSUES.md`:
- no real AI models or APIs;
- no live metadata APIs;
- no OCR;
- AI desktop apps NOT_TESTED;
- Windows untrusted-PDF sandbox not equal to Linux;
- no encryption at rest;
- self-declared identities;
- S11 v03 awaiting independent acceptance.
