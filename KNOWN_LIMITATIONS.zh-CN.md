# AACC 已知限制

- 本版本采用本地自签名证书签名，未经过 Apple 公证。使用“仍要打开”前请核对 SHA-256。
- Kimi Desktop 监控只读 `~/Library/Application Support/kimi-desktop` 下的 daimon 会话目录（WAL 感知的 `mode=ro`，刻意不用 `immutable=1` 以保证读到 WAL 中的新数据）。若未来 Kimi Desktop 版本将该数据移出 Application Support，需重新评估磁盘读取（TCC）权限。Chat 标签页为 kimi.com 网页套壳，会话在云端，无法监控。
- 桌面自动化默认 osascript 超时为 5 秒，可配置为 2–15 秒；目标 App 首次启动较慢时可适当调高。
- 全局热键及键盘/听写注入需要辅助功能权限；不涉及输入的 App 聚焦仍可使用。
- API 凭证只允许在本机 GUI 轮换；旧 Token 立即失效，不提供宽限期或远程轮换接口。
- `aacc-run` 可在 SIGINT/SIGTERM 后清理子进程，但无法承诺 SIGKILL、断电或系统崩溃后的清理。
- Codex 发现当前适配元数据兼容标识 `2026-07`。未来格式变化可能暂时导致发现降级；AACC 会保留最后状态并显示告警。
- Codex 额度是从本机有界结构化元数据读取的只读周额度指示。AACC 只接受未过期的 10080 分钟窗口，忽略旧版较短窗口；元数据缺失或变化时显示不可用，不调用 Codex 私有额度接口。
- 最低支持 macOS 13；集成检查表中未标记通过的系统/硬件组合不宣称已实测。
- Windows 终端聚焦依赖窗口标题匹配，标题被 shell 改写时可能失准。
- `SetForegroundWindow` 受 Windows 前景锁限制，激活被拒时降级处理并记录日志。
- Kimi Desktop daimon 的 Windows 路径为候选路径 best-effort，未在真机验证。
- Windows 版无代码签名，首次运行有 SmartScreen 提示。
- Windows CI 运行于托管 Windows Server SKU；消费级 Windows 10/11 行为（SmartScreen、托盘、窗口聚焦/热键、长时间运行）以人工验证清单覆盖，非自动化门禁。
- F13–F20 热键在多数 Windows 键盘需要 Fn 层映射。
