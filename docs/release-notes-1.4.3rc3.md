# AACC 1.4.3-rc.3 Release Notes / 发布说明

## English

1.4.3-rc.3 is a macOS release candidate on top of 1.4.3-rc.2. It adds OpenCode
support (Go-plan quota bar + CLI task discovery) and is a **macOS-only
increment**: the Windows build remains at 1.4.3-rc.2 with no new artifact.

- **Feature — OpenCode Go-plan quota bar.** A self-owned web view (macOS) signs
  you into opencode.ai with GitHub or Google, keeps the session cookie in the
  per-application web storage (persists across AACC restarts), and reads the
  /go workspace page. The page already renders the Go-plan quota bars, so AACC
  extracts the rolling / weekly / monthly quota (percentage + reset countdown)
  directly from the rendered page and displays it in a three-row strip below
  the Kimi quota bar. Configure `opencode_workspace_url` in `config.yaml`.
  No prompt, reply, tool command, or reasoning content is ever read; only the
  rendered percentage and reset text are extracted.
- **Feature — OpenCode CLI task discovery.** AACC polls the local opencode
  SQLite database (`~/.local/share/opencode/opencode.db`) read-only every 5
  seconds and infers per-session status from the latest message-part snapshot:
  a pending permission request (`tool` part `state.status = pending`) shows
  the yellow "waiting approval" light; active streaming or a running tool
  shows blue "running"; a finished turn (`step-finish` or completed/error
  tool) shows green "completed"; a stale session with the opencode process
  alive shows yellow "waiting input"; a stale session without the process
  shows green "completed". Only part type/status/timestamp are read — never
  prompt text, tool commands, or reasoning content.
- **Fix — finished opencode turns turn green immediately.** A completed turn
  (`step-finish` / `tool completed`) now reports the green completed state
  right away instead of staying blue until the opencode process exits.
- **Fix — forced-stop sessions leave running state immediately.** When the
  opencode process disappears after a forced stop, its matching session now
  reports green completed immediately instead of waiting for the 90-second
  activity window.
- **Known limits.** The quota bar depends on the rendered layout of the
  opencode.ai /go workspace page; if opencode.ai changes that page, the
  extraction may need updating. Status inference is approximate (official
  idle/busy is a runtime event, not persisted). OpenCode support is macOS
  first; Windows (Edge-based session and discovery) is a later iteration.

Evidence boundary: local macOS run passes 1098 tests, ruff check, ruff format,
mypy, and a 97% changed-line diff-cover. Hosted CI runs on push. The opencode
feature was exercised against a live signed-in workspace on this Mac; hosted
CI covers the code level only, and consumer Windows 10/11 behavior is not
claimed as verified.

## 中文

1.4.3-rc.3 是在 1.4.3-rc.2 之上的 macOS 发布候选。本次新增 OpenCode 支持
（Go 套餐额度条 + CLI 任务发现），且为 **macOS 单独递增**：Windows 构建保持
1.4.3-rc.2，不发布新产物。

- **功能 — OpenCode Go 套餐额度条。** 自持网页视图（macOS）以 GitHub 或 Google
  登录 opencode.ai，会话 Cookie 保存在每应用网页存储（跨 AACC 重启保留），
  并读取 /go 工作区页面。页面本身已渲染 Go 套餐额度条，因此 AACC 直接从
  渲染后的页面提取滚动/每周/每月额度（百分比 + 重置倒计时），三行展示在
  Kimi 额度条下方。在 `config.yaml` 配置 `opencode_workspace_url`。
  绝不读取 prompt、回复、工具命令或 reasoning 内容——只提取渲染出的百分比
  与重置文本。
- **功能 — OpenCode CLI 任务发现。** 每 5 秒只读轮询本机 opencode SQLite 数据库
  （`~/.local/share/opencode/opencode.db`），依据最新消息部件快照推断各会话状态：
  权限挂起（`tool` 部件 `state.status = pending`）→ 黄色"等待同意"；流式活动或
  工具执行 → 蓝色"进行中"；回合结束（`step-finish` 或 completed/error 工具）→
  绿色"已完成"；停滞且 opencode 进程在 → 黄色"等待输入"；停滞且进程不在 →
  绿色"已完成"。只读取部件类型/状态/时间戳——绝不读取 prompt 文本、工具命令或
  reasoning 内容。
- **修复 — 完成的 opencode 回合立即变绿。** 回合结束（`step-finish` /
  `tool completed`）立即上报绿色已完成，不再停留蓝色直到 opencode 进程退出。
- **修复 — 强制结束后立即退出进行中状态。** OpenCode 进程因强制结束消失后，匹配
  会话立即上报绿色已完成，不再等待 90 秒活动窗口。
- **已知限制。** 额度条依赖 opencode.ai /go 工作区页面的渲染布局；若 opencode.ai
  改版，提取可能需要跟进。状态推断为近似（官方 idle/busy 是运行时事件，不落库）。
  OpenCode 支持先做 macOS；Windows（Edge 会话与发现）为后续迭代。

证据边界：本机 macOS 运行通过 1098 项测试、ruff check、ruff format、mypy 与
97% 改动行 diff-cover。托管 CI 在推送时运行。OpenCode 功能已在本机对真实
已登录工作区实测；托管 CI 仅覆盖代码层面，消费级 Windows 10/11 行为不宣称已验证。
