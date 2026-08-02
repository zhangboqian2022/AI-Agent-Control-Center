# AACC 1.4.4-rc.2 Release Notes / 发布说明

## English

1.4.4-rc.2 is the review-fix follow-up to 1.4.4-rc.1. It incorporates the
second-round external review (Qwen × Gemini) findings that were verified
against the codebase, plus two OpenCode correctness fixes found during
acceptance. It is a prerelease, not a claim of consumer Windows 10/11
hardware validation.

- **Security — OAuth parameters never reach logs.** The OpenCode session no
  longer logs full navigation URLs (which could carry `code`/`state` query
  parameters); only scheme and host are logged.
- **Security — `/send-text` rate limit.** The send-text endpoint now throttles
  per client token (10 requests / 10 s) and returns 429 when exceeded.
- **Security — metadata bounds.** The API status endpoint validates metadata
  (max 20 keys, key length ≤ 64, serialized ≤ 8 KiB → 422) and logs a warning
  when an unknown `source` value is normalized.
- **Correctness — OpenCode step-aware inference.** A running tool inside the
  current step is no longer shadowed by a slightly newer text part, and a
  `step-finish` / completed tool only turns the session green once the signal
  goes stale (90 s) — step gaps no longer flash green while the agent is
  still processing.
- **Correctness — single-digit percentages.** OpenCode `usagePercent` is a
  0–100 integer; values like `1` are no longer scaled to 100.
- **Correctness — process liveness.** The unreadable-cwd fallback only applies
  to sessions without a known work directory, so one unreadable process no
  longer keeps every session "alive".
- **Correctness — DOM extraction retry.** The usage-extraction script uses an
  independent attempt counter instead of the global refresh generation, so
  retries never stop after the 51st refresh.
- **Lifecycle — process-group cleanup.** `aacc-run` and the macOS Codex
  app-server now spawn into a new session and signal the process group on
  POSIX, so grandchildren are reaped; Windows keeps the broker's Job Object
  path. `KNOWN_LIMITATIONS` wording updated accordingly.
- **Lifecycle — bounded same-source override.** The state machine's
  same-source newer-state carve-out now requires the current state to be
  stale or the candidate within a bounded confidence gap.
- **Stability — adapter polling.** One failing adapter no longer aborts the
  round, and the round captures a single shared process snapshot instead of
  one `psutil.process_iter` per adapter.
- **Build — transactional install.** `install.sh` builds and installs the new
  runtime into a staging venv before removing the old one; a build failure
  leaves the previous runtime untouched. `build_dmg.sh` now emits the
  documented `.dmg.sha256` sidecar, and `AACC-windows.spec` derives its root
  from the spec location instead of the build CWD.
- **Docs — least-privilege deployment.** `SECURITY.md` recommends
  `keyboard_injection: false` for environments that do not need desktop
  control; the Windows release note wording now states precisely that the
  foreground window *handle* is re-checked (PID/image are not re-verified).

Evidence boundary: local macOS run passes 1249 tests, ruff check, ruff
format, and mypy. Hosted CI runs on push. Consumer Windows 10/11 behavior is
covered by a manual verification checklist, not by automation.

## 中文

1.4.4-rc.2 是 1.4.4-rc.1 的评审修复后续版本，落实第二轮外部评审
（千问 × Gemini）中经代码核实的问题，并包含验收中发现的两项 OpenCode
正确性修复。本版本为预发布，不宣称消费级 Windows 10/11 真机验证。

- **安全 — OAuth 参数不再进入日志。** OpenCode 会话不再记录完整导航 URL
  （可能携带 `code`/`state` 查询参数），只记录 scheme 与 host。
- **安全 — `/send-text` 限流。** send-text 接口按客户端 Token 限流
  （10 次 / 10 秒），超出返回 429。
- **安全 — metadata 边界。** API 状态接口校验 metadata（键数 ≤20、
  键长 ≤64、序列化 ≤8 KiB，超限 422），未知 `source` 归一化时记录告警。
- **正确性 — OpenCode 步感知推断。** 当前 step 内的运行中工具不再被
  稍晚的 text 部件遮挡；`step-finish`/已完成工具在信号停滞（90 秒）后才
  变绿——步间停顿不再闪烁绿色。
- **正确性 — 个位百分比。** OpenCode `usagePercent` 为 0–100 整数，
  `1` 不再被放大为 100。
- **正确性 — 进程存活。** cwd 不可读的兜底只适用于无已知工作目录的会话，
  单个不可读进程不再让所有会话"存活"。
- **正确性 — DOM 提取重试。** 用量提取脚本改用独立尝试计数而非全局刷新
  代数，第 51 次刷新后重试不再失效。
- **生命周期 — 进程组回收。** `aacc-run` 与 macOS Codex app-server 在
  POSIX 上以新会话派生并按进程组发信号，孙进程可被回收；Windows 保持
  broker 的 Job Object 路径。`KNOWN_LIMITATIONS` 措辞同步更新。
- **生命周期 — 有界同源覆盖。** 状态机同源新状态豁免现在要求当前状态
  已过期，或候选置信度在有限差距内。
- **稳定性 — 适配器轮询。** 单个适配器异常不再中断整轮，每轮共享一次
  进程快照而非每个适配器各跑一次 `psutil.process_iter`。
- **构建 — 事务化安装。** `install.sh` 先将新运行时构建安装到暂存 venv，
  成功后才移除旧运行时；构建失败保留旧运行时。`build_dmg.sh` 生成文档要求
  的 `.dmg.sha256` 边车文件；`AACC-windows.spec` 从 spec 自身位置派生根目录，
  不再依赖构建 CWD。
- **文档 — 最小权限部署。** `SECURITY.md` 建议无需桌面控制的环境设置
  `keyboard_injection: false`；Windows 发布说明措辞修正为：注入前复核的是
  前台窗口**句柄**（不重新验证 PID/映像）。

证据边界：本机 macOS 运行通过 1249 项测试、ruff check、ruff format 与
mypy。托管 CI 在推送时运行。消费级 Windows 10/11 行为以人工验证清单覆盖，
非自动化门禁。
