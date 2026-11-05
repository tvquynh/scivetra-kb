# S12 security review (1.0.0rc1; addendum for 1.0.1 in section 5)

**Scope.** The cumulative `scivetra_kb` package (S02–S11), its CLI, packaging and dependency chain. The reviewer is the developer; this is not an independent audit.

**Method.** Four parts:
- an automated AST scan and secret / forbidden-file scan (`tools/s12_release_readiness.py`, gate G5);
- a manual review of every allowlisted construct;
- a mapping of the S01 v03 threat register (R05 rights leakage, R04 human semantics, network, untrusted PDFs) to its controls and tests;
- the independent architect reproducers of S04J–S11.

## 1. Result

| Area | Finding | Status |
|---|---|---|
| Code execution | No `eval`, `exec`, `__import__`, `pickle`, `marshal`, `shelve`, `os.system`, `os.popen`, `tempfile.mktemp` or `shell=True` anywhere in `src/` | PASS (G5) |
| Network | No network client in `src/`. `urllib` is used only for `urllib.parse` (URL validation). No `socket`, `http`, `ssl` or `urllib.request` outside the parse child's self-test. Tests run under an audit-hook network guard; child processes inherit a guard. | PASS |
| Processes | `subprocess` only in the two PDF sandboxes (`assets/sandbox.py`, `parse/sandbox.py`) and the parse child's self-test. Fixed argument lists, minimal environment, timeouts and resource limits. | PASS (allowlisted) |
| Native calls | `ctypes` only for Windows job-object limits in the two PDF children | PASS (allowlisted) |
| Secrets and paths | No credentials, tokens, private keys or user-profile paths in the source tree (`tools/static_checks.py`); no `.pdf`, `.sqlite`, `.whl` or `.pyc` in the source or the wheel | PASS (G5, G7) |
| Dependencies | Runtime: Python standard library only. Optional: `pypdf==6.12.1` (BSD-3-Clause), installed only from the official wheel with `--require-hashes`, and re-verified against its RECORD before every parse. | PASS (G8) |
| Data at rest | SQLite and a content-addressed store. Append-only record tables (triggers). CAS objects re-hashed on use and by `doctor --full`. **No encryption at rest**: protect the data root with OS permissions or disk encryption. | ACCEPTED RISK (R-S12-01) |
| Identity | Operator and reviewer labels are self-declared, not authenticated. Self-review is refused by label. | ACCEPTED RISK (R-S11-05, R-S12-02) |
| Untrusted PDFs on Windows | The language-level audit hook and job objects are not an OS network barrier equal to Linux | OPEN; real untrusted PDFs must not be parsed on Windows (handoff 05 #6) |
| Rights leakage (R05) | Gates A1–A10 are re-checked at every use: S06 export, S08 reveal / export, S09 per hit, S10 per request (A7 / A8 + OAG-6), S11 A5 projection and release-time currency (S11-F01, F05) | PASS: architect reproducers S08, S08-F03, S09, S10-F01, S11, S11-F05 |
| Human semantics (R04) | AI output is NON_AUTHORITATIVE. Human-only decisions; no self-review. S08 `synthesis_eligible` is not reinterpreted. S11 drafts are PUBLICATION_NOT_AUTHORIZED. | PASS (S07–S11 tests) |
| Prompt injection | Source text and statements are inert data. There is no tool-calling model. The mock models ignore instructions (S07-, S08- and S11-INJECTION tests). | PASS |
| Path safety | Absolute output folders outside the KB only; `..`, UNC, device and protected roots refused; atomic staging and rename | PASS |
| Concurrency | One writer lock per KB; SQLite `BEGIN IMMEDIATE`; S11 export release serialized against writers (SY-D20) | PASS (CAS files outside the lock: R-S11-18) |

## 2. Automated scan (gate G5)

These are rejected anywhere:
- **Calls:** `eval`, `exec`, `__import__`, `os.system`, `os.popen`, `tempfile.mktemp`, `pickle.load(s)`, `marshal.load(s)`, `yaml.load`, and any `shell=` that is not literally `False`.
- **Imports:** `pickle`, `marshal`, `shelve`, `telnetlib`, `ftplib`, `smtplib`, `xmlrpc`, `requests`, `httpx`, `aiohttp`, `paramiko`.

These are restricted to allowlisted modules: `socket`, `http`, `ssl`, `asyncio`, `subprocess`, `ctypes`, `multiprocessing`, `urllib.request`. The results are in `evidence/release_readiness.json`.

## 3. Allowlisted constructs (manual review)

| Module | Construct | Review |
|---|---|---|
| `assets/sandbox.py` | `subprocess.run` | Fixed `[sys.executable, -I, child, …]` argument list; no shell; minimal environment; `timeout`; RLIMIT memory / CPU / processes on POSIX, job object on Windows; output size bounded |
| `parse/sandbox.py` | `subprocess.run` | Same pattern for the parse child; staged copy of the input; limits from the parse profile |
| `scivetra_llm_transport` (separate component, ADR_LLM L2) | `urllib.request` | OpenAI-compatible POST to a **loopback** model server only. The host must be a loopback IP literal (`localhost` is rewritten, no DNS). There are no proxies and no redirects, the answer is size-capped, and remote URLs are refused before any connection. |
| `parse/parse_child.py` | `subprocess`, `socket` | Used only in `--self-test`, to prove the audit hook refuses process creation and sockets after the input is read |
| `assets/pdf_child.py`, `parse/parse_child.py` | `ctypes` | Windows `SetInformationJobObject` limits only; no other library is loaded |

## 4. Residual risks introduced or confirmed in S12

- **R-S12-01. No encryption at rest.** Rely on OS permissions or BitLocker for the data root and backups.
- **R-S12-02. Self-declared identities.** Owner and reviewer labels are not authenticated. Owner signing or two-person rules remain an owner decision.
- **R-S12-03. Developer-run checks.** The security scan is pattern-based and run by the developer. An independent review and a dependency vulnerability check against a current advisory database (network needed) are required before any public release.
- **R-S12-04. Release-candidate status.** The package name is provisional (OAG-10). The classifier `Private :: Do Not Upload` makes index uploads fail by design.

## 5. Addendum 1.0.1 (2026-11-05)

- **Packaging integrity (P0).** 1.0.0 shipped without `src/scivetra_kb/evidence` (unanchored `evidence/` in `.gitignore`; `ignore_patterns("evidence")` in the G5 / G7 copies). Consequence: the G5 secret / forbidden-file scan of a git checkout also skipped that package. Fixed: root-anchored ignores, `copy_tree` excludes generated output at the root only, and G7 fails on any source package file missing from the wheel or sdist, on any unresolved internal import, or on a failing installed-package E2E run.
- **No security control was weakened.** Rights gates, default deny, append-only tables, the network guard and the AST allowlist are unchanged; the S08 files are byte-identical to the S12 release-candidate ZIP.
- **Still not done:** independent security review, online dependency-advisory check, encryption at rest, verified identities (R-S12-01..03). The project is not independently security certified.
