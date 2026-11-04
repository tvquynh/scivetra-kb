"""Isolated G1 structural validator: runs ONLY as a child process (``python -I -S -B pdf_child.py --input FILE``).

Stdlib only; it imports nothing from the KB package. Before reading untrusted bytes it:
1. caps its own memory (Windows job object / POSIX RLIMIT_AS) and forbids child processes;
2. reads the input file once;
3. installs an audit hook that refuses every further ``open``, socket, DNS and process-creation event.
It prints exactly one JSON object on stdout. Anything it cannot fully parse is refused (conservative): classic
cross-reference tables only, no object/xref streams, only unfiltered or FlateDecode content streams.
Validator ``rkb-pdf-structure/1``. Exit codes: 0 result printed, 4 sandbox could not be established.
"""
import json
import os
import re
import sys
import zlib

VALIDATOR, VERSION = "rkb-pdf-structure", "1"
MAX_OBJECTS, MAX_DEPTH, MAX_PAGES, MAX_TEXT_PAGES = 200_000, 32, 10_000, 3
MAX_INFLATE = 4 * 1024 * 1024
_REFUSED_EVENTS = ("open", "socket.", "subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile",
                   "os.fork", "ctypes.dlopen", "urllib.Request", "webbrowser.open")


def _limit_memory(limit: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                          "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", ctypes.c_uint32), ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = EXTENDED()
        # PROCESS_MEMORY (0x100) | ACTIVE_PROCESS (0x8) | KILL_ON_JOB_CLOSE (0x2000) | DIE_ON_UNHANDLED_EXCEPTION (0x400)
        info.BasicLimitInformation.LimitFlags = 0x100 | 0x8 | 0x2000 | 0x400
        info.BasicLimitInformation.ActiveProcessLimit = 1
        info.ProcessMemoryLimit = limit
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return False
        return bool(kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()))
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    if hasattr(resource, "RLIMIT_NPROC"):
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
        except ValueError:      # some kernels refuse lowering below the current count; the audit hook still blocks spawning
            pass
    return True


def _install_hook():
    def audit(event, args):
        if event == "open" or event.startswith(_REFUSED_EVENTS):
            raise PermissionError("refused in PDF validator sandbox: " + event)
    sys.addaudithook(audit)


