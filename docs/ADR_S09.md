# ADR S09: Local search and rights-gated RAG context (0.9.0.dev2; v01 0.9.0.dev1)

Status: implemented for offline, synthetic development. Baseline: accepted S08 v03. Not accepted until the architect says so.
Scope: S01 v03 09 section 1 (retrieval), the S09 handoff (execution prompt, checklist, architecture and runtime decisions). Out of
scope: desktop / MCP / remote consumers and real models (S10), synthesis (S11), manual full-text intake.

## RS-D1. Authority boundary

Retrieval is a convenience layer over the KB. It never writes an S05 ballot or lock, an S07 proposal, an S08 draft or review, a
Reprovane decision or a corpus membership; the service inserts only into `rt_*` tables (static test, byte-identity test). Every
scope, hit and context is `NON_AUTHORITATIVE`. A retrieval scope is not a screened corpus, an S04J journal-harvest inventory is
never a scope basis, and accepted S08 evidence stays non-authoritative KB staging evidence (`authoritative_corpus = false`,
`synthesis_eligible = false`; S08 flags are not flipped).

## RS-D2. Objects

| Object | Where | Purpose |
|---|---|---|
| `rt_scope` | KB (0009) | explicit, digest-bound scope: `FTE_RUN` (members = S05 candidates bound to link, version, asset and S06 RESEARCH_PARSE artifact, exactly as S08 binds them; FINAL FT_INCLUDE or PROVISIONAL_NON_AUTHORITATIVE with a reason) or `RELEASE_IMPORT` (papers of one imported Reprovane release; METADATA_ONLY) |
| `rt_index_build` | KB (0009) | every index build that reached the publish step: chunker, embedder identity, manifest SHA-256, counts, exclusions |
| `rt_access_event` | KB (0009) | audit of every retrieval call: operation, consumer, mode, build, query SHA-256, returned chunk / draft ids with excerpt hashes and rights views, exclusions, outcome. Never query or document text |
| `index/fts.sqlite` | data root, derived | the search index (`rkb-retrieval-index/1`); disposable, rebuilt from the KB, not in backups |

`0009_local_retrieval.sql` only CREATEs these three tables and their triggers (append-only, basis must exist). 0001–0008 are
byte-identical to S08 v03.

## RS-D3. The derived index

- Engine: SQLite FTS5 (`unicode61 remove_diacritics 2`), BM25 ranking. FTS5 is detected at run time; without it every build and
  search fails with `FTS5_NOT_SUPPORTED` (never a false success).
