# AACC 1.4.5-rc.3 Release Notes / 发布说明

## English

1.4.5-rc.3 keeps the Qwen Code (Bailian token-plan) quota bar alive across
the Aliyun server-side session expiry, and makes the logged-out path
visible in the log. It is a prerelease, not a claim of consumer Windows
10/11 hardware validation.

- **Auto session recopy on expiry.** A copied Bailian session is expired
  server-side roughly five and a half hours after the copy (the console
  keeps the same URL and renders an inline "not logged in" banner). With
  `qwen_auto_session_recopy: true` in `config.yaml`, a hidden refresh that
  hits that banner immediately rebuilds AACC's Chrome profile from the
  daily Chrome's minimal session set — `Cookies` (SQLite online backup,
  safe while Chrome is running), `Local State`, `Preferences`,
  `Secure Preferences`, `Local Storage` and `Session Storage`; `Login Data`
  (saved passwords) is never copied — and retries the extraction within the
  same tick. Because the daily browser keeps its session alive through real
  use, the bar keeps showing quota indefinitely without any visible
  re-login. Recovery also runs while the bar shows "click to authorize"
  (for example after restarting into an already-expired session), so it
  self-heals without any click once the daily Chrome session is live
  again; an explicit logout is respected and never auto-recovered. The
  replaced managed profile is quarantined as
  `.qwen-chrome-profile.pre-dailycopy-*` next to the config directory
  (pruned to the 3 newest). If the recopy or the retry fails, the bar falls
  back to "click to authorize" as before. The flag defaults to **off**:
  enable it only on machines that already use the daily-session copy flow,
  since it reads the daily Chrome profile. Visible logins, Windows, and
  machines without a daily Chrome `Default` profile are unaffected.
- **Logged-out path no longer silent.** Detecting an expired Qwen session
  now logs a WARNING (previously the bar flipped back to "click to
  authorize" with no trace in the log), and refreshes skipped because the
  session is logged out leave a debug line.

Evidence boundary: local run passes the project's pytest / ruff /
ruff-format / mypy suite; the recopy + retry flow is covered by unit tests
and was diagnosed against live log timelines and a CDP page probe of the
real console. Hosted CI runs on push. Consumer Windows 10/11 behavior is
covered by a manual verification checklist, not by automation.

## 中文

1.4.5-rc.3 让 Qwen Code（百炼 token-plan）额度栏在阿里云服务端会话过期
后依然保持显示，并让未登录路径在日志中可见。本版本为预发布，不宣称
消费级 Windows 10/11 真机验证。

- **到期自动重复制会话。** 复制出的百炼会话大约在复制后 5.5 小时被
  服务端过期（控制台停留在原 URL，渲染「您当前处于未登录状态」内嵌
  横幅）。在 `config.yaml` 中设置 `qwen_auto_session_recopy: true` 后，
  隐藏刷新一旦撞上该横幅，会立即用日常 Chrome 的最小会话集重建 AACC
  的 Chrome profile——`Cookies`（SQLite 在线备份，Chrome 运行中也安全）、
  `Local State`、`Preferences`、`Secure Preferences`、`Local Storage` 与
  `Session Storage`；绝不复制 `Login Data` 密码库——并在同一轮刷新内
  重试提取。由于日常浏览器通过真实使用保持会话活跃，额度栏可以长期
  保持显示，无需任何可见的重新登录。处于「点击授权」状态时（例如
  重启后会话已经过期）也会继续尝试恢复，一旦日常 Chrome 的会话重新
  活跃即可无点击自愈；用户主动退出登录会被尊重，绝不被自动恢复。
  被替换的托管 profile 隔离为配置
  目录旁的 `.qwen-chrome-profile.pre-dailycopy-*`（只保留最近 3 份）。
  重复制或其后的重试失败时，额度栏照旧回退到「点击授权」。该开关
  **默认关闭**：因为它会读取日常 Chrome profile，只建议已经在使用
  「日常会话复制」流程的机器启用。可见登录、Windows 以及没有日常
  Chrome `Default` profile 的机器均不受影响。
- **未登录路径不再静默。** 检测到 Qwen 会话过期现在会打 WARNING 日志
  （此前额度栏悄悄翻回「点击授权」，日志毫无痕迹）；因未登录而被跳过
  的刷新也会留下 debug 日志。

证据边界：本机运行通过项目的 pytest / ruff / ruff-format / mypy 全套；
重复制 + 重试流程有单元测试覆盖，且诊断基于真实日志时间线与对真实
控制台的 CDP 页面探针。托管 CI 在推送时运行。消费级 Windows 10/11
行为以人工验证清单覆盖，非自动化门禁。
