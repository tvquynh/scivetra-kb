# Limits and safety — read once

## What SciVetra always does

- Runs **on your computer** and sends nothing over the network.
- **Checks usage rights at every use.** When rights change, the next command sees it.
- **Records every action** (who, when, what) and **never lets history be edited or deleted silently**.
- Treats machine output as a **proposal**; people decide.

## What this release deliberately does not do (yet)

| Not available | Why / what is needed |
|---|---|
| Real AI (ChatGPT, Claude, local models) | Every "AI" step uses a **deterministic simulator**. Real AI needs the project owner's approval (OAG-6). |
| Online DOI lookup (Crossref, DataCite) | Network access is not enabled; it needs the owner's approval (OAG-3) |
| Scanned PDFs (OCR) | Not supported |
| Sharing drafts or evidence outside | Always refused; it needs a sharing policy (OAG-7) |
| Encryption on disk | Use full-disk encryption: 🪟 BitLocker, 🍎 FileVault or 🐧 LUKS |
| Verified user identities | Reviewer names are self-declared labels |

## Real PDFs and operating systems

The PDF reader runs in an isolated sandbox. How strong that isolation is depends on the system:

| System | Isolation | Recommendation |
|---|---|---|
| 🐧 Linux | Strongest: OS resource limits (memory, CPU, no new processes, no file writes). Also a private network namespace where the kernel allows unprivileged namespaces. | **Preferred** for PDFs from unknown or untrusted sources. Do not run SciVetra as `root`. |
| 🍎 macOS | OS resource limits plus a language-level network guard; no OS network barrier | Trusted sources only (confirm with `--accept-language-level-network-guard`) |
| 🪟 Windows | Job-object limits plus a language-level network guard; **not** equivalent to Linux | **Only PDFs from sources you trust** (publishers, official repositories). `rkb parse run` asks you to confirm this with `--accept-language-level-network-guard`. |

## Real project data

The folder `E:\ResearchKB` is reserved for real project data and is **locked** until the owner chooses where it lives and where it is backed up (OAG-1, OAG-2). Until then, work under `SciVetra/data`.

## Release status

- **1.0.0 is the first release.** It passed 654 automated tests on Python 3.11 and 3.14 (Windows) and 8 release gates.
- **Synthesis stage:** awaits an independent architect review; findings will come in a 1.0.x update.
- **Local AI models:** they only *propose* evidence. On a test machine a 7B model found about half of the expected records and made some mistakes, all caught by SciVetra's checks or left for human review.
- **Platform testing:**
  - The macOS and Linux helper scripts (`1-install.sh` and the others) were tested with bash on Windows, not yet on real macOS or Linux machines.
  - The SciVetra code itself was tested on Linux by the independent reviewer for earlier versions of the synthesis stage.
- **Technical details:** `docs/CAPABILITY_MATRIX.md` and `docs/SECURITY_REVIEW_S12.md`.
