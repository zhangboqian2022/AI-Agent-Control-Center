# AACC 1.4.2（发布候选说明草案）

`v1.4.2` 尚未创建，正式 GitHub Release 与资产也尚未发布。本文件描述当前候选
代码和预期交付物，不把自动化测试等同于 Windows 10/11 真机兼容性。只有下方
人工门禁全部关闭后，才能把本草案转为正式发布说明。

候选产物：

- Windows 主下载：`AACC-1.4.2-Setup.exe` 与
  `AACC-1.4.2-Setup.exe.sha256`
- macOS：`AACC-1.4.2.dmg` 与 `AACC-1.4.2.dmg.sha256`

## 中文

### 中英文即时切换

- 面板头部新增目的语言按钮：中文界面显示 `EN`，英文界面显示 `中`；点击后
  macOS 与 Windows 的完整界面立即切换，无需重启。
- 首次启动跟随系统语言（中文系统使用中文，其他系统使用英文），之后的明确
  选择会持久保存。切换不会刷新额度，也不会改变监控任务、任务状态、Kimi
  登录状态、窗口或紧凑模式。
- 紧凑模式保留在设置和托盘菜单，只是不再占用头部按钮。本功能仍属于 1.4.2
  候选版，不改变未发布边界。

### 额度显示

- Codex 只显示一行更大的 `WEEK`，数据优先来自本机已安装 Codex
  `app-server` 的只读 `account/rateLimits/read`，不可用时才回退到有界本地
  会话元数据。AACC 不启动任务、不发送提示词、不发起登录。
- Kimi 严格按 `5H`、`WEEK`、`MONTH` 三行显示。操作系统原生的每应用
  WebView 存储保留 Kimi 第一方站点会话。对于原生网页会话复用，AACC 只保存
  受保护的复用决定，不把 Cookie、密码、网页 Bearer Token、账户名或额度值
  复制进该门禁；Kimi Code OAuth 凭据由现有凭据保护另行保存。明确退出登录
  会先同步关闭复用，再尝试有界的原生站点数据清理。
- 网页源与 Kimi Code 备用源从同一个五分钟周期开始刷新；Kimi Code 只能用
  足够新的数据补临时缺失的 `5H`/`WEEK`，不能虚构 `MONTH`。额度查询只读取
  元数据、不发送提示词，也不消耗生成 Token。
- 每行将百分比、进度条和完整本地重置日期时间分开排版。百分比已知但没有
  可信重置时间时，百分比仍显示，重置位置为 `--`；其他缺失值也诚实显示
  `--`，不会伪装为 `0%`。

### Codex 任务状态

- Codex 会话的 `turn_aborted` 现在作为“已取消”终态处理，不会再因为取消前的
  工具活动而长期误报“执行中”。过期会话降级为“状态未知”时保留最新文件时间，
  让状态机能够可靠接受这次降级。

### Windows Setup 与安全加固

- Setup 仅安装给当前用户，不请求管理员提权，默认路径为
  `%LocalAppData%\Programs\AACC`。它始终创建开始菜单快捷方式，可选但默认不
  勾选桌面快捷方式，不添加开机启动。
- 再次运行 Setup 会先请求 AACC 在 20 秒内优雅退出，再原位升级。卸载会移除
  程序、快捷方式与卸载注册信息。升级和卸载都保留 `%APPDATA%\AACC` 中由
  AACC 管理的设置、历史、数据库、凭据与复用决定。原生 WebView 存储由操作
  系统另行管理，因此不声称 Setup 会保留或移除网页会话。
- 在写入 AACC 文件前，Setup 会为当前用户确保 Microsoft Evergreen WebView2
  运行时可用；仅在运行时不存在时才需要网络，已安装且可用的运行时会被复用。若
  Kimi 原生登录视图没有加载事件，对话框会用固定的 15 秒 WebView2/网络修复诊断
  和 Microsoft 修复链接替换空白界面。
- Windows 会在 Qt 初始化前把可写的 WebView2 用户数据目录固定为
  `%LOCALAPPDATA%\AACC\kimi-web-session`，并先按当前用户保护该目录，避免
  WebView2 使用 `AACC.exe` 旁边对安装型程序不可靠的默认目录。
- 优雅退出使用当前会话内、按目标 PID 命名的 Windows Event；Event 的 Owner
  固定为当前用户，DACL 仅允许当前用户、Local System 与 Administrators。
  控制端仍会核对精确窗口标题、PID 与完整 EXE 路径，并等待同一个进程句柄
  退出；同名对象抢占、权限错误或超时都会安全失败，绝不强杀应用。
- 敏感目录、配置、Kimi 凭据、SQLite 数据库及存在时的 WAL/SHM 使用原生精确
  受保护 DACL，仅允许当前用户、Local System 与本机 Administrators 完全控制；
  不再依赖 `whoami.exe` 或 `icacls.exe`。
