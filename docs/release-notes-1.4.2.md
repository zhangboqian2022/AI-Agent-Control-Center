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
- Kimi 严格按 `5H`、`WEEK`、`MONTH` 三行显示。用户在 AACC 内直接登录
  Kimi 官网后，隔离的网页会话缓存在 AACC 本地，直到在 AACC 中明确退出登录。
  三个窗口每五分钟一起刷新；Kimi Code 只能补临时缺失的 `5H`/`WEEK`，不能
  虚构 `MONTH`。
- 每行将百分比、进度条和完整本地重置日期时间分开排版。缺失值诚实显示
  `--`，不会显示为 `0%`。额度查询只是只读元数据请求，不消耗模型 Token。

### Windows Setup 与安全加固

- Setup 仅安装给当前用户，不请求管理员提权，默认路径为
  `%LocalAppData%\Programs\AACC`。它始终创建开始菜单快捷方式，可选但默认不
  勾选桌面快捷方式，不添加开机启动。
- 再次运行 Setup 会先请求 AACC 在 20 秒内优雅退出，再原位升级。卸载会移除
  程序、快捷方式与卸载注册信息。升级和卸载都保留 `%APPDATA%\AACC`，其中包含
  设置、历史、数据库及缓存的 Kimi 网页会话。
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
- Kimi renders `5H`, `WEEK`, and `MONTH` in that order. Its isolated membership
  web session remains cached locally until explicit AACC logout, and all three
  windows refresh together every five minutes. Kimi Code may fill a missing
  `5H` or `WEEK`, never `MONTH`.
- Each available row separates percentage, progress, and complete local reset
  date/time. Missing data stays `--`, never fabricated `0%`. Metadata-only
  quota refreshes consume no model tokens.

### Windows Setup and security

- `AACC-1.4.2-Setup.exe` is a per-user, non-elevated installer that defaults to
  `%LocalAppData%\Programs\AACC`. It creates a Start Menu shortcut, offers an
  unchecked desktop shortcut, and adds no login item.
- Rerunning Setup requests bounded graceful shutdown and upgrades in place.
  Uninstall removes the program, shortcuts, and registration. Upgrade and
  uninstall preserve `%APPDATA%\AACC`, including settings, history, database,
  and the cached Kimi web session.
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
- [ ] Confirm a real read-only Codex `WEEK` refresh without starting a task.
- [ ] Attach the completed checklist evidence to the release PR or notes.
- [ ] Only then create tag and release `v1.4.2`.
