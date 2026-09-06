#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STATE=${SWE5_STATE_DIR:-"$HOME/.cache/pi-agent-swe5"}
SWE_REPO="$STATE/SWE-bench"
SWE_VENV="$STATE/swebench-venv"
mkdir -p "$STATE"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required on the SWE-5 server" >&2
  exit 2
fi
docker info >/dev/null

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -d "$SWE_REPO/.git" ]; then
  git clone --filter=blob:none https://github.com/SWE-bench/SWE-bench.git "$SWE_REPO"
else
  git -C "$SWE_REPO" fetch --depth=1 origin
  git -C "$SWE_REPO" reset --hard origin/HEAD
fi

uv venv --python 3.11 --clear "$SWE_VENV"
uv pip install --python "$SWE_VENV/bin/python" -e "$SWE_REPO" datasets huggingface_hub

uv sync --all-extras --python 3.11 --project "$ROOT"

UPSTREAM_DIR=$(python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
candidates=[]
for package in root.glob('benchmarks/agent_compare/**/package.json'):
    text=package.read_text(errors='ignore')
    if 'pi-coding-agent' in text:
        candidates.append(package.parent)
if not candidates:
    raise SystemExit('could not locate the existing TypeScript benchmark package.json')
print(sorted(candidates, key=lambda p: len(p.parts))[0])
PY
)
if [ -f "$UPSTREAM_DIR/package-lock.json" ]; then
  (cd "$UPSTREAM_DIR" && npm ci --ignore-scripts)
else
  (cd "$UPSTREAM_DIR" && npm install --ignore-scripts)
fi

python_revision=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || cat "$ROOT/benchmarks/swe5/PYTHON_GIT_SHA" 2>/dev/null || printf unknown)
cat > "$STATE/bootstrap.json" <<JSON
{
  "swebench_repo": "$(git -C "$SWE_REPO" rev-parse HEAD)",
  "python_repo": "$python_revision",
  "architecture": "$(uname -m)",
  "docker_server": "$(docker version --format '{{.Server.Version}}')"
}
JSON
touch "$STATE/bootstrap.done"
printf 'SWE5_BOOTSTRAP_OK\n'
