"""S06 synthetic PDF families (S01 v03 11 section 1 rule 2): every byte is generated here; no real publication is used.

``build_pdf(pages, syntax=...)`` writes one PDF in one cross-reference family:

* ``classic``     - classic ``xref`` table + ``trailer`` (what the legacy stdlib validator accepts);
* ``xref_stream`` - PDF 1.5 cross-reference stream (``/Type /XRef``, binary ``/W`` entries, FlateDecode);
* ``object_stream`` - non-stream objects packed into ``/Type /ObjStm`` streams, located by type-2 xref-stream entries.

``incremental_update(base, ...)`` appends a revision (new objects + an xref section with ``/Prev``).
A page is a list of draw items: ``("text", font, size, x, y, text)``, ``("image", x, y, w, h)``. Fonts: ``F1`` Helvetica
WinAnsi; ``F2`` a simple font with a ``/Differences`` encoding and a ``/ToUnicode`` CMap (Greek, maths, accents);
``F3`` a Type0 Identity-H font WITHOUT ToUnicode (undecodable codes - a font decode problem by construction).
"""
import zlib

# F2 code -> Unicode. Codes 0x80.. are remapped through /Differences glyph names AND a ToUnicode CMap.
F2_MAP = {0x80: "α", 0x81: "β", 0x82: "∑", 0x83: "≤", 0x84: "√", 0x85: "é", 0x86: "ü", 0x87: "²",
          0x88: "∫", 0x89: "±", 0x8A: "≈", 0x8B: "ç"}
F2_GLYPHS = {0x80: "alpha", 0x81: "beta", 0x82: "summation", 0x83: "lessequal", 0x84: "radical", 0x85: "eacute", 0x86: "udieresis",
             0x87: "twosuperior", 0x88: "integral", 0x89: "plusminus", 0x8A: "approxequal", 0x8B: "ccedilla"}
_F2_REVERSE = {v: k for k, v in F2_MAP.items()}


def _escape(raw: bytes) -> bytes:
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def encode_text(font: str, text: str) -> bytes:
    """Bytes for a ``Tj`` string in the given synthetic font."""
    if font == "F2":
        return bytes(_F2_REVERSE[c] if c in _F2_REVERSE else ord(c) for c in text)
    if font == "F3":
        return b"".join((0x0100 + (ord(c) % 0x0F00)).to_bytes(2, "big") for c in text)
    return text.encode("cp1252")


def _tounicode_cmap() -> bytes:
    lines = [f"<{code:02X}> <{ord(ch):04X}>" for code, ch in sorted(F2_MAP.items())]
    ascii_range = "<20> <7E> <0020>"
    body = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Synthetic-UCS def\n/CMapType 2 def\n1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
            f"1 beginbfrange\n{ascii_range}\nendbfrange\n{len(lines)} beginbfchar\n" + "\n".join(lines) + "\nendbfchar\nendcmap\n"
            "CMapName currentdict /CMap defineresource pop\nend\nend\n")
    return body.encode("ascii")


class _Doc:
    def __init__(self):
        self.objects = {}          # num -> bytes (non-stream body) or (dict_bytes, data) for streams

    def reserve(self) -> int:
        num = len(self.objects) + 1
        self.objects[num] = None
        return num

    def set(self, num, body):
        self.objects[num] = body

    def add(self, body) -> int:
        num = self.reserve()
        self.objects[num] = body
        return num


def _stream(data: bytes, extra: str = "", *, compress=True):
    if compress:
        data = zlib.compress(data, 9)
        return (f"<< /Length {len(data)} /Filter /FlateDecode {extra}>>".encode(), data)
    return (f"<< /Length {len(data)} {extra}>>".encode(), data)


def _content(items) -> bytes:
    ops = []
    for item in items:
        if item[0] == "text":
            _, font, size, x, y, text = item
            ops.append(b"BT /" + font.encode() + f" {size} Tf 1 0 0 1 {x} {y} Tm (".encode() + _escape(encode_text(font, text)) + b") Tj ET")
        elif item[0] == "image":
            _, x, y, w, h = item
            ops.append(f"q {w} 0 0 {h} {x} {y} cm /Im1 Do Q".encode())
        elif item[0] == "raw":
            ops.append(item[1])
    return b"\n".join(ops) + b"\n"