- 打包后的 Codex 只读 app-server 由 `AACC.exe` 旁的固定用途静态 broker 启动。
  broker 只接受一个固定协议与已授权的绝对 Codex 路径，并用 Job Object 管理
  子进程树；不再调用 `taskkill.exe`，也不会退化为任意命令执行器。
- Windows 构建只接受 SHA-256 锁定且 Authenticode 有效的官方 Inno Setup
  6.7.1 引导包；每次在新的私有目录中提取并探测真实编译引擎，不接受本地
  编译器覆盖或复用旧提取目录，完成后安全清理。
- 重装会在任何写入前关闭 AACC，并根据新包与已安装的精确 manifest 独占
  预检主程序、broker、动态编号的 Inno 卸载器、安装元数据及所有新旧受管理
  文件、目录和快捷方式；这些路径上的文件锁或重解析点会让 Setup 直接拒绝，
  不再依赖 Inno 并不提供的旧字节事务回滚。主程序和 broker 也会先于
  `_internal` 写入，以降低预检后竞态的影响。
- Setup、AACC 与 broker 在 1.4.2 仍未签名。Windows 会显示“未知发布者”或
  SmartScreen；必须先核对配套 SHA-256，再选择“更多信息 → 仍要运行”。

### 自动化证据边界

候选提交 `0b2730f` 的
[GitHub Actions 运行 30319350661](https://github.com/zhangboqian2022/AI-Agent-Control-Center/actions/runs/30319350661)
已在托管的 Windows Server 2022 与 Windows Server 2025 环境通过：原生
broker、PyInstaller onedir 与 Setup 构建，冻结包首次启动、安装、重装、写入前
锁定目标拒绝、卸载、ACL 与进程清理产品冒烟，以及 Setup、SHA-256 和便携 ZIP
的严格内容校验与资产上传。macOS 质量作业、Windows 双版本测试、ruff、格式、
mypy 和依赖审计也在同一次运行中通过。这些仍只是托管服务器自动证据。

即使上述托管工作流全部通过，它也不能证明消费级 Windows 10 或 Windows 11、
标准用户安装、另一账户拒读、SmartScreen 交互、真实 Kimi/Codex、托盘、聚焦、
热键与长时间运行体验。相关项目必须在真机逐项勾选。1.4.2 也不声称安装具有
断电恢复、完整事务性，或能消除预检后才出现的新文件锁；严格原子升级需在后续
版本采用 staging/backup/swap 架构。

### 尚未关闭的 macOS 双语人工门禁

- [ ] 在 macOS 上反复切换中英文，并使用真实任务、额度数据和打开的 Kimi 登录对话框；
  确认所有可见界面即时切换，而额度、任务/登录、窗口和紧凑模式状态保持不变。

## English

### Live Chinese/English UI

- The header shows a destination-language action: `EN` in the Chinese UI and
  `中` in the English UI. It switches the complete macOS or Windows UI
  immediately without a restart.
- First launch follows the system language (Chinese for a Chinese system
  language, English otherwise), and an explicit selection persists. Switching
  does not refresh quotas or change monitored tasks or login state, task state,
  window state, or compact mode.
- Compact mode remains in Settings and the tray menu; it no longer occupies a
  header button. This feature remains inside the unreleased 1.4.2 candidate.

### Quota display

- Codex renders one larger `WEEK` row. AACC first calls the installed Codex
  app-server’s read-only `account/rateLimits/read` method and falls back to
  bounded local session metadata. It does not start a task, submit a prompt, or
  initiate login.
- Kimi renders `5H`, `WEEK`, and `MONTH` in that order. The operating system's
  native per-application WebView store retains the first-party Kimi session.
  For native website-session reuse, AACC persists only a protected reuse
  decision and does not copy cookies, passwords, a website bearer token,
  account names, or quota values into that gate. Kimi Code OAuth credentials
  remain separately protected. Explicit logout synchronously disables reuse,
  then attempts bounded native site-data cleanup.
- The web source and Kimi Code fallback start in the same five-minute cycle.
  Only sufficiently fresh Kimi Code data may fill a missing `5H` or `WEEK`;
  it never supplies `MONTH`. Metadata-only lookups send no prompt and use no
  generation tokens.
- Each available row separates percentage, progress, and complete local reset
  date/time. A known percentage without a trustworthy reset remains visible
  while the reset displays `--`; other missing data also stays `--`, never
  fabricated `0%`.

### Codex task status

- A Codex `turn_aborted` event is now a terminal **Cancelled** state, so tool
  activity that preceded an aborted turn can no longer leave a second task
  permanently reported as running. A stale session downgrade also retains the
  latest file timestamp so the state machine can accept it reliably.

### Windows Setup and security

- `AACC-1.4.2-Setup.exe` is a per-user, non-elevated installer that defaults to
  `%LocalAppData%\Programs\AACC`. It creates a Start Menu shortcut, offers an
  unchecked desktop shortcut, and adds no login item.
- Rerunning Setup requests bounded graceful shutdown and upgrades in place.
  Uninstall removes the program, shortcuts, and registration. Upgrade and
  uninstall preserve AACC-owned settings, history, database, credentials, and
  reuse decision under `%APPDATA%\AACC`. The operating system owns the native
  WebView store separately, so this is not a claim that Setup preserves or
  removes the website session.
- Before AACC files are changed, Setup ensures Microsoft's Evergreen WebView2
  Runtime is available for the current user. Network is required only if the
  Runtime is absent; a usable installed Runtime is reused. If Kimi's native
  login view has no loading event, its dialog replaces the blank surface with a
  fixed 15-second WebView2/network repair diagnostic and Microsoft repair link.
- Windows sets the writable WebView2 user data folder to
  `%LOCALAPPDATA%\AACC\kimi-web-session` before Qt initializes and protects it
  for the current user first. This avoids WebView2's installer-sensitive
  default directory beside `AACC.exe`.
- Graceful shutdown uses a per-target-PID Windows Event in the current session.
  Its owner is the current user and its protected DACL allows only that user,
  Local System, and Administrators. The controller still verifies the exact
  window title, PID, and full executable path, then waits on that same process
  handle. Name squatting, access errors, and timeouts fail closed; the installer
  never force-kills AACC.
- A native DACL protects sensitive directories, configuration, credentials,
  SQLite database, WAL, and SHM with the exact current-user, Local System, and
  Administrators allowlist. Runtime protection no longer executes
  `whoami.exe` or `icacls.exe`.
- A fixed-purpose broker beside `AACC.exe` starts the packaged read-only Codex
  app-server under a Job Object. It accepts only the fixed protocol and
  authorized absolute Codex path, never arbitrary commands, and removes the
  need for `taskkill.exe`.
- Windows builds accept only the official Inno Setup 6.7.1 bootstrap with its
  pinned SHA-256 and a valid Authenticode signature. Every build extracts and
  probes the real compiler engine in a fresh private directory, rejects local
  compiler overrides and old extracted trees, and safely cleans it afterward.
- Reinstall shuts AACC down and uses the new and installed exact manifests to
  exclusively preflight the main executable, broker, dynamically numbered Inno
  uninstaller, installer metadata, shortcuts, and every old or new managed file
  before any write. Managed directories are preflighted with delete access as
  well. Locks or reparse points on those paths stop Setup before mutation
  instead of relying on a transactional byte-restore guarantee Inno does not
  provide. The two root executables are also ordered before `_internal` to
  reduce the impact of a race after preflight.
- Setup, AACC, and the broker remain unsigned in 1.4.2. Verify the companion
  SHA-256 before using the SmartScreen **More info → Run anyway** path.

### Automated evidence boundary

Candidate commit `0b2730f` passed
[GitHub Actions run 30319350661](https://github.com/zhangboqian2022/AI-Agent-Control-Center/actions/runs/30319350661)
on hosted Windows Server 2022 and Windows Server 2025: native broker,
PyInstaller onedir, and Setup builds; frozen first launch; installation;
reinstall; pre-mutation locked-target refusal; uninstall; ACL and process
cleanup product smokes; and strict Setup, SHA-256, portable ZIP content
verification and artifact upload. The same run also passed the macOS quality
job, both Windows test legs, Ruff, formatting, mypy, and dependency audit.
This remains hosted-server automated evidence only.

Hosted Windows Server evidence is not the real Windows 10/11 consumer test. It
does not replace standard-user installation, separate-account access denial,
SmartScreen interaction, real Kimi/Codex, tray, focus, hotkey, and long-running
checks. Version 1.4.2 also makes no claim of power-loss recovery, full
transactional installation, or atomic recovery from a new lock acquired after
preflight. Strict atomic upgrades require a later staging/backup/swap
architecture.

## Manual release gates

- [ ] Complete every item in
  `docs/windows-verification-checklist.zh-CN.md` on a real Windows 10/11
  machine using the final candidate.
- [ ] Confirm a separate unprivileged Windows account cannot read the
  protected configuration, credentials, database, WAL, or SHM.
- [ ] Sign in to a real Kimi membership account from AACC and confirm `5H`,
  `WEEK`, and `MONTH` refresh together and survive an app restart.
- [ ] Obtain macOS and Windows manual sign-off for native-session persistence,
  explicit logout across restart, and the shared five-minute refresh cycle.
- [ ] On macOS, repeatedly switch Chinese/English with real tasks, quota data,
  and an open Kimi login dialog; confirm all visible UI changes while quota,
  task/login, window, and compact state remain unchanged.
- [ ] Confirm a real read-only Codex `WEEK` refresh without starting a task.
- [ ] Attach the completed checklist evidence to the release PR or notes.
- [ ] Only then create tag and release `v1.4.2`.
