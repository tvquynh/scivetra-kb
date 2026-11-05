# First steps — work window, your first knowledge base, reading results

## 4.1 Open the work window

| | Do this | You get |
|---|---|---|
| 🪟 Windows | Double-click **`C:\SciVetra\scivetra-kb\3-OPEN-WORK-WINDOW.cmd`** | A PowerShell window with the prompt `(SciVetra) C:\SciVetra>` |
| 🐧 Linux | `bash ~/SciVetra/scivetra-kb/3-work-shell.sh` | A shell in `~/SciVetra`; type `exit` to leave |

In this window the **`rkb`** command works directly. Open it every time you work with SciVetra.

> **Prefer your own terminal?** Activate the environment yourself:
> - 🐧 `source ~/SciVetra/env/bin/activate`
> - 🪟 `C:\SciVetra\env\Scripts\Activate.ps1` (needs [the PowerShell setting](02-terminal-and-security.md#do-i-need-to-turn-off-powershell-security))

## 4.2 Name your knowledge base once per window

A **knowledge base (KB)** is one folder that holds everything for one project. Give each project its own KB, with a short name without spaces or accents, such as `project-1`.

Tell the window which KB you are working on. After that, **every command in this guide is the same on all systems**.

| | Paste this (once per window) |
|---|---|
| 🪟 Windows | `$KB = "C:\SciVetra\data\project-1"; $BACKUPS = "C:\SciVetra\backups"; $OUT = "C:\SciVetra\output"` |
| 🐧 Linux | `KB=~/SciVetra/data/project-1; BACKUPS=~/SciVetra/backups; OUT=~/SciVetra/output` |

## 4.3 Create the knowledge base

```
rkb init --data-root "$KB"
```

The result contains `"initialized":true`.

## 4.4 Check its health

```
rkb doctor --data-root "$KB"
```

## 4.5 Reading results

Every `rkb` command prints **one line of JSON**: pairs of `"name":value`. You only need a few of them:

| You see | It means |
|---|---|
| `"ok":true` | The command succeeded |
| `"ok":false,"error":"SOME_CODE"` | The command was refused; the code says why. Look it up in [Troubleshooting](08-troubleshooting.md#7-rkb-error-codes). |
| `"problems":[]` | No problems (empty list) |

**Making the output easier to read:**
- 🪟 Add `| ConvertFrom-Json | Format-List` to the end of a command:

  ```powershell
  rkb doctor --data-root "$KB" | ConvertFrom-Json | Format-List ok, problems, warnings, integrity_check
  ```

- 🐧 Use Python's JSON pretty-printer:

  ```bash
  rkb doctor --data-root "$KB" | python3 -m json.tool | head -40
  ```

## 4.6 Words you will see often

| Word | Meaning |
|---|---|
| `NON_AUTHORITATIVE` | A proposal or draft, not an official decision |
| `[UNVERIFIED]` | A claim that the current evidence cannot confirm |
| `[CONFLICTED]` | The same paper reports two different values for the same thing |
| `WITHHELD` | Hidden, because the current usage rights do not allow showing it |
| `STALE` | The source file changed or was damaged, so older evidence can no longer be trusted |
| `BLOCKED_RIGHTS` | Usage rights were withdrawn |

More in the [Glossary](09-glossary.md).

## 4.7 Three good habits

1. **Check health** before you start: `rkb doctor --data-root "$KB"`.
2. **Back up** before anything important ([step 8](07-backup-restore-upgrade.md)).
3. **Never** edit, delete or move files inside a KB folder by hand. Always use `rkb`.
