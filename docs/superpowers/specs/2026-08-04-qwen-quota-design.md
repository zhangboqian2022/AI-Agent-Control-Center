# Qwen Code（百炼 token-plan）额度支持设计（2026-08-04）

## 背景

AACC 已支持 Kimi（5H/WEEK/MONTH 会员额度）与 Codex（周额度）以及 opencode.ai
（ROLLING/WEEK/MONTH）三套独立额度条。用户希望补充阿里云百炼控制台的
Qwen Code 订阅 token-plan 用量展示：控制台页面
`https://bailian.console.aliyun.com/cn-beijing?tab=plan#/efm/subscription/token-plan/personal`
渲染两行窗口用量——**5 小时**（5h）与 **7 天**（7d）——正是 Kimi 额度条 5H/WEEK
两行布局的天然对应。

百炼控制台是阿里云 React SPA（qiankun 微前端 `bailian-tokenplan@0.0.74`），
没有文档化的公开用量查询 API：额度由微前端加载后向阿里云 console 内部网关
XHR 取数渲染，端点随版本灰度迁移、非稳定契约。因此**不采用 opencode.ai
`/_server` 那种同源 fetch 路径**（kimi 也只在其自有公开 Connect 端点 `/apiv2/`
上 fetch），而是照搬 opencode_web_session 的 **DOM 渲染文字提取**：AACC 自持
WebView 一次性登录、cookie 持久化，提取脚本读 `document.body.innerText` 中
「5 小时」「7 天」附近的百分比与重置时间经 `document.title` 桥接上抛。

鉴权与缓存语义与 kimi/opencode 完全一致：AACC 全程不碰账号密码；WebView 持久
化 cookie 到 AACC 私有目录；token 续期由页面自身处理，Python 只收归一化数值。

## 范围

- 平台：**先 macOS（QtWebView）**，Windows（Edge CDP）沿用既有 `opencode_edge_*`
  范式作为后续迭代（本次不实现 Windows 专属会话，service 在 win32 上按
  `_create_native_web_session` 回退到 Qt 路径，与 opencode 初版策略一致）。
- 显示：仅两行窗口用量（5 小时 / 7 天），不展示余额或订阅级别。
- 工作区来源：`config.yaml` 配置 `qwen_workspace_url`（默认百炼 token-plan
  personal 页 URL，用户可改区域）。
- 开关：`config.app.qwen_quota_enabled` 默认 `True`（与 kimi 一致）。

## 架构

新增模块与 kimi / opencode 链路并列、互不引用：

| 模块 | 职责 | 参照 |
|---|---|---|
| `qwen_web_quota.py` | 纯函数解析：脚本抓到的 `{fiveHourText, weeklyText}` 渲染文本 → 归一化 `QwenQuota{five_hour, weekly, status, fetched_at}`，复用 `kimi_quota.py::QuotaStatus` | `opencode_web_quota.py` |
| `qwen_web_error.py` | 错误归一化枚举 `QwenQuotaErrorCategory` + i18n 文案 | `opencode_web_error.py` |
| `qwen_web_session.py` | macOS QtWebView 会话：百炼登录、cookie 持久化、页面内 DOM 提取脚本、title-bridge 上抛、open_login / refresh / logout / close | `opencode_web_session.py` + `kimi_web_session.py`（登录对话框架次序与 generation 防竞态） |
| `qwen_web_quota_service.py` | `QTimer(900_000ms)` 轮询、`set_workspace_url` / `start` / `stop` / `refresh_now` / `open_login` / `logout`、信号 `quota_updated` / `login_state_changed` / `error_occurred` | `opencode_web_quota_service.py` |
| `gui.py` 扩展 | 新增 `QwenQuotaBar`（两行：5 小时 / 7 天）+ `Runtime.qwen_web_quota_service` 透传 + 启动连接 + 面板恢复显示补刷（复用 60s 节流 `showEvent` 路径） | `OpenCodeQuotaBar` |
| `config.py` | `AppConfig.app.qwen_quota_enabled: bool = True`；`AppConfig.qwen_workspace_url: str`（默认百炼 token-plan personal URL） | `opencode_workspace_url` |
| `app.py` | `_default_qwen_web_quota_service_factory` + `build_runtime` 装配 + `start_qwen_web_quota` 启动序列 + `Runtime` 持有与 shutdown stop | opencode 装配块 |
| `i18n` | 新增 `qwen.*` 键（中英双语）：`qwen.quota` / `qwen.five_hour` / `qwen.weekly` / `qwen.web_starting` / `qwen.web_need_config` 等 | `opencode.*` 键 |

