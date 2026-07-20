# AACC v1.3.0-rc.2 测试报告

测试日期：2026-07-20

测试平台：macOS 26.5.2（Build 25F84），Apple Silicon arm64

构建环境：uv 管理的 CPython 3.13.11

## 自动化质量门禁

- `uv run ruff check .`：通过，无 lint 错误。
- `uv run ruff format --check .`：通过，44 个文件格式一致。
- `uv run mypy src/aacc`：通过，23 个源文件无类型错误。
- `uv run pytest -q`：通过，177 项测试全部通过。
- 安装器在真实安装前再次运行全套测试：177 项全部通过。
- Codex 发现与服务定向测试：25 项通过，覆盖 5 秒默认间隔、安全短概括、格式异常、完成事件与发现告警。
- 状态机与计时/GUI 定向测试：30 项通过，覆盖短回合持续计时、等待状态、完成冻结与终态后重新运行归零。
- GUI 完整定向测试：24 项通过，覆盖横向卡片、任务选择、移除、自适应高度、80% 高度上限与内部滚动。

## 构建与安装验证

- 应用：`/Users/zhangboqian/Applications/AACC.app`，约 110 MB。
- 应用版本：`CFBundleShortVersionString=1.3.0-rc.2`，`CFBundleVersion=3`。
- `codesign --verify --deep --strict`：通过；当前为 ad-hoc 签名。
- DMG：`/Users/zhangboqian/Desktop/AACC-1.3.0-rc.2.dmg`，约 50 MB。
- `hdiutil verify`：通过，镜像校验有效。
- SHA-256：`8864db967046a9aadf8a53f2345102851b357ab7981da6ba6b1b0d9b921e1bdc`。
- 安装器使用生产依赖运行环境构建 `aacc_control_center-1.3.0rc2` wheel，并替换安装旧版本。
- 安装后只运行一个 AACC 进程，RSS 约 63 MB。
- 本地健康接口返回 `{"status":"ok","version":"1.3.0rc2"}`。
- `aacc doctor`：配置、SQLite 和本地 API 均通过。
- 配置文件与 SQLite 权限均为 `0600`（`-rw-------`）。

## macOS 实际界面验证

- 实际发现并同时显示两个运行中的 Codex 任务。
- 每张卡正确显示小型 `CODEX` 工具徽标、较大的任务名称、左侧大状态灯、`HH:MM:SS` 计时和一行短概括。
- 实际短概括显示过“正在执行命令”“正在检查代码”“正在修改代码”，没有显示原始命令或会话正文。
- 点击一张运行卡的 `×` 后，可见任务从 2 个变为 1 个，窗口随即向上收短。
- 在任务选择器点击“恢复自动识别”并应用后，可见任务恢复为 2 个，窗口自动拉长；测试前的监控选择已恢复。
- 自动测试在模拟 500 px 可用屏幕时确认窗口高度为 400 px，并在内容超出时启用内部滚动。

## 已知限制

- 此构建没有 Developer ID 签名，也没有 Apple 公证，只作为 GitHub RC 预发布。
- rc.2 自动发现仅支持 Codex；Claude Code、Cursor、Kimi Code 等需要各自 Adapter 提供真实状态。
- 本机测试时辅助功能权限处于未开启状态，因此未重复执行全局热键和键盘注入实测；应用已正确显示权限引导。
