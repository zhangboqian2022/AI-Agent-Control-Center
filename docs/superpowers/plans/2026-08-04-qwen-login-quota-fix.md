# Qwen 登录浏览器适配与额度刷新修复实施计划（2026-08-04）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复百炼 token-plan 登录无法完成、未登录误判为已登录（0% 假额度）、
小数百分比丢失、opencode/qwen 定时刷新不更新数据四个问题。

**Architecture:** 最终采用 **真实 Chrome + CDP**（沿用 Windows Edge CDP 范式，
见文末"方案演进"）。darwin 且装有 Chrome 时，service 选择新增的
`qwen_chrome_cdp.py` / `qwen_chrome_session.py`：可见窗口完成阿里云（含 RAM）
登录、headless 做 5 分钟刷新，cookie 落在 AACC 专属 `qwen-chrome-profile/`；
未装 Chrome 回退原生 QtWebView 会话（`qwen_web_session.py`，同为 Windows native
路径）。DOM 提取脚本改为上抛原始文本片段，Python 侧解析小数百分比；所有 DOM
提取型会话（qwen/opencode）refresh 改为整页重载，避免读旧 DOM。

**Tech Stack:** Python 3.12+ / PySide6 6.11、`websocket-client`（改为跨平台，
CDP 传输）、pytest（offscreen）、PyInstaller。**不打包任何浏览器内核，体积不变。**

## 方案演进（重要）

初稿曾计划把 Qwen 会话切到 **QWebEngineView（内嵌 Chromium）** 并已实现、测试
全绿。但该方案与项目两条打包不变量冲突（`test_packaging.py` 强制：macOS
`build_app.sh` 与 Windows spec 均不得含 QtWebEngine），且会让应用体积增加数百 MB。
经与用户确认，改为**驱动外部真实 Chrome（CDP）**：与 Windows Edge CDP 同源、
零体积增长、真实浏览器保证 RAM 登录可用。QWebEngineView 相关改动已全部回退
（spec/hooks/会话模块），仅保留解析器、GUI 小数、opencode 整页重载等与方案无关的修复。

## 根因证据（已确认）

1. **未登录误判**：日志 `Qwen quota raw={'fiveHour': {'percentage': None,
   'resetSeconds': 622800}, ...}` —— 匿名页的「5 小时 / 7 天」介绍文字被当成
   额度数据；`percentage=None` 时 `_usage()` 落到 0 → 显示 0%；`kind=quota`
   即关登录对话并 `login_state_changed(True)`。cookie 取证
   （`~/Library/HTTPStorages/com.aacc.controlcenter.binarycookies`）确认
   webview 从未持有阿里云会话 cookie —— 与 Chrome 无关。
2. **小数丢失**：JS `(\d{1,3})\s*%` 与 Python `round(int)` 双重丢精度；
   0.04% 这类真实用量无法呈现。
3. **opencode 不刷新**：日志 6 次轮询 raw 完全一致（`resetInSec: 8400` 恒定）
   —— refresh 只重跑脚本读旧 DOM，未重载页面。
4. **QtWebView 无 `createWindow`**（PySide6 6.11.1 实测 dir() 确认）：
   window.open / target=_blank 静默丢弃；QtWebView 亦无 profile API，
   `storage_path` 创建后从未被使用（死代码）。

## Global Constraints

- TDD：先写失败测试；改动行 diff-cover ≥90%（CI macOS 腿强制）。
- 时间造假一律 `time.monotonic() - INTERVAL - 1` 相对回拨。
- 版本号四件套顺序：`src/aacc/__init__.py::__version__` → `pyproject.toml`
  → `uv lock` → 双语 CHANGELOG 标题用 `public_version()` →
  `docs/release-notes-<__version__>.md`。本次 1.4.5rc1 → 1.4.5rc2。
- 文档中英双语成对；不宣称消费级 Windows 10/11 真机验证。
- 日志不得出现 URL query 参数（现有 redact 测试约束）。

---

### Task 1: qwen_web_quota.py 解析器重写（文本片段 + 小数）

**Files:** Modify `src/aacc/qwen_web_quota.py`；Test `tests/test_qwen_web_quota.py`

**Interfaces:** `parse_qwen_quota(payload, *, now) -> QwenQuota` 不变；
输入 raw 支持 `{fiveHourText: str, weeklyText: str}`（新）与既有结构化
`fiveHour/sevenDay` dict（兼容，但结构化分支 percentage 改 float 语义）。

- [ ] 先改测试：文本片段含 `0.04%` → percentage==0.04；`12.5%` → 12.5；
      重置时间只取本窗片段（5h 片段含「7 天」字样不得污染 5h reset）；
      无百分比片段 → 该窗 None；双窗皆无 → UNKNOWN。
- [ ] 实现：`_parse_window(text, *, now)`：首个
      `(\d{1,3}(?:\.\d+)?)\s*%` → float；重置按 天/小时/分钟 累加；
      `QuotaDetail.percentage` 类型 int → float（kimi_quota.py dataclass）。