def _fonts(doc):
    f1 = doc.add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    tou = doc.add(_stream(_tounicode_cmap()))
    diffs = " ".join(f"{code} /{name}" for code, name in sorted(F2_GLYPHS.items()))
    f2 = doc.add(f"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman /Encoding << /Type /Encoding /BaseEncoding /WinAnsiEncoding "
                 f"/Differences [{diffs}] >> /ToUnicode {tou} 0 R >>".encode())
    desc = doc.add(b"<< /Type /FontDescriptor /FontName /SyntheticCID /Flags 4 /FontBBox [0 0 1000 1000] /ItalicAngle 0 /Ascent 800 "
                   b"/Descent -200 /CapHeight 700 /StemV 80 >>")
    cid = doc.add(f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SyntheticCID /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) "
                  f"/Supplement 0 >> /FontDescriptor {desc} 0 R /DW 500 >>".encode())
    f3 = doc.add(f"<< /Type /Font /Subtype /Type0 /BaseFont /SyntheticCID /Encoding /Identity-H /DescendantFonts [{cid} 0 R] >>".encode())
    return f"<< /F1 {f1} 0 R /F2 {f2} 0 R /F3 {f3} 0 R >>"


def _image(doc) -> int:
    pixels = bytes((x * 31 + y * 17) % 256 for y in range(16) for x in range(16))
    return doc.add(_stream(pixels, "/Type /XObject /Subtype /Image /Width 16 /Height 16 /ColorSpace /DeviceGray /BitsPerComponent 8 "))


