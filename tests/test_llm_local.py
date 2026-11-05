"""ADR_LLM stage L2: the loopback-only transport against a scripted OpenAI-compatible server on 127.0.0.1 (stands in for Ollama /
LM Studio), and S08 evidence extraction through the router - including a model that fabricates quotes, breaks the schema or
disagrees. Synthetic PDFs only; no real model, no non-loopback network."""
import hashlib
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock as umock

from tests.s08_helpers import INJECTION_DOC, MALWARE_DOC, S08Case
from tests.test_s09_v02_regressions import supersede_rights
from scivetra_kb import cli
from scivetra_kb.evidence import mock as mock_adapter
from scivetra_kb.evidence.service import EvidenceService
from scivetra_kb.llm import config as C
from scivetra_kb.llm.evidence_extract import build_messages, extract_scope
from scivetra_kb.llm.router import Router
from scivetra_llm_transport import LoopbackTransport, loopback_url


class FakeModelServer:
    """OpenAI-compatible /v1/chat/completions on 127.0.0.1. ``answers``: {model: {prompt_sha256: text}}; ``behaviour``: OK, 429, 500,
    REDIRECT, HUGE, GARBAGE. Records each request body and whether an Authorization header was sent."""

    def __init__(self):
        self.answers, self.behaviour, self.requests = {}, "OK", []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body, "auth": "Authorization" in self.headers})
                b = outer.behaviour
                if b == "429":
                    self.send_response(429)
                    self.send_header("Retry-After", "7")
                    self.end_headers()
                    return
                if b == "500":
                    self.send_response(500)
                    self.end_headers()
                    return
                if b == "REDIRECT":
                    self.send_response(302)
                    self.send_header("Location", "http://example.invalid/steal")
                    self.end_headers()
                    return
                if b == "GARBAGE":
                    payload = b"<html>not json</html>"
                elif b == "HUGE":
                    payload = b"x" * (2 * 1024 * 1024 + 10)
                else:
                    prompt = body["messages"][-1]["content"]
                    table = outer.answers.get(body["model"], {})
                    text = table.get(hashlib.sha256(prompt.encode()).hexdigest(), table.get("*", '{"ok": true}'))
                    payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def local_provider(port, name="local-a", model="m1", family=None, host="127.0.0.1"):
    return {"name": name, "base_url": f"http://{host}:{port}/v1", "model": model, "data_policy": "LOCAL", "priority": 1,
            **({"family": family} if family else {})}


