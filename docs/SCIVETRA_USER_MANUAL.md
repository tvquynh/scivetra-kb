# SciVetra KB user manual (1.0.0, local release candidate)

This manual walks a beginner through the whole SciVetra companion Research Knowledge Base (KB), from an empty folder to an audited research synthesis draft. Every stage has a detailed guide; this manual shows how the stages connect and which safety rules apply everywhere.

**Status.** This is a **local release candidate**. It is not published, and it is not meant for public distribution until the owner approves a release (ADR_S12). Use synthetic or owner-approved data only. Real research PDFs, live AI services, remote integrations and public sharing are **not enabled**.

## 1. What SciVetra KB is (and is not)

SciVetra KB is a local, offline knowledge base that sits **next to** two other tools. It never changes them:

| Tool | Role |
|---|---|
| **Reprovane** | System of record for systematic-review screening and SEALED corpora (human decisions) |
| **PaperBatchDownloader (PBD)** | Downloads the PDFs that a human requested |
| **SciVetra KB (`rkb`)** | Metadata, a research library, PDF parsing, AI-assisted *proposals*, evidence records, search and synthesis drafts, all with provenance and rights checks |

**Ground rules that hold in every stage:**

1. **Humans decide.** AI output is a *proposal*, marked `NON_AUTHORITATIVE`. Screening decisions, evidence acceptance and draft attestations are made by registered human reviewers, and self-review is refused.
2. **Rights are checked at every use.** A rights assessment decides what may be stored, processed, shown, exported or sent to an AI (gates A1–A10). If rights change, the next command sees the change. Denied content is withheld, never truncated or guessed.
3. **Append-only history.** Records are never edited in place: a correction is a new version, and every decision keeps its provenance.
4. **No network.** `rkb` has no live network client. Metadata providers are offline fixtures (`--transport-fixtures`), and `--live` returns `LIVE_ACCESS_NOT_AUTHORIZED`.
5. **Owner gates** (OAG) guard decisions that only the project owner can take:

| Gate | What it guards |
|---|---|
| OAG-1 / OAG-2 | The real data root `E:\ResearchKB` and its backups. `rkb` refuses that root (`PROTECTED_DATA_ROOT`). |
| OAG-3 | Contact email for live metadata APIs |
| OAG-4 | Any PBD change |
| OAG-5 | Any Reprovane migration |
| OAG-6 | Cloud AI processing of full text |
| OAG-7 | Sharing evidence or synthesis packages |
| OAG-9 | Paid APIs |
| OAG-10 | Package name and public distribution |

## 2. Install (Windows, no internet needed)

**Simplest:** follow the step-by-step user guide in `guide/` (Windows, macOS, Linux; Vietnamese in `huong-dan/`). Windows: put the source at `C:\SciVetra\scivetra-kb` and double-click `1-INSTALL.cmd`. macOS / Linux: `bash 1-install.sh`. The installer creates a private Python environment, links `rkb` and fetches and verifies the parser. The manual steps below install the release wheel instead.

You need Python 3.11 or newer.

1. **Unzip the release** and open PowerShell in the `dist` folder:

   ```powershell
   py -3.11 -m venv $HOME\scivetra-venv
   & $HOME\scivetra-venv\Scripts\python.exe -m pip install --no-index --no-deps .\scivetra_kb_dev-1.0.0-py3-none-any.whl
   & $HOME\scivetra-venv\Scripts\rkb.exe --version
   ```

2. **Optional: the PDF parser (S06).** Install the pinned, hash-checked official wheel shipped in `reference/dependencies/`:

   ```powershell
   & $HOME\scivetra-venv\Scripts\python.exe -m pip install --no-index --find-links ..\reference\dependencies "pypdf==6.12.1"
   rkb parse runtime-check
   ```

   `runtime-check` must report `CONTENT_MATCHES_OFFICIAL_WHEEL`. Without the parser, everything except parsing still works, and `rkb parse` refuses with `PARSER_NOT_INSTALLED`.

3. **Create a practice KB:**

   ```powershell
   rkb init --data-root E:\SciVetraPractice\kb
   rkb doctor --data-root E:\SciVetraPractice\kb
   ```

   Every command prints one JSON document. An error looks like `{"ok": false, "error": "<CODE>", ...}`, with exit code 2 (refused) or 3 (integrity).

The complete list of commands and options is in `COMMAND_REFERENCE.md`.

## 3. The pipeline at a glance

```
S02 metadata ─► S03 library + assets + rights ─► S04 bridge (PBD) / S04J journal inventories
      ─► S05 full-text eligibility (staging) ─► S06 PDF parsing ─► S07 AI screening proposals
      ─► S08 evidence records ─► S09 search / cited context ─► S10 AI gateway (gated) ─► S11 synthesis drafts
```

