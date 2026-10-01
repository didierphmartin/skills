#!/bin/sh
# Dev-only lab gate: every test spec × both clouds → create.py → terraform init (shared plugin cache, no backend)
# → validate → fmt -check → tflint (errors fail; warnings are printed). checkov runs only with --checkov
# (advisory: it lags azurerm 5). Needs the lab (TF_LAB, default ~/Documents/synergyAI/terraform-lab); no cloud
# credentials, never plan/apply. Each project's .terraform holds only symlinks into the cache and is deleted.
# Usage: sh dev/validate_all.sh [--checkov] [--cloud aws|azure] [spec.json …]   (default: tests/fixtures/*.json except invalid-*;
# a spec named azure-* or aws-* runs on that cloud only)
set -u
LAB=${TF_LAB:-$HOME/Documents/synergyAI/terraform-lab}
SKILL=$(cd "$(dirname "$0")/.." && pwd)
CHECKOV=0
CLOUDS="aws azure"
while [ $# -gt 0 ]; do
  case $1 in
    --checkov) CHECKOV=1; shift ;;
    --cloud) CLOUDS=$2; shift 2 ;;
    *) break ;;
  esac
done
[ -x "$LAB/bin/terraform" ] || { echo "lab not found: $LAB" >&2; exit 2; }
export TF_PLUGIN_CACHE_DIR="$LAB/plugin-cache" TFLINT_PLUGIN_DIR="$LAB/tflint-plugins" CHECKPOINT_DISABLE=1
FREE_MB=$(df -m "$HOME" | awk 'NR==2 {print $4}')
[ "$FREE_MB" -ge 400 ] || { echo "only ${FREE_MB} MB free on disk: stopping (need 400)" >&2; exit 2; }
if [ $# -eq 0 ]; then set -- $(ls "$SKILL"/tests/fixtures/*.json | grep -v '/invalid-'); fi
WORK=$(mktemp -d)
FAIL=0
for SPEC in "$@"; do
  for CLOUD in $CLOUDS; do
    NAME=$(basename "$SPEC" .json)
    case $NAME in azure-*) [ "$CLOUD" = azure ] || continue ;; aws-*) [ "$CLOUD" = aws ] || continue ;; esac
    OUT="$WORK/$CLOUD"
    if ! python3 "$SKILL/scripts/create.py" -i "$SPEC" --cloud "$CLOUD" --out "$OUT" > "$WORK/report.txt" 2>&1; then
      echo "FAIL $NAME $CLOUD create.py"; cat "$WORK/report.txt"; FAIL=1; continue
    fi
    DIR=$(sed -n 's/^project: //p' "$WORK/report.txt")
    case $CLOUD in
      aws) PLUGIN='plugin "aws" {
  enabled = true
  version = "0.49.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}' ;;
      azure) PLUGIN='plugin "azurerm" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}' ;;
    esac
    printf '%s\n' "$PLUGIN" > "$DIR/.tflint.hcl"
    STEP=init
    if (cd "$DIR" && "$LAB/bin/terraform" init -backend=false -input=false -no-color > "$WORK/log.txt" 2>&1) \
      && STEP=validate && (cd "$DIR" && "$LAB/bin/terraform" validate -no-color > "$WORK/log.txt" 2>&1) \
      && STEP=fmt && (cd "$DIR" && "$LAB/bin/terraform" fmt -check -diff -no-color > "$WORK/log.txt" 2>&1) \
      && STEP=tflint && (cd "$DIR" && "$LAB/bin/tflint" --format compact --minimum-failure-severity=error > "$WORK/log.txt" 2>&1); then
      WARN=$(grep -c ': Warning - ' "$WORK/log.txt")
      echo "PASS $NAME $CLOUD (validate, fmt, tflint: 0 errors, $WARN warnings)"
      if [ "$CHECKOV" = 1 ] && [ -x "$LAB/checkov-venv/bin/checkov" ]; then
        "$LAB/checkov-venv/bin/checkov" -d "$DIR" --framework terraform --compact --quiet --skip-download \
          | grep -E '^(Passed|Failed) checks' | sed "s/^/  checkov (advisory) $NAME $CLOUD: /"
      fi
    else
      echo "FAIL $NAME $CLOUD $STEP"; cat "$WORK/log.txt"; FAIL=1
    fi
    rm -rf "${DIR:?}/.terraform"
  done
done
rm -rf "${WORK:?}"
exit $FAIL
