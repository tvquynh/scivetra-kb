# ADR LLM: free-provider LLM integration (stage L1 implemented; L2–L5 planned)

**Status:** L1 implemented and offline-tested. It is not part of the 1.0.0rc1 release artifacts.

**Owner request (2026-11-05).** Use free LLM services, switching between several of them (registering several APIs is acceptable). Claude Desktop and Claude Code are also available on the owner's machine.

**Principle.** An LLM only **proposes**. Every answer is untrusted data. It passes the existing gates (strict schema, anchors verified against the S06 text, `NON_AUTHORITATIVE`, independent human review) and never decides, accepts, attests or changes rights.

## LL-D01 Scope of stage L1

L1 delivers:
- the provider configuration (`llm/config.py`);
- the rights-aware router (`llm/router.py`): rotation, limits, Retry-After, circuit breaker, two-provider consensus and the call log;
- an offline simulator and three commands: `rkb llm check-config`, `rkb llm plan`, `rkb llm simulate`.

The transport is injected. Only a scripted `FakeTransport` exists, so **no network code is added** (security gate G5 is unchanged) and no key is ever sent. L1 is not yet wired into S07, S08, S09 or S11.

## LL-D02 Configuration outside the repository and every KB

The configuration is a TOML file (`scivetra-llm-providers/1`), for example `~/SciVetra/llm-providers.toml`. Example: `config/llm-providers.example.toml`.

Each provider has:
- `name`, `base_url` (OpenAI-compatible) and `model`;
- `key_env`, the **name** of the environment variable holding the key;
- `data_policy`, `priority` and `family`;
- optional `limits` (`rpm`, `rpd`, `tpm`), plus `timeout_seconds`, `json_mode` and `enabled`.

**Refused at load time:**
- literal key fields (`api_key`, `token`, `secret`, …): `LLM_LITERAL_KEY_REFUSED`;
- unknown fields;
- plain http to a remote host;
- credentials in the URL;
- a remote provider without `key_env`;
- duplicate names.

`rkb llm check-config` reports only whether each key variable **is set**, never its value.

## LL-D03 Data classes from rights

`data_class_for(A6, A8, training permitted)`:

| Data class | When | Providers allowed (`data_policy`) |
|---|---|---|
| none | A6 (local model processing) denied | none: no LLM at all |
| `LOCAL_ONLY` | A6 allowed, A8 (external AI) denied | `LOCAL` |
| `EXTERNAL_NO_TRAINING` | A8 allowed, but the licence forbids training use | `LOCAL`, `NO_TRAINING` |
| `EXTERNAL_ANY` | Synthetic, metadata-only or openly licensed (e.g. CC-BY) material | all, including `MAY_TRAIN` and `UNKNOWN` |

Free tiers often train on prompts. The operator records each provider's policy from its current terms, and `UNKNOWN` is treated like `MAY_TRAIN`. A provider whose policy is not allowed for a data class is never called, even when every allowed provider fails.

## LL-D04 Local data policy

`LOCAL` is accepted only for a loopback host (`127.0.0.1`, `::1`, `localhost`), for example Ollama or LM Studio. This is the default route for licensed full text.

## LL-D05 Rotation

The router walks the providers in priority order and skips:
- an unset key (`KEY_NOT_SET`);
- a rejected key (`KEY_REJECTED`, for the session);
- a provider cooling down after a 429 (`RATE_LIMITED`; Retry-After is honoured, default 60 s);
- an open breaker (`BREAKER_OPEN`);
- the per-minute or per-day limit (`MINUTE_LIMIT_REACHED`, `DAY_LIMIT_REACHED`);
- a prompt above `tpm` (`PROMPT_TOO_LARGE_FOR_TPM`).

It moves on after a rate limit, server error, timeout, network error or invalid answer. When nothing answers, the result is `EXHAUSTED` with the earliest `retry_after_seconds`, so batch jobs can be resumed later.

**Rotation happens across providers, not across accounts:** several accounts at one provider usually breach its terms.

## LL-D06 Circuit breaker

After `breaker_threshold` (3) consecutive failures, a provider is skipped for `breaker_seconds` (300 s). Rate limits use the cooldown instead, and do not count as breaker failures.

## LL-D07 Validation

`validate(text)` must return the parsed value or raise. The default `json_object` accepts exactly one JSON object: no surrounding prose and no repair. A failure counts as `INVALID_RESPONSE`. Task-specific validation (S08 schema, anchors, numbers) stays in the existing services.

## LL-D08 Two-provider consensus

`consensus()` asks two providers of **different families**:

| Result | When |
|---|---|
| `AGREED` | Their parsed answers are equal (or a custom `compare` holds) |
| `DISAGREED` | Both answers are kept; there is no vote |
| `INSUFFICIENT_PROVIDERS` | No second family answered |

For extraction, only AGREED answers become proposals for review. This reduces fabricated values at no cost on free tiers.

## LL-D09 Call log

The log is one JSON line per attempt: time, task, provider, model, data class, outcome, SHA-256 of the prompt and of the answer, and latency. It **never** holds the prompt, the answer or a key. It is written to a file outside the KB. A KB table needs an additive migration `0012` (planned for L2).

## LL-D10 Claude Desktop and Claude Code

- **Claude Desktop** reaches the KB through the existing S10 MCP server (`rkb ai mcp-serve`), behind an owner-approved profile (OAG-6). This has not yet been tested with the real app.
- **Claude Code** can use the same MCP server, or run `rkb` commands interactively.

Using a consumer subscription as an automated batch backend is **not** part of this design. Automated calls go through API keys under API terms; check current terms before doing otherwise.

## LL-D11 Roadmap

| Stage | Content | Network | Approval |
|---|---|---|---|
| L1 (done) | Config, router, simulator, CLI; offline tests | none | — |
| L2 | Local transport (Ollama / LM Studio, loopback only); migration 0012 for the call log; S08 extraction adapter on synthetic data; prompt-injection suite with a real model | loopback only | model choice |
| L3 | HTTPS transport for remote providers (stdlib, allowlisted in G5), with keys from environment variables; synthetic or CC-BY material only; per-provider evaluation | yes | OAG-6, A8, provider terms checked |
| L4 | S09 local embeddings; consensus extraction on owner-approved real papers (Linux sandbox) | as L3 | OAG-1/2/6 |
| L5 | S07 screening and S11 wording proposals | as L3 | after L2–L4 meet the owner's quality thresholds |

## LL-D12 Not done in L1

- No real call to any provider (local or remote).
- No KB schema change.
- No wiring into S07, S08, S09 or S11.
- No evaluation of real model quality.
