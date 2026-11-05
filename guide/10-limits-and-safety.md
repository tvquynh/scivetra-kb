# Limits and safety — read once

## What SciVetra always does

- Runs **on your computer** and sends nothing over the network.
- **Checks usage rights at every use.** When rights change, the next command sees it.
- **Records every action** (who, when, what) and **never lets history be edited or deleted silently**.
- Treats machine output as a **proposal**; people decide.

## What this release deliberately does not do (yet)

| Not available | Why / what is needed |
|---|---|
| Online AI (ChatGPT, Claude, Groq, Gemini…) | Not called by SciVetra. Screening (S07) uses a **deterministic simulator**. Only **local models on your own computer** (Ollama / LM Studio) can *propose* evidence ([guide 12](12-free-llm-providers.md)). Online AI needs the owner's approval (OAG-6). |
| Smart (semantic) search | Search is by words; the built-in "embedding" is a simple hashing stand-in, **not** an AI that understands meaning |
| Online DOI lookup (Crossref, DataCite) | Network access is not enabled; it needs the owner's approval (OAG-3) |
| Scanned PDFs (OCR) | Not supported |
| Sharing drafts or evidence outside | Always refused; it needs a sharing policy (OAG-7) |
| Encryption on disk | Use full-disk encryption: 🪟 BitLocker or 🐧 LUKS |
| Verified user identities | Reviewer names are self-declared labels |

## Real PDFs and operating systems

The PDF reader runs in an isolated sandbox. How strong that isolation is depends on the system:

| System | Isolation | Recommendation |
|---|---|---|
| 🐧 Linux | Strongest: OS resource limits (memory, CPU, no new processes, no file writes). Also a private network namespace where the kernel allows unprivileged namespaces. | **Preferred** for PDFs from unknown or untrusted sources. Do not run SciVetra as `root`. |
| macOS | macOS is not supported: SciVetra's PDF sandbox cannot start on macOS, so every PDF would be refused. | Use Windows or Linux. |
| 🪟 Windows | Job-object limits plus a language-level network guard; **not** equivalent to Linux | **Only PDFs from sources you trust** (publishers, official repositories). `rkb parse run` asks you to confirm this with `--accept-language-level-network-guard`. |

## Real project data

The folder `E:\ResearchKB` is reserved for real project data and is **locked** until the owner chooses where it lives and where it is backed up (OAG-1, OAG-2). Until then, work under `SciVetra/data`.

## Release status

- **Use 1.0.1 or newer.** 1.0.0 was published without one part of the program (evidence, S08), so evidence, search, synthesis and AI extraction failed in a fresh download. 1.0.1 fixes this and now tests the installed package itself.
- **What exactly works and how it was tested:** `docs/CAPABILITY_MATRIX.md`. "Verified" there means *the number and quote were found in the paper*, not that the scientific claim is true.
- **Synthesis stage:** awaits an independent architect review; findings will come in a 1.0.x update.
- **Local AI models:** they only *propose* evidence. On a test machine a 7B model found about half of the expected records and made some mistakes, all caught by SciVetra's checks or left for human review.
- **Platform testing:**
  - The Linux helper scripts (`1-install.sh` and the others) were tested with bash on Windows, not yet on real Linux machines.
  - The SciVetra code itself was tested on Linux by the independent reviewer for earlier versions of the synthesis stage.
- **Technical details:** `docs/CAPABILITY_MATRIX.md` and `docs/SECURITY_REVIEW_S12.md`.
