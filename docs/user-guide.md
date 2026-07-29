# AACC 用户指南

## 面板操作

单击卡片只会选中任务，不会切换到 Codex，因此面板不会自动隐藏。双击会在聚焦后触发对应平台的语音输入：macOS 使用系统听写，Windows 使用 Win+H；右键“切换到任务”才会唤起 Codex。右键卡片还可手动标记状态、重置、重命名或复制任务信息。头部 `EN`/`中` 按钮可让完整界面中英文即时切换，齿轮打开设置，横线将面板隐藏，电源按钮则完整退出 AACC。Windows 系统托盘图标左键显示/隐藏 AACC，右键打开持续可用的菜单，选择“退出 AACC”会停止全部后台服务。语言按钮显示要切换到的目标语言：中文界面显示 `EN`，英文界面显示 `中`。

首次启动时，中文系统语言使用中文，其他系统语言使用英文；通过头部按钮明确选择后，会在 macOS 与 Windows 上跨重启持久保存。切换只重新翻译已经保留的界面状态，不会刷新额度，也不会改变监控任务或登录状态。紧凑模式保留在设置和托盘菜单，切换语言不会改变其状态。窗口可拖动，右下角可缩放。

## Codex 自动任务

AACC 默认每 5 秒读取本机 Codex 元数据。最多 4 个具有近期可靠运行证据的任务会自动勾选、显示并进入监控。监控中的任务进入终态后会保留在“已完成 · 保留直到移除”区域，不会自动消失；点击卡片 `×`、右键“从面板移除”，或确认“全部清除”才会停止观察。已移除任务若之后出现可靠新活动，会自动重新显示。点击齿轮的“选择监控的 Codex 任务”，可手工保留非运行任务；取消自动任务的勾选会静默该任务，点击“恢复自动识别”可恢复。非运行且未选择任务只保留在选择器列表中，不进行状态监控。它读取任务 ID、标题、更新时间、会话文件修改时间、回合事件、PID 与有界的近期工具事件类别；可能检查命令类别标记来区分测试和构建，但不会把原始对话、提示词、回答、代码、命令、凭证或文件内容复制到界面、历史或日志。会话尾部的 `task_complete` 表示当前回合已完成，`turn_aborted` 表示当前回合已取消，两者都优先于更早的工具活动和文件修改时间。`task_started` 必须伴随近期活动才显示“执行中”；仅有过期启动事件会诚实显示“状态未知”。

面板默认置顶并停靠在主显示器右上角；拖动后会保留位置。设置里的“切换始终置顶”会持久保存你的选择，“停靠到桌面右上角”可随时恢复默认位置。点击自动发现的 Codex 任务会唤起 Codex，但 AACC 不会因失去焦点自动隐藏；若关闭置顶，窗口会保留在原位置但可以被 Codex 覆盖。Codex 目前没有公开的精确任务跳转接口。

Codex 元数据连续读取失败时，顶部会出现黄色告警条，但不会丢弃最后一次可信状态。“复制详情”只复制脱敏后的诊断 ID、计数、时间与日志位置；连续两次恢复正常后告警自动消失。

## Codex 周额度

Codex 额度条会优先启动本机已安装的 Codex `app-server`，使用 Codex 已配置账户仅调用只读 `account/rateLimits/read`；它不会提交提示词、启动任务或发起登录。AACC 每 60 秒重新发现安全的实时来源，因此 ChatGPT/Codex 在 AACC 之后打开或重启也能自动同步，无需重启 AACC。该路径不可用时，AACC 才回退到近期本机会话文件有界尾部的结构化 `rate_limits`。AACC 只接受未来有效的 10080 分钟周窗口，并刻意忽略旧版较短窗口，因此没有 Codex 五小时字段。元数据缺失、过期或格式变化时会显示“数据不可用”，不会显示为零用量。点击额度条可立即刷新。

Kimi 按 `5H`、`WEEK`、`MONTH` 显示。Windows 首次登录会用隔离的 AACC 专用 Edge 配置目录 `%LOCALAPPDATA%\AACC\kimi-edge-profile` 打开 Microsoft Edge，绝不读取日常 Edge 配置。该独立会话会跨 AACC 和电脑重启保留，直到手动退出、Kimi 令其失效或安全检查失败；macOS 继续使用系统原生的每应用网页会话。AACC 只保存受保护的复用决定，不把 Cookie、密码、网页 Bearer Token、账户名或额度值复制进配置；Kimi Code OAuth 凭据由 AACC 凭据保护另行保存。一个协调器让网页源和 Kimi Code 备用源从同一个五分钟周期开始刷新；Kimi Code 只能为临时缺失的 `5H` 或 `WEEK` 补位，不能虚构 `MONTH`。额度查询不发送提示词，也不消耗生成 Token。

