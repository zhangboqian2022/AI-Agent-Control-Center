#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$project_root/scripts/release_env.sh"
validate_release_credentials
AACC_VERSION="${AACC_VERSION:-1.3.0-rc.2}"
codesign_identity="${AACC_CODESIGN_IDENTITY:-}"
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
  --add-data "$project_root/src/aacc/styles.qss:aacc" \
  --exclude-module mypy \
  --exclude-module pytest \
  "$project_root/src/aacc/__main__.py"

/usr/bin/plutil -replace CFBundleShortVersionString -string "$AACC_VERSION" \
  "$project_root/dist/AACC.app/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleVersion -string "3" \
  "$project_root/dist/AACC.app/Contents/Info.plist"

if command -v codesign >/dev/null 2>&1; then
  if [[ -n "$codesign_identity" ]]; then
    codesign --force --deep --options runtime --timestamp \
      --sign "$codesign_identity" "$project_root/dist/AACC.app"
  else
    codesign --force --deep --sign - "$project_root/dist/AACC.app"
    echo "提示：使用 ad-hoc 签名；此构建仅用于 RC 预发布。"
  fi
  codesign --verify --deep --strict "$project_root/dist/AACC.app"
fi

echo "已构建：$project_root/dist/AACC.app"
