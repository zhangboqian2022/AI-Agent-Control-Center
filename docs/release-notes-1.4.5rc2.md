# AACC 1.4.5-rc.2 Release Notes / 发布说明

## English

1.4.5-rc.2 makes the Qwen Code (Bailian token-plan) login actually
completable and fixes quota freshness/accuracy across the web-session
bars. It is a prerelease, not a claim of consumer Windows 10/11 hardware
validation.

- **Login via a real Chrome (CDP) on macOS.** Aliyun's sign-in (password /
  QR / RAM) is a multi-origin flow with new-window requests; the embedded
  native web view cannot navigate it (its window-open hook is absent).
  Following the paradigm Windows already uses with Edge, the Qwen session
  now drives a real Google Chrome through the DevTools Protocol: clicking
  "authorize" opens one visible Chrome window (AACC-owned profile, separate
  from your personal Chrome data) where the login — RAM entry included —
  completes normally. Background quota refreshes then run every 15 minutes
  inside a headed-but-hidden Chrome: Aliyun's risk control voids session
  tickets presented by headless browsers, so AACC launches Chrome through
  `open -g -n` (no focus steal, no Dock bounce), pushes the window
  off-screen via CDP, and masks `navigator.webdriver` plus the off-screen
  coordinates before the page loads. Cookies stay in
  `~/Library/Application Support/AACC/qwen-chrome-profile`;
  AACC never sees or stores the account password. If Chrome is not
  installed, the previous native web view remains as fallback.
- **No more fake "authorized + 0%".** The extraction only reports quota
  when a percentage is actually rendered; the anonymous/login view repeats
  the window labels in marketing copy, which previously closed the login
  dialog and painted a 0% bar.
- **Fractional percentages kept.** `0.04%` now renders as `0.04%` instead
  of `0%`; reset countdowns are sliced per window so the 5-hour value no
  longer absorbs the neighbouring "7 天" text.
- **Refreshes actually refresh.** OpenCode and Qwen quota refreshes now
  reload the workspace page on every tick before extracting (previously the
  script re-read the stale DOM and values never changed); Qwen ticks every
  15 minutes, OpenCode every 5. Kimi is unchanged (it fetches a live API).
- **Windows fallback fixed.** The quota service no longer imports the
  not-yet-existing `aacc.qwen_edge_session`; Windows uses the native
  web-view path until a dedicated Edge-CDP session lands.
- **Dependency note.** `websocket-client` is now a cross-platform
  dependency (the CDP transport on macOS); no bundle-size growth.

Evidence boundary: local run passes the project's pytest / ruff /
ruff-format / mypy suite; extraction-script logic additionally verified
against simulated page text. Hosted CI runs on push. Consumer Windows
10/11 behavior is covered by a manual verification checklist, not by
automation.

## 中文

1.4.5-rc.2 让 Qwen Code（百炼 token-plan）登录真正可以完成，并修复各
网页额度条的刷新与精度问题。本版本为预发布，不宣称消费级 Windows
10/11 真机验证。

- **macOS 登录改走真实 Chrome（CDP）。** 阿里云登录（密码 / 扫码 / RAM）
  跨多域名、会发起新窗口请求，内嵌原生 WebView 无法完成（其弹窗钩子
  缺失）。沿用 Windows 上驱动 Edge 的既有范式，Qwen 会话现在通过
  DevTools Protocol 驱动真实的 Google Chrome：点击「授权」会打开一个
  可见的 Chrome 窗口（AACC 专属 profile，与你的个人 Chrome 数据隔离），
  登录——包括 RAM 登录——按正常浏览器流程完成。之后的额度刷新每
  15 分钟一次，走「有头但完全隐藏」的 Chrome：阿里云风控会作废由
  headless 浏览器出示的会话票据，因此 AACC 通过 `open -g -n` 启动
  Chrome（不抢焦点、不 Dock 弹跳），经 CDP 把窗口推出屏幕，并在页面
  加载前掩盖 `navigator.webdriver` 与屏外负坐标。cookie 保存在
  `~/Library/Application Support/AACC/qwen-chrome-profile`，AACC 全程
  不接触账号密码。未安装 Chrome 时回退到原有原生 WebView。
- **不再出现假的「已授权 + 0%」。** 提取只在页面真正渲染出百分比时
  才上报额度；匿名/登录页的介绍文案同样含有「5 小时 / 7 天」字样，
  此前会被误判为取数成功、自动关闭登录框并显示 0%。
- **小数百分比保留。** `0.04%` 现在显示为 `0.04%` 而非 `0%`；重置倒计时
  按窗口切片，5 小时窗口不再把相邻「7 天」文案累加进来。
- **定期刷新真正生效。** OpenCode 与 Qwen 额度每次轮询都先整页重载
  工作区页面再提取（此前只重读旧 DOM，数值永远不变）；Qwen 每
  15 分钟一次，OpenCode 每 5 分钟一次。Kimi 不变（其取数为实时 API）。
- **Windows 回退修正。** 额度服务不再导入尚不存在的
  `aacc.qwen_edge_session`；专属 Edge CDP 会话落地前，Windows 与 macOS
  一致走原生 WebView 路径。
- **依赖说明。** `websocket-client` 改为跨平台依赖（macOS 的 CDP
  传输通道）；应用体积不增加。

证据边界：本机运行通过项目的 pytest / ruff / ruff-format / mypy 全套；
提取脚本逻辑另经模拟页面文本验证。托管 CI 在推送时运行。消费级
Windows 10/11 行为以人工验证清单覆盖，非自动化门禁。
