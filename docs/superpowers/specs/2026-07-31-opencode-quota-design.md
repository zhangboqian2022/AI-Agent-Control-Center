# OpenCode 额度支持设计（2026-07-31）

## 背景

AACC 已支持 Kimi（5H/WEEK/MONTH 会员额度 + 加油包余额）与 Codex（周额度）展示。
用户希望补充 opencode.ai 的用量展示：opencode.ai 工作区页面
（`https://opencode.ai/workspace/{workspace_id}/go`）显示三行用量
（滚动用量 / 每周用量 / 每月用量），与现有 Kimi 额度条的三行布局（5H/WEEK/MONTH）天然匹配。

opencode.ai 无公开的用量查询 API。经逆向其前端 JS 包确认：

- 页面数据来自同源 RPC `POST https://opencode.ai/_server`（SolidStart server function），
  返回 `subscription.get`（`rollingUsage` / `weeklyUsage` / `monthlyUsage` 三组
  `{usagePercent, resetInSec}`），序列化为 seroval 流/JSON。
- 认证是 OpenAuth（GitHub/Google）**网站会话 cookie**，非 API key（实测带 Zen API key
  调用 `/_server` 返回 500，无会话不可用）。
- 本机 `~/.local/share/opencode/auth.json` 只有 Zen API key（`opencode` / `opencode-go`），
  没有网站登录缓存。本地 opencode.db 的 `account` / `control_account` 表为空。

结论：需要像 Kimi 一样由 AACC 自持 WebView 会话完成一次 GitHub/Google 登录，
会话 cookie 持久化后由页面内 JS 取数上抛。仅借鉴 Kimi 链路的**分层思路**，
页面结构、登录流程、取数协议均为 opencode.ai 专属新实现。

## 范围

- 平台：**先 macOS（QtWebView），Windows（Edge CDP）为后续迭代**。
- 显示：仅三行用量（5H/WEEK/MONTH），不展示余额。
- 工作区来源：`config.yaml` 配置 `opencode_workspace_url`（用户填完整 URL）。

## 架构

新增模块（与 Kimi 链路并列，互不引用）：

| 模块 | 职责 |
|---|---|
| `opencode_web_quota.py` | 纯函数解析：_server 响应 → 归一化 `OpenCodeQuota`（复用 `QuotaDetail`/`QuotaStatus` 通用模型）+ OK/PARTIAL/UNKNOWN 判定 |
| `opencode_web_error.py` | 错误归一化：UNAUTHORIZED / REFRESH_TIMEOUT / REFRESH_FAILED / PARSE_FAILED |
| `opencode_web_session.py` | macOS QtWebView 会话：OpenAuth 登录、cookie 持久化、页面内直接 fetch `/_server` 取数、title-bridge 上抛 |
| `opencode_web_quota_service.py` | QTimer 300s 轮询、状态机、信号（quota_updated / login_state_changed / error_occurred） |
| `gui.py` 扩展 | `OpenCodeQuotaBar`（复用 `QuotaBar` 三行布局）+ 设置对话框「登录/退出 OpenCode」按钮 |
| `models.py` / `config.py` | `opencode_workspace_url` 配置项（host 必须是 opencode.ai） |

## 数据流

1. WebView 加载 `{opencode_workspace_url}`（工作区 /go 用量页）。
2. **页面内直接 fetch**（与 Kimi `membership_fetch_script` 同构，非 hook 拦截）：
   refresh 时 `runJavaScript` 一段 IIFE，在页面上下文同源 `POST /_server`
   （cookie 自动带上），请求体/头用实测抓包格式：
   `X-Server-Id: 7abeebee372f304e050aaaf92be863f4a86490e382f8c79db68fd94040d691b4`、
   `X-Server-Instance: server-fn:1`、body `{"t":{"t":15,"l":1,"c":"Array","a":[{"t":2,"s":"<workspace_id>"}]},"f":31,"m":[]}`。
   理由：QtWebView（WKWebView）没有 QWebEngine 的 DocumentCreation 注入点，
   且直接 fetch 无注入时序竞争。workspace_id 从配置 URL 提取。
