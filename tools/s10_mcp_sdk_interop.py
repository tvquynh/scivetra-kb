"""Optional interop check of ``rkb ai mcp-serve`` against the OFFICIAL MCP Python SDK client (package ``mcp``).

Usage: python tools/s10_mcp_sdk_interop.py --data-root <synthetic practice KB> --profile-id aip_... [--out report.json]

The SDK is not bundled and is never downloaded by SciVetra. If it is not importable, the report says
``NOT_RUN: BLOCKED_SDK_NOT_INSTALLED`` and exits 0 without trying anything else (the capability stays BLOCKED). If an operator has
installed a reviewed SDK offline, the script runs initialize / tools/list / one tools/call through ``mcp.client.stdio`` and records
the result. Synthetic data only.
"""
import importlib.util
import json
import sys
from pathlib import Path


def main(argv):
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    report = {"tool": "s10_mcp_sdk_interop", "sdk_present": importlib.util.find_spec("mcp") is not None}
    if not report["sdk_present"]:
        report.update(state="NOT_RUN", reason="BLOCKED_SDK_NOT_INSTALLED", note="no network install is authorized; capability remains BLOCKED")
    else:
        import asyncio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        root, profile = argv[argv.index("--data-root") + 1], argv[argv.index("--profile-id") + 1]
        params = StdioServerParameters(command=sys.executable, args=["-m", "scivetra_kb", "ai", "mcp-serve", "--data-root", root, "--profile-id", profile])

        async def run():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
                    status = await session.call_tool("get_integration_status", {})
                    return {"protocol": init.protocolVersion, "tools": [t.name for t in tools.tools], "status_is_error": status.isError}
        report.update(state="RUN", result=asyncio.run(run()))
    text = json.dumps(report, indent=1) + "\n"
    if out:
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
