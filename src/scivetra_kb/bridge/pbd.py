"""PaperBatchDownloader compatibility profile ``pbd-csv-v1`` (S01 v03 06 sections 4 and 6; S04-D10).

Pinned against PaperBatchDownloader V5.2.11 at commit ``2901b04661af7edd65fa6a30705fa948c9a0b0c3``
(``PaperBatchDownloader/CsvUtils.cs`` git blob ``3d4e607faa21a1771e93fb1eac1e8145bc23cae7``,
``DownloadJob.cs`` blob ``fcd15b2183f38086559ad8160f4235f9a4201ed4``). Facts used (read-only source inspection):
* ``ReadJobs`` opens the file with BOM detection, trims every field, maps ``record_id`` to ``OpenAlexId`` only when no
  ``openalex_id`` column is present, and skips blank lines without consuming an id.
* ``WriteResults`` writes UTF-8 with BOM, the fixed 22-column header, every text field quoted with ``"`` doubled,
  ``id`` and ``attempts`` unquoted, line ends from ``StreamWriter.WriteLine`` (CRLF on Windows).
* ``StatusText`` is the ``JobStatus`` enum name (14 values).
Any other header or status is refused / quarantined; a PBD upgrade needs a new profile.
"""
from __future__ import annotations

import csv
import io

PROFILE = "pbd-csv-v1"
PBD_VERSION_EXPECTED = "V5.2.11"
PBD_PINNED_COMMIT = "2901b04661af7edd65fa6a30705fa948c9a0b0c3"
PBD_CSVUTILS_BLOB = "3d4e607faa21a1771e93fb1eac1e8145bc23cae7"
INPUT_COLUMNS = ("record_id", "doi", "title", "publication_year", "publisher", "source", "is_open_access", "oa_status",
                 "best_oa_pdf_url", "pdf_url", "landing_page", "save_as")
OUTPUT_COLUMNS = ("id", "openalex_id", "doi", "title", "publication_year", "publisher", "publisher_key", "source", "is_open_access",
                  "oa_status", "url_source", "selected_url", "status", "preflight", "page_decision", "attention_reason", "deferred_reason",
                  "pdf_url", "file_path", "attempts", "error", "save_as")
INPUT_HEADER, OUTPUT_HEADER = ",".join(INPUT_COLUMNS), ",".join(OUTPUT_COLUMNS)
BOM = b"\xef\xbb\xbf"
MAX_RESULT_CSV_BYTES = 64 * 1024 * 1024
MAX_FIELD_CHARS = 100_000

# 06 section 6.2 (exact). Values: (bridge status, final for the batch).
STATUS_MAP = {
    "Completed": ("RECEIVED_PENDING_VERIFY", False),
    "Failed": ("FAILED", True),
    "Deferred": ("DEFERRED", False),
    "LoginRequired": ("NEEDS_HUMAN_ACCESS", False),
    "InstitutionalAccessRequired": ("NEEDS_HUMAN_ACCESS", False),
    "BotCheck": ("NEEDS_HUMAN_ACTION", False),
    "WaitingForUser": ("NEEDS_HUMAN_ACTION", False),
    "ManualAttention": ("NEEDS_HUMAN_ACTION", False),
    "Skipped": ("SKIPPED", True),
    "NoUrl": ("NO_URL", True),
    "Pending": ("IN_PROGRESS_AT_EXPORT", False),
    "Navigating": ("IN_PROGRESS_AT_EXPORT", False),
    "LookingForPdf": ("IN_PROGRESS_AT_EXPORT", False),
    "Downloading": ("IN_PROGRESS_AT_EXPORT", False),
}
PBD_JOB_STATUSES = ("Pending", "Navigating", "LookingForPdf", "Downloading", "WaitingForUser", "LoginRequired", "BotCheck",
                    "InstitutionalAccessRequired", "ManualAttention", "Deferred", "Completed", "Failed", "Skipped", "NoUrl")
FINAL_ROW_STATUSES = ("VERIFIED_PDF", "REJECTED_FILE", "FAILED", "SKIPPED", "NO_URL")
PAGE_NOT_FOUND_REASON = "Permanent page-not-found response"


class CsvFormatError(ValueError):
    """Raised with a stable ``code`` when a CSV cannot be read under the profile."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def map_status(raw: str) -> tuple[str, str | None]:
    """(bridge_status, sub_reason). Unknown values are UNKNOWN_STATUS (quarantined), never coerced."""
    if raw not in STATUS_MAP:
        return "UNKNOWN_STATUS", None
    return STATUS_MAP[raw][0], None


def failed_sub_reason(deferred_reason: str) -> str:
    """Failed never means 'paper not found' (F06): only PBD's page-analysis NotFound text is named, else UNSPECIFIED."""
    return "PBD_REPORTED_PAGE_NOT_FOUND" if deferred_reason == PAGE_NOT_FOUND_REASON else "UNSPECIFIED"


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def write_input_csv(rows: list[dict]) -> bytes:
    """PBD input CSV v1: UTF-8 BOM, every field quoted, CRLF, exact 12-column header, no blank lines."""
    lines = [INPUT_HEADER]
    for row in rows:
        values = [row[c] for c in INPUT_COLUMNS]
        if any(not isinstance(v, str) or "\x00" in v for v in values):
            raise ValueError("input CSV values must be NUL-free strings")
        lines.append(",".join(_quote(v) for v in values))
    return BOM + ("\r\n".join(lines) + "\r\n").encode("utf-8")


def parse_csv(data: bytes, expected_header: str) -> list[list[str]]:
    """Strict RFC 4180 read of a profile CSV. Optional BOM; UTF-8 only; NUL refused; header byte-exact after BOM removal.

    Returns the data rows (header excluded). Physically blank lines are skipped like PBD does. Rows are returned as read;
    the caller decides on field-count anomalies per row.
    """
    if len(data) > MAX_RESULT_CSV_BYTES:
        raise CsvFormatError("CSV_TOO_LARGE", "CSV exceeds the size limit")
    if data.startswith(BOM):
        data = data[len(BOM):]
    if b"\x00" in data:
        raise CsvFormatError("ENCODING_REJECTED", "NUL byte in CSV")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise CsvFormatError("ENCODING_REJECTED", "CSV is not UTF-8") from None
    first_break = min([i for i in (text.find("\r"), text.find("\n")) if i >= 0] or [len(text)])
    if text[:first_break] != expected_header:
        raise CsvFormatError("HEADER_MISMATCH", "CSV header differs from the pinned profile")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    csv.field_size_limit(MAX_FIELD_CHARS + 1)
    try:
        rows = list(reader)
    except csv.Error:
        raise CsvFormatError("CSV_MALFORMED", "CSV quoting/escaping is malformed") from None
    if any(len(field) > MAX_FIELD_CHARS for row in rows for field in row):
        raise CsvFormatError("CSV_MALFORMED", "CSV field too long")
    return [r for r in rows[1:] if r and not all(not f.strip() for f in r)]


__all__ = ["PROFILE", "INPUT_COLUMNS", "OUTPUT_COLUMNS", "INPUT_HEADER", "OUTPUT_HEADER", "STATUS_MAP", "FINAL_ROW_STATUSES", "map_status",
           "failed_sub_reason", "write_input_csv", "parse_csv", "CsvFormatError"]
