# Download Bridge: user guide (beginner level)

The bridge moves a **list of papers to fetch** from Reprovane to PaperBatchDownloader (PBD), and the **files PBD saved** back into the SciVetra knowledge base (KB).

The bridge is a file exchange only:
- It never downloads anything itself.
- It never logs in.
- It never touches your Reprovane project or PBD's settings.

> **Real data needs owner approval.** Everything here was developed and tested with synthetic data only. Using the bridge with a real KB (`E:\ResearchKB`), real PDFs or a real Reprovane workspace needs a later owner approval (gates OAG-2 and OAG-4). Until then, use disposable test folders.

## 1. Before you start

1. **Have a KB at schema 3.**
   - For a new test KB, run `rkb init --data-root <new folder>`.
   - For an existing S03 KB, run `rkb upgrade --data-root <kb> --backup-target <backup folder>`. The upgrade always makes a verified backup first.
2. **Import the Reprovane corpus release.** Use the S03 library import:
   ```
   rkb library import-reprovane --data-root <kb> --export-dir <Stage-11 corpus export> --workspace-label <name> --operator <you>
   ```
   Do not use the older `rkb metadata import-reprovane` for bridge work: it creates duplicate papers (open issue C7).
3. **Have Reprovane's Stage-11B full-text export** of the same release: the folder with `fulltext_manifest.json`, `manual_queue.jsonl`, and so on. These are the papers Reprovane could not fetch automatically.

## 2. Export a batch for PBD

```
rkb bridge export --data-root <kb> --source-dir <Stage-11B export> --kind rv-manual-queue --workspace-label <name> --out-dir <NEW empty folder> --operator <you>
```

Optional flags:
- `--expected-manifest-sha256 <value>`: binds the export to the release you trust, using the digest you noted when the release was sealed.
- `--rv-verify-json <file>`: the output of Reprovane's own read-only `verify-export`, which the bridge cross-checks.

The out-dir then contains:
- `pbd_input_<8 chars>.csv`: give this file to PBD.
- `bridge_manifest.json`: keep it; do not edit it. It maps each PBD row to a paper, and its hash is recorded in the KB.
- `pbd_output_<8 chars>/`: an empty folder. Tell PBD to save the PDFs here.

Each row is named by a token such as `b4c1…` (24 characters). The token is also the file name PBD must use (`<token>.pdf`).
- PBD shows the token in its `openalex_id` column. **It is not an OpenAlex ID.**
- Real OpenAlex IDs are only in the manifest.

A row may have an empty DOI or URL cell. That is deliberate: no single safe DOI, a signed or unsafe URL, or a candidate URL that Reprovane's policy rejected. The row's `flags` in the manifest tell you why.

URL refusal flags (a refused URL is left out, never rewritten):

| Flag | Meaning |
|---|---|
| `URL_SCHEME_REFUSED` | not `http`/`https` |
| `URL_USERINFO_REFUSED` | `user:password@` in the URL |
| `URL_IP_LITERAL_REFUSED` | an IPv4 address or any bracketed (IPv6) host |
| `URL_NUMERIC_HOST_REFUSED` | a host a browser could read as an IPv4 address: `127.1`, `0177.0.0.1`, `0x7f.0.0.1`, `192.168.1`, `10.1`, `2130706433`, … |
| `URL_HOST_CHARACTERS_REFUSED` | a host with characters other than letters, digits, `-`, `_`, `.` (for example `%`-encoded) |
| `URL_LOCAL_HOST_REFUSED` | a single-label host, a trailing dot, `localhost`, or a reserved internal suffix (`.local`, `.internal`, `.lan`, `.home.arpa`, `.corp`, `.test`, …) |
| `URL_AUTHORITY_REFUSED` | an explicit port or an unusual authority |
| `URL_FRAGMENT_REFUSED`, `SIGNED_URL_OMITTED`, `URL_UNSAFE_CHARACTERS`, `URL_MALFORMED`, `URL_EMPTY` | as named |

