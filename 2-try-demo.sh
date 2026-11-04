#!/usr/bin/env bash
# SciVetra KB - demo on SAMPLE data (generated, not real) for macOS and Linux. Run:  bash 2-try-demo.sh
# Part A (about 1 minute): create a practice knowledge base (KB), check its health, back it up and verify the backup.
# Part B (5-10 minutes, needs the PDF parser): generate 32 simulated papers, run the whole pipeline, export a
#   citation-audited synthesis draft and open it.
# Options: --root=/other/folder   --part-a-only
set -u
ROOT="${SCIVETRA_ROOT:-$HOME/SciVetra}"
PART_A_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --part-a-only) PART_A_ONLY=1 ;;
    --root=*) ROOT="${arg#--root=}" ;;
  esac
done
REPO="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$ROOT/env/bin/python" ]; then VBIN="$ROOT/env/bin"; VPY="$VBIN/python"; else VBIN="$ROOT/env/Scripts"; VPY="$VBIN/python.exe"; fi
RKB="$VBIN/rkb"
WHL="$ROOT/packages/pypdf-6.12.1-py3-none-any.whl"
STAMP="$(date +%Y%m%d-%H%M%S)"

ok()   { printf '   \033[32mOK\033[0m    %s\n' "$1"; }
fail() { printf '\n   \033[31mERROR\033[0m %s\n   See guide/08-troubleshooting.md\n' "$1"; exit 1; }
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
field() { "$VPY" -c 'import json, sys; d = json.loads(sys.stdin.read() or "{}"); v = d
for k in sys.argv[1].split("."): v = v.get(k) if isinstance(v, dict) else None
print("" if v is None else v)' "$1"; }

[ -x "$RKB" ] || fail "Not installed yet. Run: bash 1-install.sh"

echo "=== SciVetra KB demo (sample data) ==="
printf '\n\033[36mPART A - Practice knowledge base\033[0m\n'
KB="$ROOT/data/demo-$STAMP"
[ "$("$RKB" init --data-root "$(native "$KB")" 2>/dev/null | field initialized)" = "True" ] || fail "Could not create the knowledge base."
ok "Created knowledge base: $KB"
[ "$("$RKB" doctor --data-root "$(native "$KB")" --full 2>/dev/null | field ok)" = "True" ] || fail "The health check reported a problem."
ok "Health check: no problems"
BK="$("$RKB" backup --data-root "$(native "$KB")" --target "$(native "$ROOT/backups")" 2>/dev/null | field backup_dir | tr -d '\r')"
[ -n "$BK" ] || fail "Backup failed."
ok "Backed up to: $BK"
[ "$("$RKB" verify-backup --backup "$BK" 2>/dev/null | field valid)" = "True" ] || fail "The backup is not valid."
ok "Backup verified: valid"

if [ "$PART_A_ONLY" = 1 ]; then printf '\nPart A done.\n'; exit 0; fi
if [ ! -f "$WHL" ]; then
  printf '\nSkipping part B: the PDF parser is not installed. Run bash 1-install.sh again when online.\n'
  exit 0
fi

printf '\n\033[36mPART B - Synthesis draft from 32 simulated papers (5-10 minutes, please wait...)\033[0m\n'
OUT="$ROOT/output/demo-draft-$STAMP"
LOG="$ROOT/output/demo-draft-$STAMP.log"
SCIVETRA_PYPDF_WHEEL="$(native "$WHL")" SCIVETRA_PYPDF_SITE="$(native "$WHL")" \
  "$VPY" -I -B "$(native "$REPO/tools/s11_evaluation.py")" --out-dir "$(native "$OUT")" > "$LOG" 2>&1 || fail "Part B did not complete. Log: $LOG"
SUM="$OUT/s11_evaluation_summary.json"
[ -f "$SUM" ] || fail "Part B did not complete. Log: $LOG"
"$VPY" - "$(native "$SUM")" <<'PY'
import json, sys
s = json.load(open(sys.argv[1], encoding="utf-8"))
print("   OK    %s claims from %s simulated papers" % (s["claims"], s["studies"]))
print("   OK    Agreement with the answer key: %.0f%%; wrongly supported claims: %s; citation traceability: %s"
      % (100 * s["metrics"]["agreement"], s["metrics"]["false_supported"], s["traceability"]))
PY
DRAFT="$(find "$OUT/run1/audited_example_draft" -name draft.md 2>/dev/null | head -n 1)"
if [ -n "$DRAFT" ]; then
  ok "Draft: $DRAFT"
  if command -v open >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then open -e "$DRAFT"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$DRAFT" >/dev/null 2>&1 &
  fi
fi
printf '\n\033[32m=== DEMO COMPLETE ===\033[0m\n'
echo "Results: $OUT"
echo "What the results mean: guide/05-demo.md"
