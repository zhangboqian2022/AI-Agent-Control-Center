# AACC 1.4.4-rc.4 Release Notes / 发布说明

## English

1.4.4-rc.4 fixes two task-card semantics issues found during live
acceptance: duplicate Codex cards for subagent threads, and a misleading
green Kimi card between turns of an ongoing conversation. It is a
prerelease, not a claim of consumer Windows 10/11 hardware validation.

- **Fix — Codex subagent threads are folded into the parent card.** Codex
  Desktop spawns sub-agents (e.g. review workers) as separate rollout
  sessions whose first `session_meta` carries
  `source.subagent.thread_spawn` with `forked_from_id` /
  `parent_thread_id` pointing at the parent conversation. AACC treated
  each as an independent task, so one visible conversation produced three
  blue cards. Discovery now skips sessions whose head metadata marks them
  as subagents — in both the panel and the task picker — and keeps
  user-visible forks whose `source` is a plain string. Verified against
  the live database: only the parent conversation remains.
- **Fix — Kimi CLI no longer turns green between turns.** A finished turn
  used to report green "turn completed" immediately, even though the
  conversation continued and the next prompt was seconds away. The CLI
  discovery now reports grey "idle" while the Kimi process lives; green
  is reserved for process exit. Returning to work flips the card blue
  immediately (the idle state carries no confidence advantage, so the
  state machine cannot latch it). Kimi Desktop task monitoring keeps the
  per-turn green, since those tasks are one-shot.

Evidence boundary: local macOS run passes 1256 tests, ruff check, ruff
format, and mypy. Hosted CI runs on push. Consumer Windows 10/11 behavior
is covered by a manual verification checklist, not by automation.

## 中文

1.4.4-rc.4 修复实机验收发现的两个卡片语义问题：Codex subagent 线程重复
成卡，以及持续会话回合间隙 Kimi 卡片误绿。本版本为预发布，不宣称消费级
Windows 10/11 真机验证。

- **修复 — Codex subagent 线程并入主会话卡片。** Codex Desktop 派生的
  子代理（如评审 worker）会生成独立 rollout 会话，其首条
  `session_meta` 带 `source.subagent.thread_spawn` 并以
  `forked_from_id` / `parent_thread_id` 指向主会话。AACC 此前把每个
  都当独立任务，一个可见会话显示三张蓝卡。发现逻辑现在跳过头部元数据
  标记为 subagent 的会话（面板与任务选择器同步生效），保留 `source`
  为纯字符串的用户可见分叉。已对真实数据库验证：只剩主会话一张卡。
- **修复 — Kimi CLI 回合间隙不再变绿。** 此前回合一结束就显示绿色
  "回合已完成"，但会话仍在继续、下一条提问几秒后就来。CLI 发现现在
  在 Kimi 进程存活时显示灰色"空闲"，绿色只留给进程退出后的终态。
  重新开始工作会立即恢复蓝灯（空闲状态不带置信度优势，状态机不会
  闩锁）。Kimi Desktop 任务监控保持回合结束即绿，因为那些任务是
  一次性任务。

证据边界：本机 macOS 运行通过 1256 项测试、ruff check、ruff format 与
mypy。托管 CI 在推送时运行。消费级 Windows 10/11 行为以人工验证清单
覆盖，非自动化门禁。