- [ ] 跑 `pytest tests/test_qwen_web_quota.py -q` 全绿。

### Task 2: GUI 小数百分比展示

**Files:** Modify `src/aacc/gui.py`（`_set_quota_metric` / `QwenQuotaBar._detail_tooltip`）；
Test `tests/test_qwen_quota_bar.py`（补小数用例）

- [ ] `_format_quota_percentage(value)`：整数 → `26%`；小数 → `0.04%` /
      `12.5%`（`f"{v:.2f}".rstrip('0').rstrip('.') + '%'`）。
- [ ] `_set_quota_metric(percentage: float | None, ...)`；bar `setValue(int(round))`。
- [ ] kimi/opencode 传入运行时仍为 int，显示不变（测试回归证明）。

### Task 3: qwen_web_session.py 重写为 QWebEngineView

**Files:** Modify `src/aacc/qwen_web_session.py`；Test `tests/test_qwen_web_session.py`

**关键点:**
- `QwenWebEnginePage(QWebEnginePage).createWindow(_type) -> self`：弹窗同窗导航。
- `QWebEngineProfile`（具名持久化）：`setPersistentStoragePath(storage_path)`、
  `setCachePath(storage_path/"cache")`、AllowPersistentCookies —— storage_path
  真正被使用。
- 视图惰性创建（`view` property + setter），单测继续注入 FakeWebView，
  不在 CI 构造真实引擎视图。
- 信号：`loadFinished(bool)` → `_on_load_finished`；`titleChanged` 桥接不变。
- 提取脚本：定位「5 小时」「7 天」行后切到下一个窗口标题为止（防串窗），
  上抛 `raw: {fiveHourText, weeklyText}`；**两窗均无百分比 → 发
  `kind=unauthorized`（不再假装成功）**；保留 50×1s 重试与 DOM_TIMEOUT。
- `refresh()`：一律 `_reload_workspace_url()`（当前 URL==workspace →
  `view.reload()`，否则 `setUrl`）；脚本仅在 load 成功后执行。
- `_handle_bridge`：quota → may_reuse=True 关对话；unauthorized →
  may_reuse=False，不关对话、不伪装登录成功。
- 登出：`profile.cookieStore().deleteAllCookies()` + localStorage 清理。

- [ ] 更新 FakeWebView：加 `reload()` 计数；测试改为 `_on_load_finished(True)`。
- [ ] 新增测试：createWindow 返回自身；quota 无百分比 → unauthorized；
      origin 匹配时 refresh 调 reload 不直接 runJavaScript。
- [ ] `pytest tests/test_qwen_web_session.py -q` 全绿。

### Task 4: opencode_web_session.py refresh 整页重载

**Files:** Modify `src/aacc/opencode_web_session.py`；
Test `tests/test_opencode_web_session.py`

- [ ] `refresh()` 同 Task 3 的 `_reload_workspace_url()` 语义；
      替换 `test_refresh_runs_fetch_script_without_reload_when_origin_matches`
      为重载断言（reload 计数 + load 完成后才出脚本）。
- [ ] `pytest tests/test_opencode_web_session.py -q` 全绿。

### Task 5: qwen service win32 回退修正

**Files:** Modify `src/aacc/qwen_web_quota_service.py`；
Test `tests/test_qwen_web_quota_service.py`

- [ ] 删除 `import_module("aacc.qwen_edge_session")` 分支（模块不存在，
      Windows 运行时 ImportError）；win32 与 darwin 一致走
      `_create_native_web_session`（设计文档原意：Edge CDP 后续迭代）。
- [ ] 更新 service 测试中 win32 用例断言。

### Task 6: 打包与发布四件套（1.4.5rc2）

**Files:** `AACC.spec`、`AACC-windows.spec`（hiddenimports 增
`PySide6.QtWebEngineWidgets`、`PySide6.QtWebEngineCore`）、
`src/aacc/__init__.py`、`pyproject.toml`、`uv.lock`（`uv lock`）、
`CHANGELOG.md`、`CHANGELOG.zh-CN.md`、`docs/release-notes-1.4.5rc2.md`、
`docs/superpowers/specs/2026-08-04-qwen-quota-design.md`（补修正记录）、
`AGENTS.md`（进度段）。

- [ ] 四件套按序；`pytest tests/test_packaging.py -q` 绿。
- [ ] release notes 注明：登录窗口切换 Chromium 内核、应用体积增大
      （QtWebEngine）、cookie 缓存改到 AACC 私有 profile。

### Task 7: 全量验证

- [ ] `.venv/bin/python -m pytest -q`、`ruff check`、`ruff format --check`、
      `mypy src/aacc` 全绿。
- [ ] `scripts/build_app.sh` 出包；启动冒烟：Qwen 登录窗口可打开、
      阿里云登录页可交互；提示用户实测 RAM 登录与 0.04% 展示。
