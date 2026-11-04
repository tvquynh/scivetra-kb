"""LLM provider configuration (``scivetra-llm-providers/1``, ADR_LLM LL-D02..D04): a TOML file kept OUTSIDE the repository and
every KB, listing OpenAI-compatible providers with their data policy, limits and priority.

Rules:
- API keys are never in the file: a provider names the environment variable that holds its key (``key_env``). A field that looks
  like a literal key (``api_key``, ``key``, ``token``, ``secret``, ``password``) is refused (``LLM_LITERAL_KEY_REFUSED``).
- ``data_policy`` is the operator's reading of the provider's terms: LOCAL (runs on this machine, loopback only), NO_TRAINING,
  MAY_TRAIN or UNKNOWN (treated like MAY_TRAIN). It decides which data classes may be sent (``ALLOWED_POLICIES``).
- Remote providers need https; plain http is accepted only for a loopback host, and a LOCAL provider must be on loopback.
"""
from __future__ import annotations

import ipaddress
import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from ..core.errors import ValidationError

SCHEMA = "scivetra-llm-providers/1"
POLICIES = ("LOCAL", "NO_TRAINING", "MAY_TRAIN", "UNKNOWN")
# Data classes (ADR_LLM LL-D03), derived from the rights gates of the material in the prompt (``data_class_for``).
ALLOWED_POLICIES = {
    "LOCAL_ONLY": ("LOCAL",),                                              # A6 local processing only
    "EXTERNAL_NO_TRAINING": ("LOCAL", "NO_TRAINING"),                      # A8 allowed, but the licence forbids training use
    "EXTERNAL_ANY": ("LOCAL", "NO_TRAINING", "MAY_TRAIN", "UNKNOWN"),      # synthetic, metadata-only or open (e.g. CC-BY) material
}
DATA_CLASSES = tuple(ALLOWED_POLICIES)
FIELDS = {"name", "base_url", "model", "key_env", "data_policy", "priority", "family", "limits", "timeout_seconds", "json_mode", "enabled"}
LIMIT_FIELDS = {"rpm", "rpd", "tpm"}
SECRET_FIELDS = {"api_key", "apikey", "key", "token", "secret", "password", "authorization", "bearer"}
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
LOOPBACK_NAMES = {"localhost"}


def data_class_for(*, local_process_allowed: bool, external_ai_allowed: bool, training_use_allowed: bool) -> str | None:
    """The data class of a prompt's material from its rights: None when even local model processing is not allowed (A6 deny)."""
    if not local_process_allowed:
        return None
    if not external_ai_allowed:
        return "LOCAL_ONLY"
    return "EXTERNAL_ANY" if training_use_allowed else "EXTERNAL_NO_TRAINING"


