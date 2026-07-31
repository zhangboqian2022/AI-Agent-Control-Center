# AACC 1.4.3-rc.2 Release Notes / 发布说明

## English

1.4.3-rc.2 is a release candidate on top of 1.4.3-rc.1. It fixes the Windows
Kimi quota auto-refresh stopping permanently after the panel had been minimized
for a long time. This is a **Windows-only increment**: the macOS build remains
at 1.4.3-rc.1 and no new macOS artifact is published for rc.2.

Root cause: each background refresh on Windows launches a short-lived headless
Edge window. When Kimi's access token had expired (typical after hours
minimized), the page answered 401 and the headless path declared the session
unauthorized on the very first failure, disabling automatic reuse forever —
the five-minute timer kept firing but every refresh was skipped, so the quota
only came back after you clicked the bar and the visible Edge window let
kimi.com silently renew the token.

- **Fix — one 401 no longer kills Windows background refresh.** Headless
  refreshes now retry inside a bounded 60-second grace window (2-second
  intervals), giving kimi.com's own token renewal time to replace the expired
  access token. Only a session that stays unauthorized for the whole grace
  window is treated as signed out, preserving the existing security semantics
  (manual sign-out and genuine Kimi auth expiry still require a new login).
- **UX — quota catches up the moment the panel is restored.** Unhiding or
  un-minimizing the panel now triggers an immediate Kimi quota refresh,
  throttled to at most once per 60 seconds so rapid show/hide toggling does
  not relaunch the browser back to back. This applies on both platforms and
  also covers any timer delivery delayed by Windows power throttling while
  the panel was hidden.
- **macOS — confirmed unaffected.** The native WebView keeps the membership
  page alive continuously, so Kimi's own renewal keeps the token fresh; local
  logs show the five-minute cadence completing without gaps whenever the app
  is running. The restore-triggered catch-up also ships on macOS.

Evidence boundary: local macOS run passes 964 tests, ruff check, ruff format,
and mypy. Hosted CI runs on push. Behavior of the 60-second grace retry
against the real Edge/kimi.com token renewal on a consumer Windows 10/11
machine is verified in CI at the code level only and is not claimed as
real-machine verified.

## 中文

1.4.3-rc.2 是基于 1.4.3-rc.1 的候选发布版，修复 Windows 面板长时间最小化后
Kimi 额度自动刷新永久停止的问题。本次为 **Windows 单独递增**：macOS 版本
保持 1.4.3-rc.1，rc.2 不发布新的 macOS 产物。

根因：Windows 每次后台刷新都会启动一个短生命周期的 headless Edge 窗口。
Kimi access token 过期后（长时间最小化属典型场景），页面返回 401，而
headless 路径在第一次失败就直接判定会话失效并**永久关闭自动复用**——
五分钟定时器仍在走，但每次刷新都被跳过，只有点击额度条弹出可见 Edge
窗口、由 kimi.com 静默续期 token 后额度才恢复。

- **修复 — 单次 401 不再杀死 Windows 后台刷新。** headless 刷新现在在
  60 秒有界宽限窗口内按 2 秒间隔重试，给 kimi.com 自身的 token 续期
  留出时间；只有整个宽限窗口内持续未授权的会话才判定为已退出，原有
  安全语义不变（人工退出与真实的 Kimi 鉴权失效仍需重新登录）。
- **体验 — 面板恢复时额度立即追平。** 取消隐藏/取消最小化会立即触发一次
  Kimi 额度刷新，并按 60 秒节流，快速显隐切换不会连续拉起浏览器。
  该机制双平台生效，同时覆盖面板隐藏期间被 Windows 电源节流延迟的
  定时器。
- **macOS — 已确认不受影响。** 原生 WebView 让会员页面常驻，Kimi 自身
  续期使 token 保持新鲜；本机日志显示 app 运行期间五分钟节奏无缺口。
  恢复触发补刷机制同样在 macOS 上线。

证据边界：本机 macOS 964 项测试、ruff check、ruff format、mypy 全部
通过；托管 CI 随推送运行。60 秒宽限重试在消费级 Windows 10/11 真机上
对真实 Edge/kimi.com token 续期的表现仅有代码级 CI 验证，本说明不宣称
已完成真机验证。