The filter checks the text of the URL only. It cannot stop a safe-looking site from redirecting PBD's browser, or a DNS name from later pointing somewhere internal (open issue C19).

## 3. Run PBD (by hand)

1. Open PBD V5.2.11.
2. Load the input CSV and set the download folder to `pbd_output_<…>`.
3. Run it, handling any logins, paywalls or bot checks yourself.
4. Export the results CSV.

The bridge never automates this step.

## 4. Import PBD's results

```
rkb bridge import --data-root <kb> --batch-id ab_… --result-csv <PBD results file> --pdf-folder <the pbd_output folder> --operator <you> [--manifest <out-dir>\bridge_manifest.json]
```

| Exit code | Meaning |
|---|---|
| 0 | Everything was reconciled, or the same file was already imported (`DUPLICATE_NOOP`) |
| 2 | Refused, for example: header changed (`HEADER_MISMATCH`), wrong batch (`WRONG_BATCH`), different folder (`DIFFERENT_OUTPUT_FOLDER`), unsafe path |
| 3 | Integrity problem, for example: the manifest was edited (`MANIFEST_HASH_MISMATCH`) |
| 4 | Done, but some rows need attention: missing, duplicated, unknown status, unexpected rows or files |

You can re-run the same command safely: nothing is duplicated, and an interrupted import resumes. A later, corrected results CSV adds new attempts; history is kept.

## 5. What the statuses mean

| Bridge status | From PBD | Meaning, and what it does NOT mean |
|---|---|---|
| `RECEIVED_PENDING_VERIFY` → `VERIFIED_PDF` / `REJECTED_FILE` | Completed | The KB checked the file: valid PDF structure (`VERIFIED_PDF`) or not (`REJECTED_FILE`). **Not** proof that it is the right paper, nor that you may share it |
| `FAILED` | Failed | One attempt failed. **Not** "paper does not exist" |
| `DEFERRED` | Deferred | Retry later. A file that finished in the background is only imported after a new PBD result, or with `--accept-late-files` |
| `NEEDS_HUMAN_ACCESS` | LoginRequired, InstitutionalAccessRequired | An access barrier was seen. **Not** a licence or rights decision |
| `NEEDS_HUMAN_ACTION` | BotCheck, WaitingForUser, ManualAttention | You must act in PBD |
| `SKIPPED`, `NO_URL` | Skipped, NoUrl | Final for this batch; for `NO_URL`, fix the input |
| `IN_PROGRESS_AT_EXPORT` | Pending, Navigating, LookingForPdf, Downloading | The results were exported mid-run |
| `UNKNOWN_STATUS`, `QUARANTINED` | anything else, or bad or duplicated rows | Held for review |

Every verified PDF starts with identity `UNVERIFIED`. Confirm or reject it with `rkb asset review-link` (S03). Its rights class stays `UNKNOWN` until you record licence evidence with `rkb rights assess`.

## 6. Status and closing

```
rkb bridge status --data-root <kb> --batch-id ab_…
rkb bridge close  --data-root <kb> --batch-id ab_… --operator <you> [--reason OPERATOR_CLOSE_WITH_OPEN_ROWS]
```

- A batch closes as `CLOSED` only when every row is final: `VERIFIED_PDF`, `REJECTED_FILE`, `FAILED`, `SKIPPED` or `NO_URL`.
- Otherwise you must give the reason `OPERATOR_CLOSE_WITH_OPEN_ROWS`. Open rows stay listed forever in the audit trail.

## 7. Unix equivalents

The same commands work in a POSIX shell with `/`-separated absolute paths.
- Windows drive paths such as `C:\x` are refused on POSIX.
- Drive-relative (`C:x`), `..`, `:` (alternate data streams) and reserved names (`CON`, `NUL`, …) are refused everywhere.
