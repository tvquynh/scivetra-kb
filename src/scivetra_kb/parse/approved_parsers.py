"""Reviewed parser builds (S06-F01). This table is code, reviewed with the release; it is not a user-editable local manifest.

``official_wheel_sha256`` is the SHA-256 of the PyPI release file as recorded INDEPENDENTLY by the architect
(SCIVETRA_S06_V02_REVISION_HANDOFF_v01/evidence/DEPENDENCY_VERSION_AND_OFFICIAL_HASH.txt); it is never read from the wheel or a
local file. ``package_files_sha256`` is the fingerprint of the package files INSIDE that official wheel
(SHA-256 over the canonical JSON of the sorted ``[path, sha256]`` list of every ``pypdf/`` file), computed only after the wheel's
SHA-256 matched (``tools/s06_parser_provenance.py``). An installed copy is trusted only if its files reproduce that fingerprint.
"""

PINNED = "pypdf==6.12.1"

APPROVED = {
    "pypdf==6.12.1": {
        "name": "pypdf",
        "version": "6.12.1",
        "license": "BSD-3-Clause",
        "official_wheel": "pypdf-6.12.1-py3-none-any.whl",
        "official_wheel_sha256": "8fa2a2321cf16247ed848bd7c97f193a60c08670d04abed5b0138327e51c43b0",
        "official_wheel_sha256_source": "architect-recorded PyPI release-file SHA-256 (published 2026-05-22), handoff SCIVETRA_S06_V02_REVISION_HANDOFF_v01",
        "package_files_sha256": "23c6a7c75c09b3b24ffa5cebb56b6f8a566473449c4f1450d710620b7ffc4bfd",
        "package_file_count": 56,
        "package_fingerprint_source": "tools/s06_parser_provenance.py on the official wheel (343,787 bytes) after its SHA-256 matched; "
                                      "wheel RECORD verified against the wheel bytes; downloaded once from PyPI with owner approval, 2026-11-02",
    },
}
