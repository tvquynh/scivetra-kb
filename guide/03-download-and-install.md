# Step 3 — Download and install SciVetra

## 3.1 Download

Pick **one** of the two ways.

**A. ZIP file (easiest).** Go to **https://github.com/tvquynh/scivetra-kb**, click the green **Code** button, then **Download ZIP**.

- 🪟 **Windows:** right-click the ZIP → **Properties** → tick **Unblock** → **OK** ([why](02-terminal-and-security.md#windows-protected-your-pc)).

**B. With git (if you know git).** This makes later updates a single `git pull`.

| | Command |
|---|---|
| 🪟 Windows | `git clone https://github.com/tvquynh/scivetra-kb.git C:\SciVetra\scivetra-kb` |
| 🍎🐧 macOS / Linux | `git clone https://github.com/tvquynh/scivetra-kb.git ~/SciVetra/scivetra-kb` |

## 3.2 Put it in the right place

The source code must be in a folder **without accents or spaces** in its path.

| | Folder |
|---|---|
| 🪟 Windows | `C:\SciVetra\scivetra-kb` |
| 🍎🐧 macOS / Linux | `~/SciVetra/scivetra-kb`, i.e. a folder `SciVetra` in your home folder |

**🪟 Windows, by mouse:**
1. Create the folder `C:\SciVetra`.
2. Right-click the ZIP → **Extract All…** → choose `C:\SciVetra` → **Extract**.
3. Rename the extracted folder `scivetra-kb-main` to `scivetra-kb`.

**🪟 Windows, by command:** paste into PowerShell. This assumes the ZIP is in *Downloads*.

```powershell
New-Item -ItemType Directory -Force C:\SciVetra | Out-Null
Unblock-File "$HOME\Downloads\scivetra-kb-main.zip"
Expand-Archive "$HOME\Downloads\scivetra-kb-main.zip" -DestinationPath C:\SciVetra -Force
Rename-Item C:\SciVetra\scivetra-kb-main scivetra-kb
dir C:\SciVetra\scivetra-kb
```

**🍎🐧 macOS / Linux, by command:** paste into Terminal. This assumes the ZIP is in *Downloads*.

```bash
mkdir -p ~/SciVetra
cd ~/SciVetra && unzip -q ~/Downloads/scivetra-kb-main.zip && mv scivetra-kb-main scivetra-kb
ls ~/SciVetra/scivetra-kb
```

**Check:** you must see `1-INSTALL.cmd`, `1-install.sh`, the `guide` folder and so on.

## 3.3 Install (automatic)

| | Do this |
|---|---|
| 🪟 Windows | Double-click **`C:\SciVetra\scivetra-kb\1-INSTALL.cmd`** |
| 🍎🐧 macOS / Linux | In Terminal, run `cd ~/SciVetra/scivetra-kb && bash 1-install.sh` |

The installer runs six steps:

| Step | What happens |
|---|---|
| 1/6 | Finds Python |
| 2/6 | Creates `data`, `backups`, `output` and `packages` next to the code |
| 3/6 | Creates the **virtual environment** `env`: a private Python for SciVetra that changes nothing else on your computer |
| 4/6 | Links the **`rkb`** command (SciVetra's main command) to the code |
| 5/6 | Downloads the **PDF parser** `pypdf 6.12.1` from the official Python Package Index (needs Internet), and checks its SHA-256 fingerprint to be sure it is the genuine release |
| 6/6 | Checks everything |

It worked when the last line says **`=== INSTALLATION COMPLETE ===`**.

- **No Internet:** step 5 shows **NOTE**. SciVetra still works except for reading PDFs. Run the installer again when you are online; running it again is always safe.
- **ERROR:** see [Troubleshooting](08-troubleshooting.md).

**Options:**

| Option | 🪟 Windows | 🍎🐧 macOS / Linux |
|---|---|---|
| Another working folder | `1-INSTALL.cmd -Root D:\Research\SciVetra` | `bash 1-install.sh --root=/path/to/folder` |
| Skip the PDF parser | `1-INSTALL.cmd -SkipPdf` | `bash 1-install.sh --skip-pdf` |

## 3.4 Install by hand (optional)

This is what the installer does, step by step. Run the blocks one at a time.

<details><summary>🪟 Windows (PowerShell)</summary>

```powershell
py -m venv C:\SciVetra\env
$site = & C:\SciVetra\env\Scripts\python.exe -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
Set-Content -Path "$site\scivetra-kb-src.pth" -Value "C:\SciVetra\scivetra-kb\src" -Encoding ascii
Set-Content -Path C:\SciVetra\env\Scripts\rkb.cmd -Value '@"%~dp0python.exe" -m scivetra_kb %*' -Encoding ascii
C:\SciVetra\env\Scripts\rkb.cmd --version
```

PDF parser (needs Internet):

```powershell
New-Item -ItemType Directory -Force C:\SciVetra\packages | Out-Null
C:\SciVetra\env\Scripts\python.exe -m pip download "pypdf==6.12.1" --no-deps --only-binary=:all: -d C:\SciVetra\packages
(Get-FileHash C:\SciVetra\packages\pypdf-6.12.1-py3-none-any.whl).Hash
```

The fingerprint must be `8FA2A2321CF16247ED848BD7C97F193A60C08670D04ABED5B0138327E51C43B0`. **If it is different, delete the file and do not install it.** If it matches:

```powershell
C:\SciVetra\env\Scripts\python.exe -m pip install --no-index --no-deps C:\SciVetra\packages\pypdf-6.12.1-py3-none-any.whl
C:\SciVetra\env\Scripts\rkb.cmd parse runtime-check
```

</details>

<details><summary>🍎🐧 macOS / Linux (Terminal)</summary>

```bash
python3 -m venv ~/SciVetra/env
SITE=$(~/SciVetra/env/bin/python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
echo ~/SciVetra/scivetra-kb/src > "$SITE/scivetra-kb-src.pth"
printf '#!/bin/sh\nexec "$(dirname "$0")/python" -m scivetra_kb "$@"\n' > ~/SciVetra/env/bin/rkb && chmod +x ~/SciVetra/env/bin/rkb
~/SciVetra/env/bin/rkb --version
```

PDF parser (needs Internet):

```bash
mkdir -p ~/SciVetra/packages
~/SciVetra/env/bin/python -m pip download "pypdf==6.12.1" --no-deps --only-binary=:all: -d ~/SciVetra/packages
shasum -a 256 ~/SciVetra/packages/pypdf-6.12.1-py3-none-any.whl      # Linux: sha256sum ...
```

The fingerprint must be `8fa2a2321cf16247ed848bd7c97f193a60c08670d04abed5b0138327e51c43b0`. **If it is different, delete the file and do not install it.** If it matches:

```bash
~/SciVetra/env/bin/python -m pip install --no-index --no-deps ~/SciVetra/packages/pypdf-6.12.1-py3-none-any.whl
~/SciVetra/env/bin/rkb parse runtime-check
```

</details>

**Expected results:**
- `rkb --version` prints `1.0.0rc1` (or newer).
- `runtime-check` reports `CONTENT_MATCHES_OFFICIAL_WHEEL`.

## 3.5 Uninstall

Delete the `env` folder; delete the `scivetra-kb` folder too if you want. Nothing else is installed anywhere on your computer.

**Careful:** `data` and `backups` hold **your** work. Delete them only if you are sure.

## Next

[Try the demo](05-demo.md)
