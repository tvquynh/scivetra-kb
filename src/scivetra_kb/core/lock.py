"""OS-held single-writer lock ``<data_root>/.rkb-writer.lock`` (msvcrt/fcntl)."""
import json
import os
from contextlib import contextmanager
from pathlib import Path

from .errors import LockError, ValidationError
from .paths import is_reparse_or_link

LOCK_NAME = ".rkb-writer.lock"


@contextmanager
def writer_lock(data_root: Path):
    path = Path(data_root) / LOCK_NAME
    if is_reparse_or_link(path):
        raise ValidationError("Lock path is a link", code="REPARSE_POINT")
    handle = open(path, "a+b")
    try:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise LockError("Another rkb writer is active") from None
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"schema": "rkb-writer-lock/1", "pid": os.getpid()}).encode())
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
