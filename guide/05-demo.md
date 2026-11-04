# Try the demo on sample data

You can see SciVetra work end to end **without any real data**.

| | Start the demo |
|---|---|
| 🪟 Windows | Double-click **`2-TRY-DEMO.cmd`** |
| 🍎🐧 macOS / Linux | `cd ~/SciVetra/scivetra-kb && bash 2-try-demo.sh` |

## Part A (about 1 minute): a practice knowledge base

| Line | Meaning |
|---|---|
| `Created knowledge base: …/data/demo-…` | A new KB was created |
| `Health check: no problems` | The database and the file store are intact |
| `Backed up to: …/backups/rkb-backup-…` | The whole KB was backed up |
| `Backup verified: valid` | Every file of the backup was re-checked, and it can be restored |

## Part B (5–10 minutes, needs the PDF parser): a synthesis draft

SciVetra runs its whole pipeline on **32 simulated papers**: PDFs generated on your computer, about malware-detection results.

1. Each PDF is read in the sandbox.
2. Evidence is extracted: TPR and ROC-AUC values, limitations, confidence intervals.
3. A *simulated* human reviewer accepts the evidence.
4. **284 claims** are checked against the evidence. Many of them are **deliberately wrong**: a wrong number, a wrong dataset, a missing operating point, a reversed comparison, a negative sign…
5. A **draft** is exported.

At the end you see:

```
OK    284 claims from 32 simulated papers
OK    Agreement with the answer key: 100%; wrongly supported claims: 0; citation traceability: 100%
```

| Phrase | Meaning |
|---|---|
| Agreement 100% | Every verdict matches the answer key the generator wrote beforehand |
| Wrongly supported claims: 0 | No wrong claim was accepted as correct |
| Traceability 100% | Every claim is either cited or visibly flagged |

**The draft (`draft.md`) opens automatically:**
- 🪟 in Notepad;
- 🍎 in TextEdit;
- 🐧 in your default editor, when one is configured.

In the draft, each line is one claim:
- **`[1]`, `[2]`…**: supported by evidence, with the reference numbers;
- **`[UNVERIFIED]`**: not confirmed, with the reason (for example `MISMATCH:value` means the number does not match);
- **`[CONFLICTED]`**: the paper contradicts itself.

The results are in `output/demo-draft-<date-time>/`:

| File | Content |
|---|---|
| `s11_evaluation_cases.csv` | One row per claim; opens in Excel, Numbers or LibreOffice |
| `s11_evaluation_summary.json` | The totals |
| `run1/audited_example_draft/…/draft.md` | The draft |
| `evidence_matrix.csv` (next to the draft) | Claims linked to evidence: page and character positions |

> This is **simulated data** for demonstration. The numbers do **not** measure accuracy on real papers.

## Part A only

| | Command |
|---|---|
| 🪟 | `C:\SciVetra\scivetra-kb\2-TRY-DEMO.cmd -PartAOnly` |
| 🍎🐧 | `bash ~/SciVetra/scivetra-kb/2-try-demo.sh --part-a-only` |
