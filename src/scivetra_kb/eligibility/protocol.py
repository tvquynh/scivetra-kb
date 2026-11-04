"""S05 protocol (``fte-protocol/1``) and roster validation. Pure functions, no I/O.

A protocol is frozen into every run by its SHA-256: eligibility criteria, reason vocabularies (FT_EXCLUDE and
FT_NOT_RETRIEVED), the consensus rule, the evidence/version/rights policy and the documented-retrieval-effort rule.
A roster lists human reviewers and adjudicators. ``actor_type`` must be the literal ``HUMAN``; labels that name a
system, policy engine, AI or bot are refused. Labels are self-declared audit labels, not authenticated identities.
"""
from __future__ import annotations

import re

from ..core.canonical import semantic_sha256
from ..core.errors import ValidationError
from ..library.common import operator_label

SCHEMA = "fte-protocol/1"
CONSENSUS_RULE = "TWO_INDEPENDENT_REVIEWERS_PLUS_ADJUDICATOR"
OUTCOMES = ("FT_INCLUDE", "FT_EXCLUDE", "FT_UNSURE", "FT_NOT_RETRIEVED")
FINAL_OUTCOMES = ("FT_INCLUDE", "FT_EXCLUDE", "FT_NOT_RETRIEVED")
EVIDENCE_KINDS = ("KB_ASSET", "RV_STAGE11B_ARTIFACT")
VERSION_CLASSES = ("SUBMITTED", "ACCEPTED", "PUBLISHED", "UPDATED_PUBLISHED", "UNKNOWN")
DUPLICATE_REASON = "DUPLICATE_REPORT_OF_INCLUDED_STUDY"
_CODE = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
_CRITERION_ID = re.compile(r"[A-Za-z0-9._-]{1,32}")
# Labels that would make a non-human actor look like a reviewer (ADR-05-03: AI has no vote).
_NON_HUMAN_LABEL = re.compile(r"(?i)^(system|policy[_.-]?engine|ai|llm|gpt|chatgpt|claude|codex|copilot|bot|robot|model|auto|automated|script)"
                              r"([._-].*)?$|(^|[._-])(ai|llm|bot|gpt)([._-]|$)")
KEYS = {"schema", "label", "criteria", "reason_vocabulary_version", "exclude_reasons", "not_retrieved_reasons", "consensus_rule",
        "evidence_policy", "not_retrieved_min_documented_attempts"}
POLICY_KEYS = {"allowed_kinds", "allowed_version_classes", "require_local_process_allow"}


def _fail(message: str, code: str = "PROTOCOL_INVALID"):
    raise ValidationError(message, code=code)


def validate_protocol(protocol) -> dict:
    """Return the canonical protocol (unknown keys refused) or raise PROTOCOL_INVALID."""
    if not isinstance(protocol, dict) or set(protocol) - KEYS or not KEYS <= set(protocol):
        _fail("protocol needs exactly the keys " + ", ".join(sorted(KEYS)))
    if protocol["schema"] != SCHEMA:
        _fail("protocol schema must be fte-protocol/1")
    if not isinstance(protocol["label"], str) or not 1 <= len(protocol["label"]) <= 200:
        _fail("protocol label must be 1..200 characters")
    criteria = protocol["criteria"]
    if not isinstance(criteria, list) or not criteria or not all(isinstance(c, dict) and set(c) == {"id", "text"} and isinstance(c["id"], str)
                                                                  and _CRITERION_ID.fullmatch(c["id"]) and isinstance(c["text"], str)
                                                                  and 1 <= len(c["text"]) <= 1000 for c in criteria):
        _fail("criteria must be a non-empty list of {id, text}")
    if len({c["id"] for c in criteria}) != len(criteria):
        _fail("criterion ids must be unique")
    if not isinstance(protocol["reason_vocabulary_version"], str) or not protocol["reason_vocabulary_version"]:
        _fail("reason_vocabulary_version required")
    for key in ("exclude_reasons", "not_retrieved_reasons"):
        codes = protocol[key]
        if not isinstance(codes, list) or not codes or not all(isinstance(c, str) and _CODE.fullmatch(c) for c in codes) or len(set(codes)) != len(codes):
            _fail(f"{key} must be a non-empty list of unique codes [A-Z][A-Z0-9_]")
    if set(protocol["exclude_reasons"]) & set(protocol["not_retrieved_reasons"]):
        _fail("an exclusion reason cannot also be a not-retrieved reason (NOT_RETRIEVED is not an exclusion)")
    if protocol["consensus_rule"] != CONSENSUS_RULE:
        _fail("consensus_rule must be TWO_INDEPENDENT_REVIEWERS_PLUS_ADJUDICATOR (the only tested plan)")
    policy = protocol["evidence_policy"]
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        _fail("evidence_policy needs allowed_kinds, allowed_version_classes, require_local_process_allow")
    if not isinstance(policy["allowed_kinds"], list) or not policy["allowed_kinds"] or set(policy["allowed_kinds"]) - set(EVIDENCE_KINDS):
        _fail("allowed_kinds must be a non-empty subset of KB_ASSET, RV_STAGE11B_ARTIFACT")
    if not isinstance(policy["allowed_version_classes"], list) or not policy["allowed_version_classes"] \
            or set(policy["allowed_version_classes"]) - set(VERSION_CLASSES):
        _fail("allowed_version_classes must be a non-empty subset of " + ", ".join(VERSION_CLASSES))
    if policy["require_local_process_allow"] is not True:
        _fail("require_local_process_allow must be true (rights-policy/1 local processing gate is never relaxed)")
    attempts = protocol["not_retrieved_min_documented_attempts"]
    if type(attempts) is not int or not 1 <= attempts <= 20:
        _fail("not_retrieved_min_documented_attempts must be an integer 1..20")
    return {**protocol, "exclude_reasons": list(protocol["exclude_reasons"]), "not_retrieved_reasons": list(protocol["not_retrieved_reasons"]),
            "evidence_policy": {**policy, "allowed_kinds": sorted(policy["allowed_kinds"]),
                                "allowed_version_classes": sorted(policy["allowed_version_classes"])}}


