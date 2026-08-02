#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$project_root/scripts/release_env.sh"
validate_release_credentials
codesign_identity="${AACC_CODESIGN_IDENTITY:-}"
if [[ -z "$codesign_identity" ]] && \
  security find-identity -p codesigning 2>/dev/null | grep -q "AACC Local Development"; then
  # Stable self-signed identity: keeps TCC (accessibility) grants valid
  # across rebuilds, unlike ad-hoc signing whose cdhash changes every build.
  codesign_identity="AACC Local Development"
fi
cd "$project_root"

command -v uv >/dev/null 2>&1 || { echo "错误：需要先安装 uv" >&2; exit 1; }
AACC_VERSION="${AACC_VERSION:-$(uv version --short)}"
uv sync --locked --extra dev
AACC_PUBLIC_VERSION="${AACC_PUBLIC_VERSION:-$(python3 -c 'import re, sys; v=sys.argv[1]; m=re.fullmatch(r"(\d+\.\d+\.\d+)rc(\d+)", v); print(f"{m.group(1)}-rc.{m.group(2)}" if m else v)' "$AACC_VERSION")}"
AACC_BUNDLE_VERSION="${AACC_BUNDLE_VERSION:-${AACC_VERSION//./}}"
AACC_BUNDLE_VERSION="${AACC_BUNDLE_VERSION//rc/}"
AACC_BUNDLE_VERSION="${AACC_BUNDLE_VERSION//a/}"
AACC_BUNDLE_VERSION="${AACC_BUNDLE_VERSION//b/}"
AACC_BUNDLE_VERSION="${AACC_BUNDLE_VERSION//-/}"
if [[ ! "$AACC_BUNDLE_VERSION" =~ ^[0-9]+$ ]]; then
  echo "错误：无法生成数值 CFBundleVersion：$AACC_VERSION" >&2
  exit 1
fi
uv run pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name AACC \
  --osx-bundle-identifier com.aacc.controlcenter \
  --paths "$project_root/src" \
  --additional-hooks-dir "$project_root/hooks" \
  --hidden-import Quartz \
  --hidden-import aacc.adapters \
  --hidden-import PySide6.QtWebView \
  --hidden-import aacc.kimi_web_session \
  --hidden-import aacc.opencode_web_session \
  --add-data "$project_root/src/aacc/styles.qss:aacc" \
  --exclude-module mypy \
  --exclude-module pytest \
  "$project_root/src/aacc/__main__.py"

/usr/bin/plutil -replace CFBundleShortVersionString -string "$AACC_PUBLIC_VERSION" \
  "$project_root/dist/AACC.app/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleVersion -string "$AACC_BUNDLE_VERSION" \
  "$project_root/dist/AACC.app/Contents/Info.plist"

if command -v codesign >/dev/null 2>&1; then
  if [[ -n "$codesign_identity" ]]; then
    sign_args=(--force --deep)
    if [[ "$codesign_identity" == Developer\ ID* ]]; then
      # Hardened runtime exists for notarization. Self-signed identities have
      # no Team ID, so its library validation rejects every bundled dylib
      # ("different Team IDs") and the app cannot launch — only enable it for
      # real Developer ID certificates.
      sign_args+=(--options runtime --timestamp)
    fi
    codesign "${sign_args[@]}" --sign "$codesign_identity" "$project_root/dist/AACC.app"
  else
    codesign --force --deep --sign - "$project_root/dist/AACC.app"
    echo "提示：使用 ad-hoc 签名；此构建仅用于 RC 预发布。"
  fi
  codesign --verify --deep --strict "$project_root/dist/AACC.app"
fi

echo "已构建：$project_root/dist/AACC.app"
