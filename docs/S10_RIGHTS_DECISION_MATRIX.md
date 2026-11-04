# S10 rights decision matrix (v02): what an external AI consumer can receive

Policy `scivetra-ai-consumer-policy/2`. Every row is evaluated per request, per member, on the CURRENT state (no cache across
requests). "Current A8" means the member's current rights assessment says `external_ai_process = ALLOW`, or it is `UNDETERMINED` for
a `PUBLIC_OA_LICENSED` asset and a valid owner determination (not revoked, not expired, same assessment id and SHA-256, owner active)
resolves it. A7 is the same for `redistribute` and applies to the ChatGPT file exchange only. `LOCAL_CLI` (S09) is not affected.

## By content class

| Class | Tools | Claude Desktop (MCP stdio) needs | ChatGPT file exchange needs | Without them |
|---|---|---|---|---|
| `METADATA` (title, DOI, venue, year, first author, integrity alert) | `search_papers` | OAG-6 (op + class + member) + current member + current A8 | same + current A7 | no hit; `METADATA_NOT_RELEASED:<reason>` counted |
| `ANCHOR` (paper / version / asset id, artifact and excerpt SHA-256, page, span, chunk id) | `search_passages`, `get_verified_passage` | OAG-6 + S09 checks (member current, CAS, anchor) + current A8 | same + current A7 | no hit; `ANCHOR_NOT_RELEASED:<reason>` counted |
| `PASSAGE_EXCERPT` (text) | `search_passages`, `get_verified_passage`, `build_rag_context`, exchange bundle | OAG-6 + S09 A2 + A5 + CAS / anchor + current A8 | same + current A7 | no text; ANCHOR_ONLY only if the ANCHOR row allows it, else nothing |
| `EVIDENCE_TYPED_VALUES` (typed values + anchors of accepted evidence) | `search_evidence` | OAG-6 + currently accepted S08 evidence of a current member + current A8 | same + current A7 | no hit; `EVIDENCE_NOT_RELEASED:<reason>` |
| `EVIDENCE_TEXT_FIELDS` | `search_evidence` | the row above + A5 + stored under A3 + own class approval | same + current A7 | text fields withheld |
| never released | — | quotes, reviewer / adjudicator rationales, scope labels and reasons, query text, paths, secrets | same | — |

Session tools (`list_retrieval_scopes`, `get_integration_status`) return no research-derived content: scope ids, member counts and
approval state, capability states.

## By rights state (any class, either consumer)

| Member's rights state | Result | Reason code (in `excluded_by_reason`, `rights[].A8.basis`) |
|---|---|---|
| no OAG-6 approval for the operation | DENIED, nothing | `OAG_6_NOT_APPROVED`, ... |
| approval does not name the class / the member | not released | `CONTENT_CLASS_NOT_APPROVED:<class>`, `APPROVAL_DOES_NOT_COVER_MEMBER` |
| member identity changed (new version / asset / artifact) | not released | `APPROVAL_MEMBER_IDENTITY_MISMATCH` or `STALE:*` |
| member not current (stale, blocked, CAS / PDF integrity) | not released | `MEMBER_NOT_CURRENT:<problem>` (metadata) / `STALE:*`, `BLOCKED:*` |
| metadata-only member (no asset / version) | not released | `NO_RIGHTS_ASSESSMENT` |
| `external_ai_process` UNDETERMINED, no owner determination | not released | `A8_NOT_AUTHORIZED:NO_OWNER_DETERMINATION` |
| `external_ai_process` DENY | not released; owner cannot widen it | `A8_NOT_AUTHORIZED:EXTERNAL_AI_PROCESS_DENY` |
| determination revoked / expired / for another assessment / owner revoked / tampered | not released | `A8_NOT_AUTHORIZED:DETERMINATION_*` |
| A8 ALLOW, file exchange, `redistribute` not ALLOW | not released | `A7_NOT_AUTHORIZED:*` |
| A8 ALLOW (+ A7) and every other check passes | released | reason `RELEASED_UNDER_OAG6_APPROVAL_AND_CURRENT_A8` (`_A8_A7`) |
| audit event cannot be committed (I/O, disk full, locked, read-only) | ERROR, nothing | `AUDIT_UNAVAILABLE:*` |

## What a denied response still shows

Status (`DENIED` / `NO_AUTHORIZED_EVIDENCE`), reason codes and counts, the policy / protocol hashes, and for a member whose A8 / A7 was
evaluated a rights entry with the gate decisions only (`member: WITHHELD_NOTHING_RELEASED`; no paper id, assessment id or rights
class). The audit row (`aig_access_event.rights_json`) keeps the full entry for the owner.

## Proof

- Architect reproducer `INDEPENDENT_S10_A8_METADATA_ANCHOR_REPRO.py`, unmodified: 5/5 (evidence `independent_s10_a8_repro_*`).
- `tests/test_s10_v02_rights_coverage.py`: both consumers x every tool without A8 (no title, DOI, venue, ids, chunk ids or text in
  any response), A8 DENY, A8 without A7, positive METADATA / ANCHOR release, anchor-only under A5 DENY, revocation / expiry /
  reassessment / source integrity, v01 records void, audit failure, MCP child process, exchange bundle.
