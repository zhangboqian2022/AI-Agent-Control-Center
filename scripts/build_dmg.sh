#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$project_root/scripts/release_env.sh"
validate_release_credentials
desktop_dir="${AACC_DMG_OUTPUT_DIR:-$(/usr/bin/osascript -e 'POSIX path of (path to desktop folder)')}"
command -v uv >/dev/null 2>&1 || { echo "错误：需要先安装 uv" >&2; exit 1; }
AACC_VERSION="${AACC_VERSION:-$(uv version --short)}"
codesign_identity="${AACC_CODESIGN_IDENTITY:-}"
notary_profile="${AACC_NOTARY_PROFILE:-}"
AACC_PUBLIC_VERSION="${AACC_PUBLIC_VERSION:-$(python3 -c 'import re, sys; v=sys.argv[1]; m=re.fullmatch(r"(\d+\.\d+\.\d+)rc(\d+)", v); print(f"{m.group(1)}-rc.{m.group(2)}" if m else v)' "$AACC_VERSION")}"
# Default output: AACC-${AACC_PUBLIC_VERSION}.dmg
output_path="${desktop_dir%/}/AACC-${AACC_PUBLIC_VERSION}.dmg"

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  "$project_root/scripts/build_app.sh"
elif [[ ! -d "$project_root/dist/AACC.app" ]]; then
  echo "错误：SKIP_BUILD=1 但 dist/AACC.app 不存在" >&2
  exit 1
fi
/usr/bin/hdiutil create \
  -volname "AACC ${AACC_PUBLIC_VERSION}" \
  -srcfolder "$project_root/dist/AACC.app" \
  -format UDZO \
  -ov \
  "$output_path"

if [[ -n "$notary_profile" ]]; then
  /usr/bin/xcrun notarytool submit "$output_path" \
    --keychain-profile "$notary_profile" --wait
  /usr/bin/xcrun stapler staple "$output_path"
  /usr/sbin/spctl --assess --type open \
    --context context:primary-signature --verbose "$output_path"
else
  echo "提示：未执行 Apple 公证；此 DMG 是需校验 SHA-256 的社区构建。"
fi

/usr/bin/hdiutil verify "$output_path"
(
  cd "$(dirname "$output_path")"
  /usr/bin/shasum -a 256 "$(basename "$output_path")" > "$output_path.sha256"
)

echo "已构建 DMG：$output_path"