## 数据流

```
QTimer 5min -> QwenWebQuotaService.refresh_now
            -> QwenWebSession.refresh
               -> QWebView 加载/复用百炼 token-plan URL
               -> 页面渲染后 runJavaScript(qwen_dom_extract_script)
                  -> 扫 document.body.innerText 找 "5 小时" "7 天" 附近文本
                  -> document.title = BRIDGE_PREFIX + JSON({raw:{fiveHourText, weeklyText}})
               -> titleChanged -> _handle_bridge
            -> quota_received(raw)
            -> parse_qwen_quota(raw, now) -> QwenQuota
            -> quota_updated.emit(QwenQuota)
            -> MainWindow._on_qwen_quota_updated -> QwenQuotaBar.show_quota
```

## 提取脚本策略（先骨架后调正则）

骨架版 `qwen_dom_extract_script(url, generation)`：

1. URL 白名单校验（必须是百炼 console 域 + 含 `token-plan`）。
2. 等待 SPA 渲染：每 1s 重试，最多 50 次（与 opencode 同款）。
3. 取 `document.body.innerText`，按行切分、trim、去空行。
4. 宽松正则定位「5 小时」「7 天」标题所在行号，向后若干行内匹配
   `(\d{1,3})\s*%` 与「重置」附近的时间（「X 小时 Y 分钟」「X 天 Y 小时」）。
5. 桥接 `document.title = BRIDGE_PREFIX + JSON.stringify({kind:'quota', generation,
   raw:{fiveHourText: "<拼出的文本段>", weeklyText: "<拼出的文本段>"}})`。
6. 超时 emit `{kind:'error', generation, message:'DOM_TIMEOUT'}`。

Python 侧 `parse_qwen_quota` 再对 `fiveHourText` / `weeklyText` 二次解析出
`{percentage, reset_seconds, reset_at}`，转 `QuotaDetail`（复用 kimi 模型）。

**实测后按用户反馈的真实页面文字收紧正则**——与 opencode 同款迭代方式
（其脚本注释明说 "reads the rendered text"）。骨架版以宽容为主：找不到两个字段
之一就标 `QuotaStatus.PARTIAL`，全找不到标 `UNKNOWN`，不让崩。

## 刷新节奏与面板恢复

- 主轮询 `QWEN_WEB_QUOTA_INTERVAL_MS = 900_000`（15 分钟；见 2026-08-05
  hidden-refresh 设计，headless 被风控作废后改为有头隐藏刷新并拉长间隔）。
- 面板恢复显示（showEvent / 取消最小化）补刷额度：复用 `gui.py` 现有 60s 节流
  `showEvent` 路径，加一行 qwen 分支（参照 opencode 当前实现 `gui.py:3259`）。

## 鉴权与缓存边界

- AACC 全程不碰账号密码；WebView 持久化 cookie 到 AACC 私有目录：
  - macOS `config_dir / "qwen-web-session"`
  - Windows `%LOCALAPPDATA%/AACC/qwen-web-session`
  - `protect_directory` 加 DACL（与 kimi/opencode 一致）。
- token 续期由页面自身处理，Python 只收归一化数值。
- 不宣称消费级 Windows 10/11 真机验证。

## 测试（TDD，diff-cover ≥90%）

- `tests/test_qwen_web_quota.py`：解析器对五种文本形态——
  含 5h/7d 完整百分比 + 重置 / 仅百分比 / 中英混排 / 缺一个字段 / 全空——
  归类为 `OK` / `PARTIAL` / `UNKNOWN` 正确，重置时间解析正确。
