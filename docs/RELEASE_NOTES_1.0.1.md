# SciVetra KB 1.0.1: release notes (packaging fix)

**Status:** local patch release prepared 2026-11-05. Publishing it on GitHub needs the owner's go-ahead. Not on PyPI (classifier `Private :: Do Not Upload`).

## Why 1.0.1

An independent review of tag `v1.0.0` (commit `7a19cc7`) found, and a clean clone plus the published wheel and sdist confirmed, that **the S08 evidence package `src/scivetra_kb/evidence/` was missing** from Git, the wheel and the sdist:

| Where | Result in 1.0.0 |
|---|---|
| `git clone` / GitHub "Download ZIP" | `scivetra_kb.evidence`, `retrieval.service`, `synthesis.service`, `llm.evidence_extract`: `ModuleNotFoundError` |
| Published wheel (110 files) | 6 S08 files missing; same error after a clean install |
| Published sdist | 0 `evidence` members |

So S08 evidence, S09 search / RAG, S11 synthesis and `rkb llm extract-evidence` did not work for anyone who downloaded 1.0.0. The developer tree still had the files, which is why the 654 tests and gates G1–G8 passed there.

**A second defect found while verifying 1.0.1:** on Windows, Git's default `core.autocrlf=true` converted files that SciVetra pins by SHA-256 (migrations, test fixtures, baselines) from LF to CRLF in a fresh `git clone`; 62 of 659 tests then failed (`EXPORT_HASH_MISMATCH`, migration hash differences). `.gitattributes` now keeps exact bytes (`* -text`), except the `.cmd` / `.ps1` / `.sh` launchers.

**Root causes of the missing package:** `.gitignore` had `evidence/` (matches at every level, meant only for the root test-output folder), and the G5 / G7 release copies (and the historical S06 install tool, which is hash-locked by the accepted baselines and kept as is; G7 supersedes it) used `shutil.ignore_patterns("evidence")` (also every level). G7 only ran `rkb --version / init / doctor` after installing, which never imports S08.

## What changed

- `src/scivetra_kb/evidence/` is tracked, byte-identical to the accepted S12 release-candidate ZIP (SHA-256 checked file by file). No behaviour change; schema 11; migrations 0001–0011 unchanged.
- `.gitattributes` keeps exact bytes in every checkout (REL-EXACT-BYTES).
- `.gitignore` anchors `/evidence/`, `/build/`, `/dist/` at the repository root.
- Release gate **G7** now FAILs unless: every source package file is in the wheel **and** the sdist; every internal import (including lazy imports) resolves; a wheel rebuilt from the sdist has the same files; every module imports in clean venvs installed from the wheel and from the sdist; and the S08, S09, S10, S11 and LLM-L2 behavioural tests pass against the installed wheel from a directory outside the checkout with no `PYTHONPATH`.
- `tests/test_release_integrity.py` (REL-*) pins the fixes, including negative cases: with the S08 package removed, the import check and the archive member check both report it.
- CI workflow `.github/workflows/ci.yml` (Windows, Linux, macOS × Python 3.11, 3.14) from a clean checkout. It has not run yet.
- One capability vocabulary in `CAPABILITY_MATRIX.md`; contradictory status text fixed (README, user manual, technical overview, guides 10, CLI help, security review addendum).

## Upgrade

Replace the code (download 1.0.1, or `git pull`), run the installer again. Your `data` and `backups` are untouched; no `rkb upgrade` is needed (schema 11).

## Known limitations

Unchanged from 1.0.0 (`RELEASE_NOTES_1.0.0.md`), plus `OPEN_ISSUES.md` R-101-01..05: AI quality measured on 3 synthetic papers only, consensus needs whole-answer agreement, retrieval quality not measured, Linux / macOS not run until CI runs.
