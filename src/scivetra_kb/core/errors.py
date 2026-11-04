"""Typed errors. Every error carries a stable machine code for JSON output."""


class KBError(Exception):
    code = "KB_ERROR"
    exit_code = 2

    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(message or self.code)
        if code:
            self.code = code


class ValidationError(KBError):
    code = "VALIDATION_FAILED"


class NotInitializedError(KBError):
    code = "KB_NOT_INITIALIZED"


class SchemaError(KBError):
    """Schema version or migration-ledger problem; never auto-repaired."""
    code = "KB_SCHEMA_MISMATCH"
    exit_code = 3


class IntegrityError(KBError):
    """Hash mismatch, tampering of recorded values, or broken references."""
    code = "INTEGRITY_FAILURE"
    exit_code = 3


class ImmutableRecordError(KBError):
    code = "IMMUTABLE_RECORD"
    exit_code = 3


class LockError(KBError):
    code = "WRITER_ACTIVE"


class PolicyRefusal(KBError):
    """An action refused by policy (e.g. live network, protected data root)."""
    code = "POLICY_REFUSED"


class TransportError(KBError):
    code = "TRANSPORT_ERROR"
