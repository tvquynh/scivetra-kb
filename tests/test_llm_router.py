"""ADR_LLM stage L1: provider config, rights-aware routing, rotation, limits, circuit breaker, consensus and the call log - all
against the scripted FakeTransport (no network, no real key)."""
import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT
from scivetra_kb import cli
from scivetra_kb.core.errors import KBError
from scivetra_kb.llm import config as C
from scivetra_kb.llm.router import FakeTransport, Router, TransportResult, json_object


def prov(name, policy="NO_TRAINING", priority=1, **extra):
    p = {"name": name, "base_url": f"https://{name}.example.invalid/v1", "model": "m-" + name, "key_env": name.upper().replace("-", "_") + "_KEY",
         "data_policy": policy, "priority": priority}
    if policy == "LOCAL":
        p.update(base_url="http://127.0.0.1:11434/v1")
        p.pop("key_env")
    p.update(extra)
    return p


def cfg(*providers):
    return C.parse_config({"schema": C.SCHEMA, "provider": list(providers)})


def ok(value="93.1"):
    return TransportResult("OK", text=json.dumps({"value": value}))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


MSG = [{"role": "user", "content": "Passage: TPR of 93.1%. Extract the value."}]


class ProviderConfig(unittest.TestCase):
    def test_config_rules(self):
        """IDs: LLM-L1-CONFIG-VALID, LLM-L1-NO-LITERAL-KEY, LLM-L1-HTTPS-REMOTE, LLM-L1-LOCAL-LOOPBACK, LLM-L1-EXAMPLE-CONFIG. The example config parses; a literal key field is refused; a remote provider needs https and key_env; LOCAL is only for a loopback host; unknown fields, bad policies and duplicate names are refused."""
        example = C.load_config(str(ROOT / "config" / "llm-providers.example.toml"))
        self.assertEqual(example["providers"][0]["data_policy"], "LOCAL")
        bad = [({"api_key": "x"}, "LLM_LITERAL_KEY_REFUSED"), ({"token": "x"}, "LLM_LITERAL_KEY_REFUSED"),
               ({"base_url": "http://remote.example.invalid/v1"}, "LLM_CONFIG_INVALID"),
               ({"base_url": "https://u:p@example.invalid/v1"}, "LLM_CONFIG_INVALID"),
               ({"data_policy": "LOCAL"}, "LLM_POLICY_HOST_MISMATCH"), ({"data_policy": "FREE"}, "LLM_CONFIG_INVALID"),
               ({"key_env": "lower case"}, "LLM_CONFIG_INVALID"), ({"limits": {"rpm": 0}}, "LLM_CONFIG_INVALID"), ({"colour": "x"}, "LLM_CONFIG_INVALID")]
        for change, code in bad:
            with self.assertRaises(KBError, msg=change) as ctx:
                cfg(dict(prov("a"), **change))
            self.assertEqual(ctx.exception.code, code, change)
        no_key = prov("b")
        no_key.pop("key_env")
        with self.assertRaises(KBError):
            cfg(no_key)
        with self.assertRaises(KBError):
            cfg(prov("a"), prov("a"))
        self.assertEqual([p["name"] for p in cfg(prov("z", priority=1), prov("a", priority=2))["providers"]], ["z", "a"])

    def test_data_class_from_rights(self):
        """IDs: LLM-L1-DATA-CLASS. No local-processing right (A6) means no LLM at all; A6 without external AI (A8) is LOCAL_ONLY; A8 without training permission is EXTERNAL_NO_TRAINING; only openly licensed, synthetic or metadata-only material is EXTERNAL_ANY."""
        f = C.data_class_for
        self.assertIsNone(f(local_process_allowed=False, external_ai_allowed=True, training_use_allowed=True))
        self.assertEqual(f(local_process_allowed=True, external_ai_allowed=False, training_use_allowed=True), "LOCAL_ONLY")
        self.assertEqual(f(local_process_allowed=True, external_ai_allowed=True, training_use_allowed=False), "EXTERNAL_NO_TRAINING")
        self.assertEqual(f(local_process_allowed=True, external_ai_allowed=True, training_use_allowed=True), "EXTERNAL_ANY")


