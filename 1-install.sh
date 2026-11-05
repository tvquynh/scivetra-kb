#!/usr/bin/env bash
# SciVetra KB - automatic installation (Linux). Run:  bash 1-install.sh
# What it does:
#   1. Finds Python 3.11 or newer.
#   2. Creates the working folders (default ~/SciVetra): data, backups, output, packages.
#   3. Creates a private Python virtual environment (~/SciVetra/env); the system Python is not changed.
#   4. Links the "rkb" command to the source code in this folder (no build tools needed).
#   5. Downloads the official pypdf 6.12.1 PDF parser and checks its SHA-256 (needs Internet; optional).
#   6. Checks everything: rkb --version, rkb parse runtime-check.
# Options: --root=/other/folder   --skip-pdf
# It needs no administrator rights, changes no system setting and sends none of your data anywhere.
set -u
ROOT="${SCIVETRA_ROOT:-$HOME/SciVetra}"
SKIP_PDF=0
for arg in "$@"; do
  case "$arg" in
    --skip-pdf) SKIP_PDF=1 ;;
    --root=*) ROOT="${arg#--root=}" ;;
  esac
done
PYPDF_SHA="8fa2a2321cf16247ed848bd7c97f193a60c08670d04abed5b0138327e51c43b0"
PYPDF_FILE="pypdf-6.12.1-py3-none-any.whl"
REPO="$(cd "$(dirname "$0")" && pwd)"

step() { printf '\n\033[36m[%s/6] %s\033[0m\n' "$1" "$2"; }
ok()   { printf '   \033[32mOK\033[0m    %s\n' "$1"; }
note() { printf '   \033[33mNOTE\033[0m  %s\n' "$1"; }
fail() { printf '\n   \033[31mERROR\033[0m %s\n   See guide/08-troubleshooting.md\n' "$1"; exit 1; }
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

echo "=== SciVetra KB installation ==="
echo "Source code   : $REPO"
echo "Working folder: $ROOT"
if printf '%s%s' "$REPO" "$ROOT" | LC_ALL=C grep -q '[^ -~]'; then
  fail "The path contains accented or special characters. Put the source code in ~/SciVetra/scivetra-kb (guide/03)."
fi

step 1 "Find Python 3.11 or newer"
PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if out="$("$cand" -c 'import sys; print("OK" if sys.version_info[:2] >= (3, 11) else "OLD", sys.version.split()[0])' 2>/dev/null)"; then
      case "$out" in OK*) PY="$cand"; ok "Found Python ${out#OK } ($cand)"; break ;; esac
    fi
  fi
done
[ -n "$PY" ] || fail "Python 3.11 or newer was not found. Install it (guide/01-install-python.md), then run this script again."

step 2 "Create the working folders"
mkdir -p "$ROOT/data" "$ROOT/backups" "$ROOT/output" "$ROOT/packages" || fail "Could not create $ROOT"
ok "$ROOT (data, backups, output, packages)"

step 3 "Create the Python virtual environment (env)"
VENV="$ROOT/env"
if [ -x "$VENV/bin/python" ]; then VBIN="$VENV/bin"; VPY="$VBIN/python"
elif [ -x "$VENV/Scripts/python.exe" ]; then VBIN="$VENV/Scripts"; VPY="$VBIN/python.exe"
else
  "$PY" -m venv "$VENV" || fail "Could not create the virtual environment. On Debian/Ubuntu run: sudo apt install python3-venv  (then run this script again)."
  if [ -x "$VENV/bin/python" ]; then VBIN="$VENV/bin"; VPY="$VBIN/python"; else VBIN="$VENV/Scripts"; VPY="$VBIN/python.exe"; fi
  [ -x "$VPY" ] || fail "Could not create the virtual environment."
fi
ok "$VENV"

step 4 "Link the rkb command to the source code"
SITE="$("$VPY" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])" | tr -d '\r')"
native "$REPO/src" > "$SITE/scivetra-kb-src.pth" || fail "Could not write to $SITE"
printf '%s\n' '#!/bin/sh' 'here="$(cd "$(dirname "$0")" && pwd)"' 'if [ -x "$here/python" ]; then exec "$here/python" -m scivetra_kb "$@"; else exec "$here/python.exe" -m scivetra_kb "$@"; fi' > "$VBIN/rkb"
chmod +x "$VBIN/rkb"
VER="$("$VBIN/rkb" --version 2>/dev/null | tr -d '\r')" || fail "The rkb command does not run."
ok "rkb $VER"

step 5 "PDF parser pypdf 6.12.1 (optional)"
WHL="$ROOT/packages/$PYPDF_FILE"
if [ "$SKIP_PDF" = 1 ]; then
  note "Skipped (--skip-pdf). PDF reading (rkb parse) and part B of the demo will not work."
else
  if [ ! -f "$WHL" ]; then
    echo "   Downloading pypdf 6.12.1 from PyPI (needs Internet)..."
    "$VPY" -m pip download "pypdf==6.12.1" --no-deps --only-binary=:all: -d "$(native "$ROOT/packages")" --disable-pip-version-check -q
  fi
  if [ ! -f "$WHL" ]; then
    note "Could not download pypdf (no Internet?). Everything except PDF reading works. Run this script again when online."
  else
    HASH="$("$VPY" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$(native "$WHL")" | tr -d '\r')"
    if [ "$HASH" != "$PYPDF_SHA" ]; then
      rm -f "$WHL"
      fail "The downloaded pypdf file is not the official release (wrong SHA-256) and was deleted. Please try again."
    fi
    "$VPY" -m pip install --no-index --no-deps --disable-pip-version-check -q "$(native "$WHL")" || fail "Could not install pypdf."
    ok "pypdf 6.12.1 (official release, SHA-256 verified)"
  fi
fi

step 6 "Final check"
RT="$(SCIVETRA_PYPDF_SITE= "$VBIN/rkb" parse runtime-check 2>/dev/null)"
case "$RT" in
  *CONTENT_MATCHES_OFFICIAL_WHEEL*) ok "PDF parser ready" ;;
  *PARSER_NOT_INSTALLED*) note "No PDF parser yet (everything else works)" ;;
  *) note "PDF parser check: $RT" ;;
esac

printf '\n\033[32m=== INSTALLATION COMPLETE ===\033[0m\n'
echo "Next: bash 2-try-demo.sh   (try SciVetra on sample data)"
echo "Everyday work: bash 3-work-shell.sh"