def _document(pages, info, compress, page_count_override=None, encrypt=False, extra_catalog=""):
    doc = _Doc()
    catalog, pages_num = doc.reserve(), doc.reserve()
    fonts = _fonts(doc)
    image = _image(doc)
    kids = []
    for items in pages:
        content = doc.add(_stream(_content(items), compress=compress))
        kids.append(doc.add(f"<< /Type /Page /Parent {pages_num} 0 R /MediaBox [0 0 612 792] /Resources << /Font {fonts} "
                            f"/XObject << /Im1 {image} 0 R >> >> /Contents {content} 0 R >>".encode()))
    count = len(kids) if page_count_override is None else page_count_override
    doc.set(pages_num, f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {count} >>".encode())
    doc.set(catalog, f"<< /Type /Catalog /Pages {pages_num} 0 R {extra_catalog}>>".encode())
    doc.kids, doc.fonts = kids, fonts
    info_num = doc.add(("<< " + " ".join(f"/{k} ({v})" for k, v in sorted(info.items())) + " >>").encode("latin-1")) if info else None
    encrypt_num = doc.add(b"<< /Filter /Standard /V 1 /R 2 /O <00> /U <00> /P -4 >>") if encrypt else None
    return doc, catalog, info_num, encrypt_num


def _obj_bytes(num, body) -> bytes:
    if isinstance(body, tuple):
        head, data = body
        return f"{num} 0 obj\n".encode() + head + b"\nstream\n" + data + b"\nendstream\nendobj\n"
    return f"{num} 0 obj\n".encode() + body + b"\nendobj\n"


def _xref_stream(entries: dict, size: int, trailer_extra: str, num: int, offset_of_self: int, prev=None, index=None) -> bytes:
    """entries: num -> (type, field2, field3). The xref stream's own entry is added here."""
    entries = dict(entries)
    entries[num] = (1, offset_of_self, 0)
    nums = sorted(entries) if index else list(range(size))
    rows = bytearray()
    for n in nums:
        t, a, b = entries.get(n, (0, 0, 65535 if n == 0 else 0))
        rows += bytes([t]) + a.to_bytes(4, "big") + b.to_bytes(2, "big")
    data = zlib.compress(bytes(rows), 9)
    idx = ""
    if index:
        spans, start, prev_n = [], None, None
        for n in nums:
            if start is None:
                start = prev_n = n
            elif n == prev_n + 1:
                prev_n = n
            else:
                spans.append((start, prev_n - start + 1))
                start = prev_n = n
        spans.append((start, prev_n - start + 1))
        idx = "/Index [" + " ".join(f"{s} {c}" for s, c in spans) + "] "
    prev_text = f"/Prev {prev} " if prev is not None else ""
    head = (f"<< /Type /XRef /Size {size} /W [1 4 2] {idx}{prev_text}{trailer_extra} /Length {len(data)} /Filter /FlateDecode >>").encode()
    return f"{num} 0 obj\n".encode() + head + b"\nstream\n" + data + b"\nendstream\nendobj\n"


def _trailer_extra(catalog, info_num, encrypt_num, file_id=True) -> str:
    out = f"/Root {catalog} 0 R"
    if info_num:
        out += f" /Info {info_num} 0 R"
    if encrypt_num:
        out += f" /Encrypt {encrypt_num} 0 R"
    if file_id or encrypt_num:
        out += " /ID [<53594e5448455449432d5331362d4944> <53594e5448455449432d5331362d4944>]"
    return out


def build_pdf(pages, **kw) -> bytes:
    return _build(pages, **kw)[0]


def _build(pages, *, syntax="classic", info=None, compress=True, version="1.7", page_count_override=None, encrypt=False,
           extra_catalog="", objstm_chunk=50):
    doc, catalog, info_num, encrypt_num = _document(pages, info, compress, page_count_override, encrypt, extra_catalog)
    meta = {"root": catalog, "pages_num": 2, "kids": doc.kids, "fonts": doc.fonts}
    header = f"%PDF-{version}\n%\xe2\xe3\xcf\xd3\n".encode("latin-1")
    out = bytearray(header)
    extra = _trailer_extra(catalog, info_num, encrypt_num)
    if syntax == "classic":
        offsets = {}
        for num in sorted(doc.objects):
            offsets[num] = len(out)
            out += _obj_bytes(num, doc.objects[num])
        size = len(doc.objects) + 1
        xref = len(out)
        out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n" + b"".join(f"{offsets[n]:010d} 00000 n \n".encode() for n in range(1, size))
        out += f"trailer\n<< /Size {size} {extra} >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out), dict(meta, size=size, startxref=xref)
    entries = {}
    if syntax == "xref_stream":
        for num in sorted(doc.objects):
            entries[num] = (1, len(out), 0)
            out += _obj_bytes(num, doc.objects[num])
    elif syntax == "object_stream":
        packable = [n for n in sorted(doc.objects) if not isinstance(doc.objects[n], tuple)]
        for num in sorted(doc.objects):
            if isinstance(doc.objects[num], tuple):
                entries[num] = (1, len(out), 0)
                out += _obj_bytes(num, doc.objects[num])
        next_num = len(doc.objects) + 1
        for start in range(0, len(packable), objstm_chunk):
            chunk = packable[start:start + objstm_chunk]
            stm_num, next_num = next_num, next_num + 1
            bodies, header_parts, pos = [], [], 0
            for i, n in enumerate(chunk):
                header_parts.append(f"{n} {pos}")
                body = doc.objects[n] + b"\n"
                bodies.append(body)
                pos += len(body)
                entries[n] = (2, stm_num, i)
            head_text = (" ".join(header_parts) + "\n").encode()
            data = head_text + b"".join(bodies)
            entries[stm_num] = (1, len(out), 0)
            out += _obj_bytes(stm_num, _stream(data, f"/Type /ObjStm /N {len(chunk)} /First {len(head_text)} "))
        doc_size = next_num
        xref_num = doc_size
        xref_off = len(out)
        out += _xref_stream(entries, doc_size + 1, extra, xref_num, xref_off)
        out += f"startxref\n{xref_off}\n%%EOF\n".encode()
        return bytes(out), dict(meta, size=doc_size + 1, startxref=xref_off)
    else:
        raise ValueError(syntax)
    xref_num = len(doc.objects) + 1
    xref_off = len(out)
    out += _xref_stream(entries, xref_num + 1, extra, xref_num, xref_off)
    out += f"startxref\n{xref_off}\n%%EOF\n".encode()
    return bytes(out), dict(meta, size=xref_num + 1, startxref=xref_off)


def incremental_update(pages, new_page_items, *, base_syntax="classic", syntax="classic") -> bytes:
    """Build ``pages`` in ``base_syntax``, then append one revision: a new page (content stream + page object), a
    REPLACED Pages node listing it, and an xref section (classic table or xref stream) that points back with /Prev."""
    base, meta = _build(pages, syntax=base_syntax)
    size, root, prev, pages_num, kids, fonts_ref = meta["size"], meta["root"], meta["startxref"], meta["pages_num"], meta["kids"], meta["fonts"]
    out = bytearray(base if base.endswith(b"\n") else base + b"\n")
    entries, num = {}, size
    content = num
    entries[content] = (1, len(out), 0)
    out += _obj_bytes(content, _stream(_content(new_page_items)))
    page = num + 1
    entries[page] = (1, len(out), 0)
    out += _obj_bytes(page, f"<< /Type /Page /Parent {pages_num} 0 R /MediaBox [0 0 612 792] /Resources << /Font {fonts_ref} >> "
                            f"/Contents {content} 0 R >>".encode())
    entries[pages_num] = (1, len(out), 0)
    all_kids = kids + [page]
    out += _obj_bytes(pages_num, f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in all_kids)}] /Count {len(all_kids)} >>".encode())
    new_size = num + 2
    extra = f"/Root {root} 0 R /ID [<53594e5448455449432d5331362d4944> <53594e5448455449432d5331362d4944>]"
    if syntax == "classic":
        xref = len(out)
        out += b"xref\n0 1\n0000000000 65535 f \n"
        for n in sorted(entries):
            out += f"{n} 1\n{entries[n][1]:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {new_size} {extra} /Prev {prev} >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)
    xref_num = new_size
    xref_off = len(out)
    out += _xref_stream(entries, xref_num + 1, extra, xref_num, xref_off, prev=prev, index=True)
    out += f"startxref\n{xref_off}\n%%EOF\n".encode()
    return bytes(out)


