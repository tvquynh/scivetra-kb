# How SciVetra works — and how to use it effectively

## 1. The idea in one paragraph

A systematic review produces many small decisions: which papers count, which PDF is the right version, what may be quoted, which number a paper reported, which claim a draft can make. SciVetra keeps all of them **in one local place, each with its evidence, its author and its time**.

The machine does the repetitive checking:
- identifiers;
- file integrity and usage rights;
- that numbers and contexts match;
- that every claim is traceable.

**People make every decision.** Because nothing is overwritten, you can always see *why* the draft says what it says, and what changed since.

## 2. The pipeline

```
 Reprovane screening export ──► 1 Library ──► 2 Download list ──► PaperBatchDownloader
                                    │                                      │
                                    ▼                                      ▼
                         3 Eligibility (staging) ◄──────────── 2b PDFs back, checked
                                    │
                         4 Rights ──► 5 Parse PDFs (sandbox) ──► 6 AI-style proposals (simulated)
                                    │
                         7 Evidence records ──► human review ──► 8 Search / cited context
                                    │
                         9 Synthesis draft ──► citation audit ──► human attestation ──► local review export
```

| # | Stage | What goes in | What comes out | Who decides |
|---|---|---|---|---|
| 1 | **Library** (`rkb library`, `rkb metadata`) | Reprovane exports; DOI metadata snapshots | One record per real paper, with versions; retractions and corrections tracked | Humans confirm uncertain matches |
| 2 | **Download bridge** (`rkb bridge`) | Papers that need PDFs | A CSV for PBD; then PDFs imported with checks | PBD runs by hand |
| 3 | **Full-text eligibility** (`rkb fte`) | One SEALED Reprovane release | Include / exclude decisions with reasons; a locked, verifiable export | Two independent reviewers plus an adjudicator |
| 4 | **Rights** (`rkb rights`) | Licence and availability evidence | Answers to gates A1–A10 (store, process, show, quote, export, share, AI) | The owner or operator records the assessment |
| 5 | **Parsing** (`rkb parse`) | A rights-cleared PDF | Text with page and character positions, versioned | Automatic, inside the sandbox |
| 6 | **Screening proposals** (`rkb screen`) | Parsed text plus criteria | *Proposals* with quoted anchors | Humans, blind-first |
| 7 | **Evidence** (`rkb evidence`) | Parsed text plus a research profile | Typed records (metric, value, unit, dataset, …), structurally verified | An independent human accepts or rejects |
| 8 | **Search** (`rkb index`, `rkb search`, `rkb rag`) | Accepted, rights-cleared material | Ranked, cited passages; context packs | Rights re-checked for every hit |
| 9 | **Synthesis** (`rkb synth`) | Accepted evidence plus your typed claims | A draft: every claim cited `[n]` or flagged `[UNVERIFIED]` / `[CONFLICTED]`; contradictions kept side by side | A different human attests the draft |
| — | **AI gateway** (`rkb ai`) | Approved content only | Read-only access for Claude Desktop, or a ChatGPT file exchange | The owner approves each profile (OAG-6); off by default |

You do not have to use every stage. Many teams start with the library (1), bridge (2) and rights (4), then add parsing (5) and evidence (7).

## 3. Key concepts

**Knowledge base (KB).** One folder per project. It holds a database (`db/kb.sqlite`) and a content-addressed store (`cas/`) where every file is named by its SHA-256 fingerprint, so a changed file can never pass as the original.

**Append-only history.** Records are never edited:
- a correction is a new version;
- a new rights assessment supersedes the old one;
- reviews are added, never replaced.

The "current state" is always *derived* from the history. That is why SciVetra can tell you a review is **stale**: the evidence changed after it was given.

**Re-checked at every use.** When you search, audit or export, SciVetra checks *now* that:
- the source file is intact;
- the rights still allow the action;
- the evidence is still accepted.

A permission withdrawn yesterday is respected today.

**Rights gates (A1–A10).** Each use is a separate question: store locally, process locally, store machine extracts, store human quotes, show stored quotes, run local AI, export or share, send to an external AI, and so on. Unknown means **deny**.

