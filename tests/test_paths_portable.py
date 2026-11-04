"""S02 v02/v03 / review findings S02-F02 and S02-F05: Windows-style paths on a POSIX host are refused before any filesystem effect.

The POSIX branch is exercised on every host by patching ``paths.host_is_windows``. The tests run in a fresh
temporary working directory and assert that it stays empty, so a literal directory named ``E:\\ResearchKB``
can never be created relative to the current directory.
"""
import contextlib
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import KBTest
from scivetra_kb.core import paths
from scivetra_kb.core.db import init_kb, open_reader
from scivetra_kb.core.errors import PolicyRefusal, ValidationError
from scivetra_kb.metadata import rv_import
from scivetra_kb.metadata.transport import FixtureTransport

BS = chr(92)
PROTECTED = "E:" + BS + "ResearchKB"


class PortablePathTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="rkb-paths-")
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def modes(self):
        return (("windows", True), ("posix", False))

    def test_protected_root_refused_lexically_on_both_hosts(self):
        """IDs: S02-PATH-POSIX, CORE-PATH-03. E:\\ResearchKB (any case, either slash style, any child) is PROTECTED_DATA_ROOT on Windows and POSIX hosts; nothing is created."""
        variants = [PROTECTED, PROTECTED + BS + "sub", "e:/researchkb", "E:/ResearchKB/", PROTECTED.lower() + BS]
        for mode, is_windows in self.modes():
            with mock.patch.object(paths, "host_is_windows", lambda flag=is_windows: flag):
                for text in variants:
                    with self.subTest(mode=mode, path=text):
                        with self.assertRaises(PolicyRefusal) as ctx:
                            init_kb(text)
                        self.assertEqual(ctx.exception.code, "PROTECTED_DATA_ROOT")
        self.assertEqual(os.listdir(self.tmp), [])

    def test_posix_refuses_windows_drive_and_backslash_paths(self):
        """IDs: S02-PATH-POSIX. On a POSIX host, drive-letter and backslash paths give WINDOWS_PATH_ON_POSIX for init, open and input files; nothing is created."""
        texts = ["C:" + BS + "Users" + BS + "someone" + BS + "kb", "D:/data/kb", "C:", "kb" + BS + "x"]
        with mock.patch.object(paths, "host_is_windows", lambda: False):
            for text in texts:
                with self.subTest(path=text):
                    with self.assertRaises(ValidationError) as ctx:
                        init_kb(text)
                    self.assertEqual(ctx.exception.code, "WINDOWS_PATH_ON_POSIX")
                    with self.assertRaises(ValidationError):
                        open_reader(text).__enter__()
                    with self.assertRaises(ValidationError):
                        paths.safe_input_file(text, max_bytes=10)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_unc_and_device_paths_refused_on_both_hosts(self):
        """IDs: S02-PATH-POSIX, CORE-PATH-02. UNC and device paths stay refused on Windows and POSIX hosts."""
        texts = [BS * 2 + "server" + BS + "share" + BS + "kb", BS * 2 + "?" + BS + "C:" + BS + "x", BS * 2 + "." + BS + "pipe" + BS + "x", "//server/share/kb"]
        for mode, is_windows in self.modes():
            with mock.patch.object(paths, "host_is_windows", lambda flag=is_windows: flag):
                for text in texts:
                    with self.subTest(mode=mode, path=text):
                        with self.assertRaises(ValidationError):
                            paths.safe_data_root(text, must_exist=False)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_posix_relative_and_absolute_paths_still_work(self):
        """IDs: S02-PATH-POSIX. On a POSIX host an ordinary forward-slash path is accepted (no over-blocking)."""
        with mock.patch.object(paths, "host_is_windows", lambda: False):
            result = init_kb("kb_posix/nested")
        self.assertTrue(result["initialized"])
        self.assertEqual(os.listdir(self.tmp), ["kb_posix"])

    # -- S02 v03 / review finding S02-F05: drive-relative Windows paths ------------------------------------
    DRIVE_RELATIVE = ["E:ResearchKB", "C:Data", "D:records/sub", "z:x"]

    @staticmethod
    def no_filesystem():
        """Fail loudly instead of touching any drive: every stat/list/create/resolve call raises AssertionError.

        Used for the Windows-host runs of the drive-relative cases, where a real call could reach a real drive.
        """
        def blocked(*_args, **_kwargs):
            raise AssertionError("filesystem reached before the lexical refusal")
        names = ("os.stat", "os.lstat", "os.listdir", "os.scandir", "os.mkdir", "os.makedirs", "os.path.realpath")
        stack = contextlib.ExitStack()
        for name in names:
            stack.enter_context(mock.patch(name, blocked))
        return stack

    def test_drive_relative_refused_lexically_on_every_host(self):
        """IDs: S02-PATH-DRIVE-RELATIVE, S02-PATH-POSIX. With a POSIX host predicate, every drive-prefixed form (including drive-relative E:ResearchKB, C:Data, D:records/sub) is WINDOWS_PATH_ON_POSIX from the lexical check alone; no filesystem call is made."""
        with mock.patch.object(paths, "host_is_windows", lambda: False):
            for text in self.DRIVE_RELATIVE:
                with self.subTest(path=text):
                    with self.no_filesystem():
                        for check in (paths._refuse_foreign_windows_path, paths.refuse_unsafe_path_text):
                            with self.assertRaises(ValidationError) as ctx:
                                check(text)
                            self.assertEqual(ctx.exception.code, "WINDOWS_PATH_ON_POSIX")
                        with self.assertRaises(ValidationError) as ctx:
                            paths.safe_data_root(text, must_exist=False)
                        self.assertEqual(ctx.exception.code, "WINDOWS_PATH_ON_POSIX")
                        with self.assertRaises(ValidationError):
                            paths.safe_input_file(text, max_bytes=10)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_drive_relative_refused_at_every_entrypoint(self):
        """IDs: S02-PATH-DRIVE-RELATIVE, S02-PATH-ENTRYPOINTS. init, open, input files, fixture directories and Reprovane export directories refuse drive-relative paths before any filesystem effect. On a real POSIX host this runs the real calls in an empty working directory. On Windows, init_kb is never called with drive-relative text (architect instruction); the read-only entrypoints run under no_filesystem() so a missing check fails instead of touching a drive."""
        entrypoints = [("init", lambda t: init_kb(t))] if os.name != "nt" else []
        entrypoints += [
            ("data_root", lambda t: paths.safe_data_root(t, must_exist=True)),
            ("open_reader", lambda t: open_reader(t).__enter__()),
            ("input_file", lambda t: paths.safe_input_file(t, max_bytes=10)),
            ("fixture_dir", lambda t: FixtureTransport.from_directory(t)),
            ("rv_export", lambda t: rv_import._read_export(t)),
        ]
        guard = contextlib.nullcontext if os.name != "nt" else self.no_filesystem
        with mock.patch.object(paths, "host_is_windows", lambda: False):
            for text in self.DRIVE_RELATIVE:
                for name, call in entrypoints:
                    with self.subTest(path=text, entrypoint=name):
                        with guard():
                            with self.assertRaises(ValidationError) as ctx:
                                call(text)
                        self.assertEqual(ctx.exception.code, "WINDOWS_PATH_ON_POSIX")
        self.assertEqual(os.listdir(self.tmp), [])

    def test_colon_inside_posix_name_still_valid(self):
        """IDs: S02-PATH-COLON-OK. A colon that is not a drive prefix (project:v1, data/C:x) is not refused. On a real POSIX host such a KB is created; on Windows only the lexical check runs (a colon there would name an NTFS stream)."""
        texts = ["project:v1/kb", "data/C:x", "./E:ResearchKB"]
        with mock.patch.object(paths, "host_is_windows", lambda: False):
            for text in texts:
                with self.subTest(path=text):
                    paths.refuse_unsafe_path_text(text)
            if os.name != "nt":
                self.assertTrue(init_kb("project:v1/kb")["initialized"])
                self.assertEqual(os.listdir(self.tmp), ["project:v1"])

    def test_native_host_behaviour_unchanged(self):
        """IDs: CORE-PATH-03. Without patching, the real host keeps refusing the protected root."""
        with self.assertRaises(PolicyRefusal) as ctx:
            init_kb(PROTECTED)
        self.assertEqual(ctx.exception.code, "PROTECTED_DATA_ROOT")
        self.assertEqual(os.listdir(self.tmp), [])


if __name__ == "__main__":
    unittest.main()