- Chunker `rkb-chunker/1`: one chunk per S06 text block on one page, split at whitespace above 1000 characters. Each chunk is an
  exact S06 anchor (page, char_start, char_end, SHA-256). 1000 characters (stricter than S01's ~1500) keeps every stored chunk
  within the rights-policy/1 quote limit of gate A3. No section path exists in S06, so `section_path` is null.
- Tables: `chunk` (anchors and hashes of every chunk), `chunk_fts` (contentless FTS5, rows only where A3 allowed storing the
  text), `vec` (float32 vectors, same condition), `paper` / `paper_fts` (metadata facts), `evidence` / `evidence_fts` (currently
  accepted S08 drafts). There is no column of document text: excerpts are always read from the pinned CAS artifact at query
  time.
- Build: deterministic (same KB content → same manifest SHA-256). The new file is written as `.fts.sqlite.building-<random>`,
  its manifest is recorded in `rt_index_build`, then it replaces `fts.sqlite` in one atomic rename. A crash leaves the previous
  index in place. A kept partial file is reported as an incomplete build and is never read; the next build deletes it.
  Incremental: vectors of unchanged chunks are reused when the embedder fingerprint is unchanged.
- Open: read-only. The file must pass `quick_check`, carry state COMPLETE, match its own manifest, and match a build recorded by
  this KB (`kb_instance_id`, build id, manifest). Otherwise it is refused: `INDEX_NOT_BUILT`, `INDEX_CORRUPT` or
  `INDEX_FOREIGN` (copied from another KB). A broken index can never widen access.
- `rkb index verify` additionally runs `integrity_check` and re-verifies every chunk anchor against CAS.
- Backups contain the DB and CAS only. After a restore the index is `INDEX_NOT_BUILT`; a rebuild reproduces the same manifest.

## RS-D4. Rights (S01 v03 05 section 4.3; A2 / A3 / A5 / A7 / A8)

| Point | Gate | Rule |
|---|---|---|
| Build | A2 + currency | only members whose link, version, artifact, identity, S05 eligibility and rights are current are chunked; others are counted in `excluded_by_reason` |
| Build | A3 | chunk text enters FTS and vectors only where A3 allows storing it; otherwise anchors and SHA-256 only (`CHUNK_TEXT_NOT_STORED_GATE_A3`) |
| Query, every hit | currency | scope membership; the member's link / version / artifact / identity / S05 eligibility / A2 rights NOW (`STALE:*`, `BLOCKED:*`); the chunk's artifact equals the member's; the anchor re-verifies against CAS (`ANCHOR_VERIFY_FAILED`) |
| Query, every hit | A5 | `FULL_EXCERPT` (text read from CAS) only while A5 allows; otherwise `ANCHOR_ONLY` (`GATE_A5_DENIED:<reason>`) |
| Metadata | — | `METADATA_ONLY` facts (titles, venues, years, first authors, DOIs, provider label, integrity alert). Abstracts are never indexed; a metadata hit never implies that full text exists or is allowed (`fulltext_implied: false`) |
| Export / remote | A7 / A8 | S09 has no export and no remote consumer |

`UNDETERMINED` and a missing assessment deny. Because every hit is re-checked, a stale index (rights revoked, artifact superseded,
S05 decision changed, link rejected) can never return text; `index status` counts stale entries and a rebuild removes them from
the file.

## RS-D5. Consumers

Only `LOCAL_CLI` exists. `claude-desktop`, `chatgpt-desktop`, MCP, remote APIs, exports and any other value are refused with
`CONSUMER_NOT_PERMITTED` on every entry point. They are not downgraded to a text-free answer. They need gate A8
(`external_ai_process`) and owner gate OAG-6, which belong to S10.

## RS-D6. Embeddings, vectors and hybrid ranking

- Interface `EmbeddingProvider`: `embed_query`, `embed_passages`, `model_id`, `fingerprint`, `dim`, `query_prefix` /
  `passage_prefix`, `max_chars`, `cache_identity()` and `status()`.
- `MockHashEmbedder` (`MOCK_LOCAL_RETRIEVAL`, `rkb-mock-hash-embedding/1`): deterministic feature hashing of words and character
  trigrams. It is a test double, NOT a semantic model; its scores say nothing about semantic retrieval quality.
- `LocalSentenceTransformer`: optional adapter for an owner-supplied local `sentence-transformers` model (candidate
  `intfloat/multilingual-e5-small`).
  - It needs a local folder and a manifest with the SHA-256 of every file in it. Missing, extra or changed files give
    `MODEL_FILES_UNVERIFIED`.
  - It sets `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`, loads with `local_files_only`, and never downloads anything.
  - It uses the E5 prefixes `query: ` / `passage: `; the model truncates inputs at 512 tokens.
  - Status: `NOT_CONFIGURED` without a folder, `DEPENDENCY_MISSING` without the library.
  - **No real model was exercised in S09: the semantic model is NOT_CONFIGURED.**
- Vector backends: bounded exact cosine similarity, `numpy` when importable, otherwise pure Python. Scores are rounded to 6
  decimals with ties broken by id, so both backends rank identically. `sqlite-vec` could implement the same `top_k`; it is not
  shipped or required.
- Hybrid ranking `rkb-rrf/1`:
  - reciprocal rank fusion, k = 60, over the lexical and vector branches, with ties broken by id;
  - vector candidates below cosine 0.30 are not fused;
  - every hit reports its branch ranks;
  - the mode is labelled `LEXICAL_ONLY`, `HYBRID_MOCK` or `HYBRID_LOCAL_MODEL`.
- No LangChain or LlamaIndex dependency.

## RS-D7. Query handling

- User text is never FTS5 syntax. The query is NFKC-normalised and case-folded, split into words, stripped of common English
  function words (unless nothing else is left), capped at 32 distinct words, each emitted as a quoted FTS5 string and joined
  with OR.
- Refused queries: empty, longer than 1000 characters, or containing control, private-use, unassigned or surrogate characters
  (`QUERY_INVALID`, `QUERY_TOO_LONG`).
- `limit` must be 1..50 (`LIMIT_INVALID`). Each branch takes at most 200 candidates before authorization.

## RS-D8. RAG context (`rkb-rag-context/1`)

- Deterministic and tool-less: the passages search runs, then only `FULL_EXCERPT` hits that contain at least half of the
  query's content words (`query_term_coverage` ≥ 0.5; otherwise `CONTEXT_LOW_TERM_COVERAGE`) are taken, in rank order, within
  `max_passages` (1..20) and `max_chars`. This makes the context abstain instead of padding it with weak matches.
- Each passage carries a citation id (`C1`…), its text, the full anchor (paper, version, asset, artifact id and hash, page,
  offsets, chunk id, excerpt SHA-256, chunker), member class, integrity alert, duplicate-text links, rank and score.
- `answer` is always null and `generation` is `NONE_TOOL_LESS_ASSEMBLY`. `numeric_inference` is `NONE`: values, datasets, years,
  splits and operating points stay inside their own passage, and duplicate text in two sources stays two passages.
- Outcomes:
  - `CONTEXT_READY`;
  - `NO_AUTHORIZED_EVIDENCE` (matches exist but are withheld by rights or staleness);
  - `NO_MATCH` (nothing matched well enough).
- Text is marked `UNTRUSTED_DOCUMENT_TEXT_DATA_NOT_INSTRUCTIONS`. Instructions inside documents are data: they change nothing,
  and URLs are never followed.
- The context is printed by the CLI and audited; no file is written.

## RS-D9. S08 evidence and human free text

- `search evidence` indexes and returns only drafts whose current derived state counts as accepted evidence, re-checked per hit
  (a later conflict or rights change removes the hit at once).
- A hit carries non-text typed values and anchors.
- Text fields appear only when stored under A3 and allowed by current A5. `WITHHELD_GATE_A3_DENIED` values never exist in the
  index.
- Quotes are never returned: the passage anchor is the citation.
- Reviewer and adjudicator rationales, reason codes, S08 scope labels and retrieval scope labels are never indexed, whatever
  A4 / A5 say.

## RS-D11. v02 corrections (independent architectural review of v01)

| Finding | Cause in v01 | Decision in v02 | Proof tests |
|---|---|---|---|
| S09-F01 (HIGH) stale authorization cache | `member_problems` results were memoised on the `RetrievalService` instance, so a long-lived instance kept answering with the rights, identity and S05 eligibility it saw first | caches live for **one public call** (`_per_call`): `build_index`, `index_status`, `verify_index`, `search_passages`, `get_passage`, `search_papers`, `search_evidence`, `rag_context` and `member_problems` each start empty and drop the cache on return (a nested call such as `rag_context` → `search_passages` shares its caller's snapshot). Every public operation re-evaluates A2 / A5 / identity / version / artifact / S05 eligibility NOW, in both directions (revocation and reinstatement) | `test_s09_v02_regressions.F01*` |
| S09-F02 (MEDIUM-HIGH) scope filtered after global truncation | candidate ids were taken from the whole index (`LIMIT 1000` passages, `LIMIT 200` papers / evidence) and only then filtered to the scope, so a member ranked behind more out-of-scope matches vanished | the scope is part of the candidate SQL: `… JOIN chunk c … WHERE chunk_fts MATCH ? AND <binding> IN (SELECT value FROM json_each(?))`. Passages and vectors are bound to the exact member binding (paper, artifact, artifact SHA-256, link), evidence to (paper, artifact), papers to the paper id. The scope travels as **one** JSON parameter, so a 5,000-member scope never meets SQLite's bound-variable limit; row look-ups use the same form. When per-hit authorization leaves fewer hits than requested and a branch filled its window, the window grows ×4 (`WINDOW_GROWTH`) until the scope's matches are exhausted; `candidate_window` is reported. BM25 order inside the scope is unchanged (scores are corpus statistics, not filter-dependent) | `test_s09_v02_regressions.F02*` |
| S09-F03 (HIGH) stale PDF artifact cache | parsed S06 artifacts were memoised on the instance after their first CAS read, so removed or corrupted CAS objects kept serving passages | CAS sources are re-read per public call. `member_problems` now includes the source check: the S06 artifact must read back from CAS (SHA-256 re-hashed by the store) and belong to the member's asset, and the source PDF asset must exist and re-hash to its SHA-256. Failure BLOCKs the member — `BLOCKED:SOURCE_INTEGRITY:PARSE_ARTIFACT:<CAS_MISSING / CAS_HASH_MISMATCH / …>` or `BLOCKED:SOURCE_INTEGRITY:PDF_ASSET:<…>` — in search, passage, evidence, context, status and build (which excludes the member instead of failing). `index verify` also re-hashes every source PDF | `test_s09_v02_regressions.F03*` |

Cost: each public call re-hashes the source PDF of every member it touches (once per call). Index files, migrations, the chunker,
RRF and the context schema are unchanged; an index built by v01 is read by v02 as is.

## RS-D10. Not in S09

- Real embeddings and real LLMs (owner approval, licence, pinning, evaluation).
- Desktop / MCP / remote consumers (A8, OAG-6, S10).
- Official Reprovane FTE handoff and PRISMA counts.
- Windows OS isolation for untrusted PDFs (S06 gate).
- Manual Edge / PaperBatchDownloader intake (separate workstream).
