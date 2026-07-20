#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
user_root="${HOME:?HOME is not set}"
install_root="${AACC_INSTALL_ROOT:-$user_root}"
user_apps="$install_root/Applications"
user_bin="$install_root/.local/bin"
runtime_root="$install_root/Library/Application Support/AACC/runtime"
runtime_venv="$runtime_root/.venv"
launch_app=1
if [[ "${1:-}" == "--no-launch" ]]; then
  launch_app=0
fi

command -v uv >/dev/null 2>&1 || { echo "错误：未找到 uv，请先运行 brew install uv" >&2; exit 1; }
cd "$project_root"
uv sync --extra dev
if [[ "${AACC_RUN_TESTS:-0}" == "1" ]]; then
  echo "运行测试（AACC_RUN_TESTS=1）…"
  QT_QPA_PLATFORM=offscreen uv run pytest -q
else
  echo "跳过测试（设置 AACC_RUN_TESTS=1 可在安装前运行测试）"
fi
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  "$project_root/scripts/build_app.sh"
elif [[ ! -d "$project_root/dist/AACC.app" ]]; then
  echo "错误：SKIP_BUILD=1 但 dist/AACC.app 不存在" >&2
  exit 1
fi

mkdir -p "$runtime_root"
rm -rf "$runtime_venv" "$runtime_root/wheels"
uv venv "$runtime_venv"
uv build --wheel --out-dir "$runtime_root/wheels"
uv export --locked --no-dev --no-emit-project \
  --output-file "$runtime_root/requirements.lock" --quiet
app_version="$(uv version --short)"
wheels=("$runtime_root"/wheels/aacc_control_center-"$app_version"-*.whl)
if [[ ! -f "${wheels[0]}" ]]; then
  echo "错误：未生成 AACC runtime wheel" >&2
  exit 1
fi
uv pip install --python "$runtime_venv/bin/python" \
  --requirements "$runtime_root/requirements.lock"
uv pip install --python "$runtime_venv/bin/python" "${wheels[0]}" --no-deps

mkdir -p "$user_apps" "$user_bin"
# 覆盖安装前无条件退出正在运行的旧实例（即使目标目录当前不存在）
/usr/bin/osascript -e 'tell application id "com.aacc.controlcenter" to quit' \
  >/dev/null 2>&1 || true
for attempt in {1..20}; do
  if ! pgrep -f "$user_apps/AACC.app/Contents/MacOS/AACC" >/dev/null; then
    break
  fi
  sleep 0.2
done
if [[ -d "$user_apps/AACC.app" ]]; then
  backup="$user_root/.Trash/AACC.app.backup.$(date +%Y%m%d-%H%M%S)"
  mv "$user_apps/AACC.app" "$backup"
fi
ditto "$project_root/dist/AACC.app" "$user_apps/AACC.app"
ln -sfn "$runtime_venv/bin/aacc" "$user_bin/aacc"
ln -sfn "$runtime_venv/bin/aacc-run" "$user_bin/aacc-run"
ln -sfn "$runtime_venv/bin/aacc-gui" "$user_bin/aacc-gui"
if [[ "$launch_app" == "1" ]]; then
  open "$user_apps/AACC.app"
  echo "AACC 已安装并启动：$user_apps/AACC.app"
else
  echo "AACC 已安装：$user_apps/AACC.app"
fi

echo "命令行工具：$user_bin/aacc"
