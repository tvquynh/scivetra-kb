# SciVetra KB capability matrix (1.0.2)

This file is the **single source of truth** for what works. README, user guides, release notes and the security review point here and must not claim more.

## States

| State | Meaning |
|---|---|
| `IMPLEMENTED_AND_CLEAN_INSTALL_TESTED` | Built; its tests pass in the offline suite **and** again against the wheel installed in a clean venv, run from outside the checkout (release gate G7) |
| `IMPLEMENTED_SYNTHETIC_TESTED` | Built; proven by the offline suite with synthetic data and fixtures only |
| `IMPLEMENTED_REAL_LOCAL_MODEL_SYNTHETIC_DATA` | Run with a real local model (Ollama) on synthetic papers only |
| `IMPLEMENTED_BUT_NOT_TESTED_IN_REAL_APP` | Built and tested with a test client, never with the real third-party app |
| `NOT_IMPLEMENTED` | Not built |
| `DISABLED_BY_POLICY` | Built or stubbed, refused by design until a policy changes |
| `BLOCKED_OWNER_APPROVAL` | Needs an owner decision (OAG-*) before it may be built or used |

None of these states means **production-ready**, **scientifically validated on real papers** or **independently security certified**. "Verified" in SciVetra always means **structural** verification (schema, anchor, number and unit found in the quote), never that a claim is semantically true: see `ADR_S08.md` and `ADR_S11.md`.

**Acceptance of the stages:** S02–S10 v02 accepted by the independent architect; S11 v03 decision pending; 1.0.0 (2026-11-05) shipped without the S08 package — fixed in 1.0.1 (`RELEASE_NOTES_1.0.1.md`); 1.0.2 adds the portability fixes of the first CI run (`RELEASE_NOTES_1.0.2.md`).

## Capabilities

| Stage | Capability | Entry point | State | Evidence | Limits / condition to enable |
|---|---|---|---|---|---|
| Core | KB init, doctor (`--full`), backup / verify / restore, upgrade 1…10 → 11, rollback file | `rkb init / doctor / backup / restore / upgrade` | IMPLEMENTED_AND_CLEAN_INSTALL_TESTED (init, doctor); IMPLEMENTED_SYNTHETIC_TESTED (rest) | G3, G4, G7 | — |
| S02 | DOI parsing, RA routing, Crossref / DataCite snapshot adapters, integrity events | `rkb metadata …`, `rkb integrity …` | IMPLEMENTED_SYNTHETIC_TESTED | offline suite | Offline fixtures only |
| S02 | Live metadata APIs | `metadata/transport.py` | DISABLED_BY_POLICY (`LIVE_ACCESS_NOT_AUTHORIZED`); live transport NOT_IMPLEMENTED | S02 tests | BLOCKED_OWNER_APPROVAL (OAG-3) |
| S02 / S03 | Reprovane portable export import (read-only) | `rkb library import-reprovane` | IMPLEMENTED_SYNTHETIC_TESTED | offline suite | Reprovane is never modified |
| S03 | Global library, link review, identity conflicts, versions, PDF intake, rights-policy/1 | `rkb library / asset / rights …` | IMPLEMENTED_SYNTHETIC_TESTED | offline suite | Default deny |
| S04 | PBD download bridge | `rkb bridge …` | IMPLEMENTED_SYNTHETIC_TESTED | offline suite | PBD never modified (OAG-4) |
| S04J | Journal DOI harvesting | `rkb journal …` | IMPLEMENTED_SYNTHETIC_TESTED (simulated Crossref / OpenAlex) | offline suite | Live crawling DISABLED_BY_POLICY; BLOCKED_OWNER_APPROVAL (OAG-3) |
| S05 | Full-text eligibility staging (NON_AUTHORITATIVE, not PRISMA) | `rkb fte …` | IMPLEMENTED_SYNTHETIC_TESTED | offline suite | Authoritative RV-FTE needs Reprovane (OAG-5) |
| S06 | Sandboxed, rights-gated PDF parsing (pinned pypdf 6.12.1) | `rkb parse …` | IMPLEMENTED_SYNTHETIC_TESTED (synthetic PDFs) | offline suite, `tools/s06_install_test.py` | Windows sandbox weaker than Linux; no OCR |
| S07 | AI screening proposals (deterministic mock), blind-first human review | `rkb screen …` | IMPLEMENTED_SYNTHETIC_TESTED (mock only) | offline suite | Real models NOT_IMPLEMENTED for S07 |
| S08 | Evidence drafts, structural verification, human acceptance, audited export | `rkb evidence …` | IMPLEMENTED_AND_CLEAN_INSTALL_TESTED | `tests.test_s08_operational` in G7 | Structural, not semantic, verification |
| S09 | FTS5 / hybrid search, per-hit rights, cited RAG context | `rkb search …`, `rkb rag …` | IMPLEMENTED_AND_CLEAN_INSTALL_TESTED | `tests.test_s09_operational` in G7 | Embeddings are `HYBRID_MOCK` (hashing, **not semantic**) unless a local model folder is provisioned; no generated answer |
| S10 | AI gateway, stdio MCP server, ChatGPT file exchange | `rkb ai …` | IMPLEMENTED_BUT_NOT_TESTED_IN_REAL_APP | offline suite with a test MCP client | Claude Desktop / ChatGPT apps not tested; remote MCP DISABLED_BY_POLICY; default deny (OAG-6) |
| S11 | Synthesis drafts, citation audit, contradictions, human review, LOCAL_REVIEW_ONLY export | `rkb synth …` | IMPLEMENTED_AND_CLEAN_INSTALL_TESTED | `tests.test_s11_operational` in G7 | External sharing DISABLED_BY_POLICY (OAG-7); no LLM narrative |
| S12 | Release gates, command reference, manuals, offline wheel / sdist | `tools/s12_release_readiness.py` | IMPLEMENTED_SYNTHETIC_TESTED | G1–G8 | No PyPI (private classifier) |
| LLM L1 | Provider config, rights-aware rotation, limits, breaker, consensus, simulator | `rkb llm check-config / plan / simulate` | IMPLEMENTED_SYNTHETIC_TESTED (fake transport) | `tests.test_llm_router` | Says nothing about model quality |
| LLM L2 | Loopback-only transport, `rkb llm ping`, S08 extraction (optional consensus) | `rkb llm ping / extract-evidence` | IMPLEMENTED_AND_CLEAN_INSTALL_TESTED (scripted server); IMPLEMENTED_REAL_LOCAL_MODEL_SYNTHETIC_DATA (Ollama qwen2.5:7b-instruct) | `tests.test_llm_local` in G7; `tools/llm_local_model_check.py` | 3 synthetic papers only; REAL_CORPUS_NOT_EVALUATED (OAG-1/2) |
| LLM L3 | Remote (online) providers | — | NOT_IMPLEMENTED | — | BLOCKED_OWNER_APPROVAL (OAG-6, A8, provider terms) |
| — | Operating systems | `.github/workflows/ci.yml` | Windows and Ubuntu, Python 3.11 / 3.14: IMPLEMENTED_SYNTHETIC_TESTED in CI. macOS: NOT_SUPPORTED for PDF intake / parsing (fails closed, R-101-07); other stages not separately verified on macOS | GitHub Actions | Real macOS machines not tested by the developer |
| — | Encryption at rest | — | NOT_IMPLEMENTED | — | Use full-disk encryption (R-S12-01) |
| — | Verified identities | — | NOT_IMPLEMENTED (self-declared labels) | — | R-S12-02 |
