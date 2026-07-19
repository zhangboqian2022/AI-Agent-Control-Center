#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
desktop_dir="${AACC_DMG_OUTPUT_DIR:-$(/usr/bin/osascript -e 'POSIX path of (path to desktop folder)')}"
output_path="${desktop_dir%/}/AACC-1.2.0.dmg"

"$project_root/scripts/build_app.sh"
/usr/bin/hdiutil create \
  -volname "AACC 1.2.0" \
  -srcfolder "$project_root/dist/AACC.app" \
  -format UDZO \
  -ov \
  "$output_path"

echo "已构建 DMG：$output_path"
