# S10 capability matrix (v01, 2026-11-03)

These states are exact: nothing marked NOT_TESTED or BLOCKED was tried with a real client or account. `get_integration_status` returns the same values (`ai/policy.py` `CAPABILITIES`).

| Capability | State | Evidence / reason |
|---|---|---|
| Transport-independent gateway (7 read-only tools, `scivetra-ai-tool-result/1`) | IMPLEMENTED_OFFLINE_TESTED | `tests/test_s10_gateway.py` (synthetic, in process) |
| Claude Desktop local MCP: stdio protocol (stdlib) | MCP_STDIO_STDLIB_IMPLEMENTATION_OFFLINE_MOCK_CLIENT_TESTED | `tests/test_s10_mcp.py`, real child process plus an independent client |
| Official MCP SDK client interop | BLOCKED_SDK_NOT_INSTALLED_NO_NETWORK_INSTALL | SDK absent; network install not authorized; `tools/s10_mcp_sdk_interop.py` reports NOT_RUN |
| Claude Desktop app session | NOT_TESTED (`LOCAL_MCP_NOT_CONNECTED`) | No Claude account or app was used |
| Claude Desktop Extension (`.mcpb`) | NOT_BUILT | Optional; needs a verified SDK-level protocol first |
| Network listener | NONE | stdio only; tested with socket creation forbidden |
| ChatGPT file exchange: bundle format, verify, purge | IMPLEMENTED_OFFLINE_TESTED | `tests/test_s10_exchange.py` |
| ChatGPT app upload | NOT_TESTED | Manual and owner-only; SciVetra never uploads |
| ChatGPT custom (remote) MCP | REMOTE_NOT_APPROVED | Disabled; not representable in migration 0010 |
| Secure MCP Tunnel | REMOTE_NOT_APPROVED | Disabled |
| ChatGPT desktop-only local plugin | PARTNER_GATED_NOT_AVAILABLE | Partner approval required by the vendor; not assumed |
| External AI API (any vendor) | EXTERNAL_API_DISABLED | No keys, requests, listeners or charges |
| Model called by SciVetra | NONE (`NO_MODEL_CALLED_BY_SCIVETRA`) | The gateway only assembles evidence; no answer is generated |
| Model location of desktop clients | EXTERNAL_UNVERIFIED | Content is treated as sent to a vendor-hosted model |
| Verified local inference | NOT_AVAILABLE | No local-inference path is approved |
| Semantic embedding | NOT_CONFIGURED | Hybrid mode uses the S09 mock (`HYBRID_MOCK`), which is not semantic |
| Linux | NOT_RUN by the developer | The suite is portable; the architect's Linux run decides |
| Windows clean install (Python 3.11) | See `evidence/install_test_*.json` | Offline wheel / sdist / venv install |
