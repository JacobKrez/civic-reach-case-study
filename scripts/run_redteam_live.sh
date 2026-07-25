#!/usr/bin/env bash
# Live red-team runner that survives the worker session by writing a
# completion marker file when finished. Used by the W48 chunk so the
# supervisor can poll for the retained report instead of blocking on
# per-case LLM latency inside a single tool-call budget.
#
# Usage:
#   scripts/run_redteam_live.sh <fixtures> <report_json> <marker_file>
#
# Provider keys are loaded from .env via `set -a; source .env; set +a`
# because the .env file is .gitignored and the red-team harness reads
# MINIMAX_API_KEY / OPENROUTER_API_KEY directly.
set -euo pipefail

FIXTURES="${1:-redteam/attack_cases.jsonl}"
REPORT_JSON="${2:-redteam/reports/report_live.json}"
MARKER="${3:-logs/redteam_live.done}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p "$(dirname "$REPORT_JSON")" "$(dirname "$MARKER")"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/redteam_live_${RUN_STAMP}.log"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export GUARDRAIL_CLASSIFIER="${GUARDRAIL_CLASSIFIER:-true}"
export PYTHONUNBUFFERED=1

echo "[run_redteam_live] start fixtures=$FIXTURES report=$REPORT_JSON log=$LOG_FILE" >> "$MARKER.pre"

if python3 -m redteam.run_redteam \
    --fixtures "$FIXTURES" \
    --report "$REPORT_JSON" \
    --no-gate >> "$LOG_FILE" 2>&1; then
  STATUS=done
else
  STATUS=failed
fi

echo "[run_redteam_live] $STATUS fixtures=$FIXTURES report=$REPORT_JSON exit=$STATUS" >> "$MARKER.pre"
date -u +%Y-%m-%dT%H:%M:%SZ > "$MARKER"
echo "$STATUS" >> "$MARKER"