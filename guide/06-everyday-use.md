# Everyday use — commands to copy

Type everything in the **work window** ([first steps](04-first-steps.md)).

**First, set your variables once per window** (4.2):

| | Paste |
|---|---|
| 🪟 Windows | `$KB = "C:\SciVetra\data\project-1"; $BACKUPS = "C:\SciVetra\backups"; $OUT = "C:\SciVetra\output"` |
| 🍎🐧 macOS / Linux | `KB=~/SciVetra/data/project-1; BACKUPS=~/SciVetra/backups; OUT=~/SciVetra/output` |

With these set, the commands below are **identical on Windows, macOS and Linux**.

**How to fill in a command:** replace each `<ANGLE-BRACKET>` value with your own, and **delete the brackets**. For example, `<YOUR-NAME>` becomes `alex`.

`<YOUR-NAME>` is your audit label: it records who did what. Use the same short label every time.

> **Real data needs the project owner's approval.** This release has been tested with synthetic data. Before using real projects or real PDFs, read [Limits and safety](10-limits-and-safety.md).

## A. Look after your knowledge base (works right away)

| Task | Command |
|---|---|
| Create a KB | `rkb init --data-root "$KB"` |
| Quick health check | `rkb doctor --data-root "$KB"` |
| Thorough check (re-hashes every file) | `rkb doctor --data-root "$KB" --full` |
| Back up | `rkb backup --data-root "$KB" --target "$BACKUPS"` |
| Check a backup | `rkb verify-backup --backup <BACKUP-FOLDER>` |
| Restore into a **new** KB | `rkb restore --backup <BACKUP-FOLDER> --data-root <NEW-KB-FOLDER>` |
| Show the version | `rkb --version` |
| Help for any command | `rkb <command> --help`, e.g. `rkb synth audit --help` |

**List your backups:** 🪟 `dir $BACKUPS`, 🍎🐧 `ls "$BACKUPS"`.

## B. Library of papers (from Reprovane)

**You need:** a "Stage-11 corpus export" folder from Reprovane.

| Task | Command |
|---|---|
| Import the paper list | `rkb library import-reprovane --data-root "$KB" --export-dir <EXPORT-FOLDER> --workspace-label <PROJECT-NAME> --operator <YOUR-NAME>` |
| Find by DOI | `rkb library search --data-root "$KB" --doi <DOI>` |
| Find by title | `rkb library search --data-root "$KB" --title "<PART-OF-TITLE>"` |
| Items waiting for a human decision | `rkb library review-queue --data-root "$KB"` |
| Decide a proposed link | `rkb library decide-link --data-root "$KB" --candidate-id <ID> --decision <DECISION> --operator <YOUR-NAME> --reason "<WHY>"` |

## C. Getting PDFs with PaperBatchDownloader (PBD)

**You need:** Reprovane's "Stage-11B full-text export" folder, and PBD.

1. **Make the download list.** `--out-dir` must be a **new** folder.

   ```
   rkb bridge export --data-root "$KB" --source-dir <STAGE-11B-FOLDER> --kind rv-manual-queue --workspace-label <PROJECT-NAME> --out-dir <NEW-FOLDER> --operator <YOUR-NAME>
   ```

2. **Run PBD.** In PBD, load the `pbd_input_….csv` file from that folder, and let PBD save the PDFs into the `pbd_output_…` folder next to it.
3. **Bring the results back.** `<BATCH-ID>` is the `batch_id` printed in step 1; it starts with `ab_`.

   ```
   rkb bridge import --data-root "$KB" --batch-id <BATCH-ID> --result-csv <PBD-RESULT-CSV> --pdf-folder <PBD-PDF-FOLDER> --operator <YOUR-NAME>
   rkb bridge status --data-root "$KB" --batch-id <BATCH-ID>
   ```

Every PDF is checked structurally and against its title page before it enters the KB. Details: `docs/BRIDGE_USER_GUIDE.md`.

## D. Usage rights and reading PDFs

| Task | Command |
|---|---|
| Is the PDF parser ready? | `rkb parse runtime-check` |
| Is an action allowed for a file? (`A2` = process locally) | `rkb rights gate --data-root "$KB" --asset <FILE-SHA256> --document-version-id <VERSION-ID> --action A2` |
| Record usage rights | `rkb rights assess --data-root "$KB" --asset <FILE-SHA256> --document-version-id <VERSION-ID> --evidence-file <RIGHTS-JSON> --operator <YOUR-NAME>` |
| Read a PDF (needs the A2 right) | `rkb parse run --data-root "$KB" --asset <FILE-SHA256> --link-id <LINK-ID> --operator <YOUR-NAME> --purpose RESEARCH_PARSE` |
| Reading status of a file | `rkb parse status --data-root "$KB" --asset <FILE-SHA256>` |

