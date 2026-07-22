# AACC v1.3.1 测试报告

测试日期：2026-07-22

测试平台：macOS（Apple Silicon arm64）

构建环境：uv 管理的 CPython 3.13

## 自动化质量门禁

- `.venv/bin/ruff check src tests`：通过，无 lint 错误。
- `.venv/bin/mypy src/aacc`：通过，25 个源文件无类型错误。
- `.venv/bin/python -m pytest -q`：284 项测试全部通过（含本版本新增 15 项）。
- 版本一致性：打包测试断言 `pyproject.toml`、`aacc.__version__`、构建脚本默认值、README/用户指南下载链接严格一致。
- 安全回归：示例配置中的公开占位 token 会被前缀拒识并自动轮换；高熵合法 token 不误伤。

## 构建与安装验证

- 应用：`~/Applications/AACC.app`，版本 `CFBundleShortVersionString=1.3.1`。
- 签名：稳定自签名 "AACC Local Development"；`codesign --verify --deep --strict` 通过。
  hardened runtime 仅对 Developer ID 身份启用（自签名身份无 Team ID，启用会导致启动即崩，已回归验证）。
- DMG：`~/Desktop/AACC-1.3.1.dmg`，`hdiutil verify` 通过。
- SHA-256：`c748a726441334ba24d3537050ce6a7c4b32fa176808910db9f516da8a231df9`。
- 安装后进程存活确认（`pgrep`），覆盖安装前旧实例被无条件退出。

## macOS 实际界面验证

- 面板最小化后经托盘菜单恢复；隐藏后经 Dock 图标 / Cmd-Tab 恢复（真实 Cocoa 环境复现脚本 + 实机确认）。
- Kimi Code 卡片状态右侧显示工作目录名（如 `· codelight`），tooltip 为完整路径，实机确认。
- Kimi Desktop Agent 会话以数据级仿真端到端验证：注入"进行中"会话后 AACC 判定 RUNNING 并自动上卡，追加 `usage.record` 后翻转为"回合已完成"；仿真数据已清理。
- 辅助功能：授权后热键 5 秒内自动启用、撤销自动停止；引导弹窗勾选"不再提示"后不再出现（含共享设置污染的测试隔离修复）。
- 凭证轮换弹窗不再自动写剪贴板，需用户点击"复制"。

## 已知限制

- 本版本为本地自签名、未经 Apple 公证；首次打开需在"隐私与安全性"中放行。
- Kimi Desktop 的 Chat 标签页为 kimi.com 网页套壳，会话在云端，本地无数据源，无法监控。
- 全局热键绑定 F13–F20，标准 Mac 键盘无此按键，需扩展键盘或改键工具。
