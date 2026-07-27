# AACC 1.4.2（发布候选说明草案）

`v1.4.2` 尚未创建，正式 GitHub Release 与资产也尚未发布。本文件描述当前候选
代码和预期交付物，不把自动化测试等同于 Windows 10/11 真机兼容性。只有下方
人工门禁全部关闭后，才能把本草案转为正式发布说明。

候选产物：

- Windows 主下载：`AACC-1.4.2-Setup.exe` 与
  `AACC-1.4.2-Setup.exe.sha256`
- macOS：`AACC-1.4.2.dmg` 与 `AACC-1.4.2.dmg.sha256`

## 中文

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

### Windows Setup 与安全加固

- Setup 仅安装给当前用户，不请求管理员提权，默认路径为
  `%LocalAppData%\Programs\AACC`。它始终创建开始菜单快捷方式，可选但默认不
  勾选桌面快捷方式，不添加开机启动。
- 再次运行 Setup 会先请求 AACC 在 20 秒内优雅退出，再原位升级。卸载会移除
  程序、快捷方式与卸载注册信息。升级和卸载都保留 `%APPDATA%\AACC` 中由
  AACC 管理的设置、历史、数据库、凭据与复用决定。原生 WebView 存储由操作
  系统另行管理，因此不声称 Setup 会保留或移除网页会话。
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
- Setup、AACC 与 broker 在 1.4.2 仍未签名。Windows 会显示“未知发布者”或
  SmartScreen；必须先核对配套 SHA-256，再选择“更多信息 → 仍要运行”。

### 自动化证据边界

候选工作流在 GitHub 托管的 Windows Server 2022 与 Windows Server 2025
环境构建原生 broker、PyInstaller onedir 与 Setup，并设计为执行冻结包首次启动、
安装、重装、失败回滚、卸载、ACL 与进程清理冒烟。这些属于托管服务器自动证据；
最终候选提交的完整运行结果仍需在合并前记录。

即使上述托管工作流全部通过，它也不能证明消费级 Windows 10 或 Windows 11、
标准用户安装、另一账户拒读、SmartScreen 交互、真实 Kimi/Codex、托盘、聚焦、
热键与长时间运行体验。相关项目必须在真机逐项勾选。

## English

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
- Setup, AACC, and the broker remain unsigned in 1.4.2. Verify the companion
  SHA-256 before using the SmartScreen **More info → Run anyway** path.

### Automated evidence boundary

The candidate workflow builds the broker, PyInstaller onedir payload, and
Setup on hosted Windows Server 2022 and Windows Server 2025, with product-smoke
coverage designed for frozen first launch, installation, reinstall, rollback,
uninstall, ACLs, and process cleanup. The complete result for the final
candidate commit still must be recorded before merge.

Hosted Windows Server evidence is not the real Windows 10/11 consumer test. It
does not replace standard-user installation, separate-account access denial,
SmartScreen interaction, real Kimi/Codex, tray, focus, hotkey, and long-running
checks.

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
- [ ] Confirm a real read-only Codex `WEEK` refresh without starting a task.
- [ ] Attach the completed checklist evidence to the release PR or notes.
- [ ] Only then create tag and release `v1.4.2`.
