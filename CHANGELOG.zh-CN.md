# 更新日志

## 1.3.0-rc.1 — 2026-07-20

- [Security] 配置改为原子写入，自动修复无效 API Token，强制配置与数据库私有权限，新增本地凭证轮换，并加强日志脱敏与 AppleScript 文本隔离。
- [Stability] 完整桌面自动化事务进入有界单工作线程，Qt 不再阻塞；任务起始时间保持稳定，重复历史被抑制，SQLite 历史有上限，Codex 发现故障可见且可恢复。
- [Stability] 新增 PID 身份校验、单实例锁、包装器协作式子进程清理、辅助功能权限引导、事件 tap 恢复及 Adapter 断开唤醒。
- [Breaking] 源码安装器不再把 CLI 链接到仓库 `.venv`，而是安装到 Application Support 下的运行时专用环境。
- [Delivery] QSS 作为资源打包，DMG 支持复用构建，并加入 Developer ID/公证分支及明确的 ad-hoc 预发布标识。

[English version](CHANGELOG.md)

## 1.2.0 — 2026-07-19

- Codex 任务进入完成、失败、取消或停止状态后保留卡片，绿色完成灯不会自动消失。
- 新增单卡 `×`、右键“从面板移除”和带确认的“全部清除”。
- 新增运行中/已完成分组、任务数量摘要和最后活动时间，提升快速浏览体验。
- 已移除任务再次被可靠检测为运行时会自动回到面板。

## 1.1.0 — 2026-07-19

- 新增最多 4 个近期可靠 Codex 运行任务的自动监控、任务静默记忆、过期启动事件保护和重复索引清理。
- 状态灯扩大为原先的 5 倍，便于快速发现状态变化。
- 新增仅监控已选本机 Codex 任务，以及完成事件优先的状态判断。
- 新增英文主入口、中文完整文档、开源治理文件和公开仓库元数据。

## 1.0.0 — 2026-07-17

- 新增始终置顶、透明、无边框、可拖动和可缩放的 macOS 悬浮面板。
- 新增菜单栏、紧凑模式、位置/透明度记忆、状态动画与完成/错误通知。
- 新增统一状态机、SQLite 状态历史、YAML 配置和日志脱敏。
- 新增带随机 Token 的 localhost API、`aacc` CLI 与 `aacc-run` 生命周期包装器。
- 新增 Terminal.app、iTerm2、Codex App 和通用 bundle ID 激活方式。
- 新增 F13–F20 全局快捷键、白名单键盘注入和 macOS 系统听写触发。
- 新增 Generic CLI、Codex CLI、Claude Code、Kimi Code 与 Codex App Adapter 预设。
- 新增测试、安装、恢复式卸载、PyInstaller `.app` 构建、DMG 发布产物和完整文档。
