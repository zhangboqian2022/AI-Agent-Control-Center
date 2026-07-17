# AI Agent Control Center V1.0 执行规格

本文件是用户提供的《AI Agent Control Center（AACC）macOS 多 AI Coding Agent 桌面状态控制中心》统一规格书的仓库执行版。权威需求原文保留在本项目来源附件中；以下列出本实现采用的完整产品边界与验收口径。

## 产品目标

AACC 是本机多 AI Coding Agent 统一状态与控制中心，而非单一 Codex 状态灯。默认四个任务，首期覆盖 Codex CLI/App、Claude Code、Kimi Code 与可配置 Z Code，并允许扩展 Agent Adapter 而不修改 GUI。

## 必须能力

无边框、透明、始终置顶、可拖动缩放的 macOS 悬浮面板；紧凑/展开模式；菜单栏；位置与透明度记忆；四任务状态灯；运行时间、更新时间、状态说明；点击聚焦 Terminal/iTerm2/App；手动状态；本地 Token API；CLI；状态文件/Adapter 接口；全局快捷键；Enter、1、2、方向键；系统听写；SQLite 恢复；日志脱敏；可重复 `.app` 构建。

统一状态包括 UNCONFIGURED、IDLE、STARTING、THINKING、RUNNING、WAITING_INPUT、WAITING_APPROVAL、COMPLETED、WARNING、ERROR、PAUSED、CANCELLED、STOPPED 与 UNKNOWN。每个更新包含来源和可信度；手动状态优先，低可信度新事件不能覆盖新鲜高可信度状态，终态允许由新任务启动重新进入活动态，状态历史必须持久化。

## 架构和安全

GUI、状态核心、API/CLI、Agent Adapter、终端自动化相互隔离。结构化事件优先，其次 Hook、Wrapper、文本、进程与手动。不能可靠判断时必须显示 UNKNOWN/WARNING。API 版本路径为 `/api/v1`，只绑定 127.0.0.1，使用随机 Token，限制输入长度与按键白名单，绝不接收任意 shell。所有 subprocess 使用参数数组、超时和错误处理。输入前必须确认目标应用/窗口，权限缺失和 Adapter 错误不得拖垮程序。

## V1.0 非目标

不自动批准危险命令，不绕过 Agent 权限，不读取密码框或完整对话，不依赖屏幕坐标，不做云同步、远程控制、移动端、OCR、精确 Token 成本或多 Agent 工作流编排。没有公开可靠接口的第三方 Agent 使用配置化降级，不虚构官方 Hook。

## 验收

macOS 可启动并显示四任务；紧凑/展开、置顶与位置记忆有效；CLI/API 能更新状态；Terminal/iTerm2/Codex App 能激活；Codex/Claude/Kimi 有 Adapter；Z Code 走 Generic CLI；全局快捷键和白名单输入可用；语音可触发；日志、错误隔离、本地绑定、安全校验、说明、示例配置、自动化测试、构建脚本和已知限制齐全。
