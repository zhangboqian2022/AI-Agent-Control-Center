# Windows 10/11 人工验证清单 — AACC 1.4.3

只记录实际观察结果；未勾选项不代表兼容性承诺。GitHub Actions 在 Windows
Server 2022/2025 上执行构建与自动化产品冒烟，但它**不能**替代本清单中的消费级
Windows 10/11 真机验证，也不能替代另一账户拒读测试。
完成本清单只构成 Kimi 会话保留与退出验证所需 macOS 与 Windows 人工
签字中的 Windows 部分。

候选安装包：`AACC-1.4.3-Setup.exe`

验证人：

验证日期与时间：

机器型号：

Windows 版本（`Windows 10` 或 `Windows 11`）、版本号与内部版本：

账户类型（主验证必须使用非管理员账户）：

Commit 与候选文件 SHA-256：

- [ ] **校验值与 SmartScreen**：Setup 的 SHA-256 与
  `AACC-1.4.3-Setup.exe.sha256` 一致；启动未签名候选版时出现预期的“未知
  发布者”/SmartScreen 路径，选择“更多信息 → 仍要运行”后进入安装器。
- [ ] **当前用户安装**：Setup 不请求管理员提权，并安装到
  `%LocalAppData%\Programs\AACC`。
- [ ] **Microsoft Edge 可用**：系统已安装 Microsoft Edge。Setup 不下载额外浏览器
  运行时，AACC 可以正常启动。
- [ ] **专用 Kimi 登录**：点击“使用专用 Edge 登录 Kimi”；出现可见 Edge 窗口，
  完成登录并收到三项额度后，AACC 只关闭自己启动的 Edge 进程。
- [ ] **配置隔离与后台复用**：确认 AACC 专用 Edge 配置目录位于
  `%LOCALAPPDATA%\AACC\kimi-edge-profile`，不在日常 Edge 配置中。重启 AACC
  和 Windows 后无需登录仍能刷新；五分钟后台刷新后不遗留可见 Edge 窗口。
- [ ] **快捷方式与开机启动**：开始菜单快捷方式存在；桌面快捷方式与安装时选择
  一致；没有新增开机启动项。
- [ ] **首次启动与托盘**：安装后的 AACC 面板正常打开、保持响应并驻留托盘；
  隐藏或最小化后可以恢复。左键切换面板，右键菜单保持打开；其中“退出 AACC”
  和头部电源按钮都能完整结束进程。
- [ ] **即时语言切换**：在 Kimi 登录前后反复切换语言，并保留真实任务和额度
  数据。每次都应更新全部可见界面；明确选择应在重启后保留；额度值、
  任务选择/状态、Kimi 登录状态和紧凑模式均不得改变。
- [ ] **发现与聚焦**：真实运行中的 Kimi/Codex 会话被发现并显示状态灯；右键
  “切换到任务”通过窗口标题聚焦正确的目标终端。
- [ ] **输入控制**：白名单按键与文本只进入已聚焦目标；Win+H 语音输入和已配置
  的 F13–F20 全局热键可用。
- [ ] **额度行**：不启动 Codex 任务也能刷新出一行真实 `WEEK`；先启动 AACC，
  再让 ChatGPT/Codex 在 AACC 之后打开或重启，确认最长在每 60 秒自动周期内
  恢复同步，点击 Codex 额度条可立即刷新；真实登录 Kimi
  会员后严格按 `5H`、`WEEK`、`MONTH` 排列。每个可用窗口显示完整本地重置
  日期时间；百分比已知但没有可信重置时间时，重置位置显示 `--`；不可用百分比
  也为 `--`，不能伪装成 `0%`。
- [ ] **OpenCode parity**：配置 OpenCode 工作区地址，通过专用 Edge 配置登录，确认
  滚动/每周/每月额度行均可用。确认 `%LOCALAPPDATA%\AACC\opencode-edge-profile`
  与 Kimi 配置完全隔离；强制关闭 OpenCode 终端后任务灯从蓝色立即变为绿色；
  发现的会话显示工作目录名，并能聚焦对应的 Windows Terminal 窗口。
- [ ] **设置与专用 Edge 会话**：置顶和 API 凭证重置可持久化；确认 AACC 专用
  Edge 配置目录让 Kimi 第一方会话在重启 AACC 和 Windows 后仍有效。检查
  `%APPDATA%\AACC\kimi-web-session-state.json`，确认 AACC 只保存受保护的
  原生网页会话复用决定，不把 Cookie、密码、网页 Bearer Token、账户名或额度值
  复制进该门禁；Kimi Code OAuth 凭据由 AACC 凭据保护另行保存。
- [ ] **统一刷新与退出**：观察网页源和 Kimi Code 备用源从同一个五分钟周期
  开始刷新，元数据查询不消耗生成 Token。明确退出登录必须先同步关闭复用，
  再只清理专用 Edge 配置目录，并在立即重启 AACC 后仍保持登出。
- [ ] **原生 DACL**：AACC 创建文件后检查 `config.yaml`、`aacc.db`、
  存在时的 `aacc.db-wal`/`aacc.db-shm`、`kimi-credentials.json`，以及
  `%LOCALAPPDATA%\AACC\kimi-edge-profile` 目录。每个目标均关闭继承，并且
  当前用户、Local System、本机 Administrators 各有且仅有一条完全控制 allow
  ACE；不存在其他 allow、deny 或继承 ACE。
- [ ] **另一账户拒读**：登录另一个无特权本机账户，确认操作系统拒绝读取上述
  全部敏感文件及 AACC 专用 Edge 配置目录。
- [ ] **升级与优雅退出**：AACC 运行时再次执行同一个 Setup。安装器让 AACC
  优雅退出、原位升级并可正常重启，同时保留 `%APPDATA%\AACC` 中由 AACC
  管理的设置、历史、数据库、凭据与复用决定；同时确认专用 Edge 会话升级后仍可用。
- [ ] **卸载与保留 AppData**：卸载移除程序、快捷方式和卸载注册项，但保留
  `%APPDATA%\AACC`。重新安装后可使用保留的 AACC 数据正常启动。记录独立的
  AACC 专用 Edge 配置目录是否保留；需要删除网页会话时应在卸载前从 AACC 手动退出。
- [ ] **长时间稳定性**：让 AACC 在任务和额度持续刷新时运行至少 30 分钟；
  不出现崩溃、原始 traceback、遗留 Codex broker 进程树或持续界面卡死。

证据 / 备注：

```text
在这里记录命令、实际结果、截图/日志位置与偏差。
把完成后的证据附到 Release PR 或发布说明。
```
