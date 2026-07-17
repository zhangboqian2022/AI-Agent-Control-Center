# AACC 用户指南

## 面板操作

单击卡片会激活绑定的应用与窗口；双击会在聚焦后触发系统听写。右键卡片可发送 Enter、1、2、上下键，启动语音，手动标记状态，重置或复制任务信息。顶部箭头切换紧凑/展开模式，齿轮调整透明度和置顶，横线隐藏到菜单栏。窗口可拖动，右下角可缩放。

## Codex 自动任务

AACC 默认每两秒读取本机 Codex 的任务索引和活动进程记录，自动显示 Codex task 1、task 2、task 3 等任务标题。它只读取任务 ID、标题、更新时间和 PID，不读取对话内容、提示词、代码或命令。PID 仍在运行时显示“执行中”；其他最近任务会诚实显示“状态未知”。齿轮设置中可选择只显示 Codex，或同时勾选 Claude Code、Kimi Code、Z Code / 通用 CLI。

面板默认置顶并停靠在主显示器右上角；拖动后会保留位置。设置里的“停靠到桌面右上角”可随时恢复默认位置。点击自动发现的 Codex 任务会唤起 Codex；Codex 目前没有公开的精确任务跳转接口。

## DMG 安装包

执行 `./scripts/build_dmg.sh` 会在桌面生成 `AACC-1.0.0.dmg`。双击挂载后，将 `AACC.app` 拖入“应用程序”文件夹即可安装。

## 绑定 Terminal 与 iTerm2

为每个任务设置唯一且稳定的窗口标题，例如 `AACC-TASK-1`。Terminal 使用 `terminal.type: terminal_app` 和 `app_bundle_id: com.apple.Terminal`；iTerm2 使用 `terminal.type: iterm2`。若只需激活 Codex App 或其他桌面应用，设置 `terminal.type: mac_app` 与对应 bundle identifier。

## 状态来源

最可靠的方式是 Agent Hook 调用本地 API。没有 Hook 时使用 `aacc-run` 报告进程启动、运行和退出；退出码 0 只标记 `STOPPED`，不会伪造业务完成。还可以用 `aacc status` 手动更新。手动状态优先，五分钟后可被新自动状态覆盖。

## 全局快捷键

默认 F13–F16 聚焦任务 1–4，F17 发送 Enter，F18/F19 发送 1/2，F20 触发听写。可以用 Karabiner-Elements 或键盘固件将小键盘按键映射到这些功能键。全局监听与键盘注入需要辅助功能权限，可通过 `keyboard_injection: false` 完全关闭发送能力。

## 开机启动

V1.0 的安装包不擅自修改登录项。可在 macOS“系统设置 → 通用 → 登录项”中添加 `~/Applications/AACC.app`，随时可以在同一页面移除。