class Routing(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.env = {"GROQ_KEY": "k1", "GEMINI_KEY": "k2", "OPENROUTER_KEY": "k3"}
        self.config = cfg(prov("local", "LOCAL", 1), prov("groq", "NO_TRAINING", 2), prov("gemini", "MAY_TRAIN", 3), prov("openrouter", "UNKNOWN", 4))

    def router(self, script, **kw):
        return Router(self.config, FakeTransport(script), env=self.env, monotonic=self.clock, wall=lambda: 1_790_000_000 + self.clock.t, **kw)

    def test_rights_filter_the_providers(self):
        """IDs: LLM-L1-RIGHTS-FILTER. LOCAL_ONLY material only reaches the local provider; EXTERNAL_NO_TRAINING never reaches MAY_TRAIN or UNKNOWN providers; even when every allowed provider fails, a disallowed one is never tried."""
        r = self.router({"local": [TransportResult("SERVER_ERROR")], "groq": [TransportResult("SERVER_ERROR")], "gemini": [ok()], "openrouter": [ok()]})
        out = r.complete("t", MSG, "LOCAL_ONLY", validate=json_object)
        self.assertEqual(out["status"], "EXHAUSTED")
        self.assertEqual({c["provider"] for c in r.transport.calls}, {"local"})
        out = r.complete("t", MSG, "EXTERNAL_NO_TRAINING", validate=json_object)
        self.assertEqual(out["status"], "EXHAUSTED")
        self.assertEqual({c["provider"] for c in r.transport.calls}, {"local", "groq"})
        out = r.complete("t", MSG, "EXTERNAL_ANY", validate=json_object)
        self.assertEqual((out["status"], out["provider"]), ("OK", "gemini"))

    def test_rotation_rate_limit_and_missing_keys(self):
        """IDs: LLM-L1-ROTATION, LLM-L1-RETRY-AFTER, LLM-L1-KEY-NOT-SET, LLM-L1-KEY-REJECTED. A 429 moves on to the next provider and cools the first for its Retry-After; after that time it is used again; a provider whose key variable is unset is skipped; a rejected key disables the provider for the session."""
        r = self.router({"local": [TransportResult("RATE_LIMITED", retry_after=30), ok("1")], "groq": [ok("2")]})
        out = r.complete("t", MSG, "EXTERNAL_ANY", validate=json_object)
        self.assertEqual((out["provider"], [a["outcome"] for a in out["attempts"]]), ("groq", ["RATE_LIMITED", "OK"]))
        self.assertEqual(r.plan("EXTERNAL_ANY")[0]["reason"], "RATE_LIMITED")
        self.clock.t += 31
        self.assertEqual(r.complete("t", MSG, "EXTERNAL_ANY", validate=json_object)["provider"], "local")
        del self.env["GROQ_KEY"]
        self.assertEqual(r.plan("EXTERNAL_ANY")[1]["reason"], "KEY_NOT_SET")
        r2 = self.router({"local": [TransportResult("AUTH_ERROR")], "gemini": [ok()]})
        self.assertEqual(r2.complete("t", MSG, "EXTERNAL_ANY", validate=json_object)["provider"], "gemini")
        self.assertEqual(r2.plan("EXTERNAL_ANY")[0]["reason"], "KEY_REJECTED")

    def test_limits_breaker_invalid_answers_and_exhaustion(self):
        """IDs: LLM-L1-RPM-RPD, LLM-L1-BREAKER, LLM-L1-INVALID-RESPONSE, LLM-L1-EXHAUSTED. Per-minute and per-day limits skip a provider until the window passes; three consecutive failures open its breaker for the configured time; an answer that is not one JSON object counts as a failure and the next provider is tried; when nothing answers the result is EXHAUSTED with the earliest retry time."""
        self.config = cfg(prov("a", priority=1, limits={"rpm": 2, "rpd": 3}), prov("b", priority=2))
        self.env = {"A_KEY": "x", "B_KEY": "y"}
        r = self.router({"a": [ok()], "b": [ok()]})
        names = [r.complete("t", MSG, "EXTERNAL_ANY", validate=json_object)["provider"] for _ in range(3)]
        self.assertEqual(names, ["a", "a", "b"])
        self.clock.t += 61
        self.assertEqual(r.complete("t", MSG, "EXTERNAL_ANY", validate=json_object)["provider"], "a")
        self.assertEqual(r.plan("EXTERNAL_ANY")[0]["reason"], "DAY_LIMIT_REACHED")
        self.config = cfg(prov("a", priority=1), prov("b", priority=2))
        r = self.router({"a": [TransportResult("SERVER_ERROR")], "b": [TransportResult("OK", text="Sure! The value is 93.1")]}, breaker_threshold=3,
                        breaker_seconds=300)
        for _ in range(3):
            out = r.complete("t", MSG, "EXTERNAL_ANY", validate=json_object)
            self.assertEqual(out["status"], "EXHAUSTED")
            self.assertEqual([a["outcome"] for a in out["attempts"]][-1], "INVALID_RESPONSE")
        self.assertEqual(r.plan("EXTERNAL_ANY")[0]["reason"], "BREAKER_OPEN")
        self.clock.t += 301
        self.assertIsNone(r.plan("EXTERNAL_ANY")[0]["reason"])
        r = self.router({"a": [TransportResult("RATE_LIMITED", retry_after=40)], "b": [TransportResult("RATE_LIMITED", retry_after=20)]})
        out = r.complete("t", MSG, "EXTERNAL_ANY")
        self.assertEqual((out["status"], out["retry_after_seconds"]), ("EXHAUSTED", 20.0))

    def test_consensus_of_two_families(self):
        """IDs: LLM-L1-CONSENSUS-AGREED, LLM-L1-CONSENSUS-DISAGREED, LLM-L1-CONSENSUS-INSUFFICIENT. Two providers of different families giving the same JSON are AGREED; different answers are DISAGREED with both kept (no vote); a second provider of the same family does not count."""
        self.config = cfg(prov("a", priority=1, family="fam1"), prov("a2", priority=2, family="fam1"), prov("b", priority=3, family="fam2"))
        self.env = {"A_KEY": "x", "A2_KEY": "x", "B_KEY": "y"}
        r = self.router({"a": [ok("93.1")], "a2": [ok("93.1")], "b": [ok("93.1"), ok("91.3")]})
        out = r.consensus("t", MSG, "EXTERNAL_ANY", validate=json_object)
        self.assertEqual((out["status"], [a["provider"] for a in out["answers"]]), ("AGREED", ["a", "b"]))
        out = r.consensus("t", MSG, "EXTERNAL_ANY", validate=json_object)
        self.assertEqual((out["status"], [a["parsed"]["value"] for a in out["answers"]]), ("DISAGREED", ["93.1", "91.3"]))
        r = self.router({"a": [ok()], "a2": [ok()], "b": [TransportResult("SERVER_ERROR")]})
        self.assertEqual(r.consensus("t", MSG, "EXTERNAL_ANY", validate=json_object)["status"], "INSUFFICIENT_PROVIDERS")

    def test_call_log_has_no_content_or_key(self):
        """IDs: LLM-L1-LOG-NO-CONTENT, LLM-L1-NO-NETWORK. The JSON-lines call log records provider, model, data class, outcome and SHA-256 of prompt and answer - never the prompt, the answer or the key; the llm package imports no network module (stage L1)."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            r = self.router({"local": [ok("secret-answer-93.1")]}, log_path=str(log))
            r.complete("t", [{"role": "user", "content": "PRIVATE PASSAGE pelicanquote"}], "LOCAL_ONLY", validate=json_object)
            text = log.read_text(encoding="utf-8")
        entry = json.loads(text)
        self.assertEqual(sorted(entry), ["data_class", "latency_ms", "model", "outcome", "prompt_sha256", "provider", "response_sha256", "task", "ts"])
        for secret in ("pelicanquote", "secret-answer", "k1", "k2"):
            self.assertNotIn(secret, text)
        import ast
        for p in (ROOT / "src" / "scivetra_kb" / "llm").glob("*.py"):
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    self.assertFalse(any(m.split(".")[0] in ("socket", "http", "ssl", "requests") or m.startswith("urllib.request") for m in mods), p.name)


class Cli(unittest.TestCase):
    def test_llm_commands(self):
        """IDs: LLM-L1-CLI. rkb llm check-config reports whether each key variable is set (never its value); plan lists eligible and skipped providers for a data class; simulate shows rotation, a breaker and consensus without network."""
        example = str(ROOT / "config" / "llm-providers.example.toml")
        code, out = cli.run(["llm", "check-config", "--config", example])
        self.assertEqual(code, 0)
        self.assertTrue(all(isinstance(p["key_present"], (bool, type(None))) for p in out["providers"]))
        code, out = cli.run(["llm", "plan", "--config", example, "--data-class", "LOCAL_ONLY"])
        self.assertEqual([p["status"] for p in out["plan"]][0], "ELIGIBLE")
        self.assertTrue(all(p["reason"].startswith("DATA_POLICY_NOT_ALLOWED") for p in out["plan"][1:]))
        for scenario, want in (("rotation", "OK"), ("breaker", "OK"), ("consensus", "AGREED")):
            code, out = cli.run(["llm", "simulate", "--config", example, "--scenario", scenario])
            self.assertEqual((code, out["steps"][0]["status"]), (0, want), scenario)
        self.assertEqual(out["steps"][1]["status"], "DISAGREED")


if __name__ == "__main__":
    unittest.main()
