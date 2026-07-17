# AACC Adapter 开发指南

Adapter 的职责是把第三方 Agent 的进程或输出转换为统一 `TaskStatus`，不得直接操作 GUI。配置型 Adapter 使用 `AgentConfig` 中的 `process_patterns`、`running_patterns`、`waiting_input_patterns`、`waiting_approval_patterns`、`completed_patterns` 和 `error_patterns`。

新增内置 Agent 时，在 `src/aacc/adapters.py` 的 `PRESETS` 中加入保守的显示名、进程匹配与逐行状态正则。匹配应包含明确的行首或上下文，不应使用孤立的 `allow`、`done` 等常见单词。`GenericCLIAdapter` 会清理 ANSI、拒绝超过 4096 字符的行，并对每次正则搜索设置 20ms 超时。

结构化 Hook 应向 `POST /api/v1/tasks/{task_id}/status` 发送 `status`、最多 2000 字符的 `message`、唯一 `source` 和 0–1 的 `confidence`。Hook 失败不得阻塞 Agent。不要向 AACC 发送完整提示词、私有代码、密码或 API Key。

新增 Adapter 必须为进程检测、每个显式状态模式、模糊文本不误报、ANSI 与超长行编写测试。核心 GUI 和 API 不应增加对具体 Agent 类型的分支。

