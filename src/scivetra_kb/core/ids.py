"""Injectable clock and ID providers (ULID text form, Crockford base32)."""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


class Clock:
    """System UTC clock."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def now_utc_text(self) -> str:
        return format_utc(self.now())


class FixedClock(Clock):
    """Deterministic test clock; ``advance`` moves it forward."""

    def __init__(self, start: str = "2026-11-01T00:00:00.000000Z"):
        self._now = parse_utc(start)

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta) -> None:
        self._now = self._now + timedelta(**delta)


def format_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class IdProvider:
    """Monotonic ULID-style IDs with a type prefix, e.g. ``ps_01J...``."""

    def __init__(self, clock: Clock | None = None):
        self.clock = clock or Clock()
        self._lock = threading.Lock()
        self._last_ms = -1
        self._last_rand = 0

    def _random(self) -> int:
        return int.from_bytes(os.urandom(10), "big")

    def new(self, prefix: str) -> str:
        with self._lock:
            ms = int(self.clock.now().timestamp() * 1000)
            if ms <= self._last_ms:
                ms = self._last_ms
                rand = (self._last_rand + 1) & ((1 << 80) - 1)
            else:
                rand = self._random()
            self._last_ms, self._last_rand = ms, rand
        return prefix + "_" + _b32(ms, 10) + _b32(rand, 16)


class SequentialIdProvider(IdProvider):
    """Fully deterministic IDs for tests: prefix + zero-padded counter."""

    def __init__(self, clock: Clock | None = None):
        super().__init__(clock)
        self._n = 0

    def new(self, prefix: str) -> str:
        with self._lock:
            self._n += 1
            return f"{prefix}_{self._n:026d}"
