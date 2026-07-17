#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$project_root"

if [[ -d "$project_root/dist/AACC.app" ]]; then
  open "$project_root/dist/AACC.app"
else
  command -v uv >/dev/null 2>&1 || { echo "错误：需要先安装 uv" >&2; exit 1; }
  exec uv run aacc-gui
fi

