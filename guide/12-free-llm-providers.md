# Free LLM providers — local model and provider list (stages L1–L2)

SciVetra is getting LLM support that can **rotate between several free services**: when one runs out of its free quota, the next one is used.

**Status:** SciVetra can check your provider list, simulate the rotation, and (**stage L2**) use a **model running on your own computer** (Ollama or LM Studio) to propose evidence records. Online services are not called yet; that is stage L3, after you approve it ([design](../docs/ADR_LLM.md)).

## 1. Three things to know first

| | |
|---|---|
| **Free tiers often train on your prompts** | That is the price of "free". Send them only synthetic data, metadata or openly licensed papers (e.g. CC-BY). Copyrighted full text should go to a model **on your own computer** (Ollama or LM Studio). SciVetra enforces this from each paper's usage rights. |
| **Use several providers, but one account each** | Several accounts at the same provider usually breaks its terms. SciVetra rotates *between providers*. |
| **Keys never go in files** | You put each key in an environment variable; the config only names that variable. |

## 2. Make your provider list

1. **Copy the example** to your SciVetra folder:
   - 🪟 `copy C:\SciVetra\scivetra-kb\config\llm-providers.example.toml C:\SciVetra\llm-providers.toml`
   - 🍎🐧 `cp ~/SciVetra/scivetra-kb/config/llm-providers.example.toml ~/SciVetra/llm-providers.toml`
2. **Open it** (🪟 `notepad C:\SciVetra\llm-providers.toml`, 🍎 `open -e ~/SciVetra/llm-providers.toml`, 🐧 `nano ~/SciVetra/llm-providers.toml`). For each service, check:
   - `model`: a model your account can use;
   - `data_policy`: what the provider's terms say. Use `NO_TRAINING` only if they clearly say they do not train on your prompts, otherwise `MAY_TRAIN`. If unsure, keep `UNKNOWN`.
   - `priority`: 1 is tried first;
   - `limits`: the free-tier limits (requests per minute `rpm`, per day `rpd`).
3. **Delete or set `enabled = false`** for services you have not registered.

## 3. Put your keys in environment variables

Register on each service's website yourself and create an API key there. Then store it, replacing `<KEY>` with your key.

- 🪟 **Windows.** Run this once per key, in PowerShell; it is saved for your user account. Then open a **new** window.

  ```powershell
  [Environment]::SetEnvironmentVariable("GROQ_API_KEY", "<KEY>", "User")
  ```

- 🍎🐧 **macOS / Linux.** Add the key to your shell profile, then open a new terminal. Replace `~/.zshrc` with `~/.bashrc` if you use bash.

  ```bash
  echo 'export GROQ_API_KEY="<KEY>"' >> ~/.zshrc
  ```

Never paste keys into the config file, into a KB, into a chat or into a screenshot.

## 4. Check the list

In the work window:

```
rkb llm check-config --config C:\SciVetra\llm-providers.toml
```

On macOS / Linux use `--config ~/SciVetra/llm-providers.toml`. For each provider you see:
- `key_present: true` or `false`: whether the variable is set. The key itself is never shown.
- `data_classes`: which kinds of material it may receive.

**Which providers are used for a kind of material:**

```
rkb llm plan --config C:\SciVetra\llm-providers.toml --data-class EXTERNAL_NO_TRAINING
```

| Data class | Meaning |
|---|---|
| `LOCAL_ONLY` | Only a model on your computer |
| `EXTERNAL_NO_TRAINING` | Online services that do not train on prompts |
| `EXTERNAL_ANY` | Any service; only for synthetic, metadata-only or openly licensed material |

## 5. Watch the rotation (simulation, no Internet)

```
rkb llm simulate --config C:\SciVetra\llm-providers.toml --scenario rotation
rkb llm simulate --config C:\SciVetra\llm-providers.toml --scenario breaker
rkb llm simulate --config C:\SciVetra\llm-providers.toml --scenario consensus
```

| Scenario | What it shows |
|---|---|
| `rotation` | Provider 1 is "out of quota" and provider 2 "fails", so provider 3 answers. On the next request, provider 1 is still cooling down. |
| `breaker` | After 3 failures, a provider is paused for 5 minutes. |
| `consensus` | Two different services are asked the same question. **AGREED** when they match; **DISAGREED** keeps both answers for a human to decide. |

## 6. Run a model on your own computer (stage L2)

A local model is free, works without Internet and keeps your papers on your computer. It is the right choice for copyrighted full text. You need about **16 GB RAM** for a 7–14B model. A graphics card makes it much faster; without one it works, slowly.

**1. Install Ollama** from **https://ollama.com/download**:
- 🪟 Windows: run the installer;
- 🍎 macOS: open the downloaded app;
- 🐧 Linux: follow the install command on that page.

**2. Download a model** (several GB, once). In a terminal:

```
ollama pull qwen3:8b
```

Larger models are better but slower, for example `qwen3:14b`. Ollama keeps running in the background after installation.

**3. Point SciVetra at it.** In your `llm-providers.toml`, the `local-ollama` entry must name the model you pulled, for example `model = "qwen3:8b"`.

*Using LM Studio instead?* Start its local server, then use `base_url = "http://127.0.0.1:1234/v1"`, its model name and `json_mode = false`.

**4. Test it:**

```
rkb llm ping --config C:\SciVetra\llm-providers.toml
```

On macOS / Linux use `--config ~/SciVetra/llm-providers.toml`. `"status": "OK"` means SciVetra can talk to your local model. If not, the output has a hint.

**5. Let the model propose evidence** for an S08 evidence scope ([everyday use, section E](06-everyday-use.md#e-eligibility-evidence-and-search)):

```
rkb llm extract-evidence --data-root "$KB" --scope-id <EVIDENCE-SCOPE-ID> --config C:\SciVetra\llm-providers.toml --operator <YOUR-NAME>
```

- **What happens:** the model's answers become *proposals*. SciVetra checks every quote against the PDF and every number against its quote. Reviewers then verify and accept them exactly as before (`rkb evidence verify`, `rkb evidence review`). A made-up quote or number fails verification.
- **Higher confidence:** if you have two different local models, add `--consensus`. Only records on which both models agree are proposed.
- **Rights:** only papers whose rights allow local processing are sent, and only to models on your computer.

## 7. Claude Desktop and Claude Code

You can already connect **Claude Desktop** to your KB in read-only mode through SciVetra's MCP server, with an approval profile you create (`docs/S10_CLAUDE_DESKTOP_GUIDE.md`). **Claude Code** can use the same server, or run `rkb` commands for you.

Your Claude subscription is meant for interactive use. SciVetra will not use it as an automatic background engine; automated calls use API keys.
