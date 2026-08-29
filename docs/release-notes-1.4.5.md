# AACC 1.4.5 Release Notes / 发布说明

## English

1.4.5 is the formal release of the 1.4.5 line. It finalizes the quota
refresh pipeline fixes accumulated since 1.4.5-rc.2 across the three
web-session providers (Kimi, OpenCode, Qwen), driven by live diagnosis on
this macOS machine.

- **Kimi survives expired tokens.** A background quota refresh that receives
  a 401 now runs a bounded recovery before failing closed: re-fetch after the
  page settles, one full page reload so the SPA bootstrap can renew the token,
  then a final re-fetch. The reuse gate and the "click to authorize" state are
  only written after the fresh page still answers unauthorized, so a stale
  token on a long-lived page can no longer silence auto refresh for hours.
- **kimi.com never reports load-finished any more.** A long-lived request
  keeps every page load in flight, and the whole pipeline used to wait on
  that event. The quota fetch is now scheduled 10 seconds after navigation
  starts, and the in-page script polls up to 25 seconds for the SPA bootstrap
  to write its access token before requesting anything — slow bootstraps no
  longer produce false "not logged in" results.
- **The login dialog stays usable.** The dialog keeps its rendered page
  stable and polls every 10 seconds for the user to sign in on it instead of
  churning reloads; the window is flagged always-on-top so the menu-bar
  app's dialog cannot stay buried; the macOS startup watchdog is inert (only
  Windows keeps the WebView2 repair flow).
- **OpenCode extraction is locale-free and decimal-aware.** The site
  redesign on 2026-08-25 broke the bare "NN%" positional matcher, and
  localized web views (zh-CN renders 使用量) broke the first label-anchored
  attempt. Extraction now takes the first three bare percentage lines in DOM
  order with optional decimals and pairs each with a 重置/Resets-in line —
  verified against both the captured English page text and a Chinese
  reconstruction. A workspace redirect to auth.opencode.ai is reported as
  unauthorized instead of burning the watchdog, and DOM timeouts carry the
  first lines of rendered page text into the log for diagnosis.
- **Qwen visible login cannot be starved.** The console can hold a stale
  logged-out Bailian tab next to the live one, and CDP target listing does
  not guarantee order. The login loop now evaluates every debuggable Bailian
  target until one renders the quota; the login-banner, deadline and
  daily-session recopy semantics are unchanged.

## 中文

1.4.5 是 1.4.5 系列的正式版本，收拢自 1.4.5-rc.2 以来在三个 web 会话额度
提供方（Kimi、OpenCode、Qwen）上累积的刷新管线修复，全部由本机实机诊断
驱动。

- **Kimi 不再因过期 token 停摆。** 后台额度刷新收到 401 后先走有界自恢复：
  页面稳定后重拉一次、整页重载一次让 SPA 引导续期 token、最后再拉一次。
  只有全新页面仍然回答未登录时才写入复用门与"点击授权"状态——长驻页面上
  的过期 token 不会再让自动刷新沉默数小时。
- **kimi.com 已不再回报"加载完成"。** 一个长连接让每次页面加载都无法结束，
  而整条管线都在等待该事件。现在导航开始 10 秒后即调度额度拉取，页内脚本
  在请求前最多轮询 25 秒等待 SPA 引导写入 access token——慢引导不会再产生
  假的"未登录"结果。
- **登录对话框保持可用。** 对话框保持已渲染页面稳定，每 10 秒轮询一次等待
  用户在页面中登录，不再反复重载；窗口标记置顶，菜单栏应用的对话框不会被
  其他窗口遮挡；macOS 启动看门狗不再干预（仅 Windows 保留 WebView2 修复
  流程）。
- **OpenCode 提取恢复语言无关并支持小数。** 2026-08-25 的站点改版破坏了旧的
  纯整数"NN%"定位匹配，本地化 webview（zh-CN 渲染"使用量"）又破坏了第一次
  标签锚定尝试。提取现在按 DOM 顺序取前三个裸百分比行（支持一位小数），
  并与"重置/Resets in"行配对——已用捕获的英文页面文本与中文重建文本双重
  验证。工作区重定向到 auth.opencode.ai 时如实报告未登录；DOM 超时会携带
  页面渲染文本前几行写入日志便于诊断。
- **Qwen 可见登录不会被饿死。** 控制台可能在在线标签页旁保留一个陈旧的
  未登录百炼标签页，而 CDP target 列表不保证顺序。登录循环现在会评估每个
  可调试的百炼 target 直到某个页面渲染出额度；登录横幅、超时与每日会话
  recopy 语义保持不变。

## Verification boundary / 验证边界

All 1552 unit/integration tests pass together with ruff, ruff format and
mypy; changed-line coverage (diff-cover) is 100%. On this macOS machine the
installed build delivered live OpenCode quota (0% / 48% / 82.6%) and live
Kimi quota after sign-in, with the 5-minute auto refresh cycles logged. The
Qwen multi-target login fix is covered by unit tests reproducing the
two-target scenario observed on 2026-08-28. As with every release: managed
CI does not equal consumer-grade Windows 10/11 real-machine verification.
The Windows Setup asset is published separately once built on a Windows
machine; this release ships the macOS DMG.

全部 1552 项单元/集成测试与 ruff、ruff format、mypy 一并通过；改动行
覆盖率（diff-cover）100%。在本 macOS 机器上，安装后的构建实测交付了
OpenCode 实时额度（0% / 48% / 82.6%）与登录后的 Kimi 实时额度，日志中可见
5 分钟自动刷新周期。Qwen 多 target 登录修复由复现 2026-08-28 双 target
场景的单元测试覆盖。与每个版本相同：托管 CI 不等同于消费级 Windows
10/11 真机验证。Windows Setup 资产将在 Windows 机器上构建后另行上传；
本版本先发布 macOS DMG。
