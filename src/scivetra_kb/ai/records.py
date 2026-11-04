"""S10 owner records (migration 0010): owners, consumer profiles, A7 / A8 determinations, OAG-6 release approvals, revocations.

Written only by the local operator CLI (never by an AI client or the gateway). Every record is append-only; a revocation is a new
row. Nothing here creates an approval automatically, and no record is a real-world authorization unless a human owner made it.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta

from ..core.canonical import canonical_json, semantic_sha256
from ..core.errors import PolicyRefusal, ValidationError
from ..core.ids import format_utc, parse_utc
from ..library.common import operator_label, require_reason
from ..retrieval.consumers import CONTENT_CLASSES
from ..rights import policy as rights_policy
from . import policy as P

MAX_VALID_HOURS = 24 * 90
_KINDS = {"OWNER": ("aig_owner", "owner_id", "aio_"), "PROFILE": ("aig_consumer_profile", "profile_id", "aip_"),
          "DETERMINATION": ("aig_rights_determination", "determination_id", "aid_"), "APPROVAL": ("aig_release_approval", "approval_id", "aia_")}


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hours(value) -> int:
    if type(value) is not int or not 1 <= value <= MAX_VALID_HOURS:
        raise ValidationError(f"valid_hours must be an integer 1..{MAX_VALID_HOURS}", code="VALIDITY_INVALID")
    return value


def _short(text, name, limit=500) -> str:
    text = require_reason(text)
    if len(text) > limit:
        raise ValidationError(f"{name} longer than {limit} characters", code="TEXT_TOO_LONG")
    return text


def member_identity(member: dict) -> dict:
    """The identity an approval pins for one scope member (full-text members: version, asset, artifact; release members: paper)."""
    keys = ("paper_id", "document_version_id", "asset_sha256", "parse_artifact_id", "artifact_sha256", "link_id")
    return {k: member[k] for k in keys if k in member}


class OwnerRecords:
    def __init__(self, kb):
        self.kb = kb

    # -- lookups used by the gateway (current state, evaluated per request) --------------------------------------------------
    def revoked(self, kind: str, target_id: str) -> bool:
        return self.kb.one("SELECT 1 FROM aig_revocation WHERE target_kind=? AND target_id=?", (kind, target_id)) is not None

    def expired(self, row) -> bool:
        return parse_utc(row["expires_at_utc"]) <= self.kb.clock.now()

    def owner_active(self, owner_id: str) -> bool:
        return self.kb.one("SELECT 1 FROM aig_owner WHERE owner_id=?", (owner_id,)) is not None and not self.revoked("OWNER", owner_id)

    def profile(self, profile_id) -> tuple[dict | None, str | None]:
        row = self.kb.one("SELECT * FROM aig_consumer_profile WHERE profile_id=?", (str(profile_id)[:80],))
        if row is None:
            return None, "PROFILE_NOT_REGISTERED"
        if self.revoked("PROFILE", row["profile_id"]):
            return None, "PROFILE_REVOKED"
        spec = P.CONSUMERS.get(row["consumer"])
        if spec is None or (row["transport"], row["destination_class"], row["model_location"]) != (
                spec["transport"], spec["destination_class"], spec["model_location"]):
            return None, "PROFILE_NOT_PERMITTED_BY_POLICY"
        if row["policy_sha256"] != P.POLICY_SHA256:
            return None, "PROFILE_POLICY_CHANGED"
        if semantic_sha256(self._profile_core(row)) != row["semantic_sha256"]:
            return None, "PROFILE_TAMPERED"
        return dict(row, scope_ids=json.loads(row["scope_ids_json"])), None

    def approvals(self, profile, scope_id) -> list:
        return self.kb.all("SELECT * FROM aig_release_approval WHERE profile_id=? AND scope_id=? ORDER BY rowid", (profile["profile_id"], scope_id))

    def approval_problems(self, a, profile, scope) -> list:
        """Why approval ``a`` does not cover this profile + scope NOW (empty = valid)."""
        out = []
        if self.revoked("APPROVAL", a["approval_id"]):
            out.append("APPROVAL_REVOKED")
        if self.expired(a):
            out.append("APPROVAL_EXPIRED")
        if not self.owner_active(a["owner_id"]):
            out.append("APPROVAL_OWNER_REVOKED")
        if (a["consumer"], a["destination_class"], a["model_location"]) != (profile["consumer"], profile["destination_class"], profile["model_location"]):
            out.append("APPROVAL_DESTINATION_MISMATCH")
        if a["membership_sha256"] != scope["membership_sha256"]:
            out.append("APPROVAL_SCOPE_MEMBERSHIP_CHANGED")
        if a["policy_sha256"] != P.POLICY_SHA256:
            out.append("APPROVAL_POLICY_CHANGED")
        if a["protocol_sha256"] != P.PROTOCOL_SHA256:
            out.append("APPROVAL_PROTOCOL_CHANGED")
        if semantic_sha256(self._approval_core(a)) != a["semantic_sha256"]:
            out.append("APPROVAL_TAMPERED")
        return sorted(out)

    def determination(self, permission: str, assessment: dict | None) -> tuple[dict | None, str]:
        """A valid owner determination resolving ``permission`` for the CURRENT assessment, else (None, reason)."""
        if assessment is None:
            return None, "NO_RIGHTS_ASSESSMENT"
        rows = self.kb.all("SELECT * FROM aig_rights_determination WHERE permission=? AND asset_sha256=? AND document_version_id=? ORDER BY rowid DESC",
                           (permission, assessment["asset_sha256"], assessment["document_version_id"]))
        reasons = []                                        # newest first: the reason reported is the newest determination's (v02)
        for d in rows:
            if d["rights_assessment_id"] != assessment["assessment_id"] or d["rights_assessment_sha256"] != assessment["semantic_sha256"]:
                reasons.append("DETERMINATION_FOR_OTHER_RIGHTS_ASSESSMENT")
            elif self.revoked("DETERMINATION", d["determination_id"]):
                reasons.append("DETERMINATION_REVOKED")
            elif self.expired(d):
                reasons.append("DETERMINATION_EXPIRED")
            elif not self.owner_active(d["owner_id"]):
                reasons.append("DETERMINATION_OWNER_REVOKED")
            elif semantic_sha256(self._determination_core(d)) != d["semantic_sha256"]:
                reasons.append("DETERMINATION_TAMPERED")
            else:
                return d, "OWNER_DETERMINATION"
        return None, reasons[0] if reasons else "NO_OWNER_DETERMINATION"

    # -- operator commands --------------------------------------------------------------------------------------------------
    def _owner(self, owner_label: str) -> dict:
        label = "OWNER:" + operator_label(owner_label)[len("OPERATOR:"):]
        row = self.kb.one("SELECT * FROM aig_owner WHERE owner_label=?", (label,))
        if row is None or self.revoked("OWNER", row["owner_id"]):
            raise PolicyRefusal("not a registered, active S10 owner", code="OWNER_NOT_AUTHORIZED")
        return row

    def register_owner(self, *, owner: str, reason: str, operator: str) -> dict:
        actor = operator_label(operator)
        label = "OWNER:" + operator_label(owner)[len("OPERATOR:"):]
        if self.kb.one("SELECT 1 FROM aig_owner WHERE owner_label=?", (label,)):
            raise ValidationError("owner already registered", code="OWNER_EXISTS")
        row = dict(owner_id=self.kb.ids.new("aio"), owner_label=label, assurance="LOCAL_OPERATOR_ASSERTED", reason=_short(reason, "reason"),
                   created_by=actor, created_at_utc=self.kb.now())
        with self.kb.transaction():
            self.kb.insert("aig_owner", row)
        return {"owner_id": row["owner_id"], "owner_label": label, "assurance": row["assurance"]}

    @staticmethod
    def _profile_core(row) -> dict:
        return {k: row[k] for k in ("consumer", "transport", "destination_class", "model_location", "identity_assurance", "scope_ids_json",
                                    "label", "policy_sha256", "created_by")}

    def create_profile(self, *, consumer: str, scope_ids: list, label: str, operator: str) -> dict:
        actor = operator_label(operator)
        if consumer not in P.CONSUMERS:
            code = P.DISABLED_CONSUMERS.get(consumer, "CONSUMER_UNKNOWN") if isinstance(consumer, str) else "CONSUMER_UNKNOWN"
            raise PolicyRefusal("consumer not available in S10 v01", code=code)
        if not isinstance(scope_ids, list) or not 1 <= len(scope_ids) <= 20 or len(set(map(str, scope_ids))) != len(scope_ids):
            raise ValidationError("scope_ids: 1..20 distinct retrieval scope ids", code="PROFILE_SCOPES_INVALID")
        for s in scope_ids:
            if not isinstance(s, str) or not self.kb.one("SELECT 1 FROM rt_scope WHERE scope_id=?", (s,)):
                raise ValidationError("unknown retrieval scope", code="UNKNOWN_REFERENCE")
        spec = P.CONSUMERS[consumer]
        row = dict(profile_id=self.kb.ids.new("aip"), consumer=consumer, transport=spec["transport"], destination_class=spec["destination_class"],
                   model_location=spec["model_location"], identity_assurance=P.CALLER_ASSURANCE, scope_ids_json=canonical_json(sorted(scope_ids)),
                   label=_short(label, "label", 200), policy_sha256=P.POLICY_SHA256, created_by=actor, created_at_utc=self.kb.now())
        row["semantic_sha256"] = semantic_sha256(self._profile_core(row))
        existing = self.kb.one("SELECT profile_id FROM aig_consumer_profile WHERE semantic_sha256=?", (row["semantic_sha256"],))
        if existing and not self.revoked("PROFILE", existing["profile_id"]):
            return {"profile_id": existing["profile_id"], "created": False}
        if existing:
            raise ValidationError("an identical profile was revoked; use another label", code="PROFILE_REVOKED")
        with self.kb.transaction():
            self.kb.insert("aig_consumer_profile", row)
        return {"profile_id": row["profile_id"], "created": True, "consumer": consumer, "transport": row["transport"],
                "destination_class": row["destination_class"], "model_location": row["model_location"],
                "identity_assurance": row["identity_assurance"], "scope_ids": sorted(scope_ids), "content_release": "NONE_UNTIL_OWNER_APPROVAL"}

    @staticmethod
    def _determination_core(row) -> dict:
        return {k: row[k] for k in ("permission", "decision", "asset_sha256", "document_version_id", "rights_assessment_id", "rights_assessment_sha256",
                                    "rights_class", "basis_ref", "owner_id", "expires_at_utc", "created_by")}

    def determine_rights(self, *, asset_sha256: str, document_version_id: str, permission: str, basis_ref: str, valid_hours: int, owner: str,
                         operator: str) -> dict:
        """Owner determination: UNDETERMINED -> ALLOW for one asset + version under its CURRENT rights assessment (PUBLIC_OA_LICENSED
        only; a DENY, another class or an already-decided permission is refused)."""
        actor = operator_label(operator)
        own = self._owner(owner)
        if permission not in ("external_ai_process", "redistribute"):
            raise ValidationError("permission: external_ai_process or redistribute", code="PERMISSION_INVALID")
        a = self.kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? AND document_version_id=? ORDER BY rowid DESC LIMIT 1",
                        (asset_sha256, document_version_id))
        if a is None:
            raise PolicyRefusal("no rights assessment for this asset and version", code="NO_RIGHTS_ASSESSMENT")
        if a["policy_version"] != rights_policy.POLICY_VERSION:
            raise PolicyRefusal("rights assessment under another policy version", code="RIGHTS_ASSESSMENT_STALE")
        if a["rights_class"] != "PUBLIC_OA_LICENSED":
            raise PolicyRefusal("only PUBLIC_OA_LICENSED permissions can be determined by an owner; a DENY is never widened",
                                code="DETERMINATION_NOT_PERMITTED_FOR_CLASS")
        if a[permission] != "UNDETERMINED":
            raise PolicyRefusal(f"{permission} is {a[permission]}, not UNDETERMINED", code="DETERMINATION_NOT_NEEDED_OR_DENIED")
        now = self.kb.clock.now()
        row = dict(determination_id=self.kb.ids.new("aid"), permission=permission, decision="ALLOW", asset_sha256=asset_sha256,
                   document_version_id=document_version_id, rights_assessment_id=a["assessment_id"], rights_assessment_sha256=a["semantic_sha256"],
                   rights_class=a["rights_class"], basis_ref=_short(basis_ref, "basis_ref", 300), owner_id=own["owner_id"],
                   expires_at_utc=format_utc(now + timedelta(hours=_hours(valid_hours))), created_by=actor, created_at_utc=self.kb.now())
        row["semantic_sha256"] = semantic_sha256(self._determination_core(row))
        if self.kb.one("SELECT 1 FROM aig_rights_determination WHERE semantic_sha256=?", (row["semantic_sha256"],)):
            raise ValidationError("an identical determination already exists", code="DETERMINATION_EXISTS")
        with self.kb.transaction():
            self.kb.insert("aig_rights_determination", row)
        return {"determination_id": row["determination_id"], "permission": permission, "decision": "ALLOW", "rights_assessment_id": a["assessment_id"],
                "expires_at_utc": row["expires_at_utc"], "owner_id": own["owner_id"]}

    @staticmethod
    def _approval_core(row) -> dict:
        return {k: row[k] for k in ("profile_id", "consumer", "destination_class", "model_location", "scope_id", "membership_sha256", "members_json",
                                    "operations_json", "content_classes_json", "policy_sha256", "protocol_sha256", "reason", "owner_id",
                                    "expires_at_utc", "revocation_token_sha256", "created_by")}

    def approve(self, *, profile_id: str, scope_id: str, operations: list, content_classes: list, valid_hours: int, reason: str, owner: str,
                operator: str, paper_ids: list | None = None) -> dict:
        """OAG-6 release approval. Returns the revocation token ONCE (only its SHA-256 is stored)."""
        from ..retrieval.service import RetrievalService
        actor = operator_label(operator)
        own = self._owner(owner)
        profile, why = self.profile(profile_id)
        if profile is None:
            raise PolicyRefusal("profile not usable", code=why)
        if scope_id not in profile["scope_ids"]:
            raise PolicyRefusal("scope is not configured for this profile", code="SCOPE_NOT_IN_PROFILE")
        if not isinstance(operations, list) or not operations or not set(operations) <= set(P.APPROVABLE) or len(set(operations)) != len(operations):
            raise ValidationError("operations: distinct values of " + ", ".join(P.APPROVABLE), code="OPERATIONS_INVALID")
        if not isinstance(content_classes, list) or not content_classes or not set(content_classes) <= set(CONTENT_CLASSES) \
                or len(set(content_classes)) != len(content_classes):
            raise ValidationError("content_classes: distinct values of " + ", ".join(CONTENT_CLASSES), code="CONTENT_CLASSES_INVALID")
        scope = RetrievalService(self.kb)._scope(scope_id)
        if scope["basis_kind"] != "FTE_RUN" and set(content_classes) - {"METADATA"}:
            raise PolicyRefusal("a Reprovane release scope is METADATA_ONLY", code="SCOPE_METADATA_ONLY")
        members = scope["members"]
        if paper_ids is not None:
            if not isinstance(paper_ids, list) or not paper_ids or not set(paper_ids) <= {m["paper_id"] for m in members}:
                raise ValidationError("paper_ids must be members of the scope", code="APPROVAL_MEMBERS_INVALID")
            members = [m for m in members if m["paper_id"] in set(paper_ids)]
        token = "rvk_" + secrets.token_hex(24)
        now = self.kb.clock.now()
        row = dict(approval_id=self.kb.ids.new("aia"), profile_id=profile["profile_id"], consumer=profile["consumer"],
                   destination_class=profile["destination_class"], model_location=profile["model_location"], scope_id=scope_id,
                   membership_sha256=scope["membership_sha256"], members_json=canonical_json(sorted((member_identity(m) for m in members),
                                                                                                    key=lambda m: m["paper_id"])),
                   operations_json=canonical_json(sorted(operations)), content_classes_json=canonical_json(sorted(content_classes)),
                   policy_sha256=P.POLICY_SHA256, protocol_sha256=P.PROTOCOL_SHA256, reason=_short(reason, "reason"), owner_id=own["owner_id"],
                   expires_at_utc=format_utc(now + timedelta(hours=_hours(valid_hours))), revocation_token_sha256=token_sha256(token),
                   created_by=actor, created_at_utc=self.kb.now())
        row["semantic_sha256"] = semantic_sha256(self._approval_core(row))
        with self.kb.transaction():
            self.kb.insert("aig_release_approval", row)
        return {"approval_id": row["approval_id"], "profile_id": row["profile_id"], "consumer": row["consumer"], "scope_id": scope_id,
                "members": len(members), "operations": sorted(operations), "content_classes": sorted(content_classes),
                "expires_at_utc": row["expires_at_utc"], "owner_id": own["owner_id"], "revocation_token": token,
                "note": "keep the revocation token private; it is shown once. An approval is a local owner record, not a vendor setting."}

    def revoke(self, *, target_id: str | None = None, token: str | None = None, reason: str, operator: str | None = None) -> dict:
        reason = _short(reason, "reason")
        if token is not None:
            row = self.kb.one("SELECT approval_id FROM aig_release_approval WHERE revocation_token_sha256=?", (token_sha256(str(token)),))
            if row is None:
                raise ValidationError("unknown revocation token", code="UNKNOWN_REFERENCE")
            kind, target, by = "APPROVAL", row["approval_id"], "REVOCATION_TOKEN"
        else:
            by = operator_label(operator)
            kind = next((k for k, (_, _, prefix) in _KINDS.items() if isinstance(target_id, str) and target_id.startswith(prefix)), None)
            if kind is None:
                raise ValidationError("target id must be an S10 owner, profile, determination or approval id", code="UNKNOWN_REFERENCE")
            table, key, _ = _KINDS[kind]
            if not self.kb.one(f"SELECT 1 FROM {table} WHERE {key}=?", (target_id,)):
                raise ValidationError("unknown target", code="UNKNOWN_REFERENCE")
            target = target_id
        if self.revoked(kind, target):
            return {"target_kind": kind, "target_id": target, "already_revoked": True}
        row = dict(revocation_id=self.kb.ids.new("air"), target_kind=kind, target_id=target, reason=reason, revoked_by=by, created_at_utc=self.kb.now())
        with self.kb.transaction():
            self.kb.insert("aig_revocation", row)
        return {"revocation_id": row["revocation_id"], "target_kind": kind, "target_id": target, "effective": "every request that starts after this commit"}

    def status(self) -> dict:
        kb = self.kb
        def state(kind, row):
            if self.revoked(kind, row[_KINDS[kind][1]]):
                return "REVOKED"
            return "EXPIRED" if "expires_at_utc" in row and self.expired(row) else "ACTIVE"
        owners = [dict(owner_id=r["owner_id"], owner_label=r["owner_label"], state=state("OWNER", r)) for r in kb.all("SELECT * FROM aig_owner ORDER BY rowid")]
        profiles = [dict(profile_id=r["profile_id"], consumer=r["consumer"], scope_ids=json.loads(r["scope_ids_json"]), state=state("PROFILE", r))
                    for r in kb.all("SELECT * FROM aig_consumer_profile ORDER BY rowid")]
        approvals = [dict(approval_id=r["approval_id"], profile_id=r["profile_id"], scope_id=r["scope_id"], operations=json.loads(r["operations_json"]),
                          content_classes=json.loads(r["content_classes_json"]), members=len(json.loads(r["members_json"])),
                          expires_at_utc=r["expires_at_utc"], state=state("APPROVAL", r)) for r in kb.all("SELECT * FROM aig_release_approval ORDER BY rowid")]
        determinations = [dict(determination_id=r["determination_id"], permission=r["permission"], asset_sha256=r["asset_sha256"],
                               rights_assessment_id=r["rights_assessment_id"], expires_at_utc=r["expires_at_utc"], state=state("DETERMINATION", r))
                          for r in kb.all("SELECT * FROM aig_rights_determination ORDER BY rowid")]
        return {"policy": P.POLICY_ID, "policy_sha256": P.POLICY_SHA256, "protocol": P.PROTOCOL_ID, "protocol_sha256": P.PROTOCOL_SHA256,
                "capabilities": P.CAPABILITIES, "owners": owners, "profiles": profiles, "approvals": approvals, "determinations": determinations,
                "events": kb.one("SELECT count(*) n FROM aig_access_event")["n"]}
