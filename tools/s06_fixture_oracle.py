"""Independent fixture oracle (dev-time only): do the S06 synthetic PDF families parse in a SECOND parser, pdfminer.six?

pdfminer.six (MIT) is not a project dependency and is never used by rkb; it only shows that the generated xref-stream,
object-stream and incremental-update fixtures are valid PDF syntax for another implementation, and that the negative
fixtures are rejected or yield no text there too. Usage: python -I tools/s06_fixture_oracle.py --out FILE.json
(the interpreter must have pdfminer.six installed; the version is recorded).
"""
import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

from tests import s06_pdf_fixtures as F  # noqa: E402


def cases():
    pages = [F.text_page(["Oracle title line", "Oracle second line"]), F.formula_page("Prose", "∑ x² ≤ α"),
             F.two_column_page(["left a", "left b"], ["right a", "right b"]), F.scanned_page()]
    good = {f: F.build_pdf(pages, syntax=f) for f in ("classic", "xref_stream", "object_stream")}
    good["incremental_classic"] = F.incremental_update(pages, F.text_page(["Appended oracle page"]), base_syntax="classic", syntax="classic")
    good["incremental_xref_stream"] = F.incremental_update(pages, F.text_page(["Appended oracle page"]), base_syntax="object_stream", syntax="xref_stream")
    base = good["xref_stream"]
    bad = {"truncated": F.truncated(base), "corrupt_xref_stream": F.corrupt_xref_stream(base), "broken_header": F.broken_header(base),
           "html": b"<!DOCTYPE html><html><body>challenge</body></html>\n"}
    return good, bad


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    import pdfminer
    from pdfminer.high_level import extract_text
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser
    good, bad = cases()
    report = {"schema": "rkb-s06-fixture-oracle/1", "oracle": "pdfminer.six", "oracle_version": pdfminer.__version__, "valid": {}, "negative": {}}
    ok = True
    for name, data in good.items():
        doc = PDFDocument(PDFParser(io.BytesIO(data)))
        n = sum(1 for _ in PDFPage.create_pages(doc))
        text = extract_text(io.BytesIO(data))
        expect = ["Oracle title line", "∑", "left a", "right b"] + (["Appended oracle page"] if name.startswith("incremental") else [])
        hit = all(e in text for e in expect)
        report["valid"][name] = {"pages": n, "expected_pages": 5 if name.startswith("incremental") else 4, "expected_text_found": hit}
        ok &= hit and n == report["valid"][name]["expected_pages"]
    for name, data in bad.items():
        try:
            text = extract_text(io.BytesIO(data))
            outcome = "NO_TEXT" if not text.strip() else "TEXT_EXTRACTED"
        except Exception as exc:  # noqa: BLE001 - oracle diagnostics only
            outcome = "REJECTED:" + type(exc).__name__
        report["negative"][name] = outcome
    report["ok"] = bool(ok)
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "negative": report["negative"]}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