def _is_loopback(host: str) -> bool:
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _provider(raw, index: int) -> dict:
    where = f"provider[{index}]"
    if not isinstance(raw, dict):
        raise ValidationError(f"{where}: must be a table", code="LLM_CONFIG_INVALID")
    secret = sorted(k for k in raw if str(k).lower().replace("-", "_") in SECRET_FIELDS)
    if secret:
        raise ValidationError(f"{where}: keys are never written in the config; use key_env = \"ENV_VAR_NAME\"", code="LLM_LITERAL_KEY_REFUSED")
    unknown = sorted(set(raw) - FIELDS)
    if unknown:
        raise ValidationError(f"{where}: unknown fields {unknown}", code="LLM_CONFIG_INVALID")
    name = raw.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValidationError(f"{where}: name must be 1-40 of a-z 0-9 . _ -", code="LLM_CONFIG_INVALID")
    model = raw.get("model")
    if not isinstance(model, str) or not 1 <= len(model) <= 200 or any(c.isspace() for c in model):
        raise ValidationError(f"{name}: model is required (no spaces)", code="LLM_CONFIG_INVALID")
    policy = raw.get("data_policy")
    if policy not in POLICIES:
        raise ValidationError(f"{name}: data_policy must be one of {POLICIES}", code="LLM_CONFIG_INVALID")
    url = raw.get("base_url")
    parts = urlsplit(url) if isinstance(url, str) else None
    if not parts or parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValidationError(f"{name}: base_url must be http(s)://host[:port]/path without credentials, query or fragment", code="LLM_CONFIG_INVALID")
    loopback = _is_loopback(parts.hostname)
    if parts.scheme == "http" and not loopback:
        raise ValidationError(f"{name}: a remote provider needs https", code="LLM_CONFIG_INVALID")
    if policy == "LOCAL" and not loopback:
        raise ValidationError(f"{name}: data_policy LOCAL is only for a provider on this machine (loopback host)", code="LLM_POLICY_HOST_MISMATCH")
    key_env = raw.get("key_env")
    if key_env is not None and (not isinstance(key_env, str) or not ENV_NAME.fullmatch(key_env)):
        raise ValidationError(f"{name}: key_env must be an environment variable NAME such as GROQ_API_KEY", code="LLM_CONFIG_INVALID")
    if key_env is None and not loopback:
        raise ValidationError(f"{name}: a remote provider needs key_env", code="LLM_CONFIG_INVALID")
    limits = raw.get("limits", {})
    if not isinstance(limits, dict) or set(limits) - LIMIT_FIELDS or not all(isinstance(v, int) and v > 0 for v in limits.values()):
        raise ValidationError(f"{name}: limits takes positive integers rpm, rpd, tpm", code="LLM_CONFIG_INVALID")
    priority = raw.get("priority", 100)
    timeout = raw.get("timeout_seconds", 60)
    if not isinstance(priority, int) or not isinstance(timeout, int) or not 1 <= timeout <= 600:
        raise ValidationError(f"{name}: priority must be an integer, timeout_seconds 1..600", code="LLM_CONFIG_INVALID")
    family = raw.get("family", name)
    if not isinstance(family, str) or not NAME.fullmatch(family):
        raise ValidationError(f"{name}: family must look like a name", code="LLM_CONFIG_INVALID")
    for flag in ("json_mode", "enabled"):
        if not isinstance(raw.get(flag, True), bool):
            raise ValidationError(f"{name}: {flag} must be true or false", code="LLM_CONFIG_INVALID")
    return {"name": name, "base_url": url.rstrip("/"), "model": model, "key_env": key_env, "data_policy": policy, "priority": priority,
            "family": family, "limits": dict(sorted(limits.items())), "timeout_seconds": timeout, "json_mode": raw.get("json_mode", True),
            "enabled": raw.get("enabled", True), "loopback": loopback}


def parse_config(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("schema", SCHEMA) != SCHEMA:
        raise ValidationError(f"schema must be {SCHEMA}", code="LLM_CONFIG_INVALID")
    secret = sorted(k for k in data if str(k).lower() in SECRET_FIELDS)
    if secret:
        raise ValidationError("keys are never written in the config", code="LLM_LITERAL_KEY_REFUSED")
    raw = data.get("provider")
    if not isinstance(raw, list) or not raw:
        raise ValidationError("at least one [[provider]] is required", code="LLM_CONFIG_INVALID")
    providers = [_provider(p, i) for i, p in enumerate(raw)]
    names = [p["name"] for p in providers]
    if len(set(names)) != len(names):
        raise ValidationError("provider names must be unique", code="LLM_CONFIG_INVALID")
    return {"schema": SCHEMA, "providers": sorted(providers, key=lambda p: (p["priority"], p["name"]))}


def load_config(path_text: str) -> dict:
    path = Path(path_text)
    if not path.is_file() or path.stat().st_size > 256 * 1024:
        raise ValidationError("config file not found or larger than 256 KiB", code="LLM_CONFIG_INVALID")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError(f"config is not valid TOML: {exc}"[:300], code="LLM_CONFIG_INVALID") from None
    return parse_config(data)
