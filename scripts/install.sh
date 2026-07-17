#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
user_root="$(python3 -c 'from pathlib import Path; print(Path.home())')"
user_apps="$user_root/Applications"
user_bin="$user_root/.local/bin"

command -v uv >/dev/null 2>&1 || { echo "错误：未找到 uv，请先运行 brew install uv" >&2; exit 1; }
cd "$project_root"
uv sync --extra dev
QT_QPA_PLATFORM=offscreen uv run pytest -q
"$project_root/scripts/build_app.sh"

mkdir -p "$user_apps" "$user_bin"
if [[ -d "$user_apps/AACC.app" ]]; then
  backup="$user_root/.Trash/AACC.app.backup.$(date +%Y%m%d-%H%M%S)"
  mv "$user_apps/AACC.app" "$backup"
fi
ditto "$project_root/dist/AACC.app" "$user_apps/AACC.app"
ln -sfn "$project_root/.venv/bin/aacc" "$user_bin/aacc"
ln -sfn "$project_root/.venv/bin/aacc-run" "$user_bin/aacc-run"
ln -sfn "$project_root/.venv/bin/aacc-gui" "$user_bin/aacc-gui"
open "$user_apps/AACC.app"

echo "AACC 已安装并启动：$user_apps/AACC.app"
echo "命令行工具：$user_bin/aacc"

