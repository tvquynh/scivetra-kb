"""Static, read-only audit of the pinned PaperBatchDownloader source against profile pbd-csv-v1 (no .NET execution).

Usage: python -I tools/pbd_contract_audit.py --pbd-repo <read-only clone> --out <json>

Checks (string-level, on the exact pinned files): commit and git blob ids, the WriteResults header, the JobStatus
enum, the record_id/openalex_id precedence, the quoting helper, DOI prefix stripping, field trimming, the URL
precedence and the save_as file-name guard. The clone is only read (``git rev-parse``/``git status``/``git hash-object``).
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from scivetra_kb.bridge import pbd  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbd-repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    repo = args.pbd_repo
    src = repo / "PaperBatchDownloader"
    csv_utils, job, file_utils, main_form = (src / n for n in ("CsvUtils.cs", "DownloadJob.cs", "FileUtils.cs", "MainForm.cs"))
    text = {p.name: p.read_text(encoding="utf-8-sig") for p in (csv_utils, job, file_utils, main_form)}
    enum = re.search(r"public enum JobStatus\s*\{([^}]*)\}", text["DownloadJob.cs"]).group(1)
    statuses = tuple(s.strip() for s in enum.split(",") if s.strip())
    header = re.search(r'sw\.WriteLine\("(id,openalex_id[^"]*)"\)', text["CsvUtils.cs"]).group(1)
    checks = {
        "commit_is_pinned": git(repo, "rev-parse", "HEAD") == pbd.PBD_PINNED_COMMIT,
        "working_tree_clean": git(repo, "status", "--porcelain") == "",
        "csvutils_blob_is_pinned": git(repo, "hash-object", str(csv_utils)) == pbd.PBD_CSVUTILS_BLOB,
        "output_header_equals_profile": header == pbd.OUTPUT_HEADER,
        "job_status_enum_equals_profile": statuses == pbd.PBD_JOB_STATUSES,
        "status_text_is_enum_name": "public string StatusText => Status.ToString();" in text["DownloadJob.cs"],
        "record_id_used_only_without_openalex_id": 'OpenAlexId = !string.IsNullOrWhiteSpace(Get("openalex_id")) ? Get("openalex_id") : Get("record_id")' in text["CsvUtils.cs"],
        "fields_trimmed_on_read": "fields[idx].Trim()" in text["CsvUtils.cs"],
        "bom_detected_on_read": "new StreamReader(path, Encoding.UTF8, true)" in text["CsvUtils.cs"],
        "bom_written_on_results": "new UTF8Encoding(true)" in text["CsvUtils.cs"],
        "quote_helper_doubles_quotes": 'Replace("\\"", "\\"\\"")' in text["CsvUtils.cs"],
        "id_and_attempts_unquoted": "j.Id.ToString(), Q(j.OpenAlexId)" in text["CsvUtils.cs"] and "j.Attempts.ToString(), Q(j.Error), Q(j.SaveAs)" in text["CsvUtils.cs"],
        "doi_prefixes_stripped": all(s in text["CsvUtils.cs"] for s in ('"https://doi.org/"', '"http://doi.org/"', '"doi:"')),
        "doi_url_not_percent_encoded": 'job.Url = "https://doi.org/" + job.Doi;' in text["CsvUtils.cs"],
        "url_precedence_best_pdf_then_pdf_then_landing_then_doi": text["CsvUtils.cs"].index("IsAbsoluteHttpUrl(job.BestOaPdfUrl)")
        < text["CsvUtils.cs"].index("IsAbsoluteHttpUrl(job.OaUrl)") < text["CsvUtils.cs"].index("IsAbsoluteHttpUrl(job.BestOaLandingPage)")
        < text["CsvUtils.cs"].index('"https://doi.org/" + job.Doi'),
        "pdf_url_cell_preferred_over_oa_url": 'OaUrl = !string.IsNullOrWhiteSpace(Get("pdf_url")) ? Get("pdf_url") : Get("oa_url")' in text["CsvUtils.cs"],
        "blank_rows_skipped": "fields.All(string.IsNullOrWhiteSpace)) continue;" in text["CsvUtils.cs"],
        "save_as_must_be_flat_pdf_name": "saveAs != Path.GetFileName(saveAs)" in text["FileUtils.cs"] and '.EndsWith(".pdf"' in text["FileUtils.cs"],
        "save_as_never_overwrites": "File already exists" in text["FileUtils.cs"],
        "failed_page_not_found_text": pbd.PAGE_NOT_FOUND_REASON in text["MainForm.cs"],
        "version_title_v5_2_11": pbd.PBD_VERSION_EXPECTED in text["MainForm.cs"],
        "foreground_pdf_check_is_magic_only": "HasPdfMagic" in text["FileUtils.cs"],
    }
    result = {"schema": "rkb-pbd-source-audit/1", "profile": pbd.PROFILE, "repository": "tvquynh/PaperBatchDownloader (local read-only clone)",
              "commit": git(repo, "rev-parse", "HEAD"), "files": {p.name: git(repo, "hash-object", str(p)) for p in (csv_utils, job, file_utils, main_form)},
              "job_statuses": list(statuses), "output_header": header, "checks": checks, "all_passed": all(checks.values()),
              "dotnet_execution": "NOT_RUN (no .NET SDK on the host)",
              "note": "The architect's orientation file lists CsvUtils.cs blob dfda3180 for a newer main; this audit pins blob 3d4e607f at 2901b046. "
                      "Compatibility with any other PBD revision is NOT assumed."}
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"all_passed": result["all_passed"], "failed": [k for k, v in checks.items() if not v]}))
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
