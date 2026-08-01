#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "用法：scripts/verify_release.sh <正式版本号，例如 1.4.0>" >&2
  exit 2
fi

release_version="$1"
if [[ ! "$release_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "错误：无效版本号：$release_version" >&2
  exit 2
fi

repository="${AACC_RELEASE_REPOSITORY:-zhangboqian2022/AI-Agent-Control-Center}"
release_tag="v${release_version}"
dmg_name="AACC-${release_version}.dmg"
dmg_checksum_name="AACC-${release_version}.dmg.sha256"
setup_name="AACC-${release_version}-Setup.exe"
setup_checksum_name="AACC-${release_version}-Setup.exe.sha256"
api_url="https://api.github.com/repos/${repository}/releases/tags/${release_tag}"
download_base="https://github.com/${repository}/releases/download/${release_tag}"
release_json="$(mktemp -t aacc-release.XXXXXX)"
release_downloads="$(mktemp -d -t aacc-release-assets.XXXXXX)"
trap 'rm -rf "$release_json" "$release_downloads"' EXIT

curl --fail --silent --show-error --location \
  --header "Accept: application/vnd.github+json" \
  --output "$release_json" \
  "$api_url"

python3 - "$release_json" "$release_tag" "$download_base" \
  "$dmg_name" "$dmg_checksum_name" "$setup_name" "$setup_checksum_name" <<'PY'
import json
import sys
from pathlib import Path

release_path, expected_tag, download_base, *expected_names = sys.argv[1:]
release = json.loads(Path(release_path).read_text(encoding="utf-8"))

if release.get("tag_name") != expected_tag:
    raise SystemExit(f"错误：发布 tag 不一致：{release.get('tag_name')!r}")
if release.get("draft") is not False:
    raise SystemExit("错误：发布仍是 draft")
if release.get("prerelease") is not False:
    raise SystemExit("错误：发布仍是 prerelease")

assets = {asset.get("name"): asset for asset in release.get("assets", [])}
for name in expected_names:
    asset = assets.get(name)
    if asset is None:
        raise SystemExit(f"错误：缺少发布资产：{name}")
    asset_size = asset.get("size")
    if not isinstance(asset_size, int) or asset_size <= 0:
        raise SystemExit(f"错误：发布资产为空：{name}")
    expected_url = f"{download_base}/{name}"
    if asset.get("browser_download_url") != expected_url:
        raise SystemExit(f"错误：发布资产 URL 不一致：{name}")
PY

for asset_name in \
  "$dmg_name" "$dmg_checksum_name" "$setup_name" "$setup_checksum_name"; do
  url="${download_base}/${asset_name}"
  curl --fail --silent --show-error --location --head --output /dev/null "$url"
  curl --fail --silent --show-error --location --output "$release_downloads/$asset_name" "$url"
done

python3 - "$release_downloads" "$dmg_name" "$dmg_checksum_name" "$setup_name" "$setup_checksum_name" <<'PY'
import hashlib
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
dmg_name, dmg_checksum_name, setup_name, setup_checksum_name = sys.argv[2:]
for asset_name, checksum_name in (
    (dmg_name, dmg_checksum_name),
    (setup_name, setup_checksum_name),
):
    checksum_text = (root / checksum_name).read_text(encoding="utf-8", errors="replace")
    match = re.search(r"(?im)^([0-9a-f]{64})\s+[* ]?(.+?)\s*$", checksum_text)
    if match is None:
        raise SystemExit(f"错误：校验文件格式无效：{checksum_name}")
    declared_name = Path(match.group(2)).name
    if declared_name != asset_name:
        raise SystemExit(
            f"错误：校验文件目标不一致：{checksum_name} -> {declared_name!r}"
        )
    actual = hashlib.sha256((root / asset_name).read_bytes()).hexdigest()
    if actual != match.group(1).lower():
        raise SystemExit(f"错误：资产 SHA-256 不一致：{asset_name}")
PY

echo "发布校验通过：${release_tag}（正式发布，DMG、Windows Setup 与 SHA-256 资产可下载）"