# -- minimal PDF object parser ----------------------------------------------------
class Reject(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code, self.detail = code, detail


class Ref:
    __slots__ = ("num", "gen")

    def __init__(self, num, gen):
        self.num, self.gen = num, gen


_DELIM = b"()<>[]{}/%"
_WS = b"\x00\t\n\x0c\r "
_NUM = re.compile(rb"[+-]?(\d+\.?\d*|\.\d+)")


class Lexer:
    def __init__(self, data, pos=0):
        self.data, self.pos = data, pos

    def skip(self):
        d = self.data
        while self.pos < len(d):
            c = d[self.pos]
            if c in _WS:
                self.pos += 1
            elif c == 0x25:  # comment
                while self.pos < len(d) and d[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def value(self, depth=0):
        if depth > MAX_DEPTH:
            raise Reject("PARSER_REJECTED", "nesting")
        self.skip()
        d, p = self.data, self.pos
        if p >= len(d):
            raise Reject("PARSER_REJECTED", "eof in object")
        if d.startswith(b"<<", p):
            self.pos += 2
            out = {}
            while True:
                self.skip()
                if d.startswith(b">>", self.pos):
                    self.pos += 2
                    return out
                key = self.value(depth + 1)
                if not isinstance(key, Name):
                    raise Reject("PARSER_REJECTED", "dict key")
                out[str(key)] = self.value(depth + 1)
        if d[p] == 0x5B:  # [
            self.pos += 1
            out = []
            while True:
                self.skip()
                if self.pos < len(d) and d[self.pos] == 0x5D:
                    self.pos += 1
                    return out
                out.append(self.value(depth + 1))
                if len(out) > MAX_OBJECTS:
                    raise Reject("PARSER_REJECTED", "array too long")
        if d[p] == 0x2F:  # name
            q = p + 1
            while q < len(d) and d[q] not in _WS and d[q] not in _DELIM:
                q += 1
            self.pos = q
            return Name(d[p + 1:q].decode("latin-1"))
        if d[p] == 0x28:
            return self.literal()
        if d[p] == 0x3C:
            q = d.find(b">", p)
            if q < 0:
                raise Reject("PARSER_REJECTED", "hex string")
            raw = re.sub(rb"\s", b"", d[p + 1:q])
            self.pos = q + 1
            try:
                return bytes.fromhex((raw + b"0" * (len(raw) % 2)).decode("ascii"))
            except ValueError:
                raise Reject("PARSER_REJECTED", "hex string") from None
        m = _NUM.match(d, p)
        if m:
            self.pos = m.end()
            if b"." in m.group(0):
                return float(m.group(0))
            first = int(m.group(0))
            save = self.pos
            m2 = re.compile(rb"\s+(\d+)\s+R(?![A-Za-z])").match(d, self.pos)
            if m2:
                self.pos = m2.end()
                return Ref(first, int(m2.group(1)))
            self.pos = save
            return first
        for word, val in ((b"true", True), (b"false", False), (b"null", None)):
            if d.startswith(word, p):
                self.pos += len(word)
                return val
        raise Reject("PARSER_REJECTED", "unexpected token")

    def literal(self):
        d, p = self.data, self.pos + 1
        out, level = bytearray(), 1
        escapes = {0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09, 0x62: 0x08, 0x66: 0x0C, 0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}
        while p < len(d):
            c = d[p]
            if c == 0x5C and p + 1 < len(d):
                n = d[p + 1]
                if n in escapes:
                    out.append(escapes[n])
                    p += 2
                    continue
                if 0x30 <= n <= 0x37:
                    m = re.compile(rb"[0-7]{1,3}").match(d, p + 1)
                    out.append(int(m.group(0), 8) & 0xFF)
                    p = m.end()
                    continue
                p += 2
                continue
            if c == 0x28:
                level += 1
            elif c == 0x29:
                level -= 1
                if level == 0:
                    self.pos = p + 1
                    return bytes(out)
            out.append(c)
            p += 1
        raise Reject("PARSER_REJECTED", "unterminated string")


class Name(str):
    pass


class Document:
    def __init__(self, data):
        self.data = data
        self.offsets = {}
        self.trailer = {}
        self.cache = {}

    def load_xref(self):
        d = self.data
        sx = d.rfind(b"startxref")
        if sx < 0:
            raise Reject("PARSER_REJECTED", "no startxref")
        m = re.compile(rb"startxref\s+(\d+)").match(d, sx)
        if not m:
            raise Reject("PARSER_REJECTED", "bad startxref")
        offset = int(m.group(1))
        seen = set()
        while offset is not None:
            if offset in seen or offset >= len(d):
                raise Reject("PARSER_REJECTED", "xref offset")
            seen.add(offset)
            if not d.startswith(b"xref", offset):
                raise Reject("PARSER_REJECTED", "xref stream or invalid xref (unsupported)")
            pos = offset + 4
            head = re.compile(rb"\s*(\d+)\s+(\d+)\s*?\r?\n")
            while True:
                m = head.match(d, pos)
                if not m:
                    break
                start, count = int(m.group(1)), int(m.group(2))
                pos = m.end()
                if count > MAX_OBJECTS:
                    raise Reject("PARSER_REJECTED", "xref too large")
                for i in range(count):
                    entry = d[pos:pos + 20]
                    em = re.compile(rb"(\d{10}) (\d{5}) ([nf])").match(entry)
                    if not em:
                        raise Reject("PARSER_REJECTED", "xref entry")
                    if em.group(3) == b"n" and (start + i) not in self.offsets:
                        self.offsets[start + i] = int(em.group(1))
                    pos += 20
            t = re.compile(rb"\s*trailer").match(d, pos)
            if not t:
                raise Reject("PARSER_REJECTED", "no trailer")
            lexer = Lexer(d, t.end())
            trailer = lexer.value()
            if not isinstance(trailer, dict):
                raise Reject("PARSER_REJECTED", "trailer")
            for k, v in trailer.items():
                self.trailer.setdefault(k, v)
            prev = trailer.get("Prev")
            offset = prev if isinstance(prev, int) else None

    def obj(self, ref):
        if not isinstance(ref, Ref):
            return ref
        if ref.num in self.cache:
            return self.cache[ref.num]
        offset = self.offsets.get(ref.num)
        if offset is None:
            raise Reject("PARSER_REJECTED", "missing object")
        m = re.compile(rb"(\d+)\s+(\d+)\s+obj").match(self.data, offset)
        if not m or int(m.group(1)) != ref.num:
            raise Reject("PARSER_REJECTED", "object offset")
        lexer = Lexer(self.data, m.end())
        value = lexer.value()
        if isinstance(value, dict):
            lexer.skip()
            if self.data.startswith(b"stream", lexer.pos):
                if value.get("Type") in ("ObjStm", "XRef"):
                    raise Reject("PARSER_REJECTED", "object stream (unsupported)")
                value = Stream(value, self._stream_bytes(value, lexer.pos + 6))
        self.cache[ref.num] = value
        return value

    def _stream_bytes(self, header, pos):
        d = self.data
        if d.startswith(b"\r\n", pos):
            pos += 2
        elif d.startswith(b"\n", pos):
            pos += 1
        length = self.obj(header.get("Length"))
        if not isinstance(length, int) or length < 0 or pos + length > len(d):
            raise Reject("PARSER_REJECTED", "stream length")
        return d[pos:pos + length]


class Stream:
    def __init__(self, header, raw):
        self.header, self.raw = header, raw

    def decoded(self):
        f = self.header.get("Filter")
        filters = f if isinstance(f, list) else ([f] if f is not None else [])
        data = self.raw
        for name in filters:
            if name != "FlateDecode":
                return None
            inflater = zlib.decompressobj()
            try:
                data = inflater.decompress(data, MAX_INFLATE)
            except zlib.error:
                raise Reject("PARSER_REJECTED", "flate") from None
            if inflater.unconsumed_tail:
                raise Reject("PARSER_REJECTED", "inflated stream too large")
        return data


def _pages(doc, node, depth, seen, out):
    if depth > MAX_DEPTH or len(out) > MAX_PAGES:
        raise Reject("PARSER_REJECTED", "page tree too deep or too large")
    key = node.num if isinstance(node, Ref) else id(node)
    if key in seen:
        raise Reject("PARSER_REJECTED", "page tree cycle")
    seen.add(key)
    value = doc.obj(node)
    if not isinstance(value, dict):
        raise Reject("PARSER_REJECTED", "page node")
    if value.get("Type") == "Pages":
        kids = doc.obj(value.get("Kids"))
        if not isinstance(kids, list):
            raise Reject("PARSER_REJECTED", "kids")
        for kid in kids:
            _pages(doc, kid, depth + 1, seen, out)
    elif value.get("Type") == "Page":
        out.append(value)
    else:
        raise Reject("PARSER_REJECTED", "page node type")


_TEXT_TOKENS = re.compile(rb"\((?:\\.|[^\\()])*\)|\[|\]|T\*|Tj|TJ|Td|TD|ET|'|\"")


def _page_text(doc, page):
    contents = doc.obj(page.get("Contents"))
    parts = contents if isinstance(contents, list) else [contents]
    chunks = []
    for part in parts:
        stream = doc.obj(part)
        if not isinstance(stream, Stream):
            return None
        data = stream.decoded()
        if data is None:
            return None
        chunks.append(data)
    content = b"\n".join(chunks)
    lines, current, pending = [], [], []
    for token in _TEXT_TOKENS.findall(content):
        if token.startswith(b"("):
            pending.append(Lexer(token).literal().decode("latin-1"))
        elif token in (b"Tj", b"TJ", b"'", b"\""):
            current.extend(pending)
            pending = []
            if token in (b"'", b"\""):
                lines.append("".join(current))
                current = []
        elif token in (b"Td", b"TD", b"T*", b"ET"):
            if current:
                lines.append("".join(current))
            current, pending = [], []
    if current:
        lines.append("".join(current))
    return "\n".join(lines)


def validate(data):
    result = {"validator": VALIDATOR, "validator_version": VERSION, "header_ok": False, "eof_ok": False, "pypdf_open_ok": None,
              "page_count": 0, "encrypted": False, "errors": [], "pages_text": [], "info": {}}
    head = data[:1024]
    probe = head.lstrip().lower()
    if probe.startswith((b"<!doctype html", b"<html", b"<head", b"<?xml")) or b"<html" in probe[:256]:
        result["errors"].append("HTML_OR_CHALLENGE")
        return result
    if b"%PDF-" not in head:
        result["errors"].append("NOT_PDF_HEADER")
        return result
    result["header_ok"] = True
    if b"%%EOF" not in data[-2048:]:
        result["errors"].append("TRUNCATED_NO_EOF")
        return result
    result["eof_ok"] = True
    doc = Document(data)
    try:
        doc.load_xref()
        if "Encrypt" in doc.trailer:
            result["encrypted"] = True
            result["errors"].append("ENCRYPTED")
            return result
        root = doc.obj(doc.trailer.get("Root"))
        if not isinstance(root, dict) or root.get("Type") != "Catalog":
            raise Reject("PARSER_REJECTED", "catalog")
        pages = []
        _pages(doc, root.get("Pages"), 0, set(), pages)
        tree = doc.obj(root.get("Pages"))
        if tree.get("Count") != len(pages):
            raise Reject("PARSER_REJECTED", "page count mismatch")
        result["page_count"] = len(pages)
        if not pages:
            result["errors"].append("ZERO_PAGES")
            return result
        for page in pages[:MAX_TEXT_PAGES]:
            text = _page_text(doc, page)
            result["pages_text"].append(text)
        info = doc.obj(doc.trailer.get("Info")) if doc.trailer.get("Info") is not None else {}
        if isinstance(info, dict):
            for key in ("doi", "Title"):
                if isinstance(info.get(key), bytes):
                    result["info"][key] = info[key].decode("latin-1")
    except Reject as exc:
        result["errors"].append(exc.code)
        result["parser_detail"] = exc.detail
    except (RecursionError, ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError, MemoryError):
        result["errors"].append("PARSER_REJECTED")
        result["parser_detail"] = "unhandled structure"
    return result


def _pypdf_check(data, result):
    """Optional second opinion: pypdf strict open (only when an isolated pypdf copy is supplied, imported before the hook)."""
    import io
    try:
        reader = pypdf_module.PdfReader(io.BytesIO(data), strict=True)
        count = len(reader.pages)
        result["pypdf_open_ok"] = True
        result["pypdf_page_count"] = count
        if reader.is_encrypted:
            result["errors"].append("ENCRYPTED")
        elif count != result["page_count"]:
            result["errors"].append("PARSER_REJECTED")
            result["parser_detail"] = "pypdf page count differs"
    except (pypdf_module.errors.PyPdfError, ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError, OverflowError,
            zlib.error, MemoryError):
        result["pypdf_open_ok"] = False
        result["errors"].append("PARSER_REJECTED")


pypdf_module = None


def main(argv):
    args = dict(zip(argv[1::2], argv[2::2]))
    limit = int(args.get("--memory", str(512 * 1024 * 1024)))
    if not _limit_memory(limit):
        print(json.dumps({"sandbox_error": "MEMORY_LIMIT_UNAVAILABLE"}))
        return 4
    global pypdf_module
    site = args.get("--pypdf-site")
    if site:
        sys.path.insert(0, site)
        try:
            import pypdf
            pypdf_module = pypdf
        except ImportError:
            print(json.dumps({"sandbox_error": "PYPDF_UNAVAILABLE"}))
            return 4
    if args.get("--self-test") == "memory":
        _install_hook()
        try:
            block = bytearray(limit * 2)
            outcome = "ALLOCATED" if len(block) else "ALLOCATED"
        except MemoryError:
            outcome = "LIMITED"
        print(json.dumps({"self_test": "memory", "outcome": outcome}))
        return 0
    if args.get("--self-test") == "open":
        _install_hook()
        try:
            open(args["--input"], "rb").close()
            outcome = "OPENED"
        except PermissionError:
            outcome = "REFUSED"
        print(json.dumps({"self_test": "open", "outcome": outcome}))
        return 0
    with open(args["--input"], "rb") as handle:
        data = handle.read()
    _install_hook()
    result = validate(data)
    if pypdf_module is not None and not result["errors"]:
        _pypdf_check(data, result)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
