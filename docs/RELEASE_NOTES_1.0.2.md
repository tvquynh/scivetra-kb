# SciVetra KB 1.0.2: release notes (portability fixes)

**Status:** released on GitHub 2026-11-05 on the owner's go-ahead. Not on PyPI. Schema 11 unchanged; no `rkb upgrade` needed.

## Why 1.0.2

The first CI run of 1.0.1 (Windows, Ubuntu and macOS × Python 3.11 / 3.14) found problems that a single Windows machine could not show:

| Where | Problem | Fix |
|---|---|---|
| Linux / macOS, Python 3.14 | A provider body nested 50 000 levels was still refused, but with a different reason / path: these Python builds decode it without `RecursionError` | Raw nesting deeper than 512 is refused (`NESTING_TOO_DEEP`) **before** the JSON decoder runs, so the result is the same on every Python (REL-JSON-DEPTH-PORTABLE) |
| Windows, Python 3.14.10 | Under the 80 MiB parse memory cap the interpreter died with an unhandled `MemoryError` and was reported `PARSER_CHILD_FAILED` | Classified `RESOURCE_LIMIT_EXCEEDED`. Both outcomes are FAILED; nothing is parsed; stderr is inspected, never stored |
| macOS | PDF intake and parsing: the sandbox child cannot start, so **every PDF is rejected** (fails closed) | **Declared NOT_SUPPORTED** in the guides and `CAPABILITY_MATRIX.md`; the sandbox is not weakened. The CI temp folder avoids the `/var` symlink, which SciVetra refuses as a data root by design |

No security control was relaxed.

## Verified

- **GitHub Actions:** Windows and Ubuntu, Python 3.11 and 3.14 — full offline suite plus release gates G1–G8 (wheel and sdist, clean installs, installed end-to-end tests) PASS.
- **macOS:** runs in CI as a non-blocking job; it fails as described.

## Upgrade

Download 1.0.2 (or `git pull`) and run the installer again. `data` and `backups` are untouched.

## Known limitations

As in 1.0.1 (`RELEASE_NOTES_1.0.1.md`), plus macOS PDF support (OPEN_ISSUES R-101-07).