# -- page layouts (all synthetic text) ------------------------------------------------
def text_page(lines, *, font="F1", size=11, x=72, top=720, step=14):
    return [("text", font, size, x, top - i * step, line) for i, line in enumerate(lines)]


def two_column_page(left, right, *, size=10, top=720, step=13):
    items = []
    for i, line in enumerate(left):
        items.append(("text", "F1", size, 72, top - i * step, line))
    for i, line in enumerate(right):
        items.append(("text", "F1", size, 320, top - i * step, line))
    return items


def table_page(caption, rows, *, top=700, step=16, xs=(72, 200, 320, 440)):
    items = [("text", "F1", 11, 72, top + 30, caption)]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            items.append(("text", "F1", 10, xs[c], top - r * step, cell))
    return items


def formula_page(prose, formula):
    return [("text", "F1", 11, 72, 720, prose), ("text", "F2", 12, 100, 690, formula), ("text", "F1", 11, 72, 660, "where the symbols are synthetic.")]


def footnote_page(lines, note):
    return text_page(lines) + [("text", "F1", 6, 72, 60, note)]


def scanned_page():
    return [("image", 0, 0, 612, 792)]


def undecodable_page(text):
    return [("text", "F3", 11, 72, 720, text)]


# -- negative variants ---------------------------------------------------------------
def truncated(pdf: bytes) -> bytes:
    return pdf[: len(pdf) // 2]


def broken_header(pdf: bytes) -> bytes:
    return b"%PFD" + pdf[4:]


def no_eof(pdf: bytes) -> bytes:
    return pdf[: pdf.rfind(b"%%EOF")]


def corrupt_xref_stream(pdf: bytes) -> bytes:
    """Flip bytes inside the last (xref) stream's compressed data: the stream no longer inflates."""
    end = pdf.rfind(b"endstream")
    start = pdf.rfind(b"stream\n", 0, end) + 7
    mid = (start + end) // 2
    return pdf[:mid] + bytes(b ^ 0x5A for b in pdf[mid:mid + 8]) + pdf[mid + 8:]


def wrong_startxref(pdf: bytes) -> bytes:
    i = pdf.rfind(b"startxref")
    return pdf[:i] + b"startxref\n9\n%%EOF\n"


def decompression_bomb(*, inflated_bytes=200 * 1024 * 1024) -> bytes:
    """One page whose content stream inflates to ``inflated_bytes`` of spaces (compressed it is about 0.2 MB)."""
    comp = zlib.compressobj(9)
    chunk = b" " * (1 << 20)
    data = b"".join(comp.compress(chunk) for _ in range(inflated_bytes >> 20)) + comp.flush()
    doc = _Doc()
    catalog, pages_num = doc.reserve(), doc.reserve()
    content = doc.add((f"<< /Length {len(data)} /Filter /FlateDecode >>".encode(), data))
    page = doc.add(f"<< /Type /Page /Parent {pages_num} 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R >>".encode())
    doc.set(pages_num, f"<< /Type /Pages /Kids [{page} 0 R] /Count 1 >>".encode())
    doc.set(catalog, f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode())
    return _serialize_classic(doc, catalog)


def page_explosion(n_refs=60000) -> bytes:
    """A page tree whose /Kids repeats one page reference ``n_refs`` times (declared /Count matches)."""
    doc = _Doc()
    catalog, pages_num = doc.reserve(), doc.reserve()
    content = doc.add(_stream(b"BT /F1 11 Tf 72 720 Td (x) Tj ET\n"))
    page = doc.add(f"<< /Type /Page /Parent {pages_num} 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R >>".encode())
    doc.set(pages_num, (f"<< /Type /Pages /Kids [" + " ".join([f"{page} 0 R"] * n_refs) + f"] /Count {n_refs} >>").encode())
    doc.set(catalog, f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode())
    return _serialize_classic(doc, catalog)


def operator_flood(n_ops=3_000_000) -> bytes:
    """A page whose content stream holds millions of tiny text operators (CPU / wall-clock stress, compressed small)."""
    ops = b"BT /F1 1 Tf " + b"(a) Tj " * n_ops + b"ET\n"
    return build_pdf([[("raw", ops)]], syntax="classic")


def javascript_action(text_lines) -> bytes:
    """A valid PDF whose catalog carries an /OpenAction JavaScript action (must never execute; only flagged)."""
    return build_pdf([text_page(text_lines)], extra_catalog="/OpenAction << /S /JavaScript /JS (app.alert\\(1\\);) >> /Names << >> ")


def _serialize_classic(doc, catalog) -> bytes:
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(doc.objects):
        offsets[num] = len(out)
        out += _obj_bytes(num, doc.objects[num])
    size = len(doc.objects) + 1
    xref = len(out)
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n" + b"".join(f"{offsets[n]:010d} 00000 n \n".encode() for n in range(1, size))
    out += f"trailer\n<< /Size {size} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