## macOS DMG

当前已发布的稳定安装包仍为 `AACC-1.4.1.dmg`。在当前 1.4.2 源码执行 `./scripts/build_dmg.sh` 会生成带 1.4.2 版本号的 DMG 候选产物；1.4.2 门禁关闭前，它不是正式 Release 资产。双击对应 DMG 后，将 `AACC.app` 拖入“应用程序”文件夹。此本机构建使用自签名证书且未经过 Apple 公证；先用 `shasum -a 256 <文件>.dmg` 对比配套 `.sha256`，再选择“仍要打开”。若标准路径仍失败，最后才用 `xattr -cr /Applications/AACC.app` 在本机移除隔离属性。

## Windows Setup 候选版

Windows 1.4.2 主候选产物是 `AACC-1.4.2-Setup.exe`，普通用户无需安装 Python 或 `uv`。先核对 `AACC-1.4.2-Setup.exe.sha256`，再运行 Setup。它只安装给当前用户，无需管理员提权，默认路径为 `%LocalAppData%\Programs\AACC`。安装器创建开始菜单快捷方式，提供默认不勾选的桌面快捷方式，并且不添加开机启动项。

再次运行同一个 Setup 会在有界的优雅退出后原位升级。卸载会移除程序、开始菜单项、可选桌面快捷方式和卸载注册信息。升级和卸载都保留 `%APPDATA%\AACC` 下由 AACC 管理的配置、任务历史、数据库、凭据与受保护的复用决定。

Windows Kimi 登录使用系统已安装的 Microsoft Edge；Setup 不安装额外浏览器运行时。点击“使用专用 Edge 登录 Kimi”，在打开的独立窗口完成登录；收到三项额度后 AACC 会自动关闭该窗口。随后每五分钟使用同一 AACC 专用 Edge 配置目录在后台刷新，直到手动退出或 Kimi 令会话失效。AACC 退出 Kimi 时会先禁用复用，再只清理 `%LOCALAPPDATA%\AACC\kimi-edge-profile`。

1.4.2 候选版尚未签名，Windows 可能显示“未知发布者”或 SmartScreen；请先验证校验值，再选择“更多信息 → 仍要运行”。敏感配置、凭据、数据库、WAL 与 SHM 文件使用原生精确受保护 DACL，仅允许当前用户、Local System 与 Administrators。打包后的 Codex 只读查询通过 `AACC.exe` 旁的固定用途 broker 执行，因此应用不再调用 `icacls.exe`、`whoami.exe` 或 `taskkill.exe`。

## 绑定 Terminal 与 iTerm2

为每个任务设置唯一且稳定的窗口标题，例如 `AACC-TASK-1`。Terminal 使用 `terminal.type: terminal_app` 和 `app_bundle_id: com.apple.Terminal`；iTerm2 使用 `terminal.type: iterm2`。若只需激活 Codex App 或其他桌面应用，设置 `terminal.type: mac_app` 与对应 bundle identifier。

## 状态来源

最可靠的方式是 Agent Hook 调用本地 API。没有 Hook 时使用 `aacc-run` 报告进程启动、运行和退出；退出码 0 只标记 `STOPPED`，不会伪造业务完成。还可以用 `aacc status` 手动更新。手动状态优先，五分钟后可被新自动状态覆盖。

## 全局快捷键

默认 F13–F16 聚焦任务 1–4，F17 发送 Enter，F18/F19 发送 1/2，F20 在 macOS 触发系统听写、在 Windows 触发 Win+H 语音输入。可以用 Karabiner-Elements、Fn 层或键盘固件将物理按键映射到这些功能键。macOS 的全局监听与键盘注入需要辅助功能权限；缺失时 AACC 会给出说明并可直达系统设置。Windows 使用原生全局热键与窗口 API，无需辅助功能授权。两个平台均可通过 `keyboard_injection: false` 完全关闭发送能力。

在“设置 → 重置 API 凭证”可替换本地 API Token；旧 Token 立即失效，新 Token 只复制一次。

## 开机启动

macOS 安装包不修改登录项；可在“系统设置 → 通用 → 登录项”中添加 `~/Applications/AACC.app`，并随时在同一页面移除。Windows Setup 不添加开机启动项；请从开始菜单启动 AACC，或自行维护独立的启动快捷方式。
