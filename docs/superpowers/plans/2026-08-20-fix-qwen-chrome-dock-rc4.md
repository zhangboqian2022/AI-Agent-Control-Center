# AACC 1.4.5-rc4 — macOS Qwen Chrome Dock 实例修复

## Goal

修复 macOS 上 Qwen 额度隐藏刷新重复创建 Google Chrome Dock/LaunchServices 实例并留下残留图标的问题，发布本地可运行的 AACC 1.4.5-rc4，并在不改动用户默认 Chrome profile 或 AACC 用户数据的前提下替换当前应用。

## Approach

1. 从当前 `main` 复现 rc3 的隐藏 Chrome 启动行为，使用隔离临时 profile 检查 LaunchServices/Dock 实例变化；同时对候选的 `open -j/-g/-n` 组合做 A/B 验证。
2. 为启动参数和进程生命周期添加先失败的单元测试，明确隐藏刷新必须是 headed、非 headless、无焦点抢占，并且退出后不留下 AACC-owned Chrome 进程。
3. 实现经过实机验证的启动与关闭修复，保持 visible login 路径、CDP、profile ownership 和现有安全边界不变。
4. 将项目版本从 `1.4.5rc3` 更新为 `1.4.5rc4`，补充中英文发布记录和验证记录。
5. 运行针对性 Qwen 测试、全量 Python 测试、静态检查和 macOS bundle 构建验证；确认 bundle 版本、签名结构、启动健康状态后停止 rc3 并替换当前 `/Applications/AACC.app`。

## Files likely to change

- `src/aacc/qwen_chrome_cdp.py`
- `tests/test_qwen_chrome_cdp.py`
- `pyproject.toml`
- `uv.lock`
- `docs/release-notes-1.4.5rc4.md`
- `CHANGELOG.md`
- `CHANGELOG.zh-CN.md`

## Verification commands

- `uv run pytest tests/test_qwen_chrome_cdp.py tests/test_qwen_chrome_session.py`
- `uv run pytest`
- `uv run ruff check src tests`
- `uv run mypy`
- `AACC_VERSION=1.4.5rc4 ./scripts/build_app.sh`
- `codesign --verify --deep --strict --verbose=2 dist/AACC.app`
- inspect `CFBundleShortVersionString` and `CFBundleVersion`
- launch the replacement app and inspect process/profile ownership plus LaunchServices state

## Safety constraints

- Never touch `~/Library/Application Support/Google/Chrome` or the existing AACC user-data directory during experiments/builds.
- Use only temporary profiles under a uniquely-created temporary directory and clean only those exact paths.
- Do not claim Dock cleanup or release readiness without fresh local evidence.
