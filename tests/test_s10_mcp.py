"""S10 MCP stdio adapter: protocol-level offline tests with an independent client over a real child process (tests/s10_mcp_client.py)
and in-process tests of framing, cancellation and the absence of any network use. This validates the stdlib implementation offline;
it is NOT an official-SDK or Claude Desktop validation (both reported BLOCKED / NOT_TESTED)."""
import ast
import io
import json
import socket
import unittest
from pathlib import Path
from unittest import mock as umock

from tests.s10_helpers import CANARY_A, CANARY_B, QUERY, S10Case
from tests.s10_mcp_client import StdioMcpClient
from scivetra_kb import cli
from scivetra_kb.ai import mcp_stdio
from scivetra_kb.ai import policy as P

AI_PKG = Path(__file__).resolve().parents[1] / "src" / "scivetra_kb" / "ai"


class StdioSession(S10Case):
    def client(self, profile=None):
        c = StdioMcpClient(self.root, profile or self.profile)
        self.addCleanup(lambda: c.proc.poll() is None and c.proc.kill())
        return c

    def test_handshake_discovery_calls_and_clean_shutdown(self):
        """IDs: S10-MCP-01, S10-MCP-02, S10-MCP-03, S10-MCP-05, S10-MCP-07. Over a real stdio child process: initialize negotiates
        2025-06-18 and advertises only the tools capability; tools/list returns the seven read-only tools with strict input schemas; a
        default-deny call and, after owner approval (recorded by the operator while the server runs), a released call return a text
        block plus structuredContent; EOF shuts the server down with exit 0, every stdout line is a JSON-RPC message and stderr is
        empty; a second process (reconnect) behaves the same."""
        self.ai_world()
        c = self.client()
        init = c.initialize()["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(init["serverInfo"]["name"], "scivetra-kb")
        self.assertEqual(c.request("ping")["result"], {})
        tools = c.request("tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], list(P.TOOLS))
        self.assertTrue(all(t["inputSchema"]["additionalProperties"] is False and t["annotations"]["readOnlyHint"] for t in tools))
        denied = c.call("search_passages", {"scope_id": self.scope, "query": QUERY})["result"]
        self.assertEqual(denied["structuredContent"]["status"], "DENIED")
        self.assertEqual(json.loads(denied["content"][0]["text"]), denied["structuredContent"])
        self.assertFalse(denied["isError"])
        self.full_release(real_clock=True)
        ok = c.call("build_rag_context", {"scope_id": self.scope, "query": QUERY})["result"]
        self.assertEqual(ok["structuredContent"]["status"], "OK")
        self.assertEqual(ok["structuredContent"]["transport"], "MCP_STDIO")
        self.assertEqual(ok["structuredContent"]["caller"]["declared_client"]["name"], "scivetra-offline-mock-client")
        self.assertIn(CANARY_A, ok["content"][0]["text"].lower())
        self.assertNotIn(CANARY_B, ok["content"][0]["text"].lower())
        code, out, err = c.close()
        self.assertEqual((code, err), (0, b""))
        for line in out.splitlines():
            self.assertEqual(json.loads(line)["jsonrpc"], "2.0")
        again = self.client()
        self.assertEqual(again.initialize("2024-11-05")["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(again.call("build_rag_context", {"scope_id": self.scope, "query": QUERY})["result"]["structuredContent"]["status"], "OK")
        self.assertEqual(again.close()[0], 0)

    def test_revocation_reaches_the_running_server(self):
        """IDs: S10-MCP-10, S10-SC-53. The same MCP server process releases, the owner revokes the approval through the CLI while it runs, and the
        next call in the same session is DENIED (no cached authorization in the process)."""
        self.ai_world()
        approval = self.full_release(real_clock=True)
        c = self.client()
        c.initialize()
        self.assertEqual(c.call("build_rag_context", {"scope_id": self.scope, "query": QUERY})["result"]["structuredContent"]["status"], "OK")
        code, out = cli.run(["ai", "revoke", "--data-root", self.root, "--target-id", approval["approval_id"], "--reason", "stop", "--operator", "op.owner"],
                            clock=self.clock, ids=self.ids)
        self.assertEqual(code, 0)
        res = c.call("build_rag_context", {"scope_id": self.scope, "query": QUERY})["result"]["structuredContent"]
        self.assertEqual(res["status"], "DENIED")
        self.assertIn("APPROVAL_REVOKED", res["authorization"]["reason_codes"])
        self.assertNotIn(CANARY_A, json.dumps(res).lower())
        self.assertEqual(c.close()[0], 0)

    def test_protocol_errors(self):
        """IDs: S10-MCP-04, S10-MCP-09, S10-MCP-12. Parse error, batch, wrong jsonrpc version, invalid id, unknown method, a request
        before initialize, duplicate keys, NaN and a > 1 MiB line each get the correct JSON-RPC error and the server keeps serving."""
        self.ai_world()
        c = self.client()
        self.assertEqual(c.request("tools/list")["error"]["code"], -32002)
        c.send_raw(b"{not json\n")
        self.assertEqual(c.receive()["error"]["code"], -32700)
        c.send_raw(b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]\n')
        self.assertEqual(c.receive()["error"]["code"], -32600)
        c.send_raw(b'{"jsonrpc":"1.0","id":5,"method":"ping"}\n')
        self.assertEqual(c.receive()["error"]["code"], -32600)
        c.send_raw(b'{"jsonrpc":"2.0","id":{"x":1},"method":"ping"}\n')
        self.assertEqual(c.receive()["error"]["code"], -32600)
        c.send_raw(b'{"jsonrpc":"2.0","id":6,"id":7,"method":"ping"}\n')
        self.assertEqual(c.receive()["error"]["code"], -32700)
        c.send_raw(b'{"jsonrpc":"2.0","id":8,"method":"ping","params":{"x":NaN}}\n')
        self.assertEqual(c.receive()["error"]["code"], -32700)
        c.send_raw(b'{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"x","arguments":"' + b"a" * (1024 * 1024 + 10) + b'"}}\n')
        self.assertEqual(c.receive()["error"]["code"], -32600)
        c.initialize()
        self.assertEqual(c.request("resources/list")["error"]["code"], -32601)
        self.assertEqual(c.request("initialize", {"protocolVersion": "2025-06-18"})["error"]["code"], -32600)
        self.assertEqual(c.request("tools/call", {"name": 5})["error"]["code"], -32602)
        bad = c.call("read_file", {"path": "C:/Windows/win.ini"})["result"]
        self.assertTrue(bad["isError"])
        self.assertEqual(bad["structuredContent"]["authorization"]["reason_codes"], ["UNKNOWN_TOOL"])
        self.assertEqual(c.request("ping")["result"], {})
        code, out, err = c.close()
        self.assertEqual((code, err), (0, b""))

    def test_startup_is_refused_without_a_valid_profile(self):
        """IDs: S10-MCP-06. Unknown profile, a ChatGPT file-exchange profile and a missing KB each refuse to start: exit 3, nothing on
        stdout, one fixed code on stderr without any path."""
        self.ai_world()
        chatgpt = self.make_profile("CHATGPT_FILE_EXCHANGE")
        for root, profile, code in ((self.root, "aip_unknown", b"PROFILE_NOT_REGISTERED"), (self.root, chatgpt, b"PROFILE_TRANSPORT_MISMATCH"),
                                    (self.root + "-absent", self.profile, b"KB_NOT_INITIALIZED")):
            c = StdioMcpClient(root, profile)
            exit_code, out, err = c.close()
            self.assertEqual((exit_code, out), (3, b""))
            self.assertTrue(err.startswith(b"rkb-mcp: STARTUP_REFUSED:"), err)
            self.assertIn(code, err)
            self.assertNotIn(self.tmp.encode(), err)


class InProcessProtocol(S10Case):
    def server(self, **kw):
        from scivetra_kb.ai.gateway import GatewaySession, IntegrationGateway
        return mcp_stdio.McpServer(lambda declared: IntegrationGateway(self.open_kb, GatewaySession(self.profile, "MCP_STDIO", declared)), **kw)

    @staticmethod
    def lines(*msgs):
        return io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in msgs))

    def test_cancellation_suppresses_a_queued_call(self):
        """IDs: S10-MCP-08. While call 2 is running the client cancels call 3 (queued): call 3 gets no response and is never executed (no
        audit event), call 2 and call 4 are answered."""
        self.ai_world()
        self.full_release()
        order = []
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "t"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"}]
        msgs += [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": "build_rag_context", "arguments": {"scope_id": self.scope, "query": QUERY}}}
                 for i in (2, 3)]
        msgs += [{"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 3, "reason": "user"}},
                 {"jsonrpc": "2.0", "id": 4, "method": "ping"}]
        import threading
        gate = threading.Event()
        def before_call(rid):
            order.append(rid)
            if rid == 2:
                gate.wait(10)
        srv = self.server(before_call=before_call)
        real_cancel = srv._notification
        def note(method, params):
            real_cancel(method, params)
            if method == "notifications/cancelled":
                gate.set()
        srv._notification = note
        out = io.BytesIO()
        srv.run(self.lines(*msgs), out)
        answered = [json.loads(l)["id"] for l in out.getvalue().splitlines()]
        self.assertEqual(answered, [1, 2, 4])
        self.assertEqual(order, [2, 3])
        self.assertEqual(len(self.events()), 1)

    def test_no_network_listener_or_network_import(self):
        """IDs: S10-MCP-11, S10-STATIC-NO-NETWORK. A full in-process session runs with socket creation forbidden; the ai package imports no
        socket / http / urllib / ssl / asyncio / subprocess / AI SDK module and calls no print or logging."""
        self.ai_world()
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_integration_status", "arguments": {}}}]
        out = io.BytesIO()
        with umock.patch.object(socket, "socket", side_effect=AssertionError("network use")):
            self.server().run(self.lines(*msgs), out)
        self.assertEqual(len(out.getvalue().splitlines()), 2)
        forbidden = ("socket", "ssl", "urllib", "http", "asyncio", "subprocess", "requests", "httpx", "openai", "anthropic", "mcp", "logging",
                     "webbrowser", "ctypes")
        for path in sorted(AI_PKG.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
                for name in names:
                    self.assertNotIn(name.split(".")[0], forbidden, (path.name, name))
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
                    self.fail(f"print() in {path.name}")


if __name__ == "__main__":
    unittest.main()