When the operating system gives no network isolation (🪟 Windows, 🍎 macOS, and Linux without unprivileged namespaces), `rkb parse run` also needs `--accept-language-level-network-guard`. It is your confirmation that the PDF comes from a trusted source ([why](10-limits-and-safety.md#real-pdfs-and-operating-systems)). The IDs (`asset_sha256`, `document_version_id`, `link_id`) are printed by the earlier commands. The rights evidence file format is in `docs/CLI_GUIDE.md` (*Assets and rights*); `docs/PARSE_USER_GUIDE.md` covers parsing.

**Every right is DENIED until assessed.**

## E. Eligibility, evidence and search

These steps are for review teams with **at least two registered reviewers**. The machine never decides.

| Stage | Main commands | Detailed guide |
|---|---|---|
| Full-text eligibility | `rkb fte protocol-create`, `rkb fte create`, `rkb fte decide`, `rkb fte adjudicate`, `rkb fte status`, `rkb fte export` | `docs/FTE_USER_GUIDE.md` |
| Screening proposals (simulated; no real AI) | `rkb screen scope-create`, `rkb screen build-packs`, `rkb screen run-mock`, `rkb screen initial-judgement`, `rkb screen reveal`, `rkb screen approve` | `docs/SCREENING_USER_GUIDE.md` |
| Evidence records | `rkb evidence profile-register`, `rkb evidence scope-create`, `rkb evidence build-packs`, `rkb evidence run-mock`, `rkb evidence verify`, `rkb evidence reveal`, `rkb evidence review` | `docs/EVIDENCE_USER_GUIDE.md` |
| Search | `rkb retrieval scope-create`, `rkb index build`, `rkb search passages`, `rkb rag context` | `docs/RETRIEVAL_USER_GUIDE.md` |

Example search, once you have a retrieval scope `<SCOPE-ID>`:

```
rkb search passages --data-root "$KB" --scope-id <SCOPE-ID> --query "<WORDS>" --operator <YOUR-NAME>
```

## F. Synthesis draft with citation audit

**You need:** evidence accepted by reviewers (section E).

1. **Create a synthesis scope** from an evidence scope `<EVIDENCE-SCOPE-ID>`:

   ```
   rkb synth scope-create --data-root "$KB" --evidence-scope-id <EVIDENCE-SCOPE-ID> --label "<DRAFT-NAME>" --operator <YOUR-NAME> --review-policy SINGLE_HUMAN --review-policy-reason "<WHY-ONE-REVIEWER>"
   ```

   For two independent reviewers, the default and recommended setting, drop the last two options.

2. **Write a claim** as a small JSON file, for example `claim-1.json` in your `output` folder. Open it with 🪟 `notepad "$OUT\claim-1.json"`, 🍎 `open -e "$OUT/claim-1.json"` (create the file first with `touch "$OUT/claim-1.json"`) or 🐧 `nano "$OUT/claim-1.json"`. Paste this and adjust the values:

   ```json
   {"schema": "scivetra-claim-spec/1", "kind": "REPORTED_VALUE",
    "measurement": {"metric": "TPR", "value": "93.1", "unit": "%", "fpr_operating_point": "1", "fpr_operating_point_unit": "%",
                    "dataset": "EMBER", "dataset_year": "2018", "split": "TEST"}}
   ```

3. **Add the claim, audit it and export the draft.** `<EVIDENCE-ID>` starts with `evd_`; use the claim-file path from step 2.

   ```
   rkb synth claim-add --data-root "$KB" --scope-id <SYNTH-SCOPE-ID> --claim-key N1 --spec-file <CLAIM-FILE> --cite <EVIDENCE-ID> --operator <YOUR-NAME>
   rkb synth audit --data-root "$KB" --scope-id <SYNTH-SCOPE-ID> --operator <YOUR-NAME>
   rkb synth export --data-root "$KB" --scope-id <SYNTH-SCOPE-ID> --out-dir <NEW-OUTPUT-FOLDER> --operator <YOUR-NAME>
   ```

4. **A different person** reviews the audit. `<AUDIT-ID>` is printed by `synth audit`.

   ```
   rkb synth review --data-root "$KB" --scope-id <SYNTH-SCOPE-ID> --audit-id <AUDIT-ID> --reviewer <REVIEWER-NAME> --decision ATTEST_DRAFT_ACCURATE --reason-code UNVERIFIED_CLAIMS_ACKNOWLEDGED
   ```

**Tip:** leave out `--statement` and SciVetra writes the sentence from your claim file. Only such generated sentences can be marked SUPPORTED automatically; your own wording needs a reviewer to confirm it. Details: `docs/S11_SYNTHESIS_GUIDE.md`.

## G. All 123 commands

See `docs/COMMAND_REFERENCE.md`.
