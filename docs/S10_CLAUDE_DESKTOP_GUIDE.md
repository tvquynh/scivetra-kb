# S10 Claude Desktop guide (beginner, Windows): local MCP server

**State: NOT_TESTED in the Claude Desktop app.** The server was tested only offline, against an independent mock client over a real stdio child process (`tests/s10_mcp_client.py`). The official MCP SDK was not available (BLOCKED). Nothing here was connected to a Claude account.

Do not connect a real research corpus. Do that only after the owner accepts a deployment record (handoff 04).

## 1. What happens when you connect it

Claude Desktop starts `rkb ai mcp-serve` on your computer and talks to it through stdin/stdout. The server opens no network port.

However, **Claude Desktop sends tool results to a vendor-hosted model.** Anything a tool returns therefore counts as external AI processing (gate A8), and it is returned only under an owner's OAG-6 approval (`S10_OWNER_APPROVAL_GUIDE.md`). Without one, every content tool answers `DENIED` / `NO_AUTHORIZED_EVIDENCE`.

## 2. Install into a private virtual environment (offline)

1. Unpack the deliverable to `E:\ResearchPlatformDev\SciVetra\S10_review\`.
2. Create a venv. The second command installs from the unpacked source folder only, with no Internet.

   ```powershell
   py -3.11 -m venv E:\ResearchPlatformDev\SciVetra\S10_venv
   E:\ResearchPlatformDev\SciVetra\S10_venv\Scripts\python.exe -m pip install --no-index --no-deps E:\ResearchPlatformDev\SciVetra\S10_review\SCIVETRA_S10_DESKTOP_AI_INTEGRATION_v01\source
   ```

3. Build a synthetic practice KB, an owner and a profile (`S10_OWNER_APPROVAL_GUIDE.md` sections 2.1–2.2). For a ready-made one, run `tools\s10_demo.py`. Write down the `aip_...` profile id.
4. Print a configuration entry for that profile:

   ```powershell
   E:\ResearchPlatformDev\SciVetra\S10_venv\Scripts\python.exe -m scivetra_kb ai claude-desktop-config --data-root <practice kb> --profile-id aip_...
   ```

## 3. Add it to Claude Desktop (manual, by you)

Claude Desktop reads local MCP servers from `claude_desktop_config.json`. You can usually open it from the app's settings, under the developer / local MCP servers section. The exact UI changes over time, so check the vendor page listed in `S10_VENDOR_REFERENCES.md`.

Copy the `mcpServers` entry from the previous step, or fill in `config\s10\claude_desktop_config.template.json`. Then restart the app.

To remove it, delete the entry and restart. To cut access at once without editing the file, revoke the profile:

```powershell
rkb ai revoke --target-id aip_... --reason "removed" --operator op.admin
```

## 4. What you should see (expected, NOT_TESTED in the app)

The server lists seven read-only tools:
- `list_retrieval_scopes`
- `search_papers`
- `search_passages`
- `search_evidence`
- `get_verified_passage`
- `build_rag_context`
- `get_integration_status`

Every result is a JSON envelope with the following fields:
- `status`
- `consumer`
- `model_location: EXTERNAL_UNVERIFIED`
- `authorization` (reason codes, approval id)
- `rights`
- `provenance` (index manifest SHA-256)
- `audit_event_id`
- `authority: NON_AUTHORITATIVE`
- `content_trust: UNTRUSTED_DOCUMENT_TEXT_DATA_NOT_INSTRUCTIONS`

The server ignores any instruction found inside a paper. The model cannot change the scope, the consumer or the rights through tool arguments. No tool reads files, opens URLs or writes data.

## 5. If something does not work

- **The server does not start.** Run the command from step 2.4 by hand. A message like `rkb-mcp: STARTUP_REFUSED:PROFILE_NOT_REGISTERED` names the reason. The server writes nothing else and logs no text.
- **Everything is DENIED.** This is expected until an owner approval exists. Check with `rkb ai status`.