**Non-authoritative output.** Proposals, drafts and staging decisions are labelled `NON_AUTHORITATIVE`. Official screening decisions stay in Reprovane, and a SciVetra draft is never "publication approved".

**Verdicts in a synthesis draft:**

| Verdict | Marker | Meaning |
|---|---|---|
| SUPPORTED | `[1]` | Every stated detail matches current, accepted, anchor-verified evidence |
| PARTIAL | `[UNVERIFIED]` | Close, but something is missing (context, precision, a human confirmation) |
| UNSUPPORTED | `[UNVERIFIED]` | The evidence says otherwise, or the sentence contradicts its numbers |
| MISSING_EVIDENCE / STALE_SOURCE / BLOCKED_RIGHTS | `[UNVERIFIED]` | No usable evidence right now |
| CONFLICTING | `[CONFLICTED]` | The same paper reports different values; both are kept, and there is no vote |

## 4. Using SciVetra effectively

**Set up well:**
1. **One KB per review project**, with a clear name (`2026-malware-detection-slr`). Keep practice KBs separate from real ones.
2. **Register your reviewers early** (`rkb screen reviewer-register`). Use the same short labels everywhere: they are your audit trail.
3. **Record rights as soon as a PDF arrives.** Most "why can't I…" moments are an unassessed right, and everything waits on rights.

**Work with evidence and claims:**

4. **Let SciVetra write claim sentences.** Leave out `--statement`: generated sentences can be SUPPORTED automatically. Hand-written ones always need a reviewer to confirm the exact wording.
5. **Be precise in claims.** Always give the metric *and* its unit, the operating point (for example TPR **at 1 % FPR**), the dataset *and* its year, and the split. A missing detail is never treated as "matches anything": it makes the claim PARTIAL.
6. **Treat `[UNVERIFIED]` as a to-do list, not a failure.** The reason codes say exactly what is missing (for example `CLAIM_OMITS_CONTEXT:fpr_operating_point` or `MISMATCH:dataset`). Fix the claim, add a new version, and audit again.
7. **Do not "resolve" contradictions by picking a number.** SciVetra keeps both values on purpose. Note the contradiction in your review, or ask the authors.
8. **Counts need explicit negatives.** "2 of 5 studies report X" is only SUPPORTED when the other three have *accepted* "not reported" records. A missing extraction is not a "no".

**Keep it trustworthy:**

9. **Re-audit before you share anything internally.** Rights or evidence may have changed. An export made from an outdated audit is refused, and that refusal is intentional.
10. **Back up weekly** and before big steps. Do a practice restore once a month ([backup guide](07-backup-restore-upgrade.md)).
11. **Never touch the KB folder by hand.** If something looks wrong, run `rkb doctor --full` first.
12. **Separate roles.** The person who writes claims should not attest the draft; SciVetra enforces this.

## 5. What SciVetra is not

- **Not a reference manager.** Use Zotero or EndNote for formatting bibliographies; SciVetra cites identifiers and evidence.
- **Not an AI writer.** It checks claims against evidence; it does not generate prose or "understand" meaning. Its statement check is lexical and conservative.
- **Not a statistics package.** It performs no meta-analysis, pooling or significance testing. The only arithmetic is a logged difference between two directly comparable values.
- **Not a replacement for Reprovane.** Official screening decisions and sealed corpora live there.

## 6. Where to go next

- [Everyday use: commands](06-everyday-use.md)
- Detailed stage guides (technical): `docs/BRIDGE_USER_GUIDE.md`, `docs/FTE_USER_GUIDE.md`, `docs/PARSE_USER_GUIDE.md`, `docs/SCREENING_USER_GUIDE.md`, `docs/EVIDENCE_USER_GUIDE.md`, `docs/RETRIEVAL_USER_GUIDE.md`, `docs/S11_SYNTHESIS_GUIDE.md`, and `docs/S10_*` for the AI gateway
- [Limits and safety](10-limits-and-safety.md)
