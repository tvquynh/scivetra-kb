import socket
import unittest
import urllib.request

from tests import netguard


class NetworkGuardTests(unittest.TestCase):
    def test_guard_is_installed_by_runner(self):
        """IDs: NET-GUARD-01. The offline runner installs the audit hook before any test module runs."""
        self.assertTrue(netguard.installed())

    def test_external_connect_is_blocked(self):
        """IDs: NET-GUARD-02. A raw TCP connect to a public address is refused before any packet is sent."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with self.assertRaises(PermissionError):
                sock.connect(("192.0.2.1", 443))
        finally:
            sock.close()

    def test_external_dns_is_blocked(self):
        """IDs: NET-GUARD-03. DNS resolution of provider hosts is refused."""
        for host in ("api.crossref.org", "api.datacite.org", "doi.org"):
            with self.assertRaises(PermissionError):
                socket.getaddrinfo(host, 443)

    def test_child_python_guard_active_before_network(self):
        """IDs: NET-GUARD-05, NET-GUARD-CHILD. A child Python process proves the guard is installed (GUARD_ACTIVE) before any DNS or socket attempt; both are then refused."""
        import subprocess
        import textwrap
        code = textwrap.dedent("""
            import socket
            try:
                socket.getaddrinfo('doi.org', 443)
                print('DNS_REACHED')
            except PermissionError:
                print('BLOCKED_DNS')
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(('192.0.2.1', 443))
                print('CONNECT_REACHED')
            except PermissionError:
                print('BLOCKED_CONNECT')
            finally:
                s.close()
            """)
        out = subprocess.run(netguard.child_command(code), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.split(), ["GUARD_ACTIVE", "BLOCKED_DNS", "BLOCKED_CONNECT"], out.stderr)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_child_refuses_to_run_without_guard(self):
        """IDs: NET-GUARD-CHILD. The bootstrap prints GUARD_ACTIVE before user code runs, so a missing guard cannot go unnoticed."""
        cmd = netguard.child_command("print('USER_CODE')")
        self.assertIn("-I", cmd)
        bootstrap = cmd[-1]
        self.assertLess(bootstrap.index("GUARD_ACTIVE"), bootstrap.index("exec(compile("))
        self.assertIn("raise SystemExit(3)", bootstrap)

    def test_urllib_cannot_reach_providers(self):
        """IDs: NET-GUARD-04. Even stdlib urllib cannot reach a provider during tests."""
        with self.assertRaises(Exception) as ctx:
            urllib.request.urlopen("https://api.crossref.org/works/10.99999/x", timeout=2)
        self.assertIn("forbid", str(ctx.exception).lower() + repr(ctx.exception).lower())
