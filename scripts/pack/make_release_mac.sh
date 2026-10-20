#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RELEASE_DIR="${1:-$ROOT/dist/math-platform-mac-offline}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
API_BUILD_ROOT="$ROOT/.tmp/mac-api-dist"
API_WORK_ROOT="$ROOT/.tmp/mac-pyinstaller-work"
API_SPEC_ROOT="$ROOT/.tmp/mac-pyinstaller-spec"
VENV_DIR="$ROOT/.tmp/mac-pyinstaller-venv"

if [[ -z "$NODE_BIN" || ! -x "$NODE_BIN" ]]; then
  echo "Build machine needs Node.js 20+ on PATH. The generated release will bundle this node binary."
  exit 1
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Build machine needs Python 3.11+ on PATH. The generated release will not need Python."
  exit 1
fi

rm -rf "$RELEASE_DIR" "$API_BUILD_ROOT" "$API_WORK_ROOT" "$API_SPEC_ROOT"
mkdir -p "$RELEASE_DIR/api" "$RELEASE_DIR/web" "$RELEASE_DIR/data" "$RELEASE_DIR/logs" "$RELEASE_DIR/backups" "$RELEASE_DIR/tools"
mkdir -p "$ROOT/.tmp"

"$PYTHON_BIN" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$ROOT/apps/api/requirements.txt" pyinstaller

EXCLUDED_MODULES=(
  IPython PIL cv2 dask datasets distributed huggingface_hub jupyter langchain matplotlib
  notebook numba numpy onnx onnxruntime openai pandas polars pyarrow pygame scipy sklearn
  spacy sphinx tensorboard tensorflow thinc torch torchaudio torchvision transformers wandb xarray
)
PYI_ARGS=(
  --noconfirm
  --clean
  --onedir
  --name math-api
  --distpath "$API_BUILD_ROOT"
  --workpath "$API_WORK_ROOT"
  --specpath "$API_SPEC_ROOT"
  --paths "$ROOT/apps/api"
  --hidden-import app.main
  --collect-submodules app
)
for module_name in "${EXCLUDED_MODULES[@]}"; do
  PYI_ARGS+=(--exclude-module "$module_name")
done
PYI_ARGS+=("$ROOT/apps/api/packaged_api.py")
python -m PyInstaller "${PYI_ARGS[@]}"

if [[ ! -x "$API_BUILD_ROOT/math-api/math-api" ]]; then
  echo "PyInstaller did not create $API_BUILD_ROOT/math-api/math-api"
  exit 1
fi
cp -R "$API_BUILD_ROOT/math-api/"* "$RELEASE_DIR/api/"

pushd "$ROOT/apps/web" >/dev/null
export NEXT_PUBLIC_API_BASE="http://127.0.0.1:8001"
export NEXT_IGNORE_INCORRECT_LOCKFILE="1"
npm install
npm run build
popd >/dev/null

WEB_STANDALONE="$ROOT/apps/web/.next/standalone"
WEB_STATIC="$ROOT/apps/web/.next/static"
if [[ ! -f "$WEB_STANDALONE/server.js" ]]; then
  echo "Next standalone server.js not found at $WEB_STANDALONE/server.js"
  exit 1
fi
cp -R "$WEB_STANDALONE/"* "$RELEASE_DIR/web/"
mkdir -p "$RELEASE_DIR/web/.next/static"
cp -R "$WEB_STATIC/"* "$RELEASE_DIR/web/.next/static/"
if [[ -d "$ROOT/apps/web/public" ]]; then
  cp -R "$ROOT/apps/web/public" "$RELEASE_DIR/web/public"
fi
cp "$NODE_BIN" "$RELEASE_DIR/web/node"
chmod +x "$RELEASE_DIR/web/node"

if [[ -f "$ROOT/dist/math-platform/data/math_platform_dev.db" ]]; then
  cp "$ROOT/dist/math-platform/data/math_platform_dev.db" "$RELEASE_DIR/data/math_platform_dev.db"
elif [[ -f "$ROOT/math_platform_dev.db" ]]; then
  cp "$ROOT/math_platform_dev.db" "$RELEASE_DIR/data/math_platform_dev.db"
fi

if [[ -f "$ROOT/dist/math-platform/.env" ]]; then
  cp "$ROOT/dist/math-platform/.env" "$RELEASE_DIR/.env"
