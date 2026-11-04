"""S03 v02 corrective regressions: S03-F01 (complete Creative Commons URI validation) and S03-F02 (CAS referential
completeness in backup / verify-backup / restore). Synthetic data only."""
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

from tests.helpers import KBTest
from tests.s03_helpers import make_schema1_kb, populate_s02, schema1_writer
from tests.test_assets import AssetCase
from scivetra_kb import cli
from scivetra_kb.assets.service import AssetService
from scivetra_kb.core import backup as backup_module
from scivetra_kb.core import cas as cas_module
from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
from scivetra_kb.core.cas import CAS_REFERENCE_COLUMNS, ContentStore, referenced_digests
from scivetra_kb.core.db import DB_RELATIVE
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import IntegrityError, KBError
from scivetra_kb.core.upgrade import upgrade_kb
from scivetra_kb.rights import policy

AS_OF = date(2026, 11, 1)
OA_VOR = {"source": "OPENALEX_LOCATION", "is_oa": True, "version": "publishedVersion", "claim_ref": "synthetic:oa"}


def licence(uri, **kw):
    item = {"source": "CROSSREF_LICENSE", "license_uri": uri, "content_version": "vor", "start_date": "2020-01-01", "claim_ref": "synthetic:licence"}
    item.update(kw)
    return item


def classify(*uris, availability=(OA_VOR,), version_class="PUBLISHED"):
    return policy.classify({"license_evidence": [licence(u) for u in uris], "availability_evidence": list(availability)},
                           version_class=version_class, as_of=AS_OF)


AT = chr(64)   # '@', kept out of literals so the package secret scanner sees no e-mail-shaped text
# Negative near-matches: none of these may ever be L1 (open licence). Each pair is (uri, why).
NEAR_MISSES = [
    ("https://creativecommons.org/licenses/by/not-a-real-license", "v01 finding: bogus version segment"),
    ("https://creativecommons.org/licenses/by/", "v01 finding: missing version"),
    ("https://creativecommons.org/licenses/by/99.9/", "v01 finding: unsupported version"),
    ("https://creativecommons.org/publicdomain/zero/not-real", "v01 finding: bogus CC0 version"),
    ("https://creativecommons.org/licenses/by", "missing version, no slash"),
    ("https://creativecommons.org/licenses/by/5.0/", "future version"),
    ("https://creativecommons.org/licenses/by/2.1/", "2.1 exists only as jurisdiction ports"),
    ("https://creativecommons.org/licenses/by/4/", "short version"),
    ("https://creativecommons.org/licenses/by/4.0.0/", "long version"),
    ("https://creativecommons.org/licenses/by-nc-nd/1.0/", "1.0 spelling is by-nd-nc"),
    ("https://creativecommons.org/licenses/by-nd-nc/4.0/", "by-nd-nc only exists at 1.0"),
    ("https://creativecommons.org/licenses/by-sa-nc/4.0/", "unknown family"),
    ("https://creativecommons.org/licenses/cc0/1.0/", "CC0 is under /publicdomain/"),
    ("https://creativecommons.org/licenses/", "no family"),
    ("https://creativecommons.org/publicdomain/zero/2.0/", "CC0 has only 1.0"),
    ("https://creativecommons.org/publicdomain/zero/", "CC0 without version"),
    ("https://creativecommons.org/publicdomain/mark/", "PDM without version"),
    ("https://creativecommons.org/publicdomain/mark/2.0/", "PDM has only 1.0"),
    ("https://creativecommons.org/publicdomain/", "no tool"),
    ("https://creativecommons.org/licenses/by/3.0/us/", "jurisdiction port (not recognized by this policy)"),
    ("https://creativecommons.org/licenses/by/4.0/extra", "arbitrary suffix segment"),
    ("https://creativecommons.org/licenses/by/4.0/deed.en/x", "segment after deed"),
    ("https://creativecommons.org/licenses/by/4.0/legalcode/extra", "segment after legalcode"),
    ("https://creativecommons.org/licenses/by/4.0//", "empty path segment"),
    ("https://creativecommons.org//licenses/by/4.0/", "leading empty segment"),
    ("https://creativecommons.org/licenses//by/4.0/", "duplicate slash"),
    ("https://creativecommons.org/licenses/BY/4.0/", "path is case-sensitive"),
    ("https://creativecommons.org/Licenses/by/4.0/", "path is case-sensitive"),
    ("https://creativecommons.org/licenses/by/4.0/deed..en", "malformed language suffix"),
    ("https://creativecommons.org/licenses/by/4.0/?ref=x", "query"),
    ("https://creativecommons.org/licenses/by/4.0/#legal", "fragment"),
    ("https://creativecommons.org/licenses/by/4.0/?", "empty query marker"),
    ("https://creativecommons.org/licenses/by%2F4.0/", "percent-encoded slash"),
    ("https://creativecommons.org/licenses/by/4.0/%7E", "percent-encoding"),
    ("https://creativecommons.org/licenses/by/4%2E0/", "percent-encoded dot"),
    (" https://creativecommons.org/licenses/by/4.0/", "leading whitespace"),
    ("https://creativecommons.org/licenses/by/4.0/ ", "trailing whitespace"),
    ("https://creativecommons.org/licenses/by/4.0/\n", "trailing newline"),
    ("https://creativecommons.org/licenses/by /4.0/", "inner whitespace"),
    ("https://creativecommons.org\\licenses\\by\\4.0\\", "backslashes"),
    ("https://user" + AT + "creativecommons.org/licenses/by/4.0/", "userinfo"),
    ("https://user:pw" + AT + "creativecommons.org/licenses/by/4.0/", "userinfo with password"),
    ("https://creativecommons.org" + AT + "evil.example/licenses/by/4.0/", "userinfo trick"),
    ("https://creativecommons.org:443/licenses/by/4.0/", "explicit port"),
    ("https://creativecommons.org:8080/licenses/by/4.0/", "port"),
    ("https://creativecommons.org:/licenses/by/4.0/", "empty port"),
    ("https://creativecommons.org:99999/licenses/by/4.0/", "invalid port"),
    ("https://creativecommons.org:abc/licenses/by/4.0/", "non-numeric port"),
    ("https://creativecommons.org.evil.example/licenses/by/4.0/", "lookalike suffix host"),
    ("https://evil-creativecommons.org/licenses/by/4.0/", "lookalike prefix host"),
    ("https://licenses.creativecommons.org/licenses/by/4.0/", "false subdomain"),
    ("https://evil.www.creativecommons.org/licenses/by/4.0/", "false sub-subdomain"),
    ("https://creativecommons.org./licenses/by/4.0/", "trailing-dot host"),
    ("https://creativecommons.com/licenses/by/4.0/", "wrong TLD"),
    ("https://creativecommons.net/licenses/by/4.0/", "wrong TLD"),
    ("https://creativecommonѕ.org/licenses/by/4.0/", "non-ASCII homoglyph host"),
    ("https://creativecommons.org/licenses/by/4.0/​", "zero-width character"),
    ("ftp://creativecommons.org/licenses/by/4.0/", "scheme"),
    ("javascript://creativecommons.org/licenses/by/4.0/", "scheme"),
    ("creativecommons.org/licenses/by/4.0/", "no scheme"),
    ("//creativecommons.org/licenses/by/4.0/", "scheme-relative"),
    ("https:/creativecommons.org/licenses/by/4.0/", "single slash authority"),
    ("https:///licenses/by/4.0/", "empty host"),
    ("https://[::1]/licenses/by/4.0/", "IP literal"),
    ("https://creativecommons.org/licenses/by/4.0/legalcode.", "empty language"),
    ("", "empty"),
]


