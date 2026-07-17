# AACC 用户指南

## 面板操作

单击卡片会激活绑定的应用与窗口；双击会在聚焦后触发系统听写。右键卡片可发送 Enter、1、2、上下键，启动语音，手动标记状态，重置或复制任务信息。顶部箭头切换紧凑/展开模式，齿轮调整透明度和置顶，横线隐藏到菜单栏。窗口可拖动，右下角可缩放。

## 绑定 Terminal 与 iTerm2

为每个任务设置唯一且稳定的窗口标题，例如 `AACC-TASK-1`。Terminal 使用 `terminal.type: terminal_app` 和 `app_bundle_id: com.apple.Terminal`；iTerm2 使用 `terminal.type: iterm2`。若只需激活 Codex App 或其他桌面应用，设置 `terminal.type: mac_app` 与对应 bundle identifier。

## 状态来源

最可靠的方式是 Agent Hook 调用本地 API。没有 Hook 时使用 `aacc-run` 报告进程启动、运行和退出；退出码 0 只标记 `STOPPED`，不会伪造业务完成。还可以用 `aacc status` 手动更新。手动状态优先，五分钟后可被新自动状态覆盖。

## 全局快捷键

默认 F13–F16 聚焦任务 1–4，F17 发送 Enter，F18/F19 发送 1/2，F20 触发听写。可以用 Karabiner-Elements 或键盘固件将小键盘按键映射到这些功能键。全局监听与键盘注入需要辅助功能权限，可通过 `keyboard_injection: false` 完全关闭发送能力。

## 开机启动

V1.0 的安装包不擅自修改登录项。可在 macOS“系统设置 → 通用 → 登录项”中添加 `~/Applications/AACC.app`，随时可以在同一页面移除。

