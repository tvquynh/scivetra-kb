# ADR S12: Release candidate (1.0.0rc1)

**Status:** local release candidate, built by the developer under the owner's instruction ("full authority to decide; finish the tools").

**Not done:**
- not an independent acceptance;
- not a public release.

**Baseline:** S11 v03 (`67e84e3b…1eab`), the cumulative S02–S11 source. The independent architect's decision on S11 v03 is pending. Any S11 finding will be fixed in a 1.0.0rc2.

**Normative source:** S01 v03 `11_TEST_AND_ACCEPTANCE_STRATEGY.md` §4. The S12 release exit gate is a full offline suite, a restore / rollback rehearsal, a security review and docs, with no automatic public release. `13` row 8 requires owner release approval.

## RL-D01 What a release is here

A release is a **verifiable local artifact set**:
- a wheel and an sdist built offline from the cumulative source;
- the official pinned parser wheel;
- the release readiness report;
- the documentation;
- a SHA-256 manifest.

Nothing is uploaded. The distribution name stays `scivetra-kb-dev`, with the classifier `Private :: Do Not Upload`, until OAG-10 decides the name and destination. A public release needs, separately:
- the owner's approval;
- an independent security review;
- a dependency advisory check (needs the network);
- the remaining owner gates (OAG-1/2 data root, OAG-6/7 if AI or sharing is to be enabled).

## RL-D02 Version

The version is `1.0.0rc1`. The "1.0" marks the complete S02–S11 feature set of the S01 v03 plan; "rc1" marks that the owner has not approved a release.

The schema is unchanged. Migrations 0001–0011 are byte-identical to S11 v03 and pinned in `tools/s12_migration_hashes.json`. S12 adds no table and no behaviour change to any stage.

## RL-D03 Release readiness gates (`tools/s12_release_readiness.py`)

| Gate | Checks |
|---|---|
| G1 | Version, change log, release notes and the S12 docs exist |
| G2 | Migrations 0001–0011 are contiguous and equal the pinned hashes |
| G3 | Every earlier schema 1…10 upgrades to 11, backup first, with prior tables byte-identical. Doctor `--full` is clean, the rollback file equals the original bytes, and the upgrade backup verifies and restores. |
| G4 | A populated synthetic S05–S11 KB backs up, verifies and restores. Every `syn_`, `ev_`, `rights_` and `parse_` table is byte-identical, and the S11 audit status and review state are the same. |
| G5 | Security scan (AST and static checks), as in `SECURITY_REVIEW_S12.md` |
| G6 | Every `rkb` command is documented. `docs/COMMAND_REFERENCE.md` (generated from the parser) and the S10 / S11 scenario catalogues are current (`--check`). |
| G7 | Offline wheel and sdist. The wheel has 11 migrations and no tests, PDFs or databases, with the private classifier and no runtime dependency. A clean venv installs it `--no-index`; `rkb --version`, `init` and `doctor` run from site-packages. |
| G8 | Dependency inventory: runtime none; pdf extra `pypdf==6.12.1` with the official wheel SHA-256 |

The status is `READY_FOR_OWNER_RELEASE_DECISION` only if every gate passes. A failing or skipped gate gives `NOT_READY`.

The full offline suite and every architect reproducer run separately on Python 3.11 and 3.14. Their logs are part of the release evidence.

## RL-D04 Documentation set

| Document | Content |
|---|---|
| `SCIVETRA_USER_MANUAL.md` | End-to-end beginner manual |
| `OPERATOR_RUNBOOK.md` | Routine, backup, upgrade, rollback, incidents |
| `COMMAND_REFERENCE.md` | Generated command reference |
| `CAPABILITY_MATRIX.md` | Exact state of every capability |
| `SECURITY_REVIEW_S12.md` | Security review |
| `RELEASE_NOTES_1.0.0rc1.md` | Release notes |

The stage guides and ADRs S03–S11 remain the detailed references.

## RL-D05 Not in S12

- Real models or AI APIs.
- Live metadata APIs.
- OCR.
- Real PDFs.
- Changes to Reprovane or PBD.
- Encryption at rest.
- Authenticated identities.
- Any upload.

These need owner decisions, recorded in `OPEN_ISSUES.md` and `CAPABILITY_MATRIX.md`.
