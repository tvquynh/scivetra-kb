# rkb search and RAG context (S09, 0.9.0.dev2): beginner guide

S09 lets you search the papers you have already screened and parsed, on your own computer. It finds paragraphs, shows exactly
where each one comes from, and collects cited passages for a question. It never answers the question itself, never sends
anything to the internet or to a chat app, and never changes a screening decision.

Use a practice KB with synthetic data under `E:\ResearchPlatformDev`. Never use `E:\ResearchKB`.

## 1. Choose what may be searched (a scope)

A scope lists exactly which papers may be searched. Write a file `scope.json`:

```json
{"basis_kind": "FTE_RUN", "basis_id": "ftr_...", "label": "my practice scope",
 "members": [{"candidate_id": "ftc_...", "parse_artifact_id": "pza_..."}]}
```

`ftr_...` is your S05 full-text eligibility run; each member is a candidate with its parsed PDF (the same pairs you use for S08).
Only candidates with a final FT_INCLUDE decision are accepted, unless you add `"eligibility_mode": "PROVISIONAL_NON_AUTHORITATIVE"`
and a `"provisional_reason"`. For titles and DOIs only, use `"basis_kind": "RELEASE_IMPORT"` with the imported release id and
`[{"paper_id": "gp_..."}]` members.

PowerShell:

```powershell
$kb = 'E:\ResearchPlatformDev\SciVetra\S09_practice\kb'
rkb retrieval scope-create --data-root $kb --scope-file scope.json --operator you
```

The answer contains `"scope_id": "rts_..."`.

## 2. Build the search index

```powershell
rkb index build --data-root $kb --operator you
rkb index status --data-root $kb
rkb index verify --data-root $kb
```

`status` should say `"state": "PUBLISHED"`. The index is a separate file `index\fts.sqlite` that can be deleted and rebuilt at
any time (`rkb index purge`, then `rkb index build`). Rebuild after rights changes, new parses or new decisions; until you do,
searches still hide anything that is no longer allowed.

`--embedder mock` (the default) adds a test-only word-similarity ranking called `MOCK_LOCAL_RETRIEVAL`. It is not a real
"meaning" model. `--embedder none` uses keyword search only. A real local model (`--embedder local-model --model-dir ...
--model-manifest ...`) works only with model files that the owner has supplied and listed with their SHA-256; nothing is ever
downloaded. Without them you get `EMBEDDER_NOT_CONFIGURED`.

## 3. Search

```powershell
rkb search passages --data-root $kb --scope-id rts_... --query "TPR at 1% FPR" --operator you
rkb search papers   --data-root $kb --scope-id rts_... --query "malware detector" --operator you
rkb search evidence --data-root $kb --scope-id rts_... --query "ROC-AUC" --operator you
rkb passage get     --data-root $kb --scope-id rts_... --chunk-id rch_... --operator you
```

## 4. Collect cited passages for a question

```powershell
rkb rag context --data-root $kb --scope-id rts_... --query "Which samples were excluded?" --operator you
```

You get passages `C1`, `C2`, … each with its exact source. `"answer"` is always `null`: you, or a later approved tool, write the
answer and must cite these anchors.

## What the results mean

| Field | Meaning |
|---|---|
| `rights_view: FULL_EXCERPT` | the text is shown because the licence currently allows it |
| `rights_view: ANCHOR_ONLY` | only the location (paper, page, characters) is shown; the licence does not allow showing the text now |
| `rights_view: METADATA_ONLY` | only facts such as title and DOI; this does not mean the PDF exists or may be read |
| `excluded_by_reason` | how many matches were hidden and why (rights withdrawn, newer parse, changed decision, …) |
| `BLOCKED:SOURCE_INTEGRITY:…` | the stored PDF or its parsed text is missing or damaged; nothing from that paper is shown until it is repaired (`rkb doctor --full`) |
| `candidate_window` | how many top matches per method inside your scope were examined; it grows automatically when many matches are hidden |
| `retrieval_mode` | `LEXICAL_ONLY` (keywords), `HYBRID_MOCK` (keywords + test-only mock), `HYBRID_LOCAL_MODEL` (owner-approved real model) |
| `semantic_model: NOT_CONFIGURED` | no real meaning-based model was used |
| `outcome: NO_AUTHORIZED_EVIDENCE` | matches exist, but none may be shown; nothing is invented |
| `outcome: NO_MATCH` | nothing matched |
| `integrity.alerts` | retraction or correction reports for the paper; check them yourself |
| `duplicate_text_in` | the same sentence appears in another source; both stay separate |

Rights, decisions and stored files are checked again on every command, so a change takes effect on the very next search, even
in a program that keeps one search service open.

Every search is logged without your query text or document text: `rkb retrieval access-log --data-root $kb --scope-id rts_...`.

## What S09 will not do

- It will not send text to Claude Desktop, ChatGPT Desktop or any online service: `--consumer` other than `LOCAL_CLI` is
  refused (`CONSUMER_NOT_PERMITTED`).
- It will not decide what a paper says or merge numbers from different passages.
- It will not turn a search result into a screening decision, accepted evidence or corpus membership.
- Results come from synthetic test documents unless you supply real, rights-cleared ones in a later, approved stage.

cmd.exe users: write `set kb=E:\ResearchPlatformDev\SciVetra\S09_practice\kb` and use `%kb%` instead of `$kb`.
You can also keep the path in an environment variable for the session: `$env:RKB_PRACTICE_KB = $kb`.
