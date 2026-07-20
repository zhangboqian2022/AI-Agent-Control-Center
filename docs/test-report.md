# AACC v1.3.0-rc.1 测试报告

测试环境为 Apple Silicon macOS 26.5.2（build 25F84）、Python 3.13.11、PySide6 6.11.1。

## 最终结果

- `pytest`：150 项通过，0 项失败。
- 全包行覆盖率 81%；安全关键聚焦模块为：配置 94%、模型 99%、持久化 96%、状态机 94%、任务管理 93%、自动化 98%、自动化队列 96%、发现服务 92%、脱敏 100%。未覆盖行主要集中在平台入口与真实事件循环。
- Ruff：0 个问题；strict mypy：23 个源码模块全部通过。
- wheel 已包含 `aacc/styles.qss`；安装后的独立 runtime 不含 pytest、mypy、Ruff 或 PyInstaller。
- `~/Applications/AACC.app` 版本为 `1.3.0-rc.1`，约 110 MB；ad-hoc 深度签名严格验证通过，进程保持运行，RSS 约 71 MB。
- 桌面 DMG 为 `/Users/zhangboqian/Desktop/AACC-1.3.0-rc.1.dmg`，约 49 MB；`hdiutil verify` 通过；SHA-256 为 `35069897f340c4c0da9f1c9c3380e1d888152c53986ab68345b563556b15278f`。
- 安装后配置与 SQLite 权限均为 `0600`；本地健康接口返回 `1.3.0rc1`；`aacc doctor` 通过；重复启动后仍只有一个进程。
- 真实 Codex 冒烟测试同时识别到本次运行任务和另一个已完成任务；完成保留、移除与恢复由 Qt/发现回归测试覆盖。

当前机器没有 Developer ID Application 身份，也没有配置 Apple 公证 profile，因此本产物明确是 ad-hoc GitHub 预发布版，不是已公证稳定版。macOS 13/14/15 尚未完成真机矩阵，不作已实测承诺。
