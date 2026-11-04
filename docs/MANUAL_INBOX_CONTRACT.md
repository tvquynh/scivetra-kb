# Manual Download Inbox — requirements for a future S04 acquisition addendum

**Requirements only — not implemented in S05.** S05 contains no file watcher, no downloader, no browser automation and
no route that turns a file into evidence directly. This page fixes the contract so a later, owner-approved S04
acquisition addendum (handoff `04_MANUAL_FULLTEXT_INTAKE_FOLLOWUP.md`, ADR-05-04) can be built without a backdoor into
S05 evidence.

## Why

Many publisher sites need a library login or a human CAPTCHA. The owner downloads such papers by hand. Today S05 records
these situations as acquisition attempts (`LOGIN_REQUIRED`, `BOT_CHECK`, …) and lists them in the queue as
`MANUAL_ACQUISITION_NEEDED`. The missing piece is a controlled way to bring the hand-downloaded file in.

## Required flow (future)

| Step | Requirement |
|---|---|
| 1. Token | Each manual queue entry gets a stable `candidate_token` (derived from run id + `corpus_item_id`, no personal data). The queue shows DOI, title, manifestation wanted and the attempt log. |
| 2. Operator selects the file | The operator explicitly picks ONE file and the `candidate_token` it belongs to. No folder scanning, no watcher, no automatic pairing by filename. |
| 3. Read-only staging | The file is copied into a controlled inbox staging folder inside the KB data root, opened read-only. Reject symlinks, junctions/reparse points, paths outside the selected file, oversize files. |
| 4. Validation | Re-use S03 intake: size limit, PDF structure check, SHA-256, apparent DOI/title extraction. Ambiguous or mismatching files go to quarantine with a reason. |
| 5. Suspected identity | The system may only *suggest* a paper/manifestation (published, accepted, preprint, supplement). The suggestion is never evidence. |
| 6. Human confirmation | A human confirms the identity link (`HUMAN_CONFIRMED`, role `MAIN_TEXT`, correct manifestation) using the existing S03 review-link step. Only then can S05 `attach-evidence --link-id` use it. |
| 7. Rights | An S03 rights assessment is required at ingestion and again at use. A library-login entitlement is not permission to redistribute or to send the file to a cloud AI. |
| 8. CAS import | Bytes go to the S03 CAS and the central CAS reference inventory (covered by `doctor --full`, backup, restore). An exact repeat is idempotent. |
| 9. Provenance | Keep the attempt outcome, operator label, timestamps and file hash; never store cookies, session data, passwords or browser profile paths. |
| 10. Missing stays missing | A paper that still cannot be obtained stays in the retry/manual queue; it never becomes `FT_EXCLUDE` automatically. `FT_NOT_RETRIEVED` remains a human decision with documented attempts. |

## Hard boundaries

- No CAPTCHA bypass, no scraping of login sessions, no changes to PaperBatchDownloader (OAG-4 stays CLOSED).
- No path from an inbox file to S05 evidence that skips steps 4–7.
- Remote processing of documents stays denied by default without rights evidence.
