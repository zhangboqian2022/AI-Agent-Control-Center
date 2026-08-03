# AACC 1.4.4-rc.5 Release Notes / 发布说明

## English

1.4.4-rc.5 removes a storage-hygiene flaw found during live acceptance:
stale discovered run-states never expired. It is a prerelease, not a claim
of consumer Windows 10/11 hardware validation.

- **Fix — unseen discovered run-states expire after one hour.** Discovery
  only evaluates selected sessions and returns at most 20 tasks per round,
  so a session that falls out of the window never receives a fresh
  candidate; its last RUNNING/WAITING state persisted in `current_states`
  forever (two ancient Codex sessions from June/July were still stored as
  "running"). Each poll round now scans the brand's discovered states and
  normalizes any run-state (STARTING/THINKING/RUNNING/WAITING/WARNING/
  PAUSED) whose session was not seen this round and whose last update is
  older than one hour to UNKNOWN ("长时间未更新") through the regular state
  machine, so subscribers see the correction live. Genuinely active tasks
  heartbeat at least once a minute and are never touched; terminal,
  manual, idle, and already-unknown states are out of scope.

Evidence boundary: local macOS run passes 1258 tests, ruff check, ruff
format, and mypy. Hosted CI runs on push. Consumer Windows 10/11 behavior
is covered by a manual verification checklist, not by automation.

## 中文

1.4.4-rc.5 修复实机验收发现的存储卫生问题：陈旧的 discovered 运行态永不
过期。本版本为预发布，不宣称消费级 Windows 10/11 真机验证。

- **修复 — 一小时未见的 discovered 运行态自动过期。** 发现逻辑每轮只
  评估选中的会话且最多返回 20 个任务，跌出窗口的会话永远等不到新候选，
  其最后的 RUNNING/WAITING 状态会无限期残留在 `current_states` 里
  （实测有两条 6 月/7 月的 Codex 会话至今仍存为"运行中"）。每轮轮询
  现在扫描该品牌的 discovered 状态，把本轮未见且超过一小时未更新的
  运行态（STARTING/THINKING/RUNNING/WAITING/WARNING/PAUSED）经常规
  状态机归一化为 UNKNOWN（"长时间未更新"），订阅者实时看到纠正。
  真正活跃的任务每分钟至少一次心跳，不会被误伤；终态、manual、空闲
  与未知状态不在处理范围。

证据边界：本机 macOS 运行通过 1258 项测试、ruff check、ruff format 与
mypy。托管 CI 在推送时运行。消费级 Windows 10/11 行为以人工验证清单
覆盖，非自动化门禁。
