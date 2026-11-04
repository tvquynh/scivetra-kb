"""Outbound network guard: any non-loopback socket connect/sendto or DNS lookup raises.

Installed by tools/run_offline_tests.py before tests are imported, and inherited
by child Python processes through a generated ``sitecustomize.py``.
"""
import ipaddress
import sys

_INSTALLED = False


def _loopback(host) -> bool:
    if host in ("localhost", b"localhost"):
        return True
    try:
        if isinstance(host, bytes):
            host = host.decode("ascii")
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError, UnicodeError):
        return False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    def audit(event, args):
        if event in ("socket.connect", "socket.sendto"):
            address = args[1]
            if not isinstance(address, tuple) or not _loopback(address[0]):
                raise PermissionError("rkb offline tests forbid external network connections")
        elif event == "socket.getaddrinfo" and not _loopback(args[0]):
            raise PermissionError("rkb offline tests forbid external DNS lookups")

    sys.addaudithook(audit)
    _INSTALLED = True


def installed() -> bool:
    return _INSTALLED


def child_command(user_code: str) -> list:
    """Command for a guarded child Python process (S02 v02, review finding S02-F03).

    The child does not depend on platform ``site``/``sitecustomize`` behaviour: it runs in isolated
    mode (``-I``), imports this module by absolute path, installs the guard, and prints ``GUARD_ACTIVE``
    *before* executing ``user_code``. If the guard cannot be installed it prints ``GUARD_MISSING`` and
    exits non-zero without running ``user_code``.
    """
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    bootstrap = (
        "import sys\n"
        f"sys.path.insert(0, {here!r})\n"
        "import netguard\n"
        "netguard.install()\n"
        "if not netguard.installed():\n"
        "    print('GUARD_MISSING', flush=True)\n"
        "    raise SystemExit(3)\n"
        "print('GUARD_ACTIVE', flush=True)\n"
        f"exec(compile({user_code!r}, '<guarded-child>', 'exec'))\n"
    )
    # -B: -I ignores PYTHONDONTWRITEBYTECODE, so bytecode writing is disabled explicitly (no __pycache__ in the tree).
    return [sys.executable, "-I", "-B", "-c", bootstrap]
