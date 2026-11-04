# SciVetra KB user guide — start here

This guide is for people who are **not IT specialists**. If you can click, copy (Ctrl+C / ⌘C) and paste (Ctrl+V / ⌘V), you can install and use SciVetra. Every step has a box of commands to copy exactly, plus what you should see when it worked.

It covers **Windows, macOS and Linux**. When a step differs, it is marked 🪟 **Windows**, 🍎 **macOS** or 🐧 **Linux**.

## What is SciVetra KB?

SciVetra KB is a **research knowledge base that runs on your own computer**. It is built for people who do systematic or literature reviews, and it helps you:

| Task | What SciVetra does |
|---|---|
| Keep a clean library of papers | DOIs and versions; tracks retractions and corrections; never silently merges two papers |
| Bring in PDFs | Takes PDFs downloaded with PaperBatchDownloader and checks each file before storing it |
| Respect usage rights | Records, for each document, whether you may store, process, quote, show or share it. The default is **no**. |
| Read PDFs safely | Reads them in an isolated "sandbox" process and keeps the text with exact page and character positions |
| Record scientific evidence | Values, results and limitations, each with its location in the paper. Reviewers check and accept them. |
| Search | Only across papers you are allowed to use; every result is cited |
| Write synthesis drafts | Every claim is either **cited** to accepted evidence or visibly marked **`[UNVERIFIED]`** |

**Three promises:**
1. **It works offline.** Your data never leaves your computer.
2. **The machine proposes; people decide.** Every decision has a human name on it, and self-review is refused.
3. **Nothing disappears silently.** History is append-only, and corrections are new versions.

Read [How SciVetra works](11-how-scivetra-works.md) for the full picture and tips for using it well.

## Follow these steps in order

| Step | Guide | Time |
|---|---|---|
| 1 | [Install Python](01-install-python.md) | 10 min |
| 2 | [The terminal and security prompts (read if you see a red error or a warning)](02-terminal-and-security.md) | 5 min |
| 3 | [Download and install SciVetra](03-download-and-install.md) | 10 min |
| 4 | [Try the demo on sample data](05-demo.md) | 10 min |
| 5 | [First steps: work window, your first knowledge base, reading results](04-first-steps.md) | 10 min |
| 6 | [How SciVetra works, and how to use it effectively](11-how-scivetra-works.md) | 15 min |
| 7 | [Everyday use: commands to copy](06-everyday-use.md) | when needed |
| 8 | [Backup, restore and upgrade](07-backup-restore-upgrade.md) | when needed |
| 9 | [Troubleshooting](08-troubleshooting.md) | when needed |
| 10 | [Glossary](09-glossary.md) | when needed |
| 11 | [Limits and safety](10-limits-and-safety.md) | read once |

## The fastest path (if Python is already installed)

| | 🪟 Windows | 🍎 macOS / 🐧 Linux |
|---|---|---|
| 1. Put the code in | `C:\SciVetra\scivetra-kb` | `~/SciVetra/scivetra-kb` |
| 2. Install | double-click **`1-INSTALL.cmd`** | `bash 1-install.sh` |
| 3. Demo | double-click **`2-TRY-DEMO.cmd`** | `bash 2-try-demo.sh` |
| 4. Every working session | double-click **`3-OPEN-WORK-WINDOW.cmd`** | `bash 3-work-shell.sh` |

## Your folders after installation

```
SciVetra/                 (C:\SciVetra on Windows, ~/SciVetra on macOS and Linux)
  scivetra-kb/            the source code you downloaded (do not edit anything here)
  env/                    SciVetra's private Python "virtual environment" (created by the installer)
  data/                   your knowledge bases (one folder per project)
  backups/                your backups
  output/                 exported files (drafts, reports, PDF download lists)
  packages/               the downloaded PDF parser
```

*Vietnamese speakers: a Vietnamese version of this guide is in [`../huong-dan/`](../huong-dan/00-BAT-DAU-TU-DAY.md).*
