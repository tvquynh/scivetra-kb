# SciVetra KB

**A research knowledge base that runs on your own computer**, for people who do systematic reviews and other literature reviews. Your data stays on your machine, document usage rights are respected, and **people always make the decisions**.

## What SciVetra helps you do

- **Manage a library of papers:** DOIs, versions, retractions and corrections. Two papers are never merged by mistake.
- **Take in PDF files** downloaded with PaperBatchDownloader, checking each file before it is stored.
- **Record usage rights** for each document (may it be stored, quoted, shared…). The default is *no*.
- **Read PDFs in an isolated sandbox** and keep the text with page numbers and positions.
- **Record scientific evidence** (numbers, results, limitations) for a reviewer to check and accept.
- **Search** the papers you have rights to.
- **Draft a synthesis:** every statement is cited, or clearly marked `[UNVERIFIED]`.
- **Optional local AI:** a model on your own computer (Ollama / LM Studio) can *propose* evidence. A person still reviews every proposal.

## Supported systems

| System | Status |
|---|---|
| 🪟 Windows 10 / 11 | Supported |
| 🐧 Linux | Supported |
| macOS | **Not supported:** the PDF sandbox cannot start on macOS, so every PDF would be refused |

You need **Python 3.11 or newer**.

## Get started in 4 steps (no programming needed)

| | Step | 🪟 Windows | 🐧 Linux | Guide |
|---|---|---|---|---|
| 1 | Install Python | python.org installer | your package manager | [guide/01-install-python.md](guide/01-install-python.md) |
| 2 | Download SciVetra and unzip it | to `C:\SciVetra\scivetra-kb` | to `~/SciVetra/scivetra-kb` | [guide/03-download-and-install.md](guide/03-download-and-install.md) |
| 3 | Install | double-click **`1-INSTALL.cmd`** | `bash 1-install.sh` | automatic, about 2 minutes |
| 4 | Try the demo on sample data | double-click **`2-TRY-DEMO.cmd`** | `bash 2-try-demo.sh` | [guide/05-demo.md](guide/05-demo.md) |

After that, open a work window each time you use SciVetra: double-click **`3-OPEN-WORK-WINDOW.cmd`** (Windows) or run `bash 3-work-shell.sh` (Linux).

👉 **Full step-by-step guide with commands to copy: [guide/00-START-HERE.md](guide/00-START-HERE.md)**

## Guide contents

0. [Start here](guide/00-START-HERE.md)
1. [Install Python](guide/01-install-python.md)
2. [Terminal and security settings](guide/02-terminal-and-security.md)
3. [Download and install SciVetra](guide/03-download-and-install.md)
4. [First steps](guide/04-first-steps.md)
5. [Try the demo](guide/05-demo.md)
6. [Everyday use](guide/06-everyday-use.md)
7. [Backup, restore and upgrade](guide/07-backup-restore-upgrade.md)
8. [Troubleshooting](guide/08-troubleshooting.md)
9. [Glossary](guide/09-glossary.md)
10. [Limits and safety](guide/10-limits-and-safety.md)
11. [How SciVetra works and how to use it well](guide/11-how-scivetra-works.md)
12. [Free LLM providers and local models (stages L1–L2)](guide/12-free-llm-providers.md)

## Status

- **Version 1.0.2**, released on GitHub; not on PyPI. **Do not use 1.0.0**: it lacks the S08 evidence package.
- **Tested:** the automated test suite and 8 release gates on Windows and Linux, Python 3.11 and 3.14 (GitHub Actions), including clean installs of the package. What works, and how it was tested: [`docs/CAPABILITY_MATRIX.md`](docs/CAPABILITY_MATRIX.md).
- **AI:** only models on your own computer, and they only *propose*. No online AI service is called.
- **Real research data and PDFs** need the project owner's approval. See [Limits and safety](guide/10-limits-and-safety.md).
- **Licence:** [Apache 2.0](LICENSE).

## Folder layout

| Folder / file | For |
|---|---|
| `1-INSTALL.cmd`, `2-TRY-DEMO.cmd`, `3-OPEN-WORK-WINDOW.cmd` | Windows users: double-click |
| `1-install.sh`, `2-try-demo.sh`, `3-work-shell.sh` | Linux users: `bash <file>` |
| `guide/` | User guide |
| `scripts/` | Scripts that the double-click files call |
| `docs/` | Technical documentation: architecture, design decisions, detailed stage guides |
| `src/` | Source code |
| `tests/`, `tools/` | Automated tests and release tools |

## For developers and reviewers

| If you want to | Read / run |
|---|---|
| See every command | `docs/COMMAND_REFERENCE.md` |
| Understand the design | `docs/TECHNICAL_OVERVIEW.md`, `docs/ADR_S*.md`, `docs/ADR_LLM.md` |
| Review security | `docs/SECURITY_REVIEW_S12.md` |
| Run the tests | `python -I tools/run_offline_tests.py` |
| Run the release gates | `python -I tools/s12_release_readiness.py --work DIR --out FILE.json --dist DIR` |

The runtime uses the Python standard library only, plus the optional pinned `pypdf` parser.

Licensed under the [Apache License 2.0](LICENSE).
