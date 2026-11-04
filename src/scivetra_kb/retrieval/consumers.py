"""``rkb-retrieval-consumer/1``: the explicit, versioned interface through which S10 adapts S09 retrieval for an external AI
consumer (ADR_S10 AI-D02).

S09 serves the local CLI consumer (``LOCAL_CLI``, a plain string). Every other consumer is refused, and a string can never name
one: an external consumer reaches ``RetrievalService`` only as an ``ExternalConsumer`` object built server side by the S10
gateway from a registered installation profile. S09 keeps all of its own checks (scope, member currency, CAS / anchor
verification, gate A5); for every item it would show, it additionally asks the consumer's ``release`` for the item's content
class, so S10 adds A7 / A8 / OAG-6 on top and can never relax an S09 decision. S09 passes the member's CURRENT rights assessment for
EVERY class, METADATA and ANCHOR included (S10-F01); ``None`` means there is none (e.g. a metadata-only member) and must be a deny. S09 audit details for such a consumer are handed
to ``record`` (the S10 gateway writes them durably in ``aig_access_event`` before anything is released); ``rt_access_event``
stays LOCAL_CLI only.
"""
from __future__ import annotations

INTERFACE = "rkb-retrieval-consumer/1"
EXTERNAL_CONSUMERS = ("CLAUDE_DESKTOP_LOCAL_MCP", "CHATGPT_FILE_EXCHANGE")
# Content classes an external consumer may be released, each separately approved (OAG-6). Quotes, reviewer / adjudicator
# rationales, scope labels and free text are not a class: they are never released.
CONTENT_CLASSES = ("METADATA", "ANCHOR", "PASSAGE_EXCERPT", "EVIDENCE_TYPED_VALUES", "EVIDENCE_TEXT_FIELDS")


class ExternalConsumer:
    """Base class of server-side consumer contexts. ``name`` is one of ``EXTERNAL_CONSUMERS``."""
    interface = INTERFACE
    name = None

    def release(self, content_class: str, member: dict, assessment: dict | None) -> tuple[bool, str]:
        """(allowed, reason) for releasing one item of ``content_class`` of ``member`` NOW. Default deny."""
        return False, "RELEASE_NOT_IMPLEMENTED"

    def record(self, event: dict) -> None:
        """Receive the S09 audit details of one public retrieval call (no text)."""
        raise NotImplementedError


def is_external(consumer) -> bool:
    return isinstance(consumer, ExternalConsumer) and consumer.interface == INTERFACE and consumer.name in EXTERNAL_CONSUMERS
