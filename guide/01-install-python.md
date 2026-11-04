# Step 1 — Install Python

SciVetra is written in **Python** and needs **Python 3.11 or newer** (tested with 3.11 and 3.14). You install Python once; SciVetra then keeps its own private copy of what it needs.

## 🪟 Windows

**1. Check whether Python is already installed.** Press the **Windows** key, type `powershell` and press **Enter**. In the blue or black window, paste this line and press **Enter**:

```powershell
py --version
```

- If you see `Python 3.11` or higher (3.12, 3.13, 3.14…), you are done: go to [step 3](03-download-and-install.md).
- Otherwise continue below.

**2. Download.** Go to **https://www.python.org/downloads/windows/** and download the **latest** release, choosing **Windows installer (64-bit)**.

**3. Install.** Open the downloaded file. On the first screen, tick:
- ☑ **Add python.exe to PATH** (important)
- ☑ **Use admin privileges when installing py.exe** (if shown)

Then click **Install Now** and wait for *Setup was successful*.

> If python.org only offers the **Python install manager**, install it, then run `py install 3.14` in PowerShell.

**4. Check.** **Close** all PowerShell windows, open a new one, and run `py --version` again.

## 🍎 macOS

**1. Check.** Open **Terminal** (press ⌘+Space, type `terminal`, press Enter) and run:

```bash
python3 --version
```

If it shows 3.11 or higher, you are done. Many Macs ship an older 3.9; that is too old.

**2. Download.** Go to **https://www.python.org/downloads/macos/** and download the **latest macOS 64-bit universal2 installer** (`.pkg`).

**3. Install.** Open the `.pkg` and click **Continue** through the steps.

When the installation finishes, a Finder window opens. Double-click **Install Certificates.command** in it.

**4. Check.** Close Terminal, open it again, and run `python3 --version`.

*Already use Homebrew?* Then `brew install python@3.13` works too.

## 🐧 Linux

Most current distributions already have a suitable Python. Check:

```bash
python3 --version
```

If it is 3.11 or higher, you only need the `venv` module.

| Distribution | Command |
|---|---|
| Ubuntu 24.04+, Debian 12+, Linux Mint 22+ | `sudo apt update && sudo apt install -y python3 python3-venv python3-pip` |
| Fedora 39+ | `sudo dnf install -y python3 python3-pip` |
| Arch / Manjaro | `sudo pacman -S --needed python python-pip` |
| openSUSE Tumbleweed | `sudo zypper install python3 python3-pip` |

**If your distribution's Python is older than 3.11** (for example Ubuntu 22.04 has 3.10):
- **Ubuntu 22.04:** run `sudo apt install python3.11 python3.11-venv`. The installer finds `python3.11` automatically.
- **Other distributions:** install a newer Python from your distribution's backports, or use the official installer from python.org.

`sudo` asks for **your** password. Nothing appears while you type it; that is normal.

## Next

[Step 2 — The terminal and security prompts](02-terminal-and-security.md), or go straight to [step 3](03-download-and-install.md).
