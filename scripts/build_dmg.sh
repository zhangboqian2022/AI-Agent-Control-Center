#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$project_root/scripts/release_env.sh"
validate_release_credentials
desktop_dir="${AACC_DMG_OUTPUT_DIR:-$(/usr/bin/osascript -e 'POSIX path of (path to desktop folder)')}"
AACC_VERSION="${AACC_VERSION:-1.4.0-rc.1}"
codesign_identity="${AACC_CODESIGN_IDENTITY:-}"
notary_profile="${AACC_NOTARY_PROFILE:-}"
# Default output: AACC-1.4.0-rc.1.dmg
output_path="${desktop_dir%/}/AACC-${AACC_VERSION}.dmg"

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  "$project_root/scripts/build_app.sh"
elif [[ ! -d "$project_root/dist/AACC.app" ]]; then
  echo "错误：SKIP_BUILD=1 但 dist/AACC.app 不存在" >&2
  exit 1
fi
/usr/bin/hdiutil create \
  -volname "AACC ${AACC_VERSION}" \
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
  echo "提示：未执行 Apple 公证；此 DMG 仅作为 GitHub RC 预发布。"
fi

/usr/bin/hdiutil verify "$output_path"

echo "已构建 DMG：$output_path"