| Stage | Commands | What you get | Guide |
|---|---|---|---|
| S02 | `rkb metadata route / fetch / show / refresh`, `rkb integrity …`, `rkb metadata import-reprovane` | DOI records from Crossref / DataCite snapshots (offline fixtures), retraction and correction events, human counter-evidence | `CLI_GUIDE.md` |
| S03 | `rkb library …`, `rkb asset …`, `rkb rights …` | A global library with link review (no automatic merges); document versions; PDF intake; rights assessments and gates | `CLI_GUIDE.md`, `ADR_S03.md` |
| S04 | `rkb bridge export / import / status / close` | A PBD download list from a Reprovane export; PBD results imported as checked PDFs | `BRIDGE_USER_GUIDE.md` |
| S04J | `rkb journal …` | A journal DOI inventory with coverage statements (discovery only, never screening) | `JOURNAL_HARVESTING_GUIDE.md` |
| S05 | `rkb fte …` | A full-text eligibility candidate workflow on one SEALED Reprovane release: human decisions, adjudication, locked verifiable export (NON_AUTHORITATIVE) | `FTE_USER_GUIDE.md` |
| S06 | `rkb parse …` | Sandboxed, rights-gated text extraction into page-anchored parse artifacts | `PARSE_USER_GUIDE.md` |
| S07 | `rkb screen …` | AI screening *proposals* (deterministic mock, no real model), blind-first human review | `SCREENING_USER_GUIDE.md` |
| S08 | `rkb evidence …` | Typed evidence drafts with anchors, structural verification and independent human acceptance | `EVIDENCE_USER_GUIDE.md` |
| S09 | `rkb index …`, `rkb retrieval …`, `rkb search …`, `rkb rag context`, `rkb passage get` | Rights-checked local search and cited context packs | `RETRIEVAL_USER_GUIDE.md` |
| S10 | `rkb ai …` | A default-deny gateway for Claude Desktop (local MCP, NOT_TESTED in the real app) and a ChatGPT file exchange (manual upload only), behind A7 / A8 and OAG-6 | `S10_CLAUDE_DESKTOP_GUIDE.md`, `S10_CHATGPT_FILE_EXCHANGE_GUIDE.md`, `S10_OWNER_APPROVAL_GUIDE.md` |
| S11 | `rkb synth …` | Synthesis drafts in which every claim is cited to accepted evidence or visibly marked `[UNVERIFIED]` / `[CONFLICTED]`, with human review and LOCAL_REVIEW_ONLY exports | `S11_SYNTHESIS_GUIDE.md` |

## 4. A typical journey (synthetic practice data)

1. **Metadata and library.** Route and fetch DOIs from fixture snapshots (`rkb metadata route`, `rkb metadata fetch --transport-fixtures DIR`). Import a Reprovane corpus export (`rkb library import-reprovane`) and work through the link review queue (`rkb library review-queue`, `rkb library decide-link`).
2. **Get the PDFs.** Export a download list for PBD (`rkb bridge export`). Import PBD's result folder (`rkb bridge import`): every PDF is checked structurally and against its title page before it enters the store.
3. **Rights.** Record what each file may be used for (`rkb rights assess`) and check a gate (`rkb rights gate`). The default is DENY.
4. **Eligibility.** Create a full-text eligibility run from a SEALED Reprovane release (`rkb fte create`). Reviewers decide (`rkb fte decide`), conflicts are adjudicated (`rkb fte adjudicate`), and the run is locked and exported (`rkb fte lock`, `rkb fte export`, `rkb fte verify`). This is KB staging, not an official corpus.
5. **Parse.** `rkb parse run` extracts page-anchored text in a sandbox; `rkb parse verify-anchor` re-checks a quote position.
6. **Screening proposals (optional).** `rkb screen scope-create`, `build-packs`, `run-mock`, then blind human judgements (`initial-judgement`, `reveal`, `approve`).
7. **Evidence.** Register a profile (`rkb evidence profile-register`) and create a scope on FT_INCLUDE papers (`scope-create`). Then build packs, extract (`run-mock` or `import`), verify, reveal and have an independent reviewer accept (`review`, `adjudicate`).
8. **Search.** `rkb index build`, then `rkb search passages` or `rkb rag context`. Every hit is rights-checked when it is returned.
9. **Synthesis.** Create a scope over the accepted evidence (`rkb synth scope-create`), add typed claims (`claim-add`), audit (`audit`), have a different person review (`review`), and export the review copy (`export`). External sharing (`share`) is always refused.

## 5. Everyday safety habits

- **Back up** before anything important (`rkb backup`), and check the backup (`rkb verify-backup`). Upgrades make their own backup first. See `OPERATOR_RUNBOOK.md`.
- **Read the codes.** `STALE`, `BLOCKED_RIGHTS`, `WITHHELD`, `[UNVERIFIED]` and `NOT_AUTHORITATIVE` are features: they tell you what cannot be trusted or shown right now.
- **Never paste PDF text** into a rationale, reason code or claim statement where the rights forbid storing excerpts; `rkb` refuses it.
- **Use a fresh folder** for every practice KB and every export. Exports refuse existing folders and anything inside the KB.

## 6. What is not in this release

- Real AI models or APIs (local or cloud).
- Live metadata APIs.
- OCR.
- Real-world use of the Claude Desktop or ChatGPT apps (NOT_TESTED).
- A Windows OS sandbox equal to Linux for untrusted PDFs.
- Public distribution.

See `CAPABILITY_MATRIX.md` and `OPEN_ISSUES.md`.
