# S10 owner approval guide (beginner, Windows, synthetic practice data only)

This guide shows how a human owner allows, or keeps denying, the release of research content to an AI client. Nothing is released until you finish every step. Use a practice data root under `E:\ResearchPlatformDev\...` built from synthetic data. Never use `E:\ResearchKB` or a real research corpus: real use needs a separate, owner-accepted deployment record (handoff 04).

## 1. What is denied by default

Without the records below, every AI tool returns `DENIED` or `NO_AUTHORIZED_EVIDENCE` and shows no text, metadata or anchors. The gates:

| Gate | Meaning | Where it comes from |
|---|---|---|
| A2 | local processing allowed | the rights assessment (S03), checked by S09 |
| A5 | local display allowed | the rights assessment, checked by S09 |
| A7 | redistribution allowed (a file you upload) | the rights assessment, or your determination |
| A8 | external AI processing allowed | the rights assessment, or your determination |
| OAG-6 | owner release to one AI client, for one scope, for named tools and content classes, until an expiry | your approval |

A Claude Desktop answer is treated as processed by a hosted model (`EXTERNAL_UNVERIFIED`). Your A5 permission for local reading is therefore **not** enough.

Since v02, A8 (and A7 for a ChatGPT bundle) is needed for **every** kind of content, including paper metadata (title, DOI, venue,
authors) and passage anchors (page, position, hashes). Approving only `METADATA` or `ANCHOR` without an A8 decision releases
nothing. Papers in a metadata-only scope (a Reprovane release scope) have no rights assessment for a PDF, so nothing about them is
released to an AI client at all.

**Upgrading from v01:** profiles and approvals made with v01 are void under the corrected policy (`PROFILE_POLICY_CHANGED`). Create
the profile and the approval again with the commands below; your owner registration and rights determinations stay valid.

## 2. Commands

In PowerShell, set up once:

```powershell
$env:PYTHONPATH = "$PWD\src"
$kb = 'E:\ResearchPlatformDev\SciVetra\S10_practice\kb'
function rkb { py -3.11 -m scivetra_kb @args }   # the rkb command for this PowerShell window
```

1. **Register yourself as an owner.** Assurance is `LOCAL_OPERATOR_ASSERTED`: there is no digital signature in v1.

   ```powershell
   rkb ai owner-register --data-root $kb --owner owner.one --reason "practice owner" --operator op.admin
   ```

2. **Create a consumer profile** for one AI client and the retrieval scope(s) it may ever see. The consumer is `CLAUDE_DESKTOP_LOCAL_MCP` or `CHATGPT_FILE_EXCHANGE`. The output's `profile_id` (`aip_...`) is what you put into the client configuration.

   ```powershell
   rkb ai profile-create --data-root $kb --consumer CLAUDE_DESKTOP_LOCAL_MCP --scope-id rts_... --label "practice desktop" --operator op.admin
   ```

3. **Determine A8 (and A7 for the file exchange)** for each document you are entitled to release. This is only possible when the rights class is `PUBLIC_OA_LICENSED` and the permission is `UNDETERMINED`. A `DENY` can never be widened. The determination is bound to the current rights assessment of that exact PDF and version; if either changes, you must decide again.

   ```powershell
   rkb ai determine-rights --data-root $kb --asset <asset_sha256> --document-version-id dv_... --permission external_ai_process --basis-ref "CC BY 4.0 licence page checked" --valid-hours 24 --owner owner.one --operator op.admin
   ```

4. **Approve the release (OAG-6).** Name the tools (`--operation`), the content classes (`--content-class`: `METADATA`, `ANCHOR`, `PASSAGE_EXCERPT`, `EVIDENCE_TYPED_VALUES`, `EVIDENCE_TEXT_FIELDS`) and, optionally, only some papers (`--paper-id`). The output shows a `revocation_token` once: save it in a private file, never in a chat.

   ```powershell
   rkb ai approve --data-root $kb --profile-id aip_... --scope-id rts_... --operation build_rag_context --operation search_passages --content-class PASSAGE_EXCERPT --valid-hours 8 --reason "practice: release synthetic paper A" --owner owner.one --operator op.admin
   ```

5. **Revoke.** By id, or with the saved token in a file. The revocation applies to the very next request, even in a running Claude Desktop session.

   ```powershell
   rkb ai revoke --data-root $kb --target-id aia_... --reason "stop" --operator op.admin
   rkb ai revoke --data-root $kb --token-file C:\private\token.txt --reason "stop"
   ```

   You can revoke owners (`aio_`), profiles (`aip_`), determinations (`aid_`) and approvals (`aia_`).

6. **Check.** `status` lists owners, profiles, approvals and determinations with `ACTIVE` / `EXPIRED` / `REVOKED`. `access-log` lists every AI request with hashes and decisions, never the text.

   ```powershell
   rkb ai status --data-root $kb
   rkb ai access-log --data-root $kb
   ```

## 3. What a released result still is

Everything returned is `NON_AUTHORITATIVE`. It is never:
- a screening decision;
- accepted evidence;
- a synthesis.

Document text is untrusted data. Quotes, reviewer rationales, scope labels and your query text are never released.