def validate_roster(roster) -> list[dict]:
    """Return the canonical roster (sorted by label) or raise ROSTER_INVALID / NON_HUMAN_ACTOR_REFUSED."""
    if not isinstance(roster, list) or not roster:
        _fail("roster must be a non-empty list", "ROSTER_INVALID")
    out = []
    for entry in roster:
        if not isinstance(entry, dict) or set(entry) - {"label", "role", "actor_type", "conflicts"} or not {"label", "role", "actor_type"} <= set(entry):
            _fail("roster entries need label, role, actor_type (conflicts optional)", "ROSTER_INVALID")
        if entry["actor_type"] != "HUMAN":
            _fail("only HUMAN actors may review or adjudicate (AI/system votes are refused)", "NON_HUMAN_ACTOR_REFUSED")
        label = operator_label(entry["label"])
        if _NON_HUMAN_LABEL.search(label[len("OPERATOR:"):]):
            _fail("reviewer label names a system, AI, bot or model; only human reviewers are allowed", "NON_HUMAN_ACTOR_REFUSED")
        if entry["role"] not in ("REVIEWER", "ADJUDICATOR"):
            _fail("role must be REVIEWER or ADJUDICATOR", "ROSTER_INVALID")
        conflicts = entry.get("conflicts", [])
        if not isinstance(conflicts, list) or not all(isinstance(c, str) and c for c in conflicts):
            _fail("conflicts must be a list of corpus_item_id strings", "ROSTER_INVALID")
        out.append({"label": label, "role": entry["role"], "actor_type": "HUMAN", "conflicts": sorted(set(conflicts))})
    labels = [e["label"] for e in out]
    if len(set(labels)) != len(labels):
        _fail("a label may appear only once (one person cannot be both reviewer and adjudicator)", "ROSTER_INVALID")
    if sum(e["role"] == "REVIEWER" for e in out) < 2 or sum(e["role"] == "ADJUDICATOR" for e in out) < 1:
        _fail("the plan needs at least two REVIEWERs and one ADJUDICATOR", "ROSTER_INVALID")
    return sorted(out, key=lambda e: e["label"])


def assign(roster: list[dict], candidates: list[dict]) -> dict[str, list[str]]:
    """Deterministic independent assignment: two distinct REVIEWERs per candidate (round robin), skipping conflicts."""
    reviewers = [e for e in roster if e["role"] == "REVIEWER"]
    out = {}
    for c in candidates:
        picked = []
        for k in range(len(reviewers)):
            r = reviewers[(c["ordinal"] + k) % len(reviewers)]
            if c["corpus_item_id"] not in r["conflicts"] and r["label"] not in picked:
                picked.append(r["label"])
            if len(picked) == 2:
                break
        if len(picked) < 2:
            _fail(f"roster cannot give candidate {c['corpus_item_id']} two independent reviewers without a conflict of interest",
                  "ROSTER_CANNOT_COVER_CANDIDATE")
        out[c["corpus_item_id"]] = picked
    return out


def protocol_sha(protocol: dict) -> str:
    return semantic_sha256(protocol)


__all__ = ["validate_protocol", "validate_roster", "assign", "protocol_sha", "OUTCOMES", "FINAL_OUTCOMES", "DUPLICATE_REASON", "SCHEMA",
           "CONSENSUS_RULE"]
