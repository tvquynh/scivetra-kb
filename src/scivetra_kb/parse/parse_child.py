"""Isolated S06 PDF parser child: runs ONLY as ``python -I -S -B parse_child.py --input FILE --pypdf-site DIR ...``.

It imports nothing from the KB package. Before it reads any untrusted byte it:

1. applies OS limits to itself: a Windows Job Object (process memory, per-process CPU time, ActiveProcessLimit=1,
   kill-on-close) or POSIX rlimits (RLIMIT_AS, RLIMIT_CPU, RLIMIT_NPROC=0, RLIMIT_FSIZE=0); on Linux it also tries an
   unprivileged network namespace (``unshare(CLONE_NEWUSER|CLONE_NEWNET)``), reported as ``os_network_isolation``;
2. imports the pinned pypdf copy (and every pypdf submodule and codec it may need) from ``--pypdf-site``;
3. tightens pypdf's own decompression limits;
4. reads the staged input once, then installs an audit hook refusing every further ``open``, socket, DNS, process,
   ``ctypes.dlopen`` or browser event (language-level guard; it is NOT an OS network barrier on Windows).

PDF actions, JavaScript, links and embedded files are never executed or followed: pypdf only parses objects, and the child
reports their presence as flags. Output: exactly one JSON object on stdout (ASCII), at most ``--max-output`` bytes.
Exit codes: 0 result printed, 4 sandbox could not be established.
"""
import json
import os
import sys

EXTRACTOR = "rkb-parse/1"
_REFUSED_EVENTS = ("open", "socket.", "subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "os.fork",
                   "ctypes.dlopen", "urllib.Request", "webbrowser.open", "_winapi.CreateProcess")
_CODECS = ("cp1252", "latin_1", "utf_8", "utf_16_be", "utf_16_le", "utf_16", "ascii", "charmap", "mac_roman", "raw_unicode_escape",
           "unicode_escape", "cp850", "iso8859_15")


def _limit_self(memory: int, cpu_seconds: int) -> dict:
    report = {"memory_limit_bytes": memory, "cpu_limit_seconds": cpu_seconds, "process_creation": "UNRESTRICTED",
              "os_network_isolation": "NONE", "mechanism": None, "privileged_account": hasattr(os, "geteuid") and os.geteuid() == 0}
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
            return None
        info = EXTENDED()
        # PROCESS_TIME (0x2) | ACTIVE_PROCESS (0x8) | PROCESS_MEMORY (0x100) | DIE_ON_UNHANDLED_EXCEPTION (0x400) | KILL_ON_JOB_CLOSE (0x2000)
        info.BasicLimitInformation.LimitFlags = 0x2 | 0x8 | 0x100 | 0x400 | 0x2000
        info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_seconds) * 10_000_000
        info.BasicLimitInformation.ActiveProcessLimit = 1
        info.ProcessMemoryLimit = memory
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            return None
        report.update(mechanism="WINDOWS_JOB_OBJECT", process_creation="OS_LIMIT_ACTIVE_PROCESS_1")
        return report
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    report["mechanism"] = "POSIX_RLIMIT"
    if hasattr(resource, "RLIMIT_NPROC"):
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
            # RLIMIT_NPROC does not bind a privileged (root) process: report that honestly instead of claiming a limit.
            report["process_creation"] = "NOT_ENFORCED_PRIVILEGED_ACCOUNT" if os.geteuid() == 0 else "OS_RLIMIT_NPROC_0"
        except (ValueError, OSError):
            pass
    unshare = getattr(os, "unshare", None)
    if unshare and sys.platform.startswith("linux"):
        try:
            unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET)
            report["os_network_isolation"] = "LINUX_NETNS_EMPTY"
        except OSError:
            pass
    return report


def _install_hook():
    def audit(event, args):
        if event == "open" or event.startswith(_REFUSED_EVENTS):
            raise PermissionError("refused in PDF parser sandbox: " + event)
    sys.addaudithook(audit)


