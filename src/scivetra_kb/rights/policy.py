"""``rights-policy/1`` (S01 v03 05 sections 4.2-4.3): evidence-only classification and default-deny action gates.

Pure functions, no I/O. Access route, login history, paywall and PBD status are provenance only: they are never
inputs to the class. A licence applies only to the DocumentVersion whose version it names (``vor`` -> PUBLISHED,
``am`` -> ACCEPTED) and only after its embargo. ``UNDETERMINED`` behaves as DENY for every action outside KB storage.
No path in this module ever yields ALLOW for ``redistribute`` or ``external_ai_process`` (owner gate OAG-6 closed).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlsplit

POLICY_VERSION = "rights-policy/1"
CLASSES = ("PUBLIC_OA_LICENSED", "PUBLIC_OA_UNVERIFIED", "INSTITUTIONAL_RESTRICTED", "UNKNOWN")
PERMISSIONS = ("store_locally", "redistribute", "local_process", "external_ai_process")
# 05 section 4.2 table, one row per class.
CLASS_PERMISSIONS = {
    "PUBLIC_OA_LICENSED": {"store_locally": "ALLOW", "redistribute": "UNDETERMINED", "local_process": "ALLOW", "external_ai_process": "UNDETERMINED"},
    "PUBLIC_OA_UNVERIFIED": {"store_locally": "ALLOW", "redistribute": "DENY", "local_process": "ALLOW", "external_ai_process": "DENY"},
    "INSTITUTIONAL_RESTRICTED": {"store_locally": "ALLOW", "redistribute": "DENY", "local_process": "UNDETERMINED", "external_ai_process": "DENY"},
    "UNKNOWN": {"store_locally": "ALLOW", "redistribute": "DENY", "local_process": "UNDETERMINED", "external_ai_process": "DENY"},
}
# Open-licence allowlist (versioned with the policy; S03-F01). ``rights-policy/1`` is the stored policy id (pinned by the
# migration 0002 CHECK); LICENCE_URI_RULES names the revision of the URI rules that produced an evaluation.
# A licence URI is recognized only if the WHOLE URI is one of these canonical Creative Commons forms:
#   http(s)://creativecommons.org/licenses/<family>/<version>[/[deed[.<lang>] | legalcode[.<lang>]]]
#   http(s)://creativecommons.org/publicdomain/zero/1.0[/...same suffixes]      (CC0)
#   http(s)://creativecommons.org/publicdomain/mark/1.0[/...same suffixes]      (Public Domain Mark)
# with the family/version pairs below (unported licences only). Host match is case-insensitive and ``www.`` is the only
# accepted prefix; the path is case-sensitive. Userinfo, ports, query, fragment, percent-encoding, whitespace, backslashes,
# non-ASCII, empty/duplicate path segments, jurisdiction ports (e.g. ``/by/3.0/us/``), unsupported versions and any other
# suffix are NOT recognized: such items count as unrecognized licence evidence (L3), never as an open licence (L1).
LICENCE_URI_RULES = "cc-uri-allowlist/2"
_CC_HOSTS = ("creativecommons.org", "www.creativecommons.org")
_CC_LICENCE_VERSIONS = {
    "by": ("1.0", "2.0", "2.5", "3.0", "4.0"),
    "by-sa": ("1.0", "2.0", "2.5", "3.0", "4.0"),
    "by-nd": ("1.0", "2.0", "2.5", "3.0", "4.0"),
    "by-nc": ("1.0", "2.0", "2.5", "3.0", "4.0"),
    "by-nc-sa": ("1.0", "2.0", "2.5", "3.0", "4.0"),
    "by-nc-nd": ("2.0", "2.5", "3.0", "4.0"),
    "by-nd-nc": ("1.0",),   # the 1.0 spelling of BY-NC-ND
}
_CC_FAMILY = {"by": "CC-BY", "by-sa": "CC-BY-SA", "by-nd": "CC-BY-ND", "by-nc": "CC-BY-NC", "by-nc-sa": "CC-BY-NC-SA",
              "by-nc-nd": "CC-BY-NC-ND", "by-nd-nc": "CC-BY-NC-ND"}
_CC_PUBLIC_DOMAIN = {("zero", "1.0"): "CC0", ("mark", "1.0"): "PDM"}
_CC_SUFFIX = re.compile(r"(?:deed|legalcode)(?:\.[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?)?")
_URI_CHARS = re.compile(r"[A-Za-z0-9:/._-]+")

LICENCE_SOURCES = ("CROSSREF_LICENSE", "OPENALEX_LOCATION", "UNPAYWALL", "DATACITE_RIGHTS", "PUBLISHER_PAGE_HUMAN", "PDF_STATEMENT_HUMAN")
AVAILABILITY_SOURCES = ("OPENALEX_LOCATION", "UNPAYWALL", "RV_EXPORT_OA_LOCATION", "RV_STAGE11B_EVIDENCE")
ACCESS_ROUTES = ("OPEN_WEB", "INSTITUTIONAL_LOGIN", "PERSONAL_COPY", "AUTHOR_PROVIDED", "REPOSITORY", "UNKNOWN")
CONTENT_VERSION_CLASS = {"vor": ("PUBLISHED", "UPDATED_PUBLISHED"), "am": ("ACCEPTED",), "submitted": ("SUBMITTED",)}
LOCATION_VERSION_CLASS = {"publishedVersion": ("PUBLISHED", "UPDATED_PUBLISHED"), "acceptedVersion": ("ACCEPTED",),
                          "submittedVersion": ("SUBMITTED",)}


class EvidenceError(ValueError):
    pass


def licence_family(uri: str) -> str | None:
    """Recognized open-licence family for a complete canonical CC URI, else None (fail closed; see the allowlist above)."""
    if not isinstance(uri, str) or not _URI_CHARS.fullmatch(uri):
        return None   # whitespace, %-encoding, query/fragment markers, userinfo '@', backslash, non-ASCII
    try:
        parts = urlsplit(uri)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or port is not None or parts.username is not None or parts.password is not None \
            or parts.query or parts.fragment or (parts.hostname or "") not in _CC_HOSTS or parts.netloc.lower() != parts.hostname:
        return None
    segments = parts.path.split("/")
    if len(segments) < 4 or segments[0] != "":
        return None
    kind, name, version, rest = segments[1], segments[2], segments[3], segments[4:]
    if rest and rest[-1] == "":
        rest = rest[:-1]   # one trailing slash
    if len(rest) > 1 or (rest and not _CC_SUFFIX.fullmatch(rest[0])):
        return None
    if kind == "licenses":
        return _CC_FAMILY[name] if version in _CC_LICENCE_VERSIONS.get(name, ()) else None
    if kind == "publicdomain":
        return _CC_PUBLIC_DOMAIN.get((name, version))
    return None


def _creative_commons_host(uri: str) -> bool:
    try:
        return (urlsplit(uri.strip()).hostname or "").endswith("creativecommons.org")
    except ValueError:
        return False


def _parse_date(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        raise EvidenceError("licence start_date must be YYYY-MM-DD") from None


def validate_evidence(evidence: dict) -> dict:
    """Shape check of operator-supplied rights evidence; unknown keys are refused, not ignored."""
    if not isinstance(evidence, dict) or set(evidence) - {"license_evidence", "availability_evidence", "terms_declarations", "access_provenance"}:
        raise EvidenceError("unknown evidence keys")
    out = {"license_evidence": evidence.get("license_evidence", []), "availability_evidence": evidence.get("availability_evidence", []),
           "terms_declarations": evidence.get("terms_declarations", []), "access_provenance": evidence.get("access_provenance", {})}
    for item in out["license_evidence"]:
        if not isinstance(item, dict) or item.get("source") not in LICENCE_SOURCES or not isinstance(item.get("license_uri"), str) \
                or item.get("content_version") not in CONTENT_VERSION_CLASS or not item.get("claim_ref"):
            raise EvidenceError("licence evidence needs source, license_uri, content_version (vor|am|submitted) and claim_ref")
        if "start_date" in item:
            _parse_date(item["start_date"])
        if type(item.get("delay_in_days", 0)) is not int or item.get("delay_in_days", 0) < 0:
            raise EvidenceError("delay_in_days must be a non-negative integer")
    for item in out["availability_evidence"]:
        if not isinstance(item, dict) or item.get("source") not in AVAILABILITY_SOURCES or item.get("is_oa") is not True \
                or item.get("version") not in LOCATION_VERSION_CLASS or not item.get("claim_ref"):
            raise EvidenceError("availability evidence needs source, is_oa=true, version and claim_ref")
    for item in out["terms_declarations"]:
        if not isinstance(item, dict) or not isinstance(item.get("terms"), str) or not item.get("evidence_ref") or \
                type(item.get("restrictive")) is not bool:
            raise EvidenceError("terms declaration needs terms, evidence_ref and restrictive (bool)")
    provenance = out["access_provenance"]
    if not isinstance(provenance, dict) or set(provenance) - {"access_route", "access_barrier_observed"} or \
            provenance.get("access_route", "UNKNOWN") not in ACCESS_ROUTES or \
            not all(isinstance(b, str) for b in provenance.get("access_barrier_observed", [])):
        raise EvidenceError("access_provenance holds only access_route and access_barrier_observed[]")
    return out


def classify(evidence: dict, *, version_class: str, as_of: date) -> dict:
    """Classify one asset+DocumentVersion. Returns class, permissions, flags and the per-item evaluation."""
    ev = validate_evidence(evidence)
    flags, evaluation = [], []
    l1 = l3 = False
    for item in ev["license_evidence"]:
        applies = version_class in CONTENT_VERSION_CLASS[item["content_version"]]
        family = licence_family(item["license_uri"])
        start = _parse_date(item["start_date"]) if "start_date" in item else None
        effective = (start + timedelta(days=item.get("delay_in_days", 0))) if start else None
        elapsed = effective is None or effective <= as_of
        entry = {"kind": "L1" if family else "L3", "family": family, "applies_to_version": applies, "embargo_elapsed": elapsed}
        evaluation.append(entry)
        if not family and _creative_commons_host(item["license_uri"]):
            flags.append("LICENCE_URI_NOT_RECOGNISED")
        if family and applies and elapsed:
            l1 = True
        elif family and not applies:
            flags.append("LICENCE_VERSION_MISMATCH")
        elif family and not elapsed:
            flags.append("EMBARGO_NOT_ELAPSED")
        if not family and applies:
            l3 = True
    l2 = any(version_class in LOCATION_VERSION_CLASS[item["version"]] for item in ev["availability_evidence"])
    if ev["availability_evidence"] and not l2:
        flags.append("OA_LOCATION_FOR_OTHER_VERSION")
    l4 = any(item["restrictive"] for item in ev["terms_declarations"])
    if ev["access_provenance"]:
        flags.append("ACCESS_ROUTE_RECORDED_AS_PROVENANCE_ONLY")
    if l1 and l2 and l4:
        rights_class = "UNKNOWN"
        flags.append("RIGHTS_EVIDENCE_CONFLICT")
    elif l1 and l2 and not l3 and not l4:
        rights_class = "PUBLIC_OA_LICENSED"
    elif l4 or (l3 and not l2):
        rights_class = "INSTITUTIONAL_RESTRICTED"
    elif l2 and not l4:
        rights_class = "PUBLIC_OA_UNVERIFIED"
    else:
        rights_class = "UNKNOWN"
    return {"rights_class": rights_class, "permissions": dict(CLASS_PERMISSIONS[rights_class]), "flags": sorted(set(flags)),
            "evaluation": {"L1": l1, "L2": l2, "L3": l3, "L4": l4, "licence_items": evaluation, "licence_uri_rules": LICENCE_URI_RULES},
            "policy_version": POLICY_VERSION,
            "evidence": ev}


# -- action gates (05 section 4.3) ----------------------------------------------------
ACTIONS = {
    "A1": ("store asset bytes in KB CAS", ("store_locally",)),
    "A2": ("parse / OCR / index", ("local_process",)),
    "A3": ("store a machine-extracted quote locally", ("store_locally", "local_process")),
    "A4": ("store a human-typed quote locally", ("store_locally",)),
    "A5": ("show stored quotes to the local operator", ("store_locally",)),
    "A6": ("local model processing", ("local_process",)),
    "A7": ("rights-safe export / share / upload", ("redistribute",)),
    "A8": ("desktop export / MCP / remote API", ("external_ai_process",)),
    "A9": ("stage handoff ZIP to the architect", ()),
    "A10": ("backup to operator-controlled storage", ("store_locally",)),
}
DEFAULT_MAX_QUOTE_CHARS = 1000


def gate(action: str, assessment: dict | None, *, quote_chars: int | None = None, max_quote_chars: int = DEFAULT_MAX_QUOTE_CHARS) -> dict:
    """Pure permission gate. Missing assessment, UNDETERMINED and DENY all deny; A9 is always denied."""
    if action not in ACTIONS:
        raise EvidenceError("unknown action")
    label, needed = ACTIONS[action]
    if action == "A9":
        return {"action": action, "decision": "DENY", "reason": "HANDOFF_NEVER_CARRIES_ASSET_BYTES_OR_EXCERPTS", "label": label}
    if assessment is None:
        return {"action": action, "decision": "DENY", "reason": "NO_RIGHTS_ASSESSMENT", "label": label}
    for permission in needed:
        value = assessment[permission]
        if value != "ALLOW":
            return {"action": action, "decision": "DENY", "reason": f"{permission.upper()}_{value}", "label": label}
    if action in ("A3", "A4") and (quote_chars is None or quote_chars > max_quote_chars):
        return {"action": action, "decision": "DENY", "reason": "QUOTE_LENGTH_EXCEEDS_LIMIT", "label": label}
    return {"action": action, "decision": "ALLOW", "reason": "PERMITTED", "label": label}


PDF_MAGIC = b"%PDF-"
# A real PDF body: header line, later a classic xref table with real line breaks (source code quoting these tokens does not match).
_EMBEDDED_PDF = re.compile(rb"%PDF-1\.\d[\r\n].*?xref\r?\n0 \d+\r?\n0000000000 65535 f", re.S)


def handoff_scan(paths) -> list[str]:
    """A9 enforcement helper: files that are PDF documents (``.pdf`` name, or a PDF header in the first 1024 bytes,
    the same G1 header rule) or that embed a complete PDF body (header ... ``%%EOF``). The package builder refuses them."""
    offenders = []
    for path in paths:
        with open(path, "rb") as handle:
            data = handle.read()
        embedded = bool(_EMBEDDED_PDF.search(data))
        if str(path).lower().endswith(".pdf") or PDF_MAGIC in data[:1024] or embedded:
            offenders.append(str(path))
    return offenders
