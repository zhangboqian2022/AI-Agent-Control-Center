# AACC 1.4.4-rc.1 Release Notes / 发布说明

## English

This RC is the cautious follow-up to the 1.4.3 formal release and incorporates
the verified findings from the multi-agent review. It is a prerelease, not a
claim of consumer Windows 10/11 hardware validation.

- **Truthful task terminal states.** OpenCode permission waits are shown as
  `WAITING_APPROVAL`; explicit tool failures and cancellations are preserved;
  a missing process is `STOPPED` unless an explicit completion marker exists.
  Kimi `turn.cancel` is no longer mapped to completed.
- **Stable state transitions.** Terminal states reject stale active candidates,
  initial `IDLE` no longer blocks fresh low-confidence process evidence, and
  repeated runtime discovery registration no longer reinitializes SQLite.
  CLI/API status updates use the documented manual/source contract.
- **Configured Adapter runtime.** Non-native configured adapters now run in a
  separate polling service and report only conservative process-level
  running/stopped evidence. They never infer agent-specific completion and do
  not read prompts, responses, or command output.
- **Safer desktop control.** Windows title matching is unique and foreground
  focus is rechecked immediately before input. macOS Terminal/iTerm2 matching
  is unique and frontmost application/window identity is checked before input.
  Ambiguous or changed targets fail closed.
- **Local security and diagnostics.** POSIX configuration replacement is
  anchored to an opened parent directory; loopback HTTP clients do not inherit
  proxy environment variables; AppleScript CR/LF quoting and Kimi Desktop
  candidate logging avoid the previously identified edge cases.
- **Release evidence.** macOS uses public artifact naming
  `AACC-1.4.4-rc.1.dmg`; Windows keeps the PEP 440 Setup name
  `AACC-1.4.4rc1-Setup.exe`. The macOS bundle build number is deterministic
  instead of the old hard-coded value.

### Review decisions

- Accepted: OpenCode/Kimi state corrections, lifecycle ordering, SQLite
  registration fix, loopback trust boundary, target fail-closed behavior, and
  release naming/evidence corrections.
- Partially accepted: configuration TOCTOU (anchored POSIX replacement is
  implemented; Windows relies on native protected DACLs and remains a known
  platform-specific boundary), adapter semantics (process-level only), and
  hosted Windows 10/11 compatibility contracts (not real-device testing).
- Rejected as release blockers: the claim that the current repository lacks
  `uv.lock`, and historical 1.2-era findings that are already covered by later
  releases. The old review-package provenance is not evidence about this RC.

### Evidence boundary

The CI matrix covers macOS and Windows Server 2022/2025, plus Windows 10/11
compatibility contracts executed on hosted infrastructure. It does not prove
SmartScreen, tray behavior, foreground focus, hotkeys, or long-running behavior
on consumer Windows 10/11 hardware. This RC remains unsigned/not notarized.

## 中文

本 RC 是 1.4.3 正式版之后的谨慎善后版本，纳入多机器人评审中经过代码和
测试复核的结论。它是预发布版本，不宣称已经完成消费级 Windows 10/11 真机验证。

- **终态如实展示。** OpenCode 权限等待显示为 `WAITING_APPROVAL`；工具明确失败和
  取消会保留对应状态；进程消失但没有明确完成标记时显示 `STOPPED`，不伪造完成。
  Kimi 的 `turn.cancel` 不再误判为完成。
- **状态转换稳定。** 终态会拒绝过期的活动候选；初始 `IDLE` 不再阻断新鲜的低可信度
  进程证据；重复运行时注册不会重复初始化 SQLite。CLI/API 状态更新统一使用文档化的
  manual/source 约定。
- **配置 Adapter 接入运行时。** 非原生的已配置 Adapter 通过独立轮询服务接入，
  只提供保守的进程级运行/停止证据，不推断 Agent 专属完成语义，也不读取 prompt、
  回复或命令输出。
- **桌面控制失败关闭。** Windows 标题匹配必须唯一，并在注入前立即复核前台窗口；
  macOS Terminal/iTerm2 标题匹配必须唯一，并在注入前检查前台应用/窗口身份。目标歧义
  或焦点变化都会拒绝注入。
- **本机安全与诊断。** POSIX 配置替换改为基于已打开父目录的安全相对替换；回环 HTTP
  客户端不继承代理环境变量；AppleScript 的 CR/LF 转义及 Kimi Desktop 候选日志已加固。
- **发布证据。** macOS 使用公开产物名 `AACC-1.4.4-rc.1.dmg`；Windows 保留 PEP 440
  安装包名 `AACC-1.4.4rc1-Setup.exe`。macOS Bundle 的构建号改为确定性数值，不再硬编码旧值。

### 评审决策

- 接受：OpenCode/Kimi 状态修复、生命周期排序、SQLite 注册修复、回环请求信任边界、
  桌面目标失败关闭，以及发布命名和证据边界修正。
- 部分接受：配置 TOCTOU（已实现 POSIX 锚定替换；Windows 依赖原生受保护 DACL，仍保留
  平台边界说明）、Adapter 语义（仅进程级）、托管环境的 Windows 10/11 兼容契约（不是
  真机测试）。
- 不作为发布阻塞项接受：当前仓库缺少 `uv.lock` 的说法，以及已经被后续版本覆盖的
  1.2 历史问题。旧送审包的 provenance 不能作为本 RC 的证据。

### 证据边界

CI 覆盖 macOS、Windows Server 2022/2025，以及在托管基础设施上执行的 Windows 10/11
兼容性契约；这不能证明消费级 Windows 10/11 真机上的 SmartScreen、托盘、前台聚焦、
热键或长时间运行行为。本 RC 仍未代码签名，也未公证。