elif [[ -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env" "$RELEASE_DIR/.env"
else
  cat > "$RELEASE_DIR/.env" <<'EOF'
APP_NAME=Math Platform
DATABASE_URL=sqlite:///./data/math_platform_dev.db
QUESTION_DATA_DIR=question_data
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
LIAOBOTS_BASE_URL=https://ai.liaobots2.work/v1
LIAOBOTS_API_KEY=
MODEL_PROVIDER=openai_compatible
DEFAULT_MODEL=gpt-5.5
API_REQUEST_LOG_PATH=logs/api.jsonl
EOF
fi
python - "$RELEASE_DIR/.env" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
lines = []
seen_db = False
seen_key = False
for raw in path.read_text(encoding='utf-8').splitlines():
    if raw.startswith('DATABASE_URL='):
        lines.append('DATABASE_URL=sqlite:///./data/math_platform_dev.db')
        seen_db = True
    elif raw.startswith('LIAOBOTS_API_KEY='):
        lines.append('LIAOBOTS_API_KEY=')
        seen_key = True
    else:
        lines.append(raw)
if not seen_db:
    lines.append('DATABASE_URL=sqlite:///./data/math_platform_dev.db')
if not seen_key:
    lines.append('LIAOBOTS_API_KEY=')
path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
PY

cat > "$RELEASE_DIR/START_HERE.command" <<'EOF'
#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h}"
cd "$ROOT"
mkdir -p logs data backups
if [[ ! -x "$ROOT/api/math-api" ]]; then
  echo "Missing api/math-api. This folder is incomplete; rebuild with scripts/pack/make_release_mac.sh on a Mac."
  read -k 1 "?Press any key to exit..."
  exit 1
fi
if [[ ! -x "$ROOT/web/node" || ! -f "$ROOT/web/server.js" ]]; then
  echo "Missing bundled web runtime. This folder is incomplete; rebuild with scripts/pack/make_release_mac.sh on a Mac."
  read -k 1 "?Press any key to exit..."
  exit 1
fi
export MATH_PLATFORM_ROOT="$ROOT"
export DATABASE_URL="sqlite:///$ROOT/data/math_platform_dev.db"
export HOST="127.0.0.1"
export PORT="8001"
"$ROOT/api/math-api" > "$ROOT/logs/api.out.log" 2> "$ROOT/logs/api.err.log" &
API_PID=$!
echo "$API_PID" > "$ROOT/logs/api.pid"
export NODE_ENV="production"
export HOSTNAME="127.0.0.1"
export PORT="3000"
export NEXT_PUBLIC_API_BASE="http://127.0.0.1:8001"
"$ROOT/web/node" "$ROOT/web/server.js" > "$ROOT/logs/web.out.log" 2> "$ROOT/logs/web.err.log" &
WEB_PID=$!
echo "$WEB_PID" > "$ROOT/logs/web.pid"
cleanup() {
  kill "$WEB_PID" "$API_PID" >/dev/null 2>&1 || true
  rm -f "$ROOT/logs/api.pid" "$ROOT/logs/web.pid"
}
trap cleanup EXIT INT TERM
sleep 3
open "http://127.0.0.1:3000"
echo "Math Platform started: http://127.0.0.1:3000"
echo "Close this window to stop it. Logs are in: $ROOT/logs"
wait
EOF

cat > "$RELEASE_DIR/STOP.command" <<'EOF'
#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h}"
for name in web api; do
  pid_file="$ROOT/logs/$name.pid"
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]]; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$pid_file"
  fi
done
echo "Stopped Math Platform processes recorded in this release folder."
EOF

cat > "$RELEASE_DIR/STATUS.command" <<'EOF'
#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h}"
for name in api web; do
  pid_file="$ROOT/logs/$name.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
    echo "$name: running pid $(cat "$pid_file")"
  else
    echo "$name: stopped"
  fi
done
EOF

cat > "$RELEASE_DIR/README.md" <<'EOF'
# Math Platform macOS Offline Release

This is the folder to copy to a new Mac after it has been generated on a Mac by `scripts/pack/make_release_mac.sh`.

End-user usage:

1. Copy this whole `math-platform-mac-offline` folder to the Mac.
2. Double-click `START_HERE.command`.
3. The browser opens `http://127.0.0.1:3000`.
4. Close the terminal window or double-click `STOP.command` to stop.

The target Mac does not need Python, Node.js, npm, or project source code. They are bundled into this folder during the Mac build.

If macOS blocks unsigned local binaries, right-click `START_HERE.command` and choose Open, or remove quarantine after confirming the source is trusted:

```bash
xattr -dr com.apple.quarantine /path/to/math-platform-mac-offline
```

Build note: PyInstaller is platform-specific. Build Apple Silicon packages on Apple Silicon Macs, and Intel packages on Intel Macs if you need both architectures.
EOF

chmod +x "$RELEASE_DIR/START_HERE.command" "$RELEASE_DIR/STOP.command" "$RELEASE_DIR/STATUS.command" "$RELEASE_DIR/api/math-api" "$RELEASE_DIR/web/node"

echo "macOS offline release created: $RELEASE_DIR"