class Transport(unittest.TestCase):
    def setUp(self):
        self.server = FakeModelServer()
        self.addCleanup(self.server.close)

    def call(self, provider, key=None):
        p = C.parse_config({"provider": [provider]})["providers"][0] if provider["data_policy"] == "LOCAL" else provider
        return LoopbackTransport().complete(p, [{"role": "user", "content": "hi"}], api_key=key, timeout=5, json_mode=True)

    def test_loopback_only_and_http_mapping(self):
        """IDs: LLM-L2-LOOPBACK-ONLY, LLM-L2-HTTP-MAPPING, LLM-L2-NO-PROXY-NO-REDIRECT, LLM-L2-REQUEST-SHAPE. Only loopback http hosts are contacted ('localhost' becomes 127.0.0.1, no DNS); https / remote / credentialed URLs are refused before any connection; 429 with Retry-After, 5xx, a redirect, an oversized or non-JSON body map to RATE_LIMITED, SERVER_ERROR, INVALID_RESPONSE; proxy variables are ignored; the request is an OpenAI chat completion at temperature 0 with JSON mode and no Authorization header without a key."""
        self.assertEqual(loopback_url("http://localhost:11434/v1/"), "http://127.0.0.1:11434/v1")
        for url in ("https://api.example.invalid/v1", "http://10.0.0.5:11434/v1", "http://u:p@127.0.0.1:1/v1", "http://example.invalid:80/v1"):
            self.assertIsNone(loopback_url(url), url)
            out = LoopbackTransport().complete({"name": "r", "base_url": url, "model": "m"}, [{"role": "user", "content": "x"}], api_key=None,
                                               timeout=2, json_mode=True)
            self.assertEqual((out.outcome, out.detail), ("NETWORK_ERROR", "REMOTE_NOT_ENABLED_STAGE_L2"))
        self.assertEqual(self.server.requests, [])
        with umock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9", "NO_PROXY": ""}):
            out = self.call(local_provider(self.server.port, host="localhost"))
        self.assertEqual((out.outcome, out.text), ("OK", '{"ok": true}'))
        req = self.server.requests[-1]
        self.assertEqual((req["path"], req["body"]["temperature"], req["body"]["response_format"], req["auth"]),
                         ("/v1/chat/completions", 0.0, {"type": "json_object"}, False))
        for behaviour, outcome in (("429", "RATE_LIMITED"), ("500", "SERVER_ERROR"), ("REDIRECT", "INVALID_RESPONSE"), ("HUGE", "INVALID_RESPONSE"),
                                   ("GARBAGE", "INVALID_RESPONSE")):
            self.server.behaviour = behaviour
            out = self.call(local_provider(self.server.port))
            self.assertEqual(out.outcome, outcome, behaviour)
            if behaviour == "429":
                self.assertEqual(out.retry_after, 7.0)
        self.server.close()
        out = self.call(local_provider(self.server.port))
        self.assertEqual(out.outcome, "NETWORK_ERROR")

    def test_ping_command(self):
        """IDs: LLM-L2-PING. rkb llm ping asks only LOCAL providers for a tiny JSON answer and reports OK, or the outcome with a hint; remote providers are listed as not tried."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.toml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f'[[provider]]\nname = "local"\nbase_url = "http://127.0.0.1:{self.server.port}/v1"\nmodel = "m1"\ndata_policy = "LOCAL"\n\n'
                         '[[provider]]\nname = "remote"\nbase_url = "https://api.example.invalid/v1"\nmodel = "x"\nkey_env = "X_KEY"\ndata_policy = "MAY_TRAIN"\n')
            code, out = cli.run(["llm", "ping", "--config", path])
            self.server.behaviour = "500"
            code2, out2 = cli.run(["llm", "ping", "--config", path])
        self.assertEqual([p["status"] for p in out["providers"]], ["OK", "NOT_TRIED_REMOTE_PROVIDERS_ARE_STAGE_L3"])
        self.assertEqual(out2["providers"][0]["status"], "EXHAUSTED")
        self.assertIn("hint", out2["providers"][0])


class EvidenceThroughLocalModel(S08Case):
    def setUp(self):
        super().setUp()
        self.server = FakeModelServer()
        self.addCleanup(self.server.close)

    def world(self, docs):
        run, items = self.ev_world(docs)
        scope = self.ev_scope(run, items)
        self.ecall("build_packs", scope_id=scope, operator="op.owner")
        self.items = items
        return scope

    def teach(self, scope, model, mode="normal", answer=None):
        """The fake model answers each pack in the compact scivetra-llm-evidence/1 form, derived from the deterministic mock (optionally
        with an injected fault), or with ``answer`` verbatim."""
        with self.reader() as kb:
            ev = EvidenceService(kb)
            for prow in kb.all("SELECT pack_id FROM ev_input_pack WHERE scope_id=?", (scope,)):
                _, pack = ev._pack(prow["pack_id"])
                if answer is None:
                    full = json.loads(mock_adapter.extract(ev.render(pack), pack, mode))
                    compact = {"records": [{"evidence_class": r["evidence_class"], "value_status": r["value_status"], "fields": r["fields"],
                                            "quote": r["anchors"][0]["quote"] if r["anchors"] else "", "confidence": r["confidence"]}
                                           for r in full.get("records", [])]}
                    text = json.dumps(compact)
                else:
                    text = answer
                prompt = build_messages(pack)[-1]["content"]
                self.server.answers.setdefault(model, {})[hashlib.sha256(prompt.encode()).hexdigest()] = text

    def extract(self, scope, providers, consensus=False):
        config = C.parse_config({"provider": providers})
        with self.writer() as kb:
            return extract_scope(kb, scope_id=scope, operator="op.llm", router=Router(config, LoopbackTransport()), consensus=consensus)

    def test_local_model_proposals_go_through_s08_gates(self):
        """IDs: LLM-L2-S08-IMPORT, LLM-L2-S08-PROVENANCE, LLM-L2-S08-VERIFY, LLM-L2-HUMAN-REQUIRED. Answers of a local model are imported through S08's own validation as OPERATOR_IMPORT runs with the router's provider and model as the declared (unverified) model; the drafts are NON_AUTHORITATIVE, HUMAN_REQUIRED and pass the unchanged structural verification."""
        scope = self.world([[MALWARE_DOC], [INJECTION_DOC]])
        self.teach(scope, "m1")
        out = self.extract(scope, [local_provider(self.server.port)])
        self.assertEqual([r["status"] for r in out["results"]], ["OK", "OK"])
        with self.reader() as kb:
            runs = kb.all("SELECT origin, model_identity, model_json FROM ev_extraction_run WHERE scope_id=?", (scope,))
            drafts = kb.all("SELECT draft_id, authority, review_gate, model_identity FROM ev_draft WHERE scope_id=?", (scope,))
        self.assertTrue(all(r["origin"] == "OPERATOR_IMPORT" and r["model_identity"] == "OPERATOR_DECLARED_UNVERIFIED" for r in runs))
        self.assertTrue(all(json.loads(r["model_json"])["provider"] == "local-a" and json.loads(r["model_json"])["model_id"] == "m1" for r in runs))
        self.assertTrue(drafts and all((d["authority"], d["review_gate"]) == ("NON_AUTHORITATIVE", "HUMAN_REQUIRED") for d in drafts))
        states = {self.ecall("verify", draft_id=d["draft_id"], operator="op.v")["state"] for d in drafts}
        self.assertEqual(states - {"STRUCTURALLY_VERIFIED", "ABSENCE_RECORDED"}, set())
        self.assertTrue(all(r["body"]["messages"][0]["role"] == "system" and "<<<DOCUMENT (untrusted data)" in r["body"]["messages"][1]["content"]
                            for r in self.server.requests))
        self.assertTrue(all(r["llm"]["quote_not_found_verbatim"] == 0 and r["llm"]["kept"] > 0 for r in out["results"]))

    def test_misbehaving_model_is_caught(self):
        """IDs: LLM-L2-FABRICATED-QUOTE, LLM-L2-SCHEMA-VIOLATION, LLM-L2-INJECTION-FOLLOWED. A quote that does not occur verbatim in the paper is dropped before S08 and counted (QUOTE_NOT_FOUND_VERBATIM); an answer that is not {"records": [...]} is refused by the router (no import); a record of an unknown class is rejected by S08's own validation; a model that 'follows' the injected instruction (TPR 99.9%) cannot make that value verify against its quote."""
        scope = self.world([[MALWARE_DOC], [INJECTION_DOC]])
        self.teach(scope, "m1", "hallucinate_quote")
        out = self.extract(scope, [local_provider(self.server.port)])
        self.assertGreater(sum(r["llm"]["quote_not_found_verbatim"] for r in out["results"]), 0)
        with self.reader() as kb:
            texts = [json.loads(d["quote_text_json"] or "[]") for d in kb.all("SELECT quote_text_json FROM ev_draft WHERE scope_id=?", (scope,))]
        self.assertFalse(any("perfect results on every benchmark" in q for qs in texts for q in qs))
        scope2 = self.world([[MALWARE_DOC]])
        self.teach(scope2, "m1", answer="Sure! Here are the results: TPR 93.1")
        out = self.extract(scope2, [local_provider(self.server.port)])
        self.assertEqual(out["results"][0]["status"], "NO_MODEL_ANSWER")
        scope3 = self.world([[MALWARE_DOC]])
        self.teach(scope3, "m1", answer=json.dumps({"records": [{"evidence_class": "MADE_UP_CLASS", "value_status": "REPORTED", "fields": {},
                                                                  "quote": "Objective: evaluate", "confidence": "HIGH"}]}))
        out = self.extract(scope3, [local_provider(self.server.port)])
        self.assertEqual((out["results"][0]["status"], out["results"][0]["draft_ids"]), ("PARTIAL", []))
        scope4 = self.world([[INJECTION_DOC]])
        self.teach(scope4, "m1", "inflate_number")
        out = self.extract(scope4, [local_provider(self.server.port)])
        states = [self.ecall("verify", draft_id=d, operator="op.v")["state"] for d in out["results"][0]["draft_ids"]]
        self.assertIn("UNSUPPORTED", states)
        self.assertNotIn("STRUCTURALLY_VERIFIED", states)

    def test_consensus_rights_and_unavailable_model(self):
        """IDs: LLM-L2-CONSENSUS, LLM-L2-RIGHTS-RECHECK, LLM-L2-NO-ANSWER. With --consensus two local models of different families must return the same records: AGREED imports once, DISAGREED imports nothing; a pack whose rights changed (local processing denied) is SCOPE_DENIED before any model call; with the model server down nothing is imported and NO_MODEL_ANSWER is reported."""
        scope = self.world([[MALWARE_DOC]])
        self.teach(scope, "m1")
        self.teach(scope, "m2")
        provs = [local_provider(self.server.port, "a", "m1", "fam-a"), local_provider(self.server.port, "b", "m2", "fam-b")]
        out = self.extract(scope, provs, consensus=True)
        self.assertEqual((out["results"][0]["status"], out["results"][0]["agreed_by"]), ("OK", ["a", "b"]))
        scope2 = self.world([[MALWARE_DOC]])
        self.teach(scope2, "m1")
        self.teach(scope2, "m2", "inflate_number")
        out = self.extract(scope2, provs, consensus=True)
        self.assertEqual(out["results"][0]["status"], "NOT_IMPORTED_DISAGREED")
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM ev_extraction_run WHERE scope_id=?", (scope2,))["n"], 0)
        scope3 = self.world([[MALWARE_DOC]])
        with self.writer() as kb:
            supersede_rights(kb, self.items[0]["asset_sha256"], local_process="DENY")
        before = len(self.server.requests)
        out = self.extract(scope3, [local_provider(self.server.port)])
        self.assertEqual((out["results"][0]["status"], len(self.server.requests)), ("SCOPE_DENIED", before))
        scope4 = self.world([[MALWARE_DOC]])
        self.server.close()
        out = self.extract(scope4, [local_provider(self.server.port)])
        self.assertEqual(out["results"][0]["status"], "NO_MODEL_ANSWER")


if __name__ == "__main__":
    unittest.main()
