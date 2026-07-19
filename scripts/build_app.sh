#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$project_root"

command -v uv >/dev/null 2>&1 || { echo "错误：需要先安装 uv" >&2; exit 1; }
uv sync --extra dev
uv run pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name AACC \
  --osx-bundle-identifier com.aacc.controlcenter \
  --paths "$project_root/src" \
  --hidden-import Quartz \
  --hidden-import aacc.adapters \
  --exclude-module mypy \
  --exclude-module pytest \
  "$project_root/src/aacc/__main__.py"

/usr/bin/plutil -replace CFBundleShortVersionString -string "1.1.0" \
  "$project_root/dist/AACC.app/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleVersion -string "1" \
  "$project_root/dist/AACC.app/Contents/Info.plist"

if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$project_root/dist/AACC.app"
fi

echo "已构建：$project_root/dist/AACC.app"
