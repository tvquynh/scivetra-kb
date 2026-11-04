"""Independent offline MCP stdio client used by the S10 tests and the demonstration (no SDK, no network).

It launches ``rkb ai mcp-serve`` as a child process exactly as a desktop client would (command + arguments from the operator's
configuration), speaks newline-delimited JSON-RPC 2.0 on its stdin / stdout, and checks framing itself: every stdout line must be
one JSON-RPC message (stdout purity). It is deliberately written without importing the server's protocol module.
"""
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
CLIENT_INFO = {"name": "scivetra-offline-mock-client", "version": "1.0"}


class ProtocolViolation(AssertionError):
    pass


class StdioMcpClient:
    def __init__(self, data_root: str, profile_id: str, *, extra_env=None):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (env.get("PYTHONPATH"), str(SRC)) if p)
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(extra_env or {})
        self.proc = subprocess.Popen([sys.executable, "-B", "-m", "scivetra_kb", "ai", "mcp-serve", "--data-root", data_root, "--profile-id", profile_id],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.raw_stdout, self.messages = [], queue.Queue()
        self._next = 0
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        for line in self.proc.stdout:
            self.raw_stdout.append(line)
            try:
                msg = json.loads(line)
            except ValueError:
                self.messages.put(ProtocolViolation("non-JSON line on stdout"))
                continue
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or ("result" in msg) == ("error" in msg):
                self.messages.put(ProtocolViolation("not a JSON-RPC response"))
                continue
            self.messages.put(msg)
        self.messages.put(None)

    def send_raw(self, data: bytes):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, obj):
        self.send_raw(json.dumps(obj).encode("utf-8") + b"\n")

    def receive(self, timeout=60):
        msg = self.messages.get(timeout=timeout)
        if isinstance(msg, Exception):
            raise msg
        return msg

    def request(self, method, params=None, *, timeout=60):
        self._next += 1
        rid = self._next
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, **({"params": params} if params is not None else {})})
        msg = self.receive(timeout)
        if msg is None or msg.get("id") != rid:
            raise ProtocolViolation("response id mismatch")
        return msg

    def notify(self, method, params=None):
        self.send({"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})})

    def initialize(self, version="2025-06-18"):
        out = self.request("initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": CLIENT_INFO})
        self.notify("notifications/initialized")
        return out

    def call(self, name, arguments=None):
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self, timeout=60):
        """Close stdin (the MCP stdio shutdown signal) and wait; returns (exit code, all stdout bytes, stderr bytes)."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        code = self.proc.wait(timeout)
        self._reader.join(timeout)
        err = self.proc.stderr.read()
        self.proc.stderr.close()
        self.proc.stdout.close()
        return code, b"".join(self.raw_stdout), err
