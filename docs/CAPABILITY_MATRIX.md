# SciVetra KB capability matrix (1.0.0rc1)

The states below are exact. **IMPLEMENTED_OFFLINE_TESTED** means the capability is built and proven by the offline suite with synthetic data. Anything else was not tried with real accounts, real services or real research PDFs. The detailed AI-client states are in `S10_CAPABILITY_MATRIX.md`.

**Acceptance status of the stages:**
- S02–S10 v02: accepted by the independent architect.
- S11: submitted as v03; the independent decision on it is pending.
- S12: this local release candidate.

| Stage | Capability | State | Notes |
|---|---|---|---|
| Core | KB init, doctor (`--full`), backup / verify / restore, explicit upgrade 1…10 → 11, rollback file | IMPLEMENTED_OFFLINE_TESTED | Release gate G3 rehearses every upgrade path |
| S02 | DOI parsing, RA routing, Crossref / DataCite snapshot adapters, publication-integrity events | IMPLEMENTED_OFFLINE_TESTED | Offline fixtures only |
| S02 | Live metadata APIs | DISABLED (`LIVE_ACCESS_NOT_AUTHORIZED`) | Needs OAG-3 and a transport implementation |
| S02 / S03 | Reprovane portable export import (read-only) | IMPLEMENTED_OFFLINE_TESTED | Synthetic exports; Reprovane is never modified |
| S03 | Global library, link review, identity conflicts, document versions, PDF intake (sandboxed checks), rights-policy/1 gates | IMPLEMENTED_OFFLINE_TESTED | — |
| S04 | PBD download bridge (CSV export, result import, reconciliation) | IMPLEMENTED_OFFLINE_TESTED | PBD is never modified (OAG-4) |
| S04J | Journal DOI harvesting (Crossref-first, OpenAlex enrichment) | IMPLEMENTED_OFFLINE_TESTED with simulated providers | Live crawling DISABLED |
| S05 | Full-text eligibility candidate workflow (NON_AUTHORITATIVE staging) | IMPLEMENTED_OFFLINE_TESTED | Authoritative RV-FTE needs a Reprovane change (OAG-5) |
| S06 | Local, sandboxed, rights-gated PDF parsing (pinned pypdf 6.12.1) | IMPLEMENTED_OFFLINE_TESTED on synthetic PDFs | Windows sandbox not equal to Linux: real untrusted PDFs on Windows NOT_APPROVED; no OCR |
| S07 | AI screening proposals with a deterministic mock; blind-first human review | IMPLEMENTED_OFFLINE_TESTED | Real models NOT_IMPLEMENTED / OAG-6 |
| S08 | Evidence drafts, structural verification, human acceptance, audited exports | IMPLEMENTED_OFFLINE_TESTED | Real models NOT_IMPLEMENTED |
| S09 | FTS5 / hybrid search, per-hit rights, cited RAG context | IMPLEMENTED_OFFLINE_TESTED | Semantic embeddings NOT_CONFIGURED (`HYBRID_MOCK`) |
| S10 | AI gateway, stdio MCP server, ChatGPT file exchange | IMPLEMENTED_OFFLINE_TESTED | Claude Desktop / ChatGPT apps NOT_TESTED; remote MCP / APIs DISABLED; official MCP SDK interop BLOCKED |
| S11 | Synthesis drafts, citation audit, contradictions, human review, LOCAL_REVIEW_ONLY export | IMPLEMENTED_OFFLINE_TESTED | External sharing DENIED (OAG-7); LLM narrative NOT_IMPLEMENTED |
| S12 | Release readiness gates, command reference, user manual, runbook, security review, offline wheel / sdist | IMPLEMENTED_OFFLINE_TESTED | Public release NOT_PUBLISHED (owner approval, OAG-10) |
| LLM (L1) | Provider config, rights-aware rotation, limits, breaker, consensus, simulator (`rkb llm`) | IMPLEMENTED_OFFLINE_TESTED (fake transport) | Real provider calls NOT_IMPLEMENTED (L2/L3, ADR_LLM) |
| — | Linux run by the developer | NOT_RUN | The architect ran Linux for S11 v01 and v02 |
| — | Encryption at rest | NOT_IMPLEMENTED | R-S12-01 |
