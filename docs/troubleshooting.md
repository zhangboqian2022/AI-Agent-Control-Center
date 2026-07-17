# AACC 故障排查

## 卡片点击后没有切换

运行 `aacc doctor`，确认配置和 API。检查 `terminal.type`、`app_bundle_id` 与 `window_title`。首次控制 Terminal/iTerm2 时，到“系统设置 → 隐私与安全性 → 自动化”允许 AACC 控制目标应用。标题不匹配时 AACC 会停止发送，不会盲发按键。

## 快捷键或语音无效

到“系统设置 → 隐私与安全性 → 辅助功能”允许 AACC。退出并重新打开应用。确认 F13–F20 没有被键盘驱动或其他工具占用。语音使用系统双 Fn 听写；先在“键盘 → 听写”中启用听写。

## CLI 无法连接

确认 AACC 正在运行，端口 17650 未占用，并且 CLI 读取的是同一个配置文件。执行 `aacc doctor`。如果修改过端口或 Token，需要重启 AACC。API 不支持外网监听，这是安全限制。

## 状态没有自动变色

没有结构化 Hook 的 Agent 不能可靠暴露内部阶段。用 `aacc-run --task task-1 -- codex` 获取进程生命周期，或用 `aacc status` 更新。自定义日志匹配应加入 Generic CLI 配置；无法确认时保持 UNKNOWN/WARNING 是预期行为。

## macOS 拒绝打开本地 App

本地构建使用 ad-hoc 签名，没有 Apple 公证。可在“系统设置 → 隐私与安全性”中选择仍要打开。不要从不受信任的位置下载替换本项目构建出的 App。

## 日志位置

日志、配置和数据库位于 `~/Library/Application Support/AACC/`。提交诊断信息前仍应人工检查敏感内容；AACC 默认会隐藏 Token、密码、Bearer 值与常见 `sk-` Key。

