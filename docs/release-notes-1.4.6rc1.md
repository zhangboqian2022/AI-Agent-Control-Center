# AACC 1.4.6-rc.1 Release Notes / 发布说明

## English

1.4.6-rc.1 is a prerelease focused on Qwen Code (Bailian token-plan) quota
recovery: login becomes sync-first, self-heal becomes origin-aware and can no
longer destroy fresh manual logins, and the Chrome lifecycle gets an automatic
login popup, robust cancel detection, and a zero-leftover guarantee. It does
not claim consumer Windows 10/11 hardware validation.

- **Sync-first login.** Clicking authorize no longer opens the visible Chrome
  login window immediately. AACC first fetches the quota hidden against the
  AACC-owned profile and, only when that cache is confirmed dead, syncs the
  daily Chrome Bailian session into it and retries. The visible login window
  opens only when the silent sync fails to produce a live session, so a healthy
  daily session now turns the quota bar green without any window at all. Both
  the sync-first login and the daily-session self-heal require
  `qwen_auto_session_recopy` (default off; opt-in because it copies the daily
  browser's Bailian session cookies into AACC's own profile).
- **Origin-aware protected self-heal.** Every session now records its origin
  (manual login vs. daily-session recopy). When a login banner is observed on
  a session created by a manual login, AACC waits 60 seconds and rechecks the
  live page before any recopy may overwrite it. This fixes the incident where
  a single banner observation destroyed a fresh login that was only 9 minutes
  old.
- **Automatic centered login popup.** When the session is confirmed dead and
  every automatic recovery path is exhausted, AACC opens the centered Qwen
  login window by itself instead of waiting silently on "click to authorize".
  The popup is rate-limited to once per 30 minutes and is never shown after an
  explicit user logout. Closing a login window that AACC opened automatically
  opts out: no further auto-popups appear for the rest of the current run, and
  re-authorizing stays a deliberate click on the quota bar.
- **Centered login window with stealth.** The visible login window now opens
  centered on screen, and the anti-detection (stealth) script is installed
  before the login page loads.
- **Robust login cancel.** The login watcher no longer treats cross-origin
  redirects (SMS verification, risk-control verification) as a closed login.
  Previously an auto-cancel could fire about 14 seconds into such a redirect;
  now a cancel is only declared once every page target of the owned browser has
  vanished for 3 consecutive polls (with a 30-second startup grace), so
  completing a verification flow can no longer be misreported as a cancelled
  login.
- **Zero-leftover guarantee.** After every operation (login, hidden refresh,
  recopy, logout, shutdown) AACC verifies the owned profile has no surviving
  Chrome processes and force-terminates any survivors, so no hidden browser
  can outlive the operation that created it.
- **Quota-bar status texts.** The quota bar shows bilingual status texts for
  session syncing, the sync-failed fallback that is about to open a login
  window, and session-expired auto-recovery, so background recovery is visible
  instead of looking like a frozen reading.

## 中文

1.4.6-rc.1 是一次围绕 Qwen Code（百炼 token-plan）额度恢复的预发布版本：
登录改为「同步优先」，自愈改为来源感知、不再误杀刚完成的手动登录，Chrome
生命周期新增自动弹窗登录、稳健的取消判定和零残留保证。本版本不宣称已完成
消费级 Windows 10/11 真机验证。

- **同步优先登录。** 点击授权不再立即打开可见的 Chrome 登录窗。AACC 先用
  AACC 专属 profile 里已有的会话隐藏拉取额度，只有确认该缓存失效时才把日常
  Chrome 的百炼会话同步进去并重试；只有静默同步没能拿到可用会话时才打开可见
  登录窗。日常会话健康时，额度栏直接变绿，全程无窗口。同步优先登录与日常会话
  自愈都依赖 `qwen_auto_session_recopy`（默认关闭；因会把日常浏览器的百炼会话
  Cookie 复制进 AACC 自有 profile，需用户主动开启）。
- **来源感知的受保护自愈。** 每个会话现在都记录来源（手动登录或日常会话重
  复制）。手动登录产生的会话撞上登录横幅时，AACC 先等 60 秒复核真实页面，
  确认过期才允许重复制覆盖。修复了一次横幅观测就摧毁刚登录 9 分钟的新会话
  的事故。
- **自动居中登录弹窗。** 会话确认失效且所有自动恢复手段用尽时，AACC 会自动
  打开居中的 Qwen 登录窗，而不是静默停在「点击授权」。弹窗频率限制为每 30
  分钟最多一次；用户主动退出登录后绝不自动弹窗。关闭 AACC 自动打开的登录窗
  即视为拒绝：本次运行不再自动弹窗，重新授权回到用户主动点击额度栏。
- **登录窗居中 + 提前注入反检测。** 可见登录窗现在居中打开，并在登录页加载前
  注入反检测（stealth）脚本。
- **稳健的登录取消判定。** 登录监视不再把跨域跳转（短信验证、风控验证）当作
  登录已关闭：修复前这类跳转约 14 秒就会被判定为自动取消，现在只有托管浏览器
  的所有页面目标连续 3 次轮询全部消失（并有 30 秒启动宽限）才判定取消，完成
  验证流程不会再被误报为取消登录。
- **零残留保证。** 每次操作（登录、隐藏刷新、重复制、登出、关闭）结束后，
  AACC 都会校验专属 profile 没有任何残留 Chrome 进程，并对幸存者强制终止，
  隐藏浏览器不会比创建它的操作活得更久。
- **额度栏状态文案。** 额度栏新增双语状态文案：会话同步中、同步失败即将打开
  登录窗、会话已过期自动恢复中——后台恢复过程可见，不再像读数卡死。

## Verification / 验证记录

Automated gates on the final review-fix wave (2026-09-04): targeted Qwen suites
(294 tests) green, full suite 1607 passed / 7 skipped, `ruff check`,
`ruff format --check` and `mypy src/aacc` clean.

Real-machine evidence with the installed 1.4.6-rc.1 build:

- 2026-09-04 17:57-17:59: startup refresh; a legacy state file with no recorded
  origin was treated as the manual-login origin (fail-closed); the login banner
  triggered the 60-second protected recheck, the recheck confirmed the session
  was really expired, the daily-session recopy ran, and the retry fetched the
  quota silently — the reading was restored without any user action, and the
  owned profile had zero leftover Chrome processes afterwards.
- 2026-09-04 18:12-18:14: refresh saw the banner on a daily-origin session and
  recopied immediately; the retry failed because the daily Chrome session itself
  was logged out, so the bar reported logged-out and the automatic login window
  was requested; the sync-first attempt failed in 2.8 seconds and the visible
  login window opened. Quitting AACC at 18:15 reaped every process it owns
  (a 21:21 re-check found none left).

Scope marker / 范围说明: the final smoke test is still pending user
confirmation of the centered login window; a controller finishes that step
after the rebuild. / 最终冒烟仍待用户确认居中的可见登录窗，重建后由负责人补做
该步骤。本记录不宣称已完成消费级 Windows 10/11 真机验证。