3. 页面内解析响应（JSON 或 seroval 文本表达式 `$$$a=...`，均用
   `JSON.parse`/`eval`——响应来自 opencode.ai 自有服务器，可信），递归查找
   含 `rollingUsage+weeklyUsage+monthlyUsage` 的节点，提取三组
   `usagePercent` + `resetInSec`。
4. 经 title-bridge（`window[payloadKey]` + `document.title` 前缀变化，同 Kimi 模式）
   上抛 `{kind:'quota', generation, raw:{subscription:{rollingUsage:{usagePercent,resetInSec},...}}}`。
5. Python 归一化 → `OpenCodeQuota` → 信号 → `OpenCodeQuotaBar` 渲染。

语义映射：rolling(5h) → 5H 行、weekly(7d) → WEEK 行、monthly(30d) → MONTH 行；
`resetInSec` → 重置倒计时。

## 登录与会话

- 会话目录：`{config_dir}/opencode-web-session/`（macOS，与 kimi-web-session 并列）。
- 工作区 URL 示例（用户确认）：`https://opencode.ai/workspace/wrk_01KYVH7EJDHAAE4TZ51J3TX5CS/go`。
- 首次：额度条显示「OpenCode 用量/点击授权」→ 弹 QtWebView 登录窗口 →
  OpenAuth 页选 GitHub/Google → 重定向回工作区页 → 捕获数据成功 → 登录窗口自动关闭。
- 设置对话框新增「登录 OpenCode」「退出 OpenCode」按钮（同 `settings.kimi_web_login` 模式）。
- 会话过期：工作区页被重定向到 OpenAuth 登录页（URL 检测 + 捕获不到数据）→
  unauthorized → 额度条回「点击授权」，旧数据保留标 STALE。
- 安全：cookie 留在 WebView 沙箱目录；Python 只收归一化数值；token 不落 AACC 存储。

## 错误处理与刷新

- 状态语义沿用 Kimi：OK / PARTIAL / UNKNOWN / STALE（临时失败保留已知数据标 STALE）。
- 错误分类：UNAUTHORIZED / REFRESH_TIMEOUT / REFRESH_FAILED / PARSE_FAILED，
  tooltip 显示上次成功时间 + 错误 + 点击重试。
- 刷新节奏：300s 定时轮询 + 面板恢复显示时立即补刷（60s 节流，双平台生效，
  沿用 1.4.3 showEvent 模式）。

## 测试策略（TDD，先写失败测试）

- `tests/test_opencode_web_quota.py`：纯解析器。构造 _server 响应
  （seroval 表达式 / JSON 两形态）→ 断言归一化、resetSec 映射、OK/PARTIAL/UNKNOWN。
- `tests/test_opencode_web_session.py`：桥接脚本生成断言、URL 变化检测、
  登录状态机（假 WebView 驱动）。
- `tests/test_opencode_quota_bar.py`：三行渲染、百分比/重置文案、
  unauthorized/pending/error 状态。
- `tests/test_config.py`：workspace URL 校验（host 必须是 opencode.ai）。
- service 测试：假 session 注入驱动信号（仿 Kimi service 测试）。
- 改动行覆盖率 ≥90%（CI diff-cover）。

## 手动验收（无法自动化）

本机跑一次真实登录 → 断言工作区页数据捕获成功并渲染三行用量；
记录到文档验收清单与 KNOWN_LIMITATIONS（不宣称跨平台验证）。

## 实施顺序

1. 纯解析器 + 测试（不依赖登录）。
2. 配置项 + 测试。
3. WebView 会话（登录、hook、桥接）+ 服务 + 测试。
4. GUI 额度条 + 设置项 + 测试。
5. 手动验收。
