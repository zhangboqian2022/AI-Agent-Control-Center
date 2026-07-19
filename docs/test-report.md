# AACC V1.2 测试报告

测试环境为 Apple Silicon macOS 26.5.2、Python 3.13.11、PySide6 6.11.1。自动化测试覆盖状态解析、默认配置、状态优先级、SQLite 重启恢复、历史、订阅、脱敏、API 鉴权与校验、CLI 语法、AppleScript 转义、按键白名单、注入开关、全局热键码、Agent 正则超时、适配器预设、离屏 GUI、运行时初始化以及交付文件。

## 最终结果

- `uv run --extra dev pytest -q`：79 项通过，0 项失败（包含 Codex 会话去重、过期启动事件、自动监控、完成保留、移除、重新出现和分组回归测试）。
- 核心模块覆盖率：92%；其中 API 85%、状态机 82%、配置 93%、模型 98%、持久化 96%、任务管理 91%、安全脱敏 100%。
- `uv run --extra dev ruff check src tests`：0 个问题。
- `uv run --extra dev mypy src`：20 个源码模块全部通过。
- 真实 API/CLI 联调：Bearer 鉴权、四任务查询、中文状态更新、doctor 与 SQLite 重启恢复通过。
- PyInstaller：成功构建 arm64 `AACC.app`，版本 1.2.0，ad-hoc 深度签名严格验证通过，体积约 110 MB。
- 安装与启动：`~/Applications/AACC.app` 进程保持运行，本地 API 返回 HTTP 200 和四个任务，常驻内存约 79 MB。
- Computer Use 只读检查：悬浮窗口、四卡片、中文状态、计时、顶部控制、菜单栏与 API 指示均实际渲染。

全包行覆盖率为 68%，主要未覆盖区域是 macOS 全局事件循环、系统权限分支、Qt 人机交互与进程入口；这些路径另由真实构建、启动、API 和界面检查覆盖。任何验证失败均在修正后完整重跑，没有隐藏失败。
