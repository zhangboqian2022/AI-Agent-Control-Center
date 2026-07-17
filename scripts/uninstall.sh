#!/usr/bin/env bash
set -euo pipefail

user_root="$(python3 -c 'from pathlib import Path; print(Path.home())')"
trash_dir="$user_root/.Trash/AACC-uninstall-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$trash_dir"

targets=(
  "$user_root/Applications/AACC.app"
  "$user_root/.local/bin/aacc"
  "$user_root/.local/bin/aacc-run"
  "$user_root/.local/bin/aacc-gui"
  "$user_root/Library/Application Support/AACC"
)

for target in "${targets[@]}"; do
  if [[ -e "$target" || -L "$target" ]]; then
    mv "$target" "$trash_dir/"
  fi
done

echo "AACC 已移至废纸篓：$trash_dir"
echo "如需恢复，可在废纸篓中找回这些文件。"

