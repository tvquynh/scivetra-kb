"""S09 operational checks: migration 0009, upgrade 8 -> 9, offline-only embedding adapter, static boundaries, documentation and
the S08 v03 baseline guard."""
import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import unittest
from pathlib import Path
from unittest import mock as umock

from tests.helpers import ROOT
from tests.journal_helpers import make_older_kb
from tests.s09_helpers import RETRIEVAL_DOC, S09Case
from scivetra_kb.core import migrations
from scivetra_kb.core.db import DB_RELATIVE, open_reader
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import KBError
from scivetra_kb.core.manifest import table_content_sha256
from scivetra_kb.core.migrations import packaged_migrations
from scivetra_kb.core.upgrade import upgrade_kb
from scivetra_kb.retrieval import embedding

MIG = ROOT / "src" / "scivetra_kb" / "core" / "migrations"
RETRIEVAL = ROOT / "src" / "scivetra_kb" / "retrieval"
ADAPTED = {"tests/test_core.py", "tests/test_cli.py", "tests/test_s03_cli.py", "tests/test_upgrade_backup.py", "tests/test_bridge_paths_backup.py",
           "tests/test_journal_migration.py", "tests/test_s04j_static.py", "tests/test_s05_static.py", "tests/test_fte_migration.py",
           "tests/test_s06_static.py", "tests/test_s06_artifacts.py", "tests/test_s07_operational.py", "tests/test_s08_operational.py",
           "tools/required_ids.json"}
