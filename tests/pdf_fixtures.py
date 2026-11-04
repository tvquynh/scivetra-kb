"""Synthetic PDF generator for tests (S01 v03 11 section 1 rule 2). Produces tiny text-only PDFs; no real publication.

``synthetic_pdf(pages=[["line", ...], ...], info={"doi": ...}, compress=False, encrypt=False)`` returns bytes with a
correct classic cross-reference table. Variants for negative tests are built from it (truncated, HTML, ...).
"""
import zlib


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def synthetic_pdf(pages, *, info=None, compress=False, encrypt=False, page_count_override=None) -> bytes:
    objects = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")          # placeholder, filled below
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for lines in pages:
        content = "BT /F1 11 Tf 72 720 Td " + " ".join(f"({_escape(line)}) Tj 0 -14 Td" for line in lines) + " ET"
        data = content.encode("latin-1")
        if compress:
            data = zlib.compress(data)
            header = f"<< /Length {len(data)} /Filter /FlateDecode >>".encode()
        else:
            header = f"<< /Length {len(data)} >>".encode()
        stream = add(header + b"\nstream\n" + data + b"\nendstream")
        kids.append(add(f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font} 0 R >> >> "
                        f"/Contents {stream} 0 R >>".encode()))
    count = len(kids) if page_count_override is None else page_count_override
    objects[pages_obj - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {count} >>".encode()
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    info_obj = None
    if info:
        info_obj = add(("<< " + " ".join(f"/{k} ({_escape(v)})" for k, v in info.items()) + " >>").encode("latin-1"))
    encrypt_obj = add(b"<< /Filter /Standard /V 1 /R 2 /O <00> /U <00> /P -4 >>") if encrypt else None
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    trailer = f"<< /Size {len(objects) + 1} /Root {catalog} 0 R"
    if info_obj:
        trailer += f" /Info {info_obj} 0 R"
    if encrypt_obj:
        trailer += f" /Encrypt {encrypt_obj} 0 R /ID [<00><00>]"
    out += ("trailer\n" + trailer + " >>\nstartxref\n" + str(xref) + "\n%%EOF\n").encode()
    return bytes(out)


def title_page(*, title, authors, doi=None, body=(), references=(), extra_first_lines=()):
    """Page 1 lines: optional header lines, title, authors, DOI line, body; then an optional References section."""
    lines = list(extra_first_lines) + [title, ", ".join(authors)]
    if doi:
        lines.append("https://doi.org/" + doi)
    lines += list(body)
    if references:
        lines += ["References"] + list(references)
    return lines


def html_challenge() -> bytes:
    return b"<!DOCTYPE html>\n<html><head><title>Checking your browser</title></head><body>challenge</body></html>\n"


def truncated(pdf: bytes) -> bytes:
    return pdf[: len(pdf) // 2]


def malformed_xref(pdf: bytes) -> bytes:
    index = pdf.rfind(b"startxref")
    return pdf[:index] + b"startxref\n7\n%%EOF\n"
