#!/usr/bin/env bash
set -euo pipefail

user_root="${HOME:?HOME is not set}"
install_root="${AACC_INSTALL_ROOT:-$user_root}"
# 废纸篓必须在真实 HOME 下，不跟随 AACC_INSTALL_ROOT
trash_dir="$user_root/.Trash/AACC-uninstall-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$trash_dir"

targets=(
  "$install_root/Applications/AACC.app"
  "$install_root/.local/bin/aacc"
  "$install_root/.local/bin/aacc-run"
  "$install_root/.local/bin/aacc-gui"
  "$install_root/Library/Application Support/AACC"
)

for target in "${targets[@]}"; do
  if [[ -e "$target" || -L "$target" ]]; then
    mv "$target" "$trash_dir/"
  fi
done

echo "AACC 已移至废纸篓：$trash_dir"
echo "如需恢复，可在废纸篓中找回这些文件。"