class LicenceUriTests(unittest.TestCase):
    def test_canonical_cc_uris_recognized_per_family_and_version(self):
        """IDs: S03-F01-CANONICAL. Every allowlisted licence family x version, CC0 1.0 and PDM 1.0 are recognized in their canonical forms (http/https, optional www., mixed-case host, trailing slash optional, deed/legalcode with optional language) and yield PUBLIC_OA_LICENSED with an OA location for the same version."""
        expected_family = {"by": "CC-BY", "by-sa": "CC-BY-SA", "by-nd": "CC-BY-ND", "by-nc": "CC-BY-NC", "by-nc-sa": "CC-BY-NC-SA",
                           "by-nc-nd": "CC-BY-NC-ND", "by-nd-nc": "CC-BY-NC-ND"}
        versions = {"by": "1.0 2.0 2.5 3.0 4.0", "by-sa": "1.0 2.0 2.5 3.0 4.0", "by-nd": "1.0 2.0 2.5 3.0 4.0", "by-nc": "1.0 2.0 2.5 3.0 4.0",
                    "by-nc-sa": "1.0 2.0 2.5 3.0 4.0", "by-nc-nd": "2.0 2.5 3.0 4.0", "by-nd-nc": "1.0"}
        cases = [(f"/licenses/{name}/{v}", expected_family[name]) for name, vs in versions.items() for v in vs.split()]
        cases += [("/publicdomain/zero/1.0", "CC0"), ("/publicdomain/mark/1.0", "PDM")]
        self.assertEqual(len(cases), 32)
        for path, family in cases:
            for host in ("https://creativecommons.org", "http://creativecommons.org", "https://www.creativecommons.org", "HTTPS://CreativeCommons.ORG"):
                for suffix in ("/", "", "/deed", "/deed.en", "/deed.zh-hant", "/legalcode", "/legalcode.de", "/legalcode/"):
                    uri = host + path + suffix
                    with self.subTest(uri=uri):
                        self.assertEqual(policy.licence_family(uri), family)
        for uri in ("https://creativecommons.org/licenses/by/4.0/", "https://creativecommons.org/publicdomain/zero/1.0/",
                    "http://creativecommons.org/licenses/by-nc-nd/3.0/", "https://creativecommons.org/publicdomain/mark/1.0/"):
            with self.subTest(classify=uri):
                out = classify(uri)
                self.assertEqual(out["rights_class"], "PUBLIC_OA_LICENSED")
                self.assertEqual(out["evaluation"]["licence_uri_rules"], policy.LICENCE_URI_RULES)
                self.assertNotIn("LICENCE_URI_NOT_RECOGNISED", out["flags"])

    def test_near_misses_never_open_licence(self):
        """IDs: S03-F01-NEAR-MISS. Invalid family / version / host / scheme / authority / path suffix / encoding / whitespace near-matches are not recognized; with an OA location they give PUBLIC_OA_UNVERIFIED (never PUBLIC_OA_LICENSED), keep the original URI, and creativecommons.org-like hosts carry LICENCE_URI_NOT_RECOGNISED."""
        self.assertGreaterEqual(len(NEAR_MISSES), 60)
        for uri, why in NEAR_MISSES:
            with self.subTest(uri=uri, why=why):
                self.assertIsNone(policy.licence_family(uri))
                out = classify(uri)
                self.assertEqual(out["rights_class"], "PUBLIC_OA_UNVERIFIED")
                self.assertEqual(out["evaluation"]["licence_items"][0]["kind"], "L3")
                self.assertEqual(out["evidence"]["license_evidence"][0]["license_uri"], uri)
                self.assertEqual(out["evidence"]["license_evidence"][0]["source"], "CROSSREF_LICENSE")
                for action in ("A7", "A8", "A9"):
                    self.assertEqual(policy.gate(action, out["permissions"])["decision"], "DENY")
        for uri in ("https://creativecommons.org/licenses/by/not-a-real-license", "https://creativecommons.org/licenses/by/",
                    "https://creativecommons.org/licenses/by/99.9/", "https://creativecommons.org/publicdomain/zero/not-real",
                    "https://licenses.creativecommons.org/licenses/by/4.0/"):
            with self.subTest(flag=uri):
                self.assertIn("LICENCE_URI_NOT_RECOGNISED", classify(uri)["flags"])
        self.assertNotIn("LICENCE_URI_NOT_RECOGNISED", classify("https://publisher.example.invalid/tdm")["flags"])
        for value in (None, 4.0, b"https://creativecommons.org/licenses/by/4.0/"):
            self.assertIsNone(policy.licence_family(value))
        # Without OA availability an unrecognized licence stays restricted/unknown, never licensed.
        self.assertEqual(classify("https://creativecommons.org/licenses/by/", availability=())["rights_class"], "INSTITUTIONAL_RESTRICTED")

    def test_policy_semantics_otherwise_unchanged(self):
        """IDs: S03-F01-SEMANTICS. A valid licence still needs OA for the same version, an elapsed embargo, no restrictive terms and version applicability; a bogus licence next to a valid one is conservative (UNVERIFIED); A7/A8/A9 stay DENY for LICENSED."""
        good = "https://creativecommons.org/licenses/by/4.0/"
        licensed = classify(good)
        for action, decision in (("A1", "ALLOW"), ("A2", "ALLOW"), ("A7", "DENY"), ("A8", "DENY"), ("A9", "DENY")):
            self.assertEqual(policy.gate(action, licensed["permissions"])["decision"], decision)
        self.assertEqual(classify(good, "https://creativecommons.org/licenses/by/99.9/")["rights_class"], "PUBLIC_OA_UNVERIFIED")
        self.assertEqual(classify(good, availability=())["rights_class"], "UNKNOWN")
        embargo = policy.classify({"license_evidence": [licence(good, start_date="2026-10-01", delay_in_days=365)], "availability_evidence": [OA_VOR]},
                                  version_class="PUBLISHED", as_of=AS_OF)
        self.assertEqual((embargo["rights_class"], "EMBARGO_NOT_ELAPSED" in embargo["flags"]), ("PUBLIC_OA_UNVERIFIED", True))
        other = policy.classify({"license_evidence": [licence(good, content_version="am")], "availability_evidence": [OA_VOR]},
                                version_class="PUBLISHED", as_of=AS_OF)
        self.assertEqual((other["rights_class"], "LICENCE_VERSION_MISMATCH" in other["flags"]), ("PUBLIC_OA_UNVERIFIED", True))
        terms = policy.classify({"license_evidence": [licence(good)], "availability_evidence": [OA_VOR],
                                 "terms_declarations": [{"terms": "subscription", "evidence_ref": "synthetic", "restrictive": True}]},
                                version_class="PUBLISHED", as_of=AS_OF)
        self.assertEqual(terms["rights_class"], "UNKNOWN")
        routes = {policy.classify({"license_evidence": [licence(good)], "availability_evidence": [OA_VOR],
                                   "access_provenance": {"access_route": r, "access_barrier_observed": ["Paywall"]}},
                                  version_class="PUBLISHED", as_of=AS_OF)["rights_class"] for r in policy.ACCESS_ROUTES}
        self.assertEqual(routes, {"PUBLIC_OA_LICENSED"})
        self.assertEqual(policy.POLICY_VERSION, "rights-policy/1")


