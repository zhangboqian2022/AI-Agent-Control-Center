# AACC 1.4.4-rc.6 Release Notes / 发布说明

## English

1.4.4-rc.6 completes the stale discovered run-state expiry shipped in
rc.5: live acceptance showed the two ancient Codex RUNNING states were
still present after installing rc.5. It is a prerelease, not a claim of
consumer Windows 10/11 hardware validation.

- **Fix — expiry now covers states that only exist in storage.** rc.5's
  sweep iterated the in-memory task table, which after an app restart
  only holds YAML-configured and freshly discovered tasks. A zombie
  run-state whose session is no longer discovered is never re-registered,
  so it was invisible to the sweep and kept its stale RUNNING/WAITING
  status forever. The sweep now reads persisted states directly from the
  store and normalizes qualifying run-states (unseen this round, last
  update older than one hour) to UNKNOWN ("长时间未更新") through the
  regular state machine, notifying subscribers the same way as a normal
  update. A regression test reproduces the restart scenario: register a
  stale discovered run-state, reopen the store with a fresh task manager,
  and verify the state expires.

Evidence boundary: local macOS run passes 1259 tests, ruff check, ruff
format, and mypy. Hosted CI runs on push. Consumer Windows 10/11 behavior
is covered by a manual verification checklist, not by automation.

## 中文

1.4.4-rc.6 补全 rc.5 的陈旧 discovered 运行态过期：实机验收发现安装
rc.5 后两条古老的 Codex RUNNING 状态仍在。本版本为预发布，不宣称消费级
Windows 10/11 真机验证。

- **修复 — 过期扫描覆盖只存在于存储中的状态。** rc.5 的扫描遍历内存
  任务表，而应用重启后内存里只有 YAML 配置的任务和本轮新发现的任务；
  会话不再被发现的僵尸运行态永远不会重新注册，对扫描不可见，于是无限期
  保留陈旧的 RUNNING/WAITING 状态。现在改为直接读取存储中的持久状态，
  把符合条件的运行态（本轮未见、超过一小时未更新）经常规状态机归一化为
  UNKNOWN（"长时间未更新"），并像普通更新一样通知订阅者。新增回归测试
  复现重启场景：注册一条陈旧的 discovered 运行态后用全新的任务管理器
  重新打开存储，验证状态被过期。

证据边界：本机 macOS 运行通过 1259 项测试、ruff check、ruff format 与
mypy。托管 CI 在推送时运行。消费级 Windows 10/11 行为以人工验证清单
覆盖，非自动化门禁。