RT_TABLES = 3


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Migration(S09Case):
    def test_0009_additive_and_0001_0008_unchanged(self):
        """IDs: S09-MIG-HASH, S09-MIG-ADDITIVE, S09-MIG-FRESH-INIT, S09-APPEND-ONLY-TRIGGERS. 0001-0008 are byte-identical to the accepted
        S08 v03; 0009_local_retrieval.sql only CREATEs rt_* tables and triggers; every rt_* table has no-update/no-delete triggers that
        refuse direct SQL on non-empty tables; a fresh KB is schema 9."""
        expected = json.loads((ROOT / "tools" / "s08v03_migration_hashes.json").read_text())
        actual = {m[1]: m[2] for m in packaged_migrations()}
        self.assertEqual(len(expected), 8)
        self.assertEqual({k: actual[k] for k in expected}, expected)
        self.assertEqual([m[1] for m in packaged_migrations()][8], "0009_local_retrieval.sql")  # S10: docs/S10_TEST_ADAPTATIONS.md #26 (after S09)
        for statement in migrations._statements((MIG / "0009_local_retrieval.sql").read_text(encoding="utf-8")):
            self.assertRegex(re.sub(r"--[^\n]*", "", statement).strip(), r"^CREATE (TABLE|TRIGGER) rt_")
        run, items, scope, _ = self.rworld()
        self.passages(scope, "EMBER")
        with self.writer() as kb:
            self.assertEqual(kb.schema["schema_version"], 11)  # S10: docs/S10_TEST_ADAPTATIONS.md #26 (after S09); S11: docs/S11_TEST_ADAPTATIONS.md #26 (after S10, S09, S08, S07, S06)
            tables = [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'rt_*'")]
            triggers = {r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='trigger'")}
            self.assertEqual(len(tables), RT_TABLES)
            for t in tables:
                self.assertTrue({f"{t}_no_update", f"{t}_no_delete"} <= triggers, t)
                self.assertGreater(kb.one(f"SELECT count(*) n FROM {t}")["n"], 0, t)
                col = kb.all(f"PRAGMA table_info({t})")[1]["name"]
                for sql in (f"UPDATE {t} SET {col}={col}", f"DELETE FROM {t}"):
                    with self.assertRaises(KBError, msg=sql):
                        kb.execute(sql)

    def test_upgrade_8_to_9_backup_first_and_rollback(self):
        """IDs: S09-UPGRADE-8-9, S09-ROLLBACK. A schema-8 KB is upgraded only after a verified backup; only 0009 is applied; every prior
        table is byte-identical (except the ledger and upgrade audit rows); the kept pre-upgrade file restores the exact schema-8 bytes."""
        v8 = os.path.join(self.tmp, "kb_v8")
        make_older_kb(v8, 8, clock=self.clock, ids=self.ids, client="0.8.0.dev3")
        with umock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:8]):
            with open_reader(v8) as kb:
                before = {t: table_content_sha256(kb.connection, t) for t in
                          [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]}
        original = sha(Path(v8, DB_RELATIVE))
        out = upgrade_kb(v8, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"], [a["name"] for a in out["applied"]]), (8, 11, ["0009_local_retrieval.sql", "0010_ai_integration_gateway.sql", "0011_scientific_synthesis.sql"]))  # S10: docs/S10_TEST_ADAPTATIONS.md #27 (after S09); S11: docs/S11_TEST_ADAPTATIONS.md #27 (after S10, S09, S08, S07, S06)
        self.assertTrue(out["checks"]["prior_tables_byte_identical"])
        with open_reader(v8) as kb:
            after = {t: table_content_sha256(kb.connection, t) for t in before}
        audit = ("kb_upgrade_event", "schema_migration")
        self.assertEqual({k: v for k, v in after.items() if k not in audit}, {k: v for k, v in before.items() if k not in audit})
        rollback = os.path.join(self.tmp, "rollback")
        shutil.copytree(v8, rollback)
        shutil.copyfile(Path(v8, "db", out["pre_upgrade_file"]), Path(rollback, DB_RELATIVE))
        self.assertEqual(sha(Path(rollback, DB_RELATIVE)), original)
        report = doctor(v8, full=True)
        self.assertEqual(report["problems"], [])
        self.assertEqual(set(report["retrieval_invariants"].values()), {0})


class EmbeddingAdapter(S09Case):
    def test_local_model_adapter_is_offline_and_never_fetches(self):
        """IDs: S09-EMBEDDER-NOT-CONFIGURED, S09-EMBEDDER-SHA256, S09-NO-AUTO-FETCH, S09-EMBEDDER-INTERFACE. Without an owner-supplied local
        model folder the adapter is NOT_CONFIGURED and building with it is refused; a folder whose files do not match the SHA-256 manifest
        exactly (changed, missing or extra file) is MODEL_FILES_UNVERIFIED; a model id string is never treated as something to download;
        the mock implements the full interface and says it is not semantic."""
        self.assertEqual(embedding.LocalSentenceTransformer().status()["status"], "NOT_CONFIGURED")
        self.assertEqual(embedding.LocalSentenceTransformer(model_dir="intfloat/multilingual-e5-small").status()["status"], "MODEL_FILES_UNVERIFIED")
        model = Path(self.tmp, "model")
        model.mkdir()
        (model / "config.json").write_text("{}", encoding="utf-8")
        (model / "weights.bin").write_bytes(b"synthetic, not a model")
        manifest = Path(self.tmp, "manifest.json")
        manifest.write_text(json.dumps({"files": {"config.json": sha(model / "config.json"), "weights.bin": "0" * 64}}), encoding="utf-8")
        st = embedding.LocalSentenceTransformer(model_dir=str(model), manifest_file=str(manifest)).status()
        self.assertEqual((st["status"], st["problems"]), ("MODEL_FILES_UNVERIFIED", ["MODEL_FILE_HASH_MISMATCH:weights.bin"]))
        manifest.write_text(json.dumps({"files": {"config.json": sha(model / "config.json")}}), encoding="utf-8")
        self.assertIn("MODEL_FILE_NOT_IN_MANIFEST:weights.bin", embedding.LocalSentenceTransformer(model_dir=str(model), manifest_file=str(manifest)).status()["problems"])
        manifest.write_text(json.dumps({"files": {"config.json": sha(model / "config.json"), "weights.bin": sha(model / "weights.bin")}}), encoding="utf-8")
        ok = embedding.LocalSentenceTransformer(model_dir=str(model), manifest_file=str(manifest)).status()
        self.assertIn(ok["status"], ("DEPENDENCY_MISSING", "READY"))
        run, items, scope, _ = self.rworld(build=False)
        with self.assertRaises(KBError) as ctx:
            self.rcall("build_index", operator="op.index", embedder="local-model")
        self.assertEqual(ctx.exception.code, "EMBEDDER_NOT_CONFIGURED")
        mock = embedding.MockHashEmbedder()
        ident = mock.cache_identity()
        self.assertEqual((ident["kind"], ident["semantic"], ident["dim"], len(mock.embed_passages(["a", "b"]))), ("MOCK_LOCAL_RETRIEVAL", False, 256, 2))
        self.assertEqual(len(mock.fingerprint), 64)


class StaticAndDocs(S09Case):
    def test_retrieval_package_boundaries(self):
        """IDs: S09-STATIC-BOUNDARY, S09-NO-NETWORK, S09-NO-CLOUD-AI, S09-NO-LANGCHAIN, S09-ONLY-RT-WRITES. The retrieval package imports no
        network / HTTP client, cloud AI SDK, LangChain / LlamaIndex, subprocess, browser or MCP server; sentence_transformers is imported
        only lazily inside the optional adapter; numpy only inside the optional vector backend; it inserts only into rt_* tables."""
        forbidden = re.compile(r"^(socket|ssl|urllib|http|requests|httpx|aiohttp|openai|anthropic|langchain|llama_index|subprocess|webbrowser|selenium|"
                               r"playwright|mcp|huggingface_hub|torch|transformers)")
        for path in sorted(RETRIEVAL.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    self.assertIsNone(forbidden.match(name), (path.name, name))
            top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
            for node in top:
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                self.assertFalse(any(n.startswith(("numpy", "sentence_transformers")) for n in names), (path.name, names))
            self.assertNotRegex(text, r"urlopen|requests\.get|webbrowser\.open|snapshot_download|from_pretrained")
            for table in re.findall(r'insert\(\s*"([a-z_]+)"', text):
                self.assertTrue(table.startswith("rt_"), (path.name, table))

    def test_docs_and_version(self):
        """IDs: S09-DOCS, S09-STATIC-VERSION. Version 0.9.0.dev2 (v02); the ADR, the beginner guide and the test adaptations exist and state the
        derived index, rights gates per hit, LOCAL_CLI only, mock labelling, NOT_CONFIGURED real model and abstention."""
        from scivetra_kb import __version__
        self.assertEqual(__version__, "1.0.0")  # S10: docs/S10_TEST_ADAPTATIONS.md #28 (after S09); S11: docs/S11_TEST_ADAPTATIONS.md #28 (after S10, S09, S08, S07, S06)
        adr = (ROOT / "docs" / "ADR_S09.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "RETRIEVAL_USER_GUIDE.md").read_text(encoding="utf-8")
        for needle in ("NON_AUTHORITATIVE", "LOCAL_CLI", "FULL_EXCERPT", "ANCHOR_ONLY", "METADATA_ONLY", "MOCK_LOCAL_RETRIEVAL", "NOT_CONFIGURED",
                       "0009_local_retrieval.sql", "FTS5", "A3", "A5", "A8", "OAG-6", "rkb-rrf/1", "rkb-chunker/1", "NO_AUTHORIZED_EVIDENCE",
                       "S09-F01", "S09-F02", "S09-F03", "json_each", "BLOCKED:SOURCE_INTEGRITY"):
            self.assertIn(needle, adr)
        for needle in ("rkb index build", "rkb search passages", "rkb rag context", "synthetic", "$env:", "What the results mean"):
            self.assertIn(needle, guide)
        adaptations = (ROOT / "docs" / "S09_TEST_ADAPTATIONS.md").read_text(encoding="utf-8")
        for n in range(1, 26):
            self.assertIn(f"| {n} |", adaptations)
        for path in sorted(list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")) + list((ROOT / "tools").rglob("*.py"))):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_accepted_s08_tests_changed_only_on_marked_lines(self):
        """IDs: S09-TEST-ADAPTATIONS, S09-BASELINE-PRESERVED. Every S08 v03 test and tool file is still present; files outside the adapted
        set are byte-identical to the accepted S08 v03 ZIP; in adapted files every changed or added block carries an 'S09' marker; no
        accepted line is deleted."""
        baseline = json.loads((ROOT / "tools" / "s08v03_test_baseline.json").read_text(encoding="utf-8"))["files"]
        changed = 0
        for rel, info in sorted(baseline.items()):
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            data = path.read_bytes()
            if rel not in ADAPTED:
                self.assertEqual(hashlib.sha256(data).hexdigest(), info["sha256"], rel)
                continue
            lines = data.splitlines()
            now = [hashlib.sha256(line).hexdigest()[:16] for line in lines]
            for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(a=info["line_sha256"], b=now, autojunk=False).get_opcodes():
                if tag == "equal":
                    continue
                self.assertNotEqual(tag, "delete", (rel, a0, a1))
                block = [lines[i].decode("utf-8") for i in range(b0, b1)]
                self.assertTrue(any("S09" in line for line in block), (rel, block))
                if rel.startswith("tests/"):
                    changed += b1 - b0
        self.assertLess(changed, 40)


if __name__ == "__main__":
    unittest.main()