def _preload(site: str):
    sys.path.insert(0, site)
    import codecs
    import importlib
    import pkgutil
    import unicodedata  # noqa: F401  (extension module: must load before the hook)
    import zlib  # noqa: F401
    for name in _CODECS:
        codecs.lookup(name)
    import pypdf
    for info in pkgutil.walk_packages(pypdf.__path__, "pypdf."):
        try:
            importlib.import_module(info.name)
        except ImportError:      # optional back-ends (cryptography, Pillow, ...) are absent by design
            pass
    return pypdf


def _imported_identity(pypdf, site: str) -> dict:
    """Version and package-file fingerprint of the pypdf this process ACTUALLY imported (before the audit hook), using the
    same fingerprint rule as the parent (sorted [path, sha256] list of pypdf/ files, canonical JSON, SHA-256)."""
    import hashlib
    import zipfile
    origin = os.path.realpath(pypdf.__file__)
    site_real = os.path.realpath(site)
    entries = []
    if os.path.isfile(site_real):
        with zipfile.ZipFile(site_real) as z:
            entries = [[n, hashlib.sha256(z.read(n)).hexdigest()] for n in z.namelist() if n.startswith("pypdf/") and not n.endswith("/")]
        inside = origin.startswith(site_real + os.sep)
    else:
        base = os.path.join(site_real, "pypdf")
        for folder, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in names:
                full = os.path.join(folder, name)
                with open(full, "rb") as handle:
                    entries.append([os.path.relpath(full, site_real).replace(os.sep, "/"), hashlib.sha256(handle.read()).hexdigest()])
        inside = origin.startswith(base + os.sep)
    text = json.dumps(sorted(entries), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"version": getattr(pypdf, "__version__", None), "build_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "files": len(entries), "imported_from_site": inside}


def _tighten(pypdf, max_stream: int):
    filters = pypdf.filters
    for name in ("ZLIB_MAX_OUTPUT_LENGTH", "LZW_MAX_OUTPUT_LENGTH", "RUN_LENGTH_MAX_OUTPUT_LENGTH", "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
                 "MAX_DECLARED_STREAM_LENGTH", "JBIG2_MAX_OUTPUT_LENGTH"):
        if hasattr(filters, name):
            setattr(filters, name, min(getattr(filters, name), max_stream))


class Refuse(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code, self.detail = code, detail


class _Warnings:
    """Collects pypdf logger records WITHOUT their message text (messages may quote document content)."""

    def __init__(self):
        import logging
        self.items = {}

        class Handler(logging.Handler):
            def emit(inner, record):
                key = f"{record.module}:{record.funcName}"
                self.items[key] = self.items.get(key, 0) + 1
        logger = logging.getLogger("pypdf")
        logger.setLevel(logging.WARNING)
        logger.addHandler(Handler())
        logger.propagate = False


def _syntax(data, reader):
    tail = data[-2048:]
    i = tail.rfind(b"startxref")
    family = "UNKNOWN"
    if i >= 0:
        try:
            offset = int(tail[i + 9:].split()[0])
            head = data[offset:offset + 32].lstrip()
            family = "CLASSIC" if head.startswith(b"xref") else ("XREF_STREAM" if b" obj" in head else "UNKNOWN")
        except (ValueError, IndexError):
            family = "UNKNOWN"
    version = data[:16].split(b"\n")[0].split(b"\r")[0][5:].decode("ascii", "replace")[:8]
    return {"xref_family": family, "object_streams": bool(getattr(reader, "xref_objStm", None)),
            "revisions": data.count(b"%%EOF"), "header_version": version}


def _active_content(reader):
    """Names of active/external features present in the catalog or pages. Never executed or followed."""
    from pypdf.generic import DictionaryObject
    found = set()
    root = reader.trailer["/Root"].get_object()
    if "/OpenAction" in root:
        action = root["/OpenAction"].get_object()
        found.add("OPEN_ACTION_JAVASCRIPT" if isinstance(action, DictionaryObject) and action.get("/S") == "/JavaScript" else "OPEN_ACTION")
    if "/AA" in root:
        found.add("ADDITIONAL_ACTIONS")
    names = root.get("/Names")
    if names is not None:
        names = names.get_object()
        if "/JavaScript" in names:
            found.add("DOCUMENT_JAVASCRIPT")
        if "/EmbeddedFiles" in names:
            found.add("EMBEDDED_FILES")
    if "/AcroForm" in root:
        found.add("ACROFORM")
    for page in reader.pages:
        if "/AA" in page:
            found.add("PAGE_ADDITIONAL_ACTIONS")
        for annot in (page.get("/Annots") or []):
            a = annot.get_object()
            action = a.get("/A")
            if action is not None:
                s = action.get_object().get("/S")
                found.add({"/URI": "LINK_URI", "/Launch": "LAUNCH_ACTION", "/JavaScript": "ANNOTATION_JAVASCRIPT",
                           "/SubmitForm": "SUBMIT_FORM"}.get(s, "ANNOTATION_ACTION"))
    return sorted(found)


def _open(pypdf, data, limits):
    import io
    reader = pypdf.PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise Refuse("ENCRYPTED")
    root = reader.trailer["/Root"].get_object()
    if root.get("/Type") != "/Catalog":
        raise Refuse("PARSER_REJECTED", "catalog")
    tree = root["/Pages"].get_object()
    declared = tree.get("/Count")
    if not isinstance(declared, int) or declared < 0:
        raise Refuse("PARSER_REJECTED", "page count")
    if declared > limits["max_pages"]:
        raise Refuse("PAGE_LIMIT_EXCEEDED")
    # Walk the page tree ourselves first: bounded, cycle- and duplicate-safe, before pypdf flattens it.
    seen, leaves, stack = set(), 0, [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 64:
            raise Refuse("PARSER_REJECTED", "page tree depth")
        kids = node.get("/Kids")
        if kids is None:
            raise Refuse("PARSER_REJECTED", "page tree node")
        if len(kids) > limits["max_pages"]:
            raise Refuse("PAGE_LIMIT_EXCEEDED")
        for ref in kids:
            key = (getattr(ref, "idnum", None), getattr(ref, "generation", None))
            if key[0] is None or key in seen:
                raise Refuse("PARSER_REJECTED", "page tree duplicate or direct node")
            seen.add(key)
            kid = ref.get_object()
            kind = kid.get("/Type")
            if kind == "/Pages":
                stack.append((kid, depth + 1))
            elif kind == "/Page":
                leaves += 1
                if leaves > limits["max_pages"]:
                    raise Refuse("PAGE_LIMIT_EXCEEDED")
            else:
                raise Refuse("PARSER_REJECTED", "page tree node type")
    if leaves != declared or len(reader.pages) != leaves:
        raise Refuse("PARSER_REJECTED", "page count mismatch")
    if leaves == 0:
        raise Refuse("ZERO_PAGES")
    # Every cross-reference entry must resolve (no silent repair of a broken table or object stream).
    count = 0
    from pypdf.generic import IndirectObject
    for gen, entries in sorted(reader.xref.items()):
        for num in sorted(entries):
            count += 1
            if count > limits["max_objects"]:
                raise Refuse("PARSER_REJECTED", "too many objects")
            if reader.get_object(IndirectObject(num, gen, reader)) is None:
                raise Refuse("PARSER_REJECTED", "unresolvable object")
    for num in sorted(getattr(reader, "xref_objStm", {}) or {}):
        count += 1
        if count > limits["max_objects"]:
            raise Refuse("PARSER_REJECTED", "too many objects")
        if reader.get_object(IndirectObject(num, 0, reader)) is None:
            raise Refuse("PARSER_REJECTED", "unresolvable compressed object")
    return reader


_MATH_EXTRA = set("±×÷²³¹⁰ⁱ⁴⁵⁶⁷⁸⁹⁺⁻₀₁₂")


def _char_flags(text):
    import unicodedata
    flags = set()
    for ch in text:
        o = ord(ch)
        if ch == "�":
            flags.add("FONT_DECODE_UNCERTAIN")
        elif 0xE000 <= o <= 0xF8FF or o >= 0xF0000:
            flags.add("FONT_DECODE_UNCERTAIN")
        elif o < 0x20 and ch not in "\n\t":
            flags.add("FONT_DECODE_UNCERTAIN")
        elif unicodedata.category(ch) == "Sm" or ch in _MATH_EXTRA or 0x0391 <= o <= 0x03C9 or 0x2200 <= o <= 0x22FF or 0x1D400 <= o <= 0x1D7FF:
            flags.add("FORMULA_CONTENT_UNCERTAIN")
    return flags


def _font_flags(font):
    """A font whose codes cannot be mapped to Unicode with certainty: composite (Type0) or custom-encoded simple fonts
    without a /ToUnicode CMap, and Type3 fonts."""
    if font is None:
        return set()
    try:
        font = font.get_object()
        if "/ToUnicode" in font:
            return set()
        subtype = font.get("/Subtype")
        if subtype in ("/Type0", "/Type3"):
            return {"FONT_DECODE_UNCERTAIN"}
        enc = font.get("/Encoding")
        if enc is not None and not isinstance(enc.get_object(), str) and "/Differences" in enc.get_object():
            return {"FONT_DECODE_UNCERTAIN"}
    except (AttributeError, KeyError, TypeError):
        return {"FONT_DECODE_UNCERTAIN"}
    return set()


def _norm(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def _cpt(v):
    return int(round(v * 100))


def _page(page, limits):
    chunks, shows = [], []

    def visitor(text, cm, tm, font, size):
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        chunks.append([_norm(text), _cpt(x), _cpt(y), _cpt(size), _font_flags(font)])
        if len(chunks) > limits["max_chunks_per_page"]:
            raise Refuse("OUTPUT_LIMIT_EXCEEDED", "chunks")

    def operators(op, args, cm, tm):
        if op in (b"Tj", b"TJ", b"'", b'"'):
            shows.append((_cpt(tm[4] * cm[0] + tm[5] * cm[2] + cm[4]), _cpt(tm[4] * cm[1] + tm[5] * cm[3] + cm[5])))
            if len(shows) > limits["max_chunks_per_page"]:
                raise Refuse("OUTPUT_LIMIT_EXCEEDED", "operators")

    contents = page.get_contents()
    if contents is not None:
        contents.get_data()          # decompression limits apply here and raise; extract_text alone may degrade silently
    page.extract_text(visitor_text=visitor, visitor_operand_before=operators)
    # pypdf reports some flushed text with a zero origin and the real origin on the following empty chunk.
    for i, c in enumerate(chunks):
        if c[0].strip() and c[1] == 0 and c[2] == 0:
            nxt = next((d for d in chunks[i + 1:] if d[1] or d[2]), None)
            if nxt is not None:
                c[1], c[2] = nxt[1], nxt[2]
    text_parts, pos, spans = [], 0, []
    flags = set()
    for text, x, y, size, fflags in chunks:
        if not text:
            continue
        spans.append((pos, pos + len(text), x, y, size))
        text_parts.append(text)
        pos += len(text)
        if text.strip():
            flags |= fflags
        if pos > limits["max_chars_per_page"]:
            raise Refuse("OUTPUT_LIMIT_EXCEEDED", "page chars")
    text = "".join(text_parts)
    flags |= _char_flags(text)
    blocks, start = [], 0
    for line in text.split("\n"):
        end = start + len(line)
        if line.strip():
            first = next(((s, e, x, y, z) for (s, e, x, y, z) in spans if e > start and s < end and text[max(s, start):min(e, end)].strip()), None)
            members = [sp for sp in spans if sp[1] > start and sp[0] < end and text[max(sp[0], start):min(sp[1], end)].strip()]
            blocks.append({"start": start, "end": end, "origin_cpt": [first[2], first[3]] if first else None,
                           "font_size_cpt": first[4] if first else None, "span_count": len(members)})
        start = end + 1
    has_text = any(b for b in blocks)
    # layout heuristics (HEURISTIC flags; extraction order is the content-stream order)
    baselines = {}
    for x, y in shows:
        baselines.setdefault(y, set()).add(x)
    multi = [xs for xs in baselines.values() if len(xs) >= 2]
    if sum(1 for xs in multi if len(xs) >= 3) >= 2:
        flags.add("TABLE_LAYOUT_SUSPECTED")
        flags.add("READING_ORDER_UNCERTAIN")
    elif len(multi) >= 2:
        flags.add("MULTI_COLUMN_SUSPECTED")
        flags.add("READING_ORDER_UNCERTAIN")
    sizes = sorted(b["font_size_cpt"] for b in blocks if b["font_size_cpt"])
    if sizes:
        median = sizes[len(sizes) // 2]
        if any(s * 10 < median * 7 for s in sizes):
            flags.add("SMALL_TEXT_PRESENT")
    images = False
    try:
        res = page.get("/Resources")
        xobj = res.get_object().get("/XObject") if res is not None else None
        if xobj is not None:
            images = any(v.get_object().get("/Subtype") == "/Image" for v in xobj.get_object().values())
    except (AttributeError, KeyError, TypeError):
        images = False
    if not has_text:
        flags.add("NO_TEXT_LAYER")
        if images:
            flags.add("IMAGE_ONLY_PAGE")
    return {"text": text, "blocks": blocks, "flags": sorted(flags), "has_text": has_text, "has_images": images}


def run(mode, data, pypdf, limits):
    warnings = _Warnings()
    result = {"extractor": EXTRACTOR, "parser": {"name": "pypdf", "version": pypdf.__version__}, "mode": mode}
    probe = data[:1024].lstrip().lower()
    if probe.startswith((b"<!doctype html", b"<html", b"<head", b"<?xml")) or b"<html" in probe[:256]:
        raise Refuse("HTML_OR_CHALLENGE")
    if b"%PDF-" not in data[:1024]:
        raise Refuse("NOT_PDF_HEADER")
    if b"%%EOF" not in data[-2048:]:
        raise Refuse("TRUNCATED_NO_EOF")
    reader = _open(pypdf, data, limits)
    result["syntax"] = _syntax(data, reader)
    result["active_content"] = _active_content(reader)
    result["page_count"] = len(reader.pages)
    pages, total = [], 0
    for index, page in enumerate(reader.pages):
        if mode == "validate":
            contents = page.get_contents()
            if contents is not None:
                contents.get_data()
            if index < 3:
                pages.append({"text": _page(page, limits)["text"]})
            continue
        info = _page(page, limits)
        total += len(info["text"])
        if total > limits["max_total_chars"]:
            raise Refuse("OUTPUT_LIMIT_EXCEEDED", "document chars")
        pages.append(info)
    result["pages"] = pages
    if mode == "validate":
        info = reader.metadata or {}
        result["info"] = {k: str(info[f"/{k}"])[:500] for k in ("Title", "doi") if f"/{k}" in info}
    result["parser_warnings"] = dict(sorted(warnings.items.items()))
    return result


def _self_test(name, args):
    if name == "memory":
        try:
            block = bytearray(int(args["--memory"]) * 2)
            return {"self_test": name, "outcome": "ALLOCATED" if len(block) else "ALLOCATED"}
        except MemoryError:
            return {"self_test": name, "outcome": "LIMITED"}
    if name == "cpu":
        n = 0
        while True:          # the OS CPU limit must kill this process
            n += 1
    if name == "spawn_os":
        import subprocess
        try:
            subprocess.run([sys.executable, "-c", "pass"], check=True, timeout=10)
            return {"self_test": name, "outcome": "SPAWNED"}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"self_test": name, "outcome": "REFUSED_BY_OS", "error": type(exc).__name__}
    import socket  # noqa: F401  (imported before the hook, as an attacker-controlled parser could not import it after)
    _install_hook()
    if name == "spawn_hook":
        import subprocess
        try:
            subprocess.run([sys.executable, "-c", "pass"], timeout=10)
            return {"self_test": name, "outcome": "SPAWNED"}
        except PermissionError:
            return {"self_test": name, "outcome": "REFUSED_BY_AUDIT_HOOK"}
    if name == "network":
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.close()
            return {"self_test": name, "outcome": "SOCKET_CREATED"}
        except PermissionError:
            return {"self_test": name, "outcome": "REFUSED_BY_AUDIT_HOOK"}
    if name == "dns":
        try:
            socket.getaddrinfo("localhost", 80)
            return {"self_test": name, "outcome": "RESOLVED"}
        except PermissionError:
            return {"self_test": name, "outcome": "REFUSED_BY_AUDIT_HOOK"}
    if name == "open":
        try:
            open(args["--input"], "rb").close()
            return {"self_test": name, "outcome": "OPENED"}
        except PermissionError:
            return {"self_test": name, "outcome": "REFUSED_BY_AUDIT_HOOK"}
    if name == "sleep":
        import time
        time.sleep(3600)
    return {"self_test": name, "outcome": "UNKNOWN_SELF_TEST"}


def _emit(obj, max_output):
    text = json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(text) > max_output:
        text = json.dumps({"error": "OUTPUT_LIMIT_EXCEEDED", "detail": "result json"}, sort_keys=True)
    sys.stdout.write(text)
    sys.stdout.flush()


def main(argv):
    args = dict(zip(argv[1::2], argv[2::2]))
    memory, cpu = int(args["--memory"]), int(args["--cpu-seconds"])
    max_output = int(args["--max-output"])
    limits = {k: int(args["--" + k.replace("_", "-")]) for k in ("max_pages", "max_chars_per_page", "max_total_chars", "max_stream_bytes",
                                                                 "max_objects", "max_chunks_per_page")}
    sandbox = _limit_self(memory, cpu)
    if sandbox is None:
        _emit({"sandbox_error": "OS_LIMITS_UNAVAILABLE"}, max_output)
        return 4
    try:
        pypdf = _preload(args["--pypdf-site"])
    except ImportError:
        _emit({"sandbox_error": "PYPDF_UNAVAILABLE"}, max_output)
        return 4
    sandbox["parser_identity"] = _imported_identity(pypdf, args["--pypdf-site"])
    _tighten(pypdf, limits["max_stream_bytes"])
    if args.get("--self-test"):
        _emit(dict(_self_test(args["--self-test"], args), sandbox=sandbox), max_output)
        return 0
    with open(args["--input"], "rb") as handle:
        data = handle.read()
    _install_hook()
    try:
        out = {"result": run(args["--mode"], data, pypdf, limits)}
    except Refuse as exc:
        out = {"error": exc.code, "detail": exc.detail}
    except MemoryError:
        out = {"error": "RESOURCE_LIMIT_EXCEEDED", "detail": "memory"}
    except RecursionError:
        out = {"error": "PARSER_REJECTED", "detail": "recursion"}
    except PermissionError as exc:
        out = {"error": "SANDBOX_REFUSED_EVENT", "detail": str(exc)[:120]}
    except pypdf.errors.LimitReachedError:
        out = {"error": "DECOMPRESSION_LIMIT_EXCEEDED", "detail": "pypdf limit"}
    except (pypdf.errors.PyPdfError, ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError, LookupError,
            UnicodeError, AssertionError, ZeroDivisionError) as exc:
        out = {"error": "PARSER_REJECTED", "detail": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001 - any other parser failure is a rejection, never a crash of the KB
        out = {"error": "PARSER_REJECTED", "detail": "unexpected " + type(exc).__name__}
    out["sandbox"] = sandbox
    _emit(out, max_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