def _v01_prefix_family(uri):
    """The v01 prefix rule, reproduced only to build a historical assessment for the reassessment test."""
    match = re.match(r"^https?://creativecommons\.org/licenses/by/", uri or "")
    return "CC-BY" if match else None


class RightsPersistenceTests(AssetCase):
    BOGUS = "https://creativecommons.org/licenses/by/not-a-real-license"

    def evidence(self, uri):
        return {"license_evidence": [licence(uri)], "availability_evidence": [OA_VOR]}

    def test_assess_rights_persists_unverified_for_fake_licence(self):
        """IDs: S03-F01-E2E. AssetService.assess_rights with a fake CC URI + OA location persists PUBLIC_OA_UNVERIFIED with the original URI, source and LICENCE_URI_NOT_RECOGNISED; the gates read from the DB deny A7/A8; a canonical CC BY 4.0 URI persists PUBLIC_OA_LICENSED."""
        asset = self.register(self.put("a.pdf", self.paper_a_pdf()))["asset_sha256"]
        with self.writer() as kb:
            svc = AssetService(kb)
            out = svc.assess_rights(asset_sha256=asset, document_version_id=self.dv_a, operator="op.r", evidence=self.evidence(self.BOGUS))
            row = kb.one("SELECT * FROM rights_assessment WHERE assessment_id=?", (out["assessment_id"],))
            gates = {a: svc.gate(asset_sha256=asset, document_version_id=self.dv_a, action=a)["decision"] for a in ("A2", "A7", "A8", "A9")}
            good = svc.assess_rights(asset_sha256=asset, document_version_id=self.dv_a, operator="op.r",
                                     evidence=self.evidence("https://creativecommons.org/licenses/by/4.0/"))
            good_row = kb.one("SELECT rights_class, supersedes_assessment_id FROM rights_assessment WHERE assessment_id=?", (good["assessment_id"],))
        self.assertEqual(row["rights_class"], "PUBLIC_OA_UNVERIFIED")
        self.assertEqual((row["redistribute"], row["external_ai_process"]), ("DENY", "DENY"))
        stored = json.loads(row["license_evidence_json"])[0]
        self.assertEqual((stored["license_uri"], stored["source"]), (self.BOGUS, "CROSSREF_LICENSE"))
        self.assertIn("LICENCE_URI_NOT_RECOGNISED", json.loads(row["flags_json"]))
        self.assertEqual(row["policy_version"], "rights-policy/1")
        self.assertEqual(gates, {"A2": "ALLOW", "A7": "DENY", "A8": "DENY", "A9": "DENY"})
        self.assertEqual(good_row["rights_class"], "PUBLIC_OA_LICENSED")
        self.assertEqual(good_row["supersedes_assessment_id"], out["assessment_id"])

    def test_reassessment_of_v01_result_is_append_only(self):
        """IDs: S03-F01-REASSESS. A historical PUBLIC_OA_LICENSED row produced by the v01 prefix rule is never modified; doctor --full lists it as RIGHTS_REASSESSMENT_DUE (warning, read-only); re-running assess with the same evidence appends a superseding PUBLIC_OA_UNVERIFIED row, after which nothing is due."""
        asset = self.register(self.put("a.pdf", self.paper_a_pdf()))["asset_sha256"]
        with self.writer() as kb:
            svc = AssetService(kb)
            with mock.patch.object(policy, "licence_family", _v01_prefix_family):
                old = svc.assess_rights(asset_sha256=asset, document_version_id=self.dv_a, operator="op.r", evidence=self.evidence(self.BOGUS))
            before = dict(kb.one("SELECT * FROM rights_assessment WHERE assessment_id=?", (old["assessment_id"],)))
        report = doctor(self.root, full=True)
        self.assertEqual(report["rights_reassessment_due"], [old["assessment_id"]])
        self.assertIn("RIGHTS_REASSESSMENT_DUE:1", report["warnings"])
        self.assertEqual(report["problems"], [])
        with self.writer() as kb:
            svc = AssetService(kb)
            new = svc.assess_rights(asset_sha256=asset, document_version_id=self.dv_a, operator="op.r", evidence=self.evidence(self.BOGUS))
            after = dict(kb.one("SELECT * FROM rights_assessment WHERE assessment_id=?", (old["assessment_id"],)))
            new_row = kb.one("SELECT rights_class, supersedes_assessment_id FROM rights_assessment WHERE assessment_id=?", (new["assessment_id"],))
        self.assertEqual(old["rights_class"], "PUBLIC_OA_LICENSED")
        self.assertEqual(before, after)
        self.assertNotEqual(new["assessment_id"], old["assessment_id"])
        self.assertEqual((new_row["rights_class"], new_row["supersedes_assessment_id"]), ("PUBLIC_OA_UNVERIFIED", old["assessment_id"]))
        self.assertEqual(doctor(self.root, full=True)["rights_reassessment_due"], [])


