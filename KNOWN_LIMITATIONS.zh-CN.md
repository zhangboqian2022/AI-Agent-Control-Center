# AACC 已知限制

- 本版本采用本地自签名证书签名，未经过 Apple 公证。使用“仍要打开”前请核对 SHA-256。
- Kimi Desktop 监控只读 `~/Library/Application Support/kimi-desktop` 下的 daimon 会话目录（WAL 感知的 `mode=ro`，刻意不用 `immutable=1` 以保证读到 WAL 中的新数据）。若未来 Kimi Desktop 版本将该数据移出 Application Support，需重新评估磁盘读取（TCC）权限。Chat 标签页为 kimi.com 网页套壳，会话在云端，无法监控。
- 桌面自动化默认 osascript 超时为 5 秒，可配置为 2–15 秒；目标 App 首次启动较慢时可适当调高。
- 全局热键及键盘/听写注入需要辅助功能权限；不涉及输入的 App 聚焦仍可使用。
- API 凭证只允许在本机 GUI 轮换；旧 Token 立即失效，不提供宽限期或远程轮换接口。
- `aacc-run` 可在 SIGINT/SIGTERM 后清理子进程，但无法承诺 SIGKILL、断电或系统崩溃后的清理。
- Codex 发现当前适配元数据兼容标识 `2026-07`。未来格式变化可能暂时导致发现降级；AACC 会保留最后状态并显示告警。
- 最低支持 macOS 13；集成检查表中未标记通过的系统/硬件组合不宣称已实测。
