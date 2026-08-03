# AACC 1.4.4-rc.3 Release Notes / 发布说明

## English

1.4.4-rc.3 fixes a false "waiting for approval" yellow light on OpenCode
task cards. It is a prerelease, not a claim of consumer Windows 10/11
hardware validation.

- **Fix — pending tool parts are not approval requests.** A `tool` part with
  `state.status = "pending"` only means the call was created but has not
  started (arguments may still be streaming). Verified against a live
  database: opencode does not persist permission requests anywhere (the
  `permission` table holds configured rules; the event journal carries no
  permission events), so a pending part can never be distinguished from a
  genuine approval wait. Pending is therefore no longer inferred as
  "waiting for approval". Fresh pending parts report running; stale pending
  parts resolve through the generic stalled-session path (waiting input
  while the process lives, stopped once the process exits). Before this
  fix, fully autonomous sessions flickered yellow mid-turn and the yellow
  state — at confidence 0.97 — suppressed the blue running state for up to
  300 seconds after work resumed, or stayed latched until process exit when
  a turn was aborted.
- **Docs — known limitation declared.** `KNOWN_LIMITATIONS` (both
  languages) now states that OpenCode approval waits are not persisted and
  cannot be shown; if a future opencode version stores permission requests,
  detection can be restored on a real signal.

Evidence boundary: local macOS run passes 1253 tests, ruff check, ruff
format, and mypy. Hosted CI runs on push. Consumer Windows 10/11 behavior
is covered by a manual verification checklist, not by automation.

## 中文

1.4.4-rc.3 修复 OpenCode 任务卡片误报黄色"等待同意"的问题。本版本为预发布，
不宣称消费级 Windows 10/11 真机验证。

- **修复 — pending 工具部件不等于授权请求。** `state.status = "pending"`
  的工具部件只表示调用已创建、尚未开始（参数可能仍在流式生成）。已对
  真实数据库核实：opencode 不把权限请求落库（`permission` 表存的是配置
  规则，事件日志无任何权限事件），pending 部件与真实的授权等待无法
  区分，因此不再把 pending 推断为"等待同意"。新鲜 pending 判为"进行中"；
  停滞 pending 走通用停滞会话路径（进程在 → 等待输入，进程退出 →
  已停止）。修复前，全自动会话会在回合中反复闪烁黄灯，且黄灯置信度
  0.97 会在恢复运行后最长压制蓝灯 300 秒；回合中止留下永久 pending
  部件时，黄灯一直挂到进程退出。
- **文档 — 声明已知限制。** `KNOWN_LIMITATIONS`（双语）新增说明：
  OpenCode 授权等待不落库、无法展示；若未来 opencode 版本将权限请求
  落库，可基于真实信号恢复检测。

证据边界：本机 macOS 运行通过 1253 项测试、ruff check、ruff format 与
mypy。托管 CI 在推送时运行。消费级 Windows 10/11 行为以人工验证清单
覆盖，非自动化门禁。
