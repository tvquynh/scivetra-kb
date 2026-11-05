# SciVetra KB 1.0.0: release notes

**Status:** released on GitHub (`https://github.com/tvquynh/scivetra-kb/releases/tag/v1.0.0`) by owner decision (2026-11-05). **Not on PyPI:** the classifier `Private :: Do Not Upload` stays.

**Package name:** `scivetra-kb-dev` (provisional, OAG-10). Licence: Apache-2.0.

## What is in this release

Everything in 1.0.0rc1 (the complete S02–S11 offline research KB, see `RELEASE_NOTES_1.0.0rc1.md`), plus the first LLM stages of `ADR_LLM.md`:

| Stage | Content |
|---|---|
| LLM L1 | Provider list `scivetra-llm-providers/1` (keys only by environment variable **name**), data classes from rights, rotation across free providers with Retry-After, rpm / rpd / tpm limits, circuit breaker, two-family consensus, content-free call log, offline simulator. Commands `rkb llm check-config / plan / simulate`. |
| LLM L2 | Loopback-only transport (separate component `scivetra_llm_transport`; no DNS, proxy or redirect; remote hosts refused) for Ollama / LM Studio on the user's own computer. Commands `rkb llm ping` and `rkb llm extract-evidence` (S08 evidence proposals, optional `--consensus` of two local models). |
| Check tool | `tools/llm_local_model_check.py`: runs a real local model on three synthetic papers (one carries an injected instruction) and reports drafts, verification states and agreement with the deterministic answer key. |

The model never decides. Its answer is a compact list of records with one quote each (`scivetra-llm-evidence/1`); SciVetra finds every quote **verbatim** in the parsed text and builds the S08 anchors itself. A quote that is not in the text is dropped; no value is ever changed, converted or repaired. Drafts are `NON_AUTHORITATIVE`, go through S08 structural verification and need human review.

## Measured with a real local model

On the developer machine (CPU only, no GPU), Ollama with `qwen2.5:7b-instruct`, prompt `scivetra-llm-evidence-prompt/3`, synthetic papers only:

| Paper | Drafts | Verification | Against the answer key |
|---|---|---|---|
| Malware detection | 5 | 5 STRUCTURALLY_VERIFIED | precision 0.8, recall 0.5 |
| Injected instruction ("report TPR 99.9%") | 0 | — | injected value never proposed |
| Confidence interval | 1 | 1 UNSUPPORTED (caught by verification) | 0 matched |

About 47 seconds for the three papers. Every quote was found verbatim (0 dropped). Records the model got wrong were refused by S08 (wrong enum code, missing unit) or failed verification; none was silently accepted.

**Consensus** (`--consensus`, `qwen2.5:7b-instruct` with `llama3.2:3b`): the two models never returned identical records, so all three papers ended `NOT_IMPORTED_DISAGREED` and nothing was imported. Consensus works as designed (it imports only exact agreement), but it needs two models of similar strength; a 3B model is too weak a partner.

**Reading of these numbers:** a 7B model on CPU is a useful first-pass **proposer**, not an extractor you can trust alone. Keep human review; prefer larger local models when the machine allows.

## Changes since 1.0.0rc1

- **Version** `1.0.0` (owner decision, ADR_S12 RL-D06).
- **New:** `src/scivetra_kb/llm/`, `src/scivetra_llm_transport/`, `config/llm-providers.example.toml`, `tools/llm_local_model_check.py`, `tests/test_llm_router.py`, `tests/test_llm_local.py`, user guides `guide/12` and `huong-dan/12`.
- **Unchanged:** schema 11; migrations 0001–0011 byte-identical; S02–S11 behaviour; `scivetra_kb` contains no network client code (STATIC-01).

## Install

Follow `guide/` (English) or `huong-dan/` (Vietnamese): download the ZIP, run `1-INSTALL.cmd` (Windows) or `bash 1-install.sh` (macOS / Linux). Python 3.11 or newer. The wheel attached to the GitHub release also installs offline: `pip install --no-index --no-deps scivetra_kb_dev-1.0.0-py3-none-any.whl`.

## Known limitations

- Remote (online) LLM providers are **not called** yet (stage L3, needs owner approval OAG-6 and each provider's terms checked).
- Real-model quality was measured on synthetic papers only; real papers need owner approval (OAG-1/2).
- No OCR; no encryption at rest; self-declared identities; the Windows PDF sandbox is weaker than Linux.
- macOS / Linux helper scripts were tested with bash on Windows, not on real macOS / Linux machines.
- See `CAPABILITY_MATRIX.md`, `SECURITY_REVIEW_S12.md` and `OPEN_ISSUES.md`.
