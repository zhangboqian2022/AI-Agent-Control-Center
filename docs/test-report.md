# AACC V1.0 测试报告

测试环境为 Apple Silicon macOS 26.5.2、Python 3.13.11、PySide6 6.11.1。自动化测试覆盖状态解析、默认配置、状态优先级、SQLite 重启恢复、历史、订阅、脱敏、API 鉴权与校验、CLI 语法、AppleScript 转义、按键白名单、注入开关、全局热键码、Agent 正则超时、适配器预设、离屏 GUI、运行时初始化以及交付文件。

最终发布前会重新运行完整 pytest、覆盖率、Ruff、mypy、API/CLI 真实进程联调、PyInstaller 构建、签名验证与 App 启动检查。最终命令与结果会随本文件更新，任何失败都不会被隐藏。