# -- S03-F02 -------------------------------------------------------------------------
def cas_file(folder, digest):
    return Path(folder, "cas", "sha256", digest[:2], digest)


def rebuild_metadata(folder):
    """Make an edited backup self-consistent again (CAS count/list/bytes, manifest, SHA256SUMS), as an attacker or a careless tool could."""
    folder = Path(folder)
    items = sorted(p.name for p in (folder / "cas" / "sha256").rglob("*") if p.is_file())
    manifest_path = folder / "backup_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cas"]["object_count"] = len(items)
    manifest["cas"]["object_list_sha256"] = hashlib.sha256("".join(x + "\n" for x in items).encode("ascii")).hexdigest()
    manifest["cas"]["total_bytes"] = sum(p.stat().st_size for p in (folder / "cas" / "sha256").rglob("*") if p.is_file())
    manifest["cas"].pop("referenced_object_count", None)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    db_sha = hashlib.sha256((folder / "db" / "kb.sqlite").read_bytes()).hexdigest()
    man_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    (folder / "SHA256SUMS.txt").write_text(f"{db_sha}  db/kb.sqlite\n{man_sha}  backup_manifest.json\n", encoding="ascii")


def make_link(link: Path, target: Path) -> bool:
    """Create a real symlink if the platform allows it; return False when it does not (caller then simulates the link)."""
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        return False
    return True


