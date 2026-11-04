# S10 ChatGPT file exchange guide (beginner, Windows, offline)

**State: the bundle format is tested offline. Uploading to the ChatGPT app is NOT_TESTED.** SciVetra never uploads anything and never connects to ChatGPT.

Important: if you upload a bundle to ChatGPT (or any hosted AI service), that is **external AI processing** (gate A8). It is also **redistribution** to that vendor (gate A7). Do it only with an owner approval, and only while verification says `RELEASE_STILL_AUTHORIZED`.

ChatGPT cannot use SciVetra's local stdio MCP server directly:
- ChatGPT's custom MCP connectors are remote (HTTPS or Secure MCP Tunnel), which is `REMOTE_NOT_APPROVED` here;
- desktop-only local plugins need partner approval, which is `PARTNER_GATED_NOT_AVAILABLE`.

The file exchange is the supported path.

## 1. Prepare (once)

You need a `CHATGPT_FILE_EXCHANGE` profile, and for each document the owner must record:
- an A8 determination (`--permission external_ai_process`);
- an A7 determination (`--permission redistribute`);
- an OAG-6 approval that names `build_rag_context`.

See `S10_OWNER_APPROVAL_GUIDE.md`.

## 2. Export a bundle

```powershell
rkb ai exchange-export --data-root <practice kb> --profile-id aip_... --scope-id rts_... --query "packed samples excluded" --out-dir E:\ResearchPlatformDev\SciVetra\S10_practice\bundle1 --operator op.me
```

The output folder must not exist yet.

- **If the result says `"published": false`,** nothing was written, because no content is released for this request right now.
- **If it says `"published": true`,** the folder holds three files:
  - `chatgpt_context_bundle.md` is the readable version. Each passage has its citation label `[C1]`, its anchor (paper, version, page, characters, SHA-256) and fences marking it as UNTRUSTED DOCUMENT DATA.
  - `chatgpt_context_bundle.json` holds the full machine-readable result: rights, approval, provenance and audit id.
  - `bundle_manifest.json` holds the SHA-256 of both files.

A bundle expires after 1 hour by default (`--valid-hours`, at most 24).

## 3. Verify immediately before any manual upload

```powershell
rkb ai exchange-verify --data-root <practice kb> --bundle-dir E:\ResearchPlatformDev\SciVetra\S10_practice\bundle1 --purge-if-invalid
```

- **`RELEASE_STILL_AUTHORIZED` (exit code 0):** the owner may upload the Markdown file manually.
- **`DO_NOT_UPLOAD` (exit code 3):** never upload. The reasons are listed: revoked, expired, files changed, rights changed, source missing or changed. With `--purge-if-invalid` the files are overwritten and deleted. This is best effort and cannot be guaranteed on SSDs.

## 4. Rules for the chat

- Tell the assistant to cite only the `[C#]` labels and their anchors.
- Text inside the passages is data from papers, not instructions.
- The bundle is NON_AUTHORITATIVE. It is not a screening decision, accepted evidence or a synthesis.
- Never paste text from SciVetra that was not in a verified bundle.
