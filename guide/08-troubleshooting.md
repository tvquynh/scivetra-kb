# Troubleshooting

## 1. Python is not found

**Symptoms:**
- 🪟 *"The term 'py' is not recognized"*, or typing `python` opens the Microsoft Store.
- 🐧 *"command not found: python3"*, or the version is older than 3.11.

**🪟 Windows:**
1. Reinstall Python ([step 1](01-install-python.md)) and tick **Add python.exe to PATH**.
2. Close every PowerShell window and open a new one; restart the computer if needed.
3. Turn off the Store aliases: **Settings → Apps → Advanced app settings → App execution aliases**, then switch off **python.exe** and **python3.exe**.

**🐧 Linux:** install `python3` with your package manager ([step 1](01-install-python.md#-linux)). If your distribution only has 3.10 or older, install `python3.11` (or newer) as described there.

## 2. 🐧 "ensurepip is not available" / "Could not create the virtual environment"

Debian and Ubuntu split `venv` into a separate package. Run:

```bash
sudo apt install python3-venv
```

or `python3.11-venv`, matching your version. Then run `bash 1-install.sh` again.

## 3. 🪟 "running scripts is disabled on this system"

You ran a `.ps1` file yourself. Either use the `.cmd` files, or apply [the PowerShell setting](02-terminal-and-security.md#do-i-need-to-turn-off-powershell-security).

## 4. 🪟 "Windows protected your PC"

See [the terminal and security prompts](02-terminal-and-security.md).

## 5. The installer says the path has accented or special characters

The source code is in a folder whose path contains accents or other non-ASCII characters (for example inside *Documents* with a localized name). Move it to `C:\SciVetra\scivetra-kb` (🪟) or `~/SciVetra/scivetra-kb` (🐧).

## 6. "Could not download pypdf"

There is no Internet, or your network blocks PyPI (`pypi.org`, `files.pythonhosted.org`). Everything except PDF reading works; run the installer again when online.

- **"wrong SHA-256":** the download was not the genuine file and was deleted. Try again, or use another network.

## 7. `rkb` error codes

`rkb` is not recognized at all? You are not in the work window: open it ([4.1](04-first-steps.md)).

| Code | Meaning | What to do |
|---|---|---|
| `DATA_ROOT_REQUIRED` | `--data-root` is missing, or `$KB` / `KB` is not set in this window | Set the variables ([4.2](04-first-steps.md#42-name-your-knowledge-base-once-per-window)) |
| `KB_NOT_INITIALIZED` | The folder is not a KB | Check the name, or run `rkb init` |
| `WRITER_ACTIVE` | Another `rkb` command is writing to this KB | Wait for it, or close other `rkb` windows |
| `PROTECTED_DATA_ROOT` | `E:\ResearchKB` is reserved until the project owner approves it | Use a folder under `SciVetra/data` |
| `KB_UPGRADE_REQUIRED` | The KB was made by an older version | See [7.3](07-backup-restore-upgrade.md#73-update-scivetra-to-a-new-version) |
| `KB_CLIENT_TOO_OLD` | The KB was made by a newer version than the one installed | Update SciVetra |
| `LIVE_ACCESS_NOT_AUTHORIZED` | Network access is not enabled in this release | Expected; see [Limits](10-limits-and-safety.md) |
| `PARSER_NOT_INSTALLED` | No PDF parser | Run the installer again when online |
| `OUT_DIR_INVALID`, `EXPORT_FOLDER_EXISTS` | The output folder is not allowed, or already exists | Use a new, full path outside the KB folder |
| `HUMAN_TEXT_SOURCE_EXCERPT_REFUSED` | You pasted text copied from a PDF, and the rights do not allow storing quotes | Write it in your own words |
| `SELF_REVIEW_REFUSED` | Authors cannot review their own work | Ask another registered reviewer |
| `CLAIM_SPEC_INVALID` | The claim JSON file has an error | Compare it with the example in [6F](06-everyday-use.md#f-synthesis-draft-with-citation-audit); numbers must be in quotes, such as `"93.1"` |
| `EXPORT_RELEASE_REFUSED` | Rights, sources or evidence changed during the export | Run `rkb synth audit` again, then export again |
| `BACKUP_SOURCE_INCOMPLETE` | A stored file is missing, so the backup was refused | Run `rkb doctor --full`, then restore from your latest backup |

## 8. Still stuck?

Run the two commands below and send the output (a screenshot is fine) to whoever supports you. `doctor` shows counts and states only; it never shows the content of your papers.

```
rkb --version
rkb doctor --data-root "$KB"
```