class BackupReferenceTests(AssetCase):
    def setUp(self):
        super().setUp()
        self.pdf = self.register(self.put("a.pdf", self.paper_a_pdf()))["asset_sha256"]
        with self.reader() as kb:
            self.release = dict(kb.one("SELECT * FROM reprovane_release_import"))
            self.refs = referenced_digests(kb.connection, kb.schema["schema_version"])
        self.target = os.path.join(self.tmp, "operator-backups")

    def backup(self):
        self.clock.advance(seconds=1)
        return create_backup(self.root, self.target, clock=self.clock)["backup_dir"]

    def assert_refused(self, call, code):
        with self.assertRaises(KBError) as ctx:
            call()
        self.assertEqual(ctx.exception.code, code)
        self.assertNotIn(self.tmp, str(ctx.exception))
        return ctx.exception

    def folders(self):
        return sorted(os.listdir(self.target)) if os.path.isdir(self.target) else []

    def test_reference_inventory_is_schema_aware_and_shared(self):
        """IDs: S03-F02-INVENTORY. One read-only inventory names pdf_asset, all four Reprovane export components, provider snapshots and operator documents by schema version; doctor uses it; a schema-2 DB lacking a referencing table or holding a non-digest value is CAS_REFERENCE_INVALID."""
        self.assertEqual({(t, c) for _, t, c in CAS_REFERENCE_COLUMNS},
                         {("provider_snapshot", "response_bytes_sha256"), ("operator_document", "document_sha256"), ("pdf_asset", "asset_sha256"),
                          ("reprovane_release_import", "manifest_bytes_sha256"), ("reprovane_release_import", "corpus_jsonl_bytes_sha256"),
                          ("reprovane_release_import", "corpus_csv_bytes_sha256"), ("reprovane_release_import", "sums_bytes_sha256"),
                          # S04 adaptation (docs/S04_TEST_ADAPTATIONS.md #8): the bridge manifest and input CSV objects (schema 3).
                          ("bridge_batch", "manifest_bytes_sha256"), ("bridge_batch", "input_csv_bytes_sha256"),
                          # S04J adaptation (docs/S04J_TEST_ADAPTATIONS.md #8): raw journal provider pages (schema 4).
                          ("jh_page", "response_bytes_sha256"),
                          ("parse_artifact", "artifact_sha256"), ("ai_input_pack", "pack_sha256"), ("ev_input_pack", "pack_sha256"),  # S06: docs/S06_TEST_ADAPTATIONS.md #8 (derived parse artifacts, schema 6); S07: docs/S07_TEST_ADAPTATIONS.md #8 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #8 (after S07, S06)
                          # S04J/S05: docs/S05_TEST_ADAPTATIONS.md #8: the locked FTE handoff manifest (schema 5).
                          ("fte_lock", "handoff_manifest_sha256")})
        self.assertIn(self.pdf, self.refs)
        for column in ("manifest_bytes_sha256", "corpus_jsonl_bytes_sha256", "corpus_csv_bytes_sha256", "sums_bytes_sha256"):
            self.assertIn(self.release[column], self.refs)
        self.assertEqual(doctor(self.root, full=True)["cas_referenced"], len(self.refs))
        con = sqlite3.connect(":memory:")
        con.executescript("CREATE TABLE provider_snapshot(response_bytes_sha256 TEXT); CREATE TABLE operator_document(document_sha256 TEXT);")
        self.assertEqual(referenced_digests(con, 1), {})
        with self.assertRaises(IntegrityError) as ctx:
            referenced_digests(con, 2)
        self.assertEqual(ctx.exception.code, "CAS_REFERENCE_INVALID")
        con.execute("INSERT INTO operator_document VALUES ('../../etc/passwd')")
        with self.assertRaises(IntegrityError) as ctx:
            referenced_digests(con, 1)
        self.assertEqual(ctx.exception.code, "CAS_REFERENCE_INVALID")
        con.close()

    def test_healthy_backup_with_registered_pdf(self):
        """IDs: S03-F02-HEALTHY. A KB with a registered synthetic PDF backs up, verifies and restores; every referenced object is present in backup and restore; restored doctor --full is clean."""
        folder = self.backup()
        verified = verify_backup(folder)
        self.assertEqual((verified["valid"], verified["cas_referenced_count"]), (True, len(self.refs)))
        self.assertTrue(cas_file(folder, self.pdf).is_file())
        manifest = json.loads(Path(folder, "backup_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["cas"]["referenced_object_count"], len(self.refs))
        restored = restore_backup(folder, os.path.join(self.tmp, "restored"))
        self.assertTrue(all(restored["comparison"].values()))
        self.assertTrue(restored["comparison"]["cas_references_complete"])
        self.assertEqual(doctor(os.path.join(self.tmp, "restored"), full=True)["problems"], [])

    def test_create_refuses_missing_or_corrupt_referenced_object(self):
        """IDs: S03-F02-CREATE. create_backup refuses (BACKUP_SOURCE_INCOMPLETE, no backup folder created) when a DB-referenced PDF is missing or corrupted; doctor --full reports the same digest; the source is not changed."""
        path = cas_file(self.root, self.pdf)
        original = path.read_bytes()
        for name, damage, kind in (("missing", lambda: path.unlink(), "CAS_MISSING"), ("corrupt", lambda: path.write_bytes(b"corrupt"), "CAS_HASH_MISMATCH")):
            with self.subTest(name=name):
                if path.exists():
                    path.unlink()
                path.write_bytes(original)
                damage()
                self.assertIn(f"{kind}:{self.pdf}", doctor(self.root, full=True)["problems"])
                exc = self.assert_refused(self.backup, "BACKUP_SOURCE_INCOMPLETE")
                self.assertIn(kind, str(exc))
                self.assertEqual(self.folders(), [])
        path.unlink()
        path.write_bytes(original)
        self.assertTrue(verify_backup(self.backup())["valid"])

    def test_verify_and_restore_refuse_self_consistent_incomplete_backup(self):
        """IDs: S03-F02-VERIFY, S03-F02-RESTORE. A backup whose PDF object was removed and whose manifest/SHA256SUMS were rebuilt consistently is refused by verify_backup (BACKUP_CAS_REFERENCE_MISSING, read-only) and by restore_backup before any target is created; a pre-existing empty target stays empty."""
        good = self.backup()
        bad = os.path.join(self.tmp, "incomplete")
        shutil.copytree(good, bad)
        cas_file(bad, self.pdf).unlink()
        rebuild_metadata(bad)
        before = {p.relative_to(bad).as_posix(): p.read_bytes() for p in Path(bad).rglob("*") if p.is_file()}
        self.assert_refused(lambda: verify_backup(bad), "BACKUP_CAS_REFERENCE_MISSING")
        self.assertEqual({p.relative_to(bad).as_posix(): p.read_bytes() for p in Path(bad).rglob("*") if p.is_file()}, before)
        absent = os.path.join(self.tmp, "restore-absent")
        self.assert_refused(lambda: restore_backup(bad, absent), "BACKUP_CAS_REFERENCE_MISSING")
        self.assertFalse(os.path.exists(absent))
        empty = os.path.join(self.tmp, "restore-empty")
        os.makedirs(empty)
        self.assert_refused(lambda: restore_backup(bad, empty), "BACKUP_CAS_REFERENCE_MISSING")
        self.assertEqual(os.listdir(empty), [])

    def test_restore_post_check_removes_broken_target(self):
        """IDs: S03-F02-RESTORE. If the backup changes after verification (verification bypassed here to model the race), the post-restore reference check fails with RESTORE_MISMATCH and the restore removes everything it wrote."""
        good = self.backup()
        bad = os.path.join(self.tmp, "raced")
        shutil.copytree(good, bad)
        real_verify = backup_module.verify_backup
        verified = real_verify(bad)

        def verify_then_lose_object(text):
            cas_file(bad, self.pdf).unlink()
            return verified
        target = os.path.join(self.tmp, "restore-raced")
        with mock.patch.object(backup_module, "verify_backup", verify_then_lose_object):
            exc = self.assert_refused(lambda: restore_backup(bad, target), "RESTORE_MISMATCH")
        self.assertIn("CAS_MISSING", str(exc))
        self.assertFalse(os.path.exists(target))

    def test_non_pdf_references_reprovane_export_and_operator_bytes(self):
        """IDs: S03-F02-NON-PDF. A missing Reprovane export component (corpus.csv) blocks create_backup; a backup missing the export manifest object with rebuilt metadata is refused by verify and restore."""
        csv_digest = self.release["corpus_csv_bytes_sha256"]
        good = self.backup()
        path = cas_file(self.root, csv_digest)
        data = path.read_bytes()
        path.unlink()
        self.assert_refused(self.backup, "BACKUP_SOURCE_INCOMPLETE")
        path.write_bytes(data)
        bad = os.path.join(self.tmp, "no-manifest-object")
        shutil.copytree(good, bad)
        cas_file(bad, self.release["manifest_bytes_sha256"]).unlink()
        rebuild_metadata(bad)
        self.assert_refused(lambda: verify_backup(bad), "BACKUP_CAS_REFERENCE_MISSING")
        self.assert_refused(lambda: restore_backup(bad, os.path.join(self.tmp, "r2")), "BACKUP_CAS_REFERENCE_MISSING")

    def test_unreferenced_orphan_and_stray_entries(self):
        """IDs: S03-F02-ORPHAN. An unreferenced hash-valid orphan is backed up (doctor warns) and does not block verify/restore; a stray temp file in the source CAS is skipped and counted; any non-object entry inside a backup is refused."""
        orphan = ContentStore(Path(self.root)).put(b"synthetic orphan bytes")
        stray = Path(self.root, "cas", "sha256", orphan[:2], ".put-stray")
        stray.write_bytes(b"partial")
        self.assertTrue(any(w.startswith("UNREFERENCED_OBJECTS") for w in doctor(self.root)["warnings"]))
        folder = self.backup()
        manifest = json.loads(Path(folder, "backup_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["cas"]["skipped_entries"], {"TEMP_FILE": 1})
        self.assertTrue(cas_file(folder, orphan).is_file())
        self.assertFalse(Path(folder, "cas", "sha256", orphan[:2], ".put-stray").exists())
        self.assertEqual(verify_backup(folder)["cas_object_count"], len(self.refs) + 1)
        self.assertTrue(restore_backup(folder, os.path.join(self.tmp, "r-orphan"))["restored"])
        copy = os.path.join(self.tmp, "stray-in-backup")
        shutil.copytree(folder, copy)
        Path(copy, "cas", "sha256", orphan[:2], ".put-x").write_bytes(b"x")
        self.assert_refused(lambda: verify_backup(copy), "BACKUP_INVENTORY_MISMATCH")

    def test_link_in_place_of_referenced_object_refused(self):
        """IDs: S03-F02-LINK. A referenced object that is a symlink/reparse point (real symlink where the platform permits, otherwise the link check is simulated) is refused by create_backup (CAS_LINK_REFUSED) and by verify_backup."""
        source = cas_file(self.root, self.pdf)
        outside = Path(self.tmp, "outside.bin")
        outside.write_bytes(source.read_bytes())
        source.unlink()
        if make_link(source, outside):
            self.assertIn(f"CAS_LINK_REFUSED:{self.pdf}", doctor(self.root, full=True)["problems"])
            exc = self.assert_refused(self.backup, "BACKUP_SOURCE_INCOMPLETE")
            source.unlink()
        else:
            source.write_bytes(outside.read_bytes())
            real = cas_module.is_reparse_or_link
            with mock.patch.object(cas_module, "is_reparse_or_link", lambda p: Path(p).name == self.pdf or real(p)):
                exc = self.assert_refused(self.backup, "BACKUP_SOURCE_INCOMPLETE")
            source.unlink()
        self.assertIn("CAS_LINK_REFUSED", str(exc))
        source.write_bytes(outside.read_bytes())
        good = self.backup()
        target = cas_file(good, self.pdf)
        real_backup = backup_module.is_reparse_or_link
        with mock.patch.object(backup_module, "is_reparse_or_link", lambda p: Path(p).name == self.pdf or real_backup(p)):
            self.assert_refused(lambda: verify_backup(good), "BACKUP_INVENTORY_MISMATCH")
        real_cas = cas_module.is_reparse_or_link
        with mock.patch.object(cas_module, "is_reparse_or_link", lambda p: Path(p).name == self.pdf or real_cas(p)):
            self.assert_refused(lambda: verify_backup(good), "BACKUP_CAS_REFERENCE_MISSING")
        self.assertTrue(target.is_file())

    def test_cli_backup_failure_and_healthy_restore(self):
        """IDs: S03-F02-CLI. rkb backup exits non-zero with BACKUP_SOURCE_INCOMPLETE for a missing referenced object; rkb verify-backup / restore succeed on a healthy backup and fail with BACKUP_CAS_REFERENCE_MISSING on a rebuilt incomplete one."""
        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(list(argv))
            return code, json.loads(out.getvalue())
        self.clock.advance(seconds=1)
        code, out = run("backup", "--data-root", self.root, "--target", self.target)
        self.assertEqual(code, 0)
        folder = out["backup_dir"]
        self.assertEqual(run("verify-backup", "--backup", folder)[0], 0)
        self.assertEqual(run("restore", "--backup", folder, "--data-root", os.path.join(self.tmp, "cli-restored"))[0], 0)
        bad = os.path.join(self.tmp, "cli-bad")
        shutil.copytree(folder, bad)
        cas_file(bad, self.pdf).unlink()
        rebuild_metadata(bad)
        for argv in (("verify-backup", "--backup", bad), ("restore", "--backup", bad, "--data-root", os.path.join(self.tmp, "cli-r2"))):
            code, out = run(*argv)
            self.assertNotEqual(code, 0)
            self.assertEqual(out["error"], "BACKUP_CAS_REFERENCE_MISSING")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "cli-r2")))
        cas_file(self.root, self.pdf).unlink()
        code, out = run("backup", "--data-root", self.root, "--target", os.path.join(self.tmp, "cli-backups-2"))
        self.assertNotEqual(code, 0)
        self.assertEqual(out["error"], "BACKUP_SOURCE_INCOMPLETE")
        self.assertFalse(any(Path(self.tmp, "cli-backups-2").glob("rkb-backup-*")))


class Schema1BackupReferenceTests(KBTest):
    def setUp(self):
        super().setUp()
        self.v1 = os.path.join(self.tmp, "kb_v1")
        make_schema1_kb(self.v1, clock=self.clock, ids=self.ids)
        with schema1_writer(self.v1, clock=self.clock, ids=self.ids) as kb:
            populate_s02(kb, self.tmp, self)
            self.refs = referenced_digests(kb.connection, 1)
            self.snapshot = kb.one("SELECT response_bytes_sha256 d FROM provider_snapshot WHERE response_bytes_sha256 IS NOT NULL LIMIT 1")["d"]
            self.document = kb.one("SELECT document_sha256 d FROM operator_document LIMIT 1")["d"]
        self.target = os.path.join(self.tmp, "operator-backups")

    def test_schema1_backup_references_and_upgrade_guard(self):
        """IDs: S03-F02-SCHEMA1. Schema-1 (S02) references are provider snapshots and operator documents: a healthy schema-1 backup verifies and restores as schema 1; a missing provider snapshot body blocks create_backup and therefore the backup-first upgrade, leaving the S02 KB byte-identical at schema 1; a rebuilt backup missing the operator document is refused."""
        self.assertIn(self.snapshot, self.refs)
        self.assertIn(self.document, self.refs)
        self.assertTrue(all(src in ("provider_snapshot.response_bytes_sha256", "operator_document.document_sha256") for v in self.refs.values() for src in v))
        good = create_backup(self.v1, self.target, clock=self.clock)["backup_dir"]
        verified = verify_backup(good)
        self.assertEqual((verified["kb_schema_version"], verified["cas_referenced_count"]), (1, len(self.refs)))
        restored = restore_backup(good, os.path.join(self.tmp, "restored-v1"))
        self.assertEqual((restored["kb_schema_version"], restored["comparison"]["cas_references_complete"]), (1, True))
        bad = os.path.join(self.tmp, "v1-incomplete")
        shutil.copytree(good, bad)
        cas_file(bad, self.document).unlink()
        rebuild_metadata(bad)
        with self.assertRaises(KBError) as ctx:
            verify_backup(bad)
        self.assertEqual(ctx.exception.code, "BACKUP_CAS_REFERENCE_MISSING")
        db_before = hashlib.sha256(Path(self.v1, DB_RELATIVE).read_bytes()).hexdigest()
        cas_file(self.v1, self.snapshot).unlink()
        self.clock.advance(seconds=1)
        with self.assertRaises(KBError) as ctx:
            create_backup(self.v1, os.path.join(self.tmp, "b2"), clock=self.clock)
        self.assertEqual(ctx.exception.code, "BACKUP_SOURCE_INCOMPLETE")
        with self.assertRaises(KBError) as ctx:
            upgrade_kb(self.v1, os.path.join(self.tmp, "b3"), clock=self.clock, ids=self.ids)
        self.assertEqual(ctx.exception.code, "BACKUP_SOURCE_INCOMPLETE")
        self.assertEqual(hashlib.sha256(Path(self.v1, DB_RELATIVE).read_bytes()).hexdigest(), db_before)
        self.assertEqual(sorted(os.listdir(os.path.join(self.v1, "db"))), ["kb.sqlite"])
        con = sqlite3.connect(Path(self.v1, DB_RELATIVE).resolve().as_uri() + "?mode=ro", uri=True)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
        con.close()


if __name__ == "__main__":
    unittest.main()
