# Step 2 — The terminal and security prompts

A **terminal** is a window where you type commands:
- 🪟 **PowerShell** on Windows;
- 🍎 **Terminal** on macOS;
- 🐧 **Terminal** (or Konsole, GNOME Terminal…) on Linux.

## Using a terminal

| Action | 🪟 Windows | 🍎 macOS | 🐧 Linux |
|---|---|---|---|
| Open it | Windows key → type `powershell` → Enter | ⌘+Space → `terminal` → Enter | Ctrl+Alt+T, or search "Terminal" |
| Paste | Right-click or Ctrl+V | ⌘V | Ctrl+Shift+V |
| Run | Enter | Enter | Enter |
| Repeat the previous command | ↑ | ↑ | ↑ |
| Stop a running command | Ctrl+C | Ctrl+C | Ctrl+C |

A block with several lines can be pasted at once; press Enter after it.

## 🪟 Windows

### Do I need to "turn off PowerShell security"?

**Usually not.** The three double-click files (`1-INSTALL.cmd`, `2-TRY-DEMO.cmd` and `3-OPEN-WORK-WINDOW.cmd`) allow their script **for that single run only**. Your computer's setting stays unchanged.

You only need the setting below if you see **"running scripts is disabled on this system"**, or if you want to run `.ps1` files yourself. Open PowerShell normally (no administrator needed) and run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

If asked, type `Y` and press Enter.

- **What it does:** only your user account may run local scripts and downloaded scripts that you have unblocked. This is the level Microsoft recommends; it does **not** turn off security for the whole computer.
- **To check it:** run `Get-ExecutionPolicy -Scope CurrentUser`.
- **To undo it:** run the command below.

  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Undefined
  ```

**Never** set `Unrestricted` or `Bypass` for the whole machine. SciVetra does not need it.

### "Windows protected your PC"

Windows marks files downloaded from the Internet. Either:
- **before unzipping:** right-click the ZIP → **Properties** → tick **Unblock** → **OK**; or
- **when the blue box appears:** click **More info** → **Run anyway**.

Do this only for files downloaded from `github.com/tvquynh/scivetra-kb`.

## 🍎 macOS

- **Run scripts with `bash`**, for example `bash 1-install.sh`. You do not need to make files executable, and Gatekeeper does not block scripts run this way.
- **Quarantine warnings.** If macOS shows *"cannot be opened because it is from an unidentified developer"* for any file in the folder, remove the download quarantine from the SciVetra folder (only this folder):

  ```bash
  xattr -dr com.apple.quarantine ~/SciVetra/scivetra-kb
  ```

- **Python certificates.** If Python from python.org shows certificate errors while installing the PDF parser, double-click **Install Certificates.command** in *Applications → Python 3.x*.

## 🐧 Linux

- **Run scripts with `bash`**, for example `bash 1-install.sh`. No `chmod` and no `sudo` are needed for SciVetra itself.
- **Use `sudo` only** for installing Python packages from your distribution ([step 1](01-install-python.md)).
- **SELinux / AppArmor.** The default policies of the common distributions do not interfere with SciVetra.

## Next

[Step 3 — Download and install SciVetra](03-download-and-install.md)
