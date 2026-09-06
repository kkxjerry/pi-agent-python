#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STATE=${SWE5_STATE_DIR:-"$HOME/.cache/pi-agent-swe5"}
PYTHON="$STATE/swebench-venv/bin/python"
RUN_ID=${SWE5_RUN_ID:-"swe5-qwen-plus-$(date -u +%Y%m%d-%H%M%S)"}
RESULTS="$ROOT/.swe5/results/$RUN_ID"
STATUS="$ROOT/.swe5/driver-status.json"
LOCK="$ROOT/.swe5/driver.lock"
mkdir -p "$ROOT/.swe5" "$ROOT/.swe5/results"

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "another SWE-5 driver is already running" >&2
  exit 9
fi

stage=bootstrap
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
write_status() {
  local state=$1
  local message=${2:-}
  "$PYTHON" - "$STATUS" "$state" "$stage" "$RUN_ID" "$started" "$message" <<'PY'
import json,sys,time
from pathlib import Path
path=Path(sys.argv[1])
path.write_text(json.dumps({
  "state":sys.argv[2],
  "stage":sys.argv[3],
  "runId":sys.argv[4],
  "startedAt":sys.argv[5],
  "updatedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
  "message":sys.argv[6],
},indent=2,sort_keys=True)+"\n")
PY
}
failed() {
  status=$?
  write_status failed "exit=$status"
  exit "$status"
}
trap failed ERR

write_status running
stage=bootstrap
if [ ! -f "$STATE/bootstrap.done" ]; then
  bash "$ROOT/benchmarks/swe5/server_bootstrap.sh"
fi
write_status running

stage=selection
if [ ! -s "$ROOT/benchmarks/swe5/swe5.json" ]; then
  "$PYTHON" "$ROOT/benchmarks/swe5/select_verified.py" \
    --output "$ROOT/benchmarks/swe5/swe5.json" \
    --candidates-output "$ROOT/benchmarks/swe5/selection-candidates.json"
fi
write_status running

stage=preflight
preflight_valid=$(
  "$PYTHON" - "$ROOT/.swe5/preflight/preflight.json" <<'PY'
import json,sys
from pathlib import Path
path=Path(sys.argv[1])
try:
    value=json.loads(path.read_text())
except Exception:
    print("no")
else:
    print("yes" if value.get("valid") else "no")
PY
)
if [ "$preflight_valid" != yes ]; then
  rm -rf "$ROOT/.swe5/preflight"
  mkdir -p "$ROOT/.swe5/preflight"
  "$PYTHON" "$ROOT/benchmarks/swe5/preflight.py" \
    --manifest "$ROOT/benchmarks/swe5/swe5.json" \
    --workdir "$ROOT/.swe5/preflight" \
    --python "$PYTHON" \
    --max-workers 2
fi
write_status running

stage=agent_runs
if [ ! -f "$RESULTS/agent-runs.done" ]; then
  "$PYTHON" "$ROOT/benchmarks/swe5/run_swe5.py" \
    --manifest "$ROOT/benchmarks/swe5/swe5.json" \
    --output "$ROOT/.swe5/results" \
    --run-id "$RUN_ID" \
    --model qwen-plus \
    --timeout 900 \
    --max-model-calls 15 \
    --max-tool-calls 60 \
    --max-input-tokens 250000 \
    --max-output-tokens 20000
fi
write_status running

stage=grading
grades_valid=$(
  "$PYTHON" - "$RESULTS/grades.json" <<'PY'
import json,sys
from pathlib import Path
try:
    value=json.loads(Path(sys.argv[1]).read_text())
    systems=value["systems"]
except Exception:
    print("no")
else:
    print("yes" if all(item.get("unknownCount") == 0 for item in systems.values()) else "no")
PY
)
if [ "$grades_valid" != yes ]; then
  rm -rf "$RESULTS/grader"
  "$PYTHON" "$ROOT/benchmarks/swe5/grade_swe5.py" \
    --results "$RESULTS" \
    --python "$PYTHON" \
    --workdir "$RESULTS/grader" \
    --max-workers 2
fi
write_status running

stage=report
"$PYTHON" "$ROOT/benchmarks/swe5/report_swe5.py" --results "$RESULTS"
write_status done "$RESULTS"
printf '%s\n' "$RESULTS"
