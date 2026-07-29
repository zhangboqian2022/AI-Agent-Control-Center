# Windows 10/11 人工验证清单 — AACC 1.4.2

只记录实际观察结果；未勾选项不代表兼容性承诺。GitHub Actions 在 Windows
Server 2022/2025 上执行构建与自动化产品冒烟，但它**不能**替代本清单中的消费级
Windows 10/11 真机验证，也不能替代另一账户拒读测试。
完成本清单只构成原生 WebView 会话保留与退出验证所需 macOS 与 Windows 人工
签字中的 Windows 部分。

候选安装包：`AACC-1.4.2-Setup.exe`

验证人：

验证日期与时间：

机器型号：

Windows 版本（`Windows 10` 或 `Windows 11`）、版本号与内部版本：

账户类型（主验证必须使用非管理员账户）：

Commit 与候选文件 SHA-256：

- [ ] **校验值与 SmartScreen**：Setup 的 SHA-256 与
  `AACC-1.4.2-Setup.exe.sha256` 一致；启动未签名候选版时出现预期的“未知
  发布者”/SmartScreen 路径，选择“更多信息 → 仍要运行”后进入安装器。
- [ ] **当前用户安装**：Setup 不请求管理员提权，并安装到
  `%LocalAppData%\Programs\AACC`。
- [ ] **WebView2 运行时配置——未安装**：在一台真实 Windows 10 或 Windows 11
  标准用户机器上确认未安装 WebView2 运行时，联网运行 Setup。它会先为当前用户
  安装 Microsoft Evergreen WebView2 运行时，再安装 AACC；第一次 Kimi 登录应创建
  可用原生视图，而不是空白对话框。
- [ ] **WebView2 运行时配置——已安装**：在另一台真实 Windows 10 或 Windows 11
  标准用户机器上确认已安装 WebView2 运行时，以网络监控方式或下载后断网运行
  Setup。Setup 应识别运行时且不需要网络安装；第一次 Kimi 登录仍应创建可用原生
  视图。确认可写的 WebView2 用户数据目录创建在
  `%LOCALAPPDATA%\AACC\kimi-web-session`，而不是 `AACC.exe` 旁边。
- [ ] **WebView2 诊断**：若 Kimi 登录原生视图不能产生加载事件，它会用固定的
  15 秒 WebView2/网络修复诊断和 Microsoft 修复页面操作替换空白界面。记录观察到
  的类别，不记录账户信息或页面 URL。
- [ ] **快捷方式与开机启动**：开始菜单快捷方式存在；桌面快捷方式与安装时选择
  一致；没有新增开机启动项。
- [ ] **首次启动与托盘**：安装后的 AACC 面板正常打开、保持响应并驻留托盘；
  隐藏或最小化后可以恢复。
- [ ] **即时语言切换**：反复切换语言，并同时保留真实任务、额度数据和打开的
  Kimi 登录对话框。每次都应更新全部可见界面；明确选择应在重启后保留；额度值、
  任务选择/状态、Kimi 登录状态和紧凑模式均不得改变。
- [ ] **发现与聚焦**：真实运行中的 Kimi/Codex 会话被发现并显示状态灯；右键
  “切换到任务”通过窗口标题聚焦正确的目标终端。
- [ ] **输入控制**：白名单按键与文本只进入已聚焦目标；Win+H 语音输入和已配置
  的 F13–F20 全局热键可用。
- [ ] **额度行**：不启动 Codex 任务也能刷新出一行真实 `WEEK`；真实登录 Kimi
  会员后严格按 `5H`、`WEEK`、`MONTH` 排列。每个可用窗口显示完整本地重置
  日期时间；百分比已知但没有可信重置时间时，重置位置显示 `--`；不可用百分比
  也为 `--`，不能伪装成 `0%`。
- [ ] **设置与原生会话**：置顶和 API 凭证重置可持久化；确认操作系统原生的
  每应用 WebView 存储让 Kimi 第一方会话在重启 AACC 后仍有效。检查
  `%APPDATA%\AACC\kimi-web-session-state.json`，确认 AACC 只保存受保护的
  原生网页会话复用决定，不把 Cookie、密码、网页 Bearer Token、账户名或额度值
  复制进该门禁；Kimi Code OAuth 凭据由 AACC 凭据保护另行保存。
- [ ] **统一刷新与退出**：观察网页源和 Kimi Code 备用源从同一个五分钟周期
  开始刷新，元数据查询不消耗生成 Token。明确退出登录必须先同步关闭复用，
  再尝试有界的原生站点数据清理，并在立即重启 AACC 后仍保持登出。
- [ ] **原生 DACL**：AACC 创建文件后检查 `config.yaml`、`aacc.db`、
  存在时的 `aacc.db-wal`/`aacc.db-shm` 以及 `kimi-credentials.json`。
  每个文件均关闭继承，并且当前用户、Local System、本机 Administrators 各有且
  仅有一条完全控制 allow ACE；不存在其他 allow、deny 或继承 ACE。
- [ ] **另一账户拒读**：登录另一个无特权本机账户，确认操作系统拒绝读取上述
  全部敏感文件。
- [ ] **升级与优雅退出**：AACC 运行时再次执行同一个 Setup。安装器让 AACC
  优雅退出、原位升级并可正常重启，同时保留 `%APPDATA%\AACC` 中由 AACC
  管理的设置、历史、数据库、凭据与复用决定。把原生 WebView 会话保留作为
  独立的平台实测结果记录，不能当作 AppData 保留保证。
- [ ] **卸载与保留 AppData**：卸载移除程序、快捷方式和卸载注册项，但保留
  `%APPDATA%\AACC`。重新安装后可使用保留的 AACC 数据正常启动。原生 WebView
  存储由操作系统另行管理，本项不声称卸载会保留或移除网页会话。
- [ ] **长时间稳定性**：让 AACC 在任务和额度持续刷新时运行至少 30 分钟；
  不出现崩溃、原始 traceback、遗留 Codex broker 进程树或持续界面卡死。

证据 / 备注：

```text
在这里记录命令、实际结果、截图/日志位置与偏差。
把完成后的证据附到 Release PR 或发布说明。
```
