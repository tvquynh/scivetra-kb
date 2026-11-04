#!/usr/bin/env bash
# SciVetra KB - open a work shell (macOS and Linux): the "rkb" command is ready and the current folder is ~/SciVetra.
# Run:  bash 3-work-shell.sh      (type "exit" to leave)
set -u
ROOT="${SCIVETRA_ROOT:-$HOME/SciVetra}"
for arg in "$@"; do case "$arg" in --root=*) ROOT="${arg#--root=}" ;; esac; done
if [ -x "$ROOT/env/bin/rkb" ]; then VBIN="$ROOT/env/bin"; elif [ -x "$ROOT/env/Scripts/rkb" ]; then VBIN="$ROOT/env/Scripts"
else echo "Not installed yet. Run: bash 1-install.sh"; exit 1; fi
export PATH="$VBIN:$PATH"
export VIRTUAL_ENV="$ROOT/env"
cd "$ROOT" || exit 1
echo "=== SciVetra KB work shell ==="
echo "rkb $(rkb --version)"
echo "Current folder: $ROOT   (data, backups, output)"
echo "Try:  rkb doctor --data-root ~/SciVetra/data/<KB-NAME>"
echo "Commands: guide/06-everyday-use.md      Leave this shell: exit"
exec "${SHELL:-/bin/bash}" -i
