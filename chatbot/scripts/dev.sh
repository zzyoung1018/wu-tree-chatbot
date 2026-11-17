#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python || ! -d frontend/node_modules ]]; then
  echo '请先按 README 安装后端和前端依赖。'
  exit 1
fi
cleanup() {
  kill "${api_pid:-}" "${web_pid:-}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM
.venv/bin/python -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers &
api_pid=$!
(cd frontend && exec node node_modules/vite/bin/vite.js) &
web_pid=$!
echo '树语本地地址：http://127.0.0.1:5173'
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do
  sleep 1
done
# Exiting either service also cleans up its peer; no orphan Vite process.
