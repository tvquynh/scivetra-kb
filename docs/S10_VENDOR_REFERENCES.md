# S10 vendor references (time-sensitive)

These are the official references the architect supplied with the S10 handoff, checked by the architect on **2026-11-03**. The developer did not re-fetch them in this stage: the stage is offline, and nothing in it connects to a vendor.

Recheck every statement against the current page and the actual client, account and workspace permissions **immediately before** any real installation or data transfer (handoff 04).

| Topic | URL | How S10 v01 uses it |
|---|---|---|
| Claude Desktop local MCP servers and desktop extensions | https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop | Basis for the stdio server and the `claude_desktop_config.json` template. The app is NOT_TESTED, and no `.mcpb` was built. |
| ChatGPT custom MCP servers and Secure MCP Tunnel | https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt | ChatGPT custom MCP is remote (HTTPS or tunnel), so in v01 it is REMOTE_NOT_APPROVED and disabled. The local stdio server is not claimed to work with ChatGPT. |
| ChatGPT desktop-only local plugins | https://developers.openai.com/plugins/deploy/desktop-only | Treated as an availability constraint (partner-gated): PARTNER_GATED_NOT_AVAILABLE. |
| OpenAI API remote MCP and Secure MCP Tunnel | https://developers.openai.com/api/docs/guides/tools-connectors-mcp | An extension point only: EXTERNAL_API_DISABLED, with no keys and no requests. |

The MCP protocol versions the server negotiates are 2025-06-18 (preferred), 2025-03-26 and 2024-11-05. Only the base protocol and the tools subset are implemented.
