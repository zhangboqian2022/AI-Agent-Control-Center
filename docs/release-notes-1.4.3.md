# AACC 1.4.3 Release Notes / 发布说明

## English

1.4.3 is the formal cross-platform release that completes the accepted OpenCode
and Windows parity work on top of the 1.4.3 release candidates.

- **OpenCode quota on both platforms.** macOS keeps its native per-application
  web view. Windows uses a separate AACC-owned Microsoft Edge profile at
  `%LOCALAPPDATA%\AACC\opencode-edge-profile`, isolated from Kimi, and a
  loopback-only CDP boundary. Both paths accept only the configured secure
  `opencode.ai/workspace/...` page and extract the rendered rolling, weekly, and
  monthly Go-plan quota without reading cookies, prompts, replies, tool
  commands, or reasoning content.
- **OpenCode task parity.** Windows discovery prefers
  `%LOCALAPPDATA%\opencode\opencode.db`, supports the documented profile-level
  XDG fallback and release-channel filenames, and uses each session's working
  directory for terminal-title focus. Finished turns and forced-stop sessions
  leave the blue running state and render green completed promptly.
- **Windows hardening.** OpenCode Edge profile ownership, logout cleanup, CDP
  page targeting, endpoint validation, and quota payload parsing fail closed.
  Kimi and OpenCode browser profiles and session-state files cannot be reused
  interchangeably.
- **CI and delivery.** The release runs the full macOS and Windows quality,
  packaging, frozen-product, smoke, and artifact-check pipelines. A separate
  `windows-10`/`windows-11` compatibility-contract matrix exercises the Windows
  feature contracts on hosted Windows Server 2022 runners. This is useful
  compatibility evidence, but it is not consumer Windows 10/11 hardware
  verification; the manual checklist remains the evidence boundary for tray,
  SmartScreen, foreground focus, hotkeys, and long-running real-device behavior.

### Release assets

- macOS: `AACC-1.4.3.dmg` and `AACC-1.4.3.dmg.sha256`
- Windows: `AACC-1.4.3-Setup.exe` and `AACC-1.4.3-Setup.exe.sha256`

The Windows build remains unsigned. Verify the companion SHA-256 before
launching Setup. The macOS community build is ad-hoc signed and not notarized.

## 中文

1.4.3 是正式跨平台版本，在 1.4.3 候选版本基础上完成了 OpenCode 与 Windows
能力对齐。

- **两平台 OpenCode 额度。** macOS 保持原生每应用网页视图；Windows 使用
  `%LOCALAPPDATA%\AACC\opencode-edge-profile` 下独立的 AACC 专用 Microsoft
  Edge 配置目录，与 Kimi 完全隔离，并通过仅允许回环地址的 CDP 边界访问。
  两边只接受配置的安全 `opencode.ai/workspace/...` 页面，提取渲染后的 Go
  套餐滚动/每周/每月额度，不读取 Cookie、prompt、回复、工具命令或 reasoning。
- **OpenCode 任务能力对齐。** Windows 优先发现
  `%LOCALAPPDATA%\opencode\opencode.db`，兼容文档规定的用户目录 XDG 回退位置
  和发布通道数据库名；有工作目录时按会话工作目录定位终端窗口。回合完成或
  强制结束后，任务会及时退出蓝色进行中状态并显示绿色已完成。
- **Windows 安全加固。** OpenCode Edge 配置目录归属、退出清理、CDP 页面绑定、
  端点校验和额度数据解析均采用失败即拒绝；Kimi 与 OpenCode 的浏览器配置目录
  和会话状态文件不可互相复用。
- **CI 与交付。** 正式发布前执行完整 macOS/Windows 质量、打包、冻结程序、冒烟
  与资产校验流水线；另有 `windows-10`/`windows-11` 兼容性契约矩阵，在托管
  Windows Server 2022 运行器上执行 Windows 功能契约。这是兼容性证据，不是消费级
  Windows 10/11 真机验证；托盘、SmartScreen、前景聚焦、热键和真实长时间运行仍
  以人工验证清单作为证据边界。

### 发布资产

- macOS：`AACC-1.4.3.dmg` 与 `AACC-1.4.3.dmg.sha256`
- Windows：`AACC-1.4.3-Setup.exe` 与 `AACC-1.4.3-Setup.exe.sha256`

Windows 版本仍未签名，运行 Setup 前请核对配套 SHA-256。macOS 社区版本使用
ad-hoc 签名，未经过 Apple 公证。
