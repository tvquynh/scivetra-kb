# ADR LLM: free-provider LLM integration (stages L1 and L2 implemented; L3–L5 planned)

**Status:**
- L1 is implemented and offline-tested.
- L2 is implemented and tested against a scripted OpenAI-compatible server on 127.0.0.1. A run with a real local model (Ollama / LM Studio) is **NOT_RUN** on the developer machine: no model server is installed there.

Neither stage is part of the 1.0.0rc1 release artifacts.

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

## LL-D13 Stage L2: the loopback-only transport

The transport is a **separate, optional component**, `src/scivetra_llm_transport/`, shipped in the same wheel. The `scivetra_kb` package itself keeps the S02 invariant of no HTTP client code (STATIC-01, `tests/test_static.py`), and only `rkb llm ping` and `rkb llm extract-evidence` import the transport. Release gate G5 allowlists it as the only network code.

It sends an OpenAI-compatible `POST {base_url}/chat/completions` with:
- `temperature` 0 and `seed` 0;
- `max_tokens`;
- `response_format: json_object` when `json_mode` is set. Set `json_mode = false` for servers that reject it, such as some LM Studio versions.

**Hosts.** It contacts only a loopback IP literal. `localhost` is rewritten to `127.0.0.1`, so no DNS lookup happens. Https, private-network, public and credentialed URLs are refused before any connection (`REMOTE_NOT_ENABLED_STAGE_L2`).

**Safeguards.** Proxy variables are ignored, redirects are refused, and answers are capped at 2 MiB.

**Result mapping:**

| HTTP result | Outcome |
|---|---|
| 429 | `RATE_LIMITED`, with Retry-After |
| 401 / 403 | `AUTH_ERROR` |
| 5xx | `SERVER_ERROR` |
| Other 4xx, a non-chat-completion body or an oversized body | `INVALID_RESPONSE` |
| Timeout | `TIMEOUT` |
| Connection failure | `NETWORK_ERROR` |

**Ping.** `rkb llm ping --config F` asks every enabled LOCAL provider for `{"ok": true}`. Remote providers are listed as not tried.

## LL-D14 Stage L2: S08 extraction through the router

`rkb llm extract-evidence --data-root R --scope-id S --config F --operator O [--consensus] [--log FILE]` takes every member's latest S08 input pack.

**Before any model call**, the pack must still be current (S08 `_pack_current`: identity, eligibility, rights A2 / A6, artifact).

**Prompt.** It is S08's own template plus the UNTRUSTED pack (`EvidenceService.render`), unchanged.

**Data class.** S08 scopes allow `LOCAL_PROCESS` models only (CHECK in migration 0008), so the data class is always `LOCAL_ONLY`.

**Import.** The answer goes through `EvidenceService.import_extraction`. Strict schema validation records the run as `OPERATOR_IMPORT` / `OPERATOR_DECLARED_UNVERIFIED`, with the router's provider and model as the declared model and the model version `scivetra-llm-router/L2`. Drafts are `NON_AUTHORITATIVE` and `HUMAN_REQUIRED`. Structural verification and human review are unchanged.

**Consensus.** With `--consensus`, two local models of different families must return the same records (class, status, fields and anchor positions). DISAGREED or INSUFFICIENT imports nothing.

**Tested misbehaviour:**

| Model behaviour | Result |
|---|---|
| Fabricated quotes | Fail verification |
| Schema violations | `SCHEMA_INVALID` run with no drafts |
| An injected value | `UNSUPPORTED` at verification |
| Changed rights | `SCOPE_DENIED` before any call |
| Model server down | `NO_MODEL_ANSWER` |

**Decision change from L1.** Migration 0012 is **not** needed. The provenance of each model answer is already recorded in the KB by S08 (`ev_extraction_run`: origin, declared model, raw response SHA-256, template SHA-256, pack SHA-256). The router's attempt log stays a JSON-lines file outside the KB (`--log`). Schema 11 is unchanged, so no accepted test or migration is touched.

## LL-D11 Roadmap

| Stage | Content | Network | Approval |
|---|---|---|---|
| L1 (done) | Config, router, simulator, CLI; offline tests | none | — |
| L2 (done; real-model run NOT_RUN) | Loopback transport, `rkb llm ping`, S08 extraction with optional consensus; tested against a scripted local server; migration not needed (LL-D14) | loopback only | Model choice; a real run once Ollama or LM Studio is installed |
| L3 | HTTPS transport for remote providers (stdlib, allowlisted in G5), with keys from environment variables; synthetic or CC-BY material only; per-provider evaluation | yes | OAG-6, A8, provider terms checked |
| L4 | S09 local embeddings; consensus extraction on owner-approved real papers (Linux sandbox) | as L3 | OAG-1/2/6 |
| L5 | S07 screening and S11 wording proposals | as L3 | after L2–L4 meet the owner's quality thresholds |

## LL-D12 Not done (after L2)

- No call to a remote provider.
- No real local model run on the developer machine.
- No KB schema change.
- No wiring into S07, S09 or S11.
- No evaluation of real model quality.