- `tests/test_qwen_web_session.py`：桥接 title 解析、generation 竞态、
  URL 白名单拒绝、刷新超时 watch dog。
- `tests/test_qwen_web_error.py`：错误归一化与 i18n 文案。
- `tests/test_qwen_web_quota_service.py`：定时器、信号转发、start/stop、
  `workspace_url` 空时不启动。
- `tests/test_app.py`：factory 装配、`start_qwen_web_quota` 分支、
  `qwen_quota_enabled=False` 时 service 不创建。
- GUI 测试参照 `test_opencode_*` 结构测 `QwenQuotaBar` 各显示状态。
- 时间造假一律 `time.monotonic() - INTERVAL - 1` 相对回拨（AGENTS.md 红线）。

## 版本号

新功能，按 `pyproject.toml` 次版本递增
（1.4.4 → 1.4.5rc1），走四件套同步顺序：
`src/aacc/__init__.py::__version__` → `pyproject.toml` → `uv lock` →
双语 CHANGELOG 最新段标题用 `public_version()` →
`docs/release-notes-<__version__>.md` 存在。
`tests/test_packaging.py` 校验全部一致。

## 不做

- 不做 Qwen Code 任务发现（CLI 任务发现是另一回事，不在本次范围）。
- 不改 kimi / opencode 现有额度流程。
- 不写 fetch 拦截（百炼 API 内部不稳定，DOM 提取更稳）。
- 不宣称消费级 Windows 10/11 真机验证。
- 本次不做 Windows Edge CDP 专属会话（service 在 win32 按 native 回退，
  与 opencode 初版策略一致；Edge CDP 作为后续迭代）。

## 修正记录（2026-08-04，1.4.5-rc.2 落地）

初版（1.4.5-rc.1）上线后实测暴露四处问题，修正如下（详见
`docs/superpowers/plans/2026-08-04-qwen-login-quota-fix.md`）：

1. **授权浏览器：macOS 登录改走真实 Chrome（CDP）。** 阿里云登录
   （密码/扫码/RAM）跨多域名且会发起新窗口请求；QtWebView 6.11 无
   `createWindow` 钩子，弹窗被静默丢弃，RAM 入口链接无法跳转。沿用
   Windows Edge CDP 范式：新增 `qwen_chrome_cdp.py` + `qwen_chrome_session.py`，
   darwin 且装有 Chrome 时由 service 选择 Chrome 会话——可见窗口完成
   登录、headless 做 5 分钟刷新，cookie 落在 AACC 专属
   `qwen-chrome-profile/`；未装 Chrome 回退原生 QtWebView 会话
   （`qwen_web_session.py`，同时是 Windows native 路径）。不打包任何
   浏览器内核，体积不变；`websocket-client` 依赖改为跨平台。
2. **未登录误判修复。** 匿名/登录页的介绍文案同样含「5 小时 / 7 天」
   字样；提取脚本改为上抛原始文本片段（`fiveHourText` / `weeklyText`），
   片段中没有任何百分比即发 `kind=unauthorized`，不再冒充额度关闭登录框。
3. **小数百分比与防串窗。** 百分比按 `\d{1,3}(?:\.\d+)?\s*%` 捕获并以
   float 贯穿 `QuotaDetail.percentage`（int → float | None），GUI 以
   `format_quota_percentage` 渲染（0.04% / 12.5% / 30%）；重置倒计时只取
   本窗切片（切到下一窗口标题为止），且跳过首行标签、优先含「重置/后」
   标记的行。
4. **刷新整页重载。** qwen/opencode 会话 `refresh()` 改为
   `_reload_workspace_url()`（当前 URL 即工作区 → `reload()`，否则
   `setUrl`），脚本在 load 完成后执行——初版只重跑脚本读旧 DOM，
   5 分钟轮询数值恒定。kimi 为实时 API fetch，不受影响。
5. **service win32 回退。** 初版 `_ensure_session` 在 win32 导入不存在
   的 `aacc.qwen_edge_session`；改回设计原意的 native 回退。