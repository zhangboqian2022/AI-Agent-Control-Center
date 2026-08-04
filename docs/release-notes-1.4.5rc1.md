# AACC 1.4.5-rc.1 Release Notes / 发布说明

## English

1.4.5-rc.1 adds Qwen Code (Aliyun Bailian token-plan) quota monitoring
alongside the existing Kimi and OpenCode bars. It is a prerelease, not a
claim of consumer Windows 10/11 hardware validation.

- **New — Qwen Code quota bar (5 小时 / 7 天).** AACC keeps an embedded
  WebView of the Aliyun Bailian personal token-plan page
  (`https://bailian.console.aliyun.com/cn-beijing?tab=plan#/efm/subscription/token-plan/personal`).
  Sign in once and the cookie is cached in AACC's private directory; the
  5-hour and 7-day windows then refresh every 5 minutes by reading the
  rendered page text (`document.body.innerText`). AACC never sees or
  stores the account password, and no internal Bailian XHR is intercepted
  (the API end points are internal and version-drift frequently, so the
  DOM-rendered text is the stable contract). macOS uses the QtWebView path;
  Windows uses a native QtWebView fallback in this iteration (Edge CDP
  tuning is deferred). New config fields: `app.qwen_quota_enabled`
  (default on) and `qwen_workspace_url` (default bailian personal page).

Evidence boundary: local run passes the project's pytest / ruff /
ruff-format / mypy suite. Hosted CI runs on push. Consumer Windows 10/11
behavior is covered by a manual verification checklist, not by automation,
and the Edge CDP variant is out of scope for this prerelease.

## 中文

1.4.5-rc.1 在 Kimi、OpenCode 额度条之外新增 Qwen Code（阿里云百炼
token-plan）额度监控。本版本为预发布，不宣称消费级 Windows 10/11 真机验证。

- **新功能 — Qwen Code 额度条（5 小时 / 7 天）。** AACC 内嵌一个阿里云百炼
  「个人 token 套餐」页面的 WebView
  （`https://bailian.console.aliyun.com/cn-beijing?tab=plan#/efm/subscription/token-plan/personal`）。
  登录一次后 cookie 缓存到 AACC 私有目录，5 小时与 7 天窗口每 5 分钟刷新一次，
  读取的是页面渲染后的文字（`document.body.innerText`）。AACC 全程不接触账号
  密码，也不拦截百炼内部 XHR（其端点为内部接口、版本灰度变化频繁，DOM 渲染
  文字反而是稳定契约）。本次 macOS 走 QtWebView；Windows 走原生 QtWebView
  回退（Edge CDP 调优作为后续迭代）。新增配置项：`app.qwen_quota_enabled`
  （默认开启）与 `qwen_workspace_url`（默认百炼个人 token 套餐页）。

证据边界：本机运行通过项目的 pytest / ruff / ruff-format / mypy 全套。
托管 CI 在推送时运行。消费级 Windows 10/11 行为以人工验证清单覆盖，非
自动化门禁；Edge CDP 变体不在此预发布范围内。