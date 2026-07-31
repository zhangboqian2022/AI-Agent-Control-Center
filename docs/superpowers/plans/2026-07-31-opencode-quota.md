# OpenCode 用量（Go 套餐）支持实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AACC 面板新增 OpenCode 用量条：自持 QtWebView 会话登录 opencode.ai 工作区，展示 Go 套餐滚动/每周/每月用量（百分比 + 重置倒计时），数据源为工作区 `/go` 页面同源 `/_server` RPC `subscription.get`。

**Architecture:** 仿 Kimi web 链路分层，与 Kimi 模块并列互不引用：会话（`opencode_web_session.py`）持 QtWebView 存 cookie，refresh 时在页面上下文直接 fetch `/_server`（实测 wire 格式），title-bridge 上抛原始载荷 → 纯函数解析器（`opencode_web_quota.py`）归一化为 `OpenCodeQuota` → 服务（`opencode_web_quota_service.py`）300s 轮询 + 状态机 → GUI 三行额度条（`OpenCodeQuotaBar`）。工作区 URL 来自 `config.yaml` 的 `opencode_workspace_url`。macOS 先行，Windows 后续迭代。

**Tech Stack:** Python 3.12+ / PySide6（QtWebView.QWebView）/ pytest / ruff / mypy。解析器为纯 Python 无网络依赖。

## Global Constraints

- 平台：先 macOS（QtWebView）；Windows（Edge CDP）为后续迭代，本迭代 win32 不装配 opencode 服务（app.py 工厂返回 None）。
- 显示：仅三行用量（滚动→5H、每周→WEEK、每月→MONTH），不展示余额。
- 工作区 URL：`config.yaml` 新增 `opencode_workspace_url`；host 必须 `opencode.ai`、路径必须以 `/workspace/` 开头；示例（用户确认）`https://opencode.ai/workspace/wrk_01KYVH7EJDHAAE4TZ51J3TX5CS/go`。
- 数据源：同源 `POST https://opencode.ai/_server` RPC `subscription.get`，请求头 `X-Server-Id: 7abeebee372f304e050aaaf92be863f4a86490e382f8c79db68fd94040d691b4`、`X-Server-Instance: server-fn:1`、`Content-Type: application/json`；body `{"t":{"t":15,"l":1,"c":"Array","a":[{"t":2,"s":"<workspace_id>"}]},"f":31,"m":[]}`；响应含 `subscription.rollingUsage/weeklyUsage/monthlyUsage` 各 `{usagePercent, resetInSec}`。
- 会话目录：`{config_dir}/opencode-web-session/`（macOS）。
- 刷新节奏：300s 定时（`OPENCODE_WEB_QUOTA_INTERVAL_MS = 300_000`）+ 面板恢复显示 60s 节流补刷（沿用 `RESTORE_QUOTA_REFRESH_INTERVAL_SECONDS`）。
- 状态语义：OK / PARTIAL / UNKNOWN / STALE（临时失败保留已知数据标 STALE）；错误分类：UNAUTHORIZED / REFRESH_TIMEOUT / REFRESH_FAILED / PARSE_FAILED。
- 安全：cookie 留在 WebView 沙箱目录；Python 只收归一化数值；日志不得含 token/会话内容。
- TDD：每任务先写失败测试再实现；CI diff-cover 改动行覆盖率 ≥90%。
- 测试时间造假必须相对当前时刻计算（`datetime.now(UTC)` 作基准 + `timedelta` 相对断言，或注入 `now` 回调），禁止写死绝对时间点。
- i18n 中英双语成对（i18n.py 两份 catalog + `LANGUAGE_SUBSCRIBER_COMPONENTS` 两处）。
- 提交信息英文 `feat:` / `fix:` / `docs:`；每任务一个提交。
- 本机全绿检查：`.venv/bin/python -m pytest -q`、`.venv/bin/ruff check src tests`、`.venv/bin/ruff format --check src tests`、`.venv/bin/mypy src/aacc`。
- 不宣称消费级 Windows 真机验证。

## File Structure

| 文件 | 责任 |
|---|---|
| Create `src/aacc/opencode_web_quota.py` | 纯解析器：`OpenCodeUsage`/`OpenCodeQuota` 模型 + `parse_opencode_quota` |
| Create `src/aacc/opencode_web_error.py` | 错误分类归一化 + i18n 文案映射 |
| Create `src/aacc/opencode_web_session.py` | QtWebView 会话：fetch 脚本生成 + 登录/登出/刷新/桥接 |
| Create `src/aacc/opencode_web_quota_service.py` | QTimer 300s 轮询服务（信号 quota_updated/login_state_changed/error_occurred） |
| Modify `src/aacc/models.py` | `AppConfig.opencode_workspace_url` + field_validator |
| Modify `src/aacc/i18n.py` | `opencode.*` / `settings.opencode_*` 键（中英）+ 组件列表 |
| Modify `src/aacc/gui.py` | `OpenCodeQuotaBar` + SettingsDialog 按钮 + MainWindow 接线 |
| Modify `src/aacc/app.py` | Runtime 字段 + 工厂 + build_runtime + MainWindow 装配 |
| Create `tests/test_opencode_web_quota.py` | 解析器测试 |
| Create `tests/test_opencode_web_error.py` | 错误归一化测试 |
| Create `tests/test_opencode_web_session.py` | 会话测试（FakeWebView + 直接驱动私有回调） |
| Create `tests/test_opencode_web_quota_service.py` | 服务测试（FakeSession 注入） |
| Create `tests/test_opencode_quota_bar.py` | 额度条渲染测试 |
| Modify `tests/test_config.py` | workspace URL 校验测试 |
| Modify `tests/test_gui_quota_wiring.py` | MainWindow 接线测试（Fake 服务） |

设计文档：`docs/superpowers/specs/2026-07-31-opencode-quota-design.md`（已提交 0357eed，本计划含一处实现修正：取数方式由"DocumentCreation hook 拦截"改为"页面内直接 fetch"，因 QtWebView 无 DocumentCreation 注入点，设计文档已同步更新）。

---

## Task 1: 纯解析器 `opencode_web_quota.py`

**Files:**
- Create: `src/aacc/opencode_web_quota.py`
- Test: `tests/test_opencode_web_quota.py`

**Interfaces:**
- Consumes: `aacc.kimi_quota.QuotaStatus`（已存在，`OK/PARTIAL/UNKNOWN/STALE` StrEnum）
- Produces:
  - `OpenCodeUsage` dataclass：`percentage: int | None`、`reset_seconds: int | None`、`reset_at: datetime | None`
  - `OpenCodeQuota` dataclass：`rolling/weekly/monthly: OpenCodeUsage | None`、`status: QuotaStatus`、`fetched_at: datetime`
  - `parse_opencode_quota(payload: object, *, now: datetime) -> OpenCodeQuota`（payload 为桥接载荷 dict 或原始响应；`{raw: {subscription: ...}}` 或 `{subscription: ...}` 或直接 `{rollingUsage: ...}` 三种形态均可）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opencode_web_quota.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aacc.kimi_quota import QuotaStatus
from aacc.opencode_web_quota import parse_opencode_quota


def _now() -> datetime:
    return datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _payload(node: object) -> object:
    return {"raw": {"subscription": node}}


def test_parse_full_payload_ok() -> None:
    now = _now()
    quota = parse_opencode_quota(
        _payload(
            {
                "rollingUsage": {"usagePercent": 0, "resetInSec": 17760},
                "weeklyUsage": {"usagePercent": 42.5, "resetInSec": 226800},
                "monthlyUsage": {"usagePercent": 100, "resetInSec": 2674800},
            }
        ),
        now=now,
    )
    assert quota.status is QuotaStatus.OK
    assert quota.rolling is not None
    assert quota.rolling.percentage == 0
    assert quota.rolling.reset_seconds == 17760
    assert quota.rolling.reset_at == now + timedelta(seconds=17760)
    assert quota.weekly is not None and quota.weekly.percentage == 43
    assert quota.monthly is not None and quota.monthly.percentage == 100


def test_parse_fraction_percent_scaled() -> None:
    quota = parse_opencode_quota(
        {"subscription": {"rollingUsage": {"usagePercent": 0.42, "resetInSec": 60}}},
        now=_now(),
    )
    assert quota.rolling is not None and quota.rolling.percentage == 42


def test_parse_partial_when_window_missing() -> None:
    quota = parse_opencode_quota(
        {"subscription": {"rollingUsage": {"usagePercent": 10, "resetInSec": 60}}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.PARTIAL
    assert quota.rolling is not None
    assert quota.weekly is None and quota.monthly is None


def test_parse_unknown_when_no_subscription() -> None:
    quota = parse_opencode_quota({"unrelated": {}}, now=_now())
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.rolling is None and quota.weekly is None and quota.monthly is None


def test_parse_direct_subscription_node() -> None:
    quota = parse_opencode_quota(
        {"rollingUsage": {"usagePercent": 5, "resetInSec": 3600}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.PARTIAL
    assert quota.rolling is not None and quota.rolling.percentage == 5


def test_parse_invalid_values_ignored() -> None:
    quota = parse_opencode_quota(
        {
            "subscription": {
                "rollingUsage": {"usagePercent": 101, "resetInSec": -1},
                "weeklyUsage": {"usagePercent": "abc", "resetInSec": "not-a-number"},
                "monthlyUsage": {"usagePercent": True, "resetInSec": None},
            }
        },
        now=_now(),
    )
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.rolling is None and quota.weekly is None and quota.monthly is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_opencode_web_quota.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'aacc.opencode_web_quota'`）

- [ ] **Step 3: Write minimal implementation**

```python
# src/aacc/opencode_web_quota.py
"""Parse opencode.ai workspace usage payloads into the shared quota model.

The workspace page renders Go-plan usage through the same-origin ``/_server``
RPC ``subscription.get``; this module converts captured payloads into a
normalized model without coupling network or browser state to the parser.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from aacc.kimi_quota import QuotaStatus


@dataclass(frozen=True)
class OpenCodeUsage:
    percentage: int | None
    reset_seconds: int | None
    reset_at: datetime | None


@dataclass(frozen=True)
class OpenCodeQuota:
    rolling: OpenCodeUsage | None
    weekly: OpenCodeUsage | None
    monthly: OpenCodeUsage | None
    status: QuotaStatus
    fetched_at: datetime


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _percentage(value: object) -> int | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    if number <= 1:
        number *= 100
    if number > 100:
        return None
    return round(number)


def _usage(value: object, *, now: datetime) -> OpenCodeUsage | None:
    if not isinstance(value, dict):
        return None
    percentage = _percentage(value.get("usagePercent"))
    reset = _number(value.get("resetInSec"))
    reset_seconds = int(reset) if reset is not None and reset >= 0 else None
    if percentage is None and reset_seconds is None:
        return None
    reset_at = now + timedelta(seconds=reset_seconds) if reset_seconds is not None else None
    return OpenCodeUsage(percentage, reset_seconds, reset_at)


def parse_opencode_quota(payload: object, *, now: datetime) -> OpenCodeQuota:
    """Convert a captured opencode.ai usage payload into ``OpenCodeQuota``."""

    raw: object = payload.get("raw") if isinstance(payload, dict) else payload
    node: object = None
    if isinstance(raw, dict):
        subscription = raw.get("subscription")
        if isinstance(subscription, dict):
            node = subscription
        elif isinstance(raw.get("rollingUsage"), dict):
            node = raw
    if not isinstance(node, dict):
        return OpenCodeQuota(None, None, None, QuotaStatus.UNKNOWN, now)
    rolling = _usage(node.get("rollingUsage"), now=now)
    weekly = _usage(node.get("weeklyUsage"), now=now)
    monthly = _usage(node.get("monthlyUsage"), now=now)
    known = sum(item is not None for item in (rolling, weekly, monthly))
    status = (
        QuotaStatus.OK if known == 3 else QuotaStatus.PARTIAL if known else QuotaStatus.UNKNOWN
    )
    return OpenCodeQuota(rolling, weekly, monthly, status, now)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_opencode_web_quota.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add src/aacc/opencode_web_quota.py tests/test_opencode_web_quota.py
git commit -m "feat: add opencode usage quota parser"
```

---

## Task 2: 配置项 `opencode_workspace_url`

**Files:**
- Modify: `src/aacc/models.py`（AppConfig，L121-126）
- Test: `tests/test_config.py`（追加）

**Interfaces:**
- Consumes: 无
- Produces: `AppConfig.opencode_workspace_url: str`（默认 `""`，host 必须 `opencode.ai` 且路径以 `/workspace/` 开头，否则 ValidationError）

- [ ] **Step 1: Write the failing test**

在 `tests/test_config.py` 末尾追加（若文件头已 import `pytest`、`default_config`、`save_config`、`load_config` 则复用；缺哪个补哪个）：

```python
import pytest
from pydantic import ValidationError

from aacc.config import default_config, load_config, save_config
from aacc.models import AppConfig


def test_opencode_workspace_url_accepts_valid_workspace_page() -> None:
    config = default_config()
    config.opencode_workspace_url = (
        "https://opencode.ai/workspace/wrk_01KYVH7EJDHAAE4TZ51J3TX5CS/go"
    )
    assert config.opencode_workspace_url.endswith("/go")


def test_opencode_workspace_url_defaults_empty() -> None:
    assert AppConfig().opencode_workspace_url == ""


def test_opencode_workspace_url_rejects_foreign_host() -> None:
    with pytest.raises(ValidationError):
        AppConfig(opencode_workspace_url="https://example.com/workspace/wrk_1")


def test_opencode_workspace_url_rejects_http_scheme() -> None:
    with pytest.raises(ValidationError):
        AppConfig(opencode_workspace_url="http://opencode.ai/workspace/wrk_1")


def test_opencode_workspace_url_rejects_non_workspace_path() -> None:
    with pytest.raises(ValidationError):
        AppConfig(opencode_workspace_url="https://opencode.ai/zen")


def test_opencode_workspace_url_round_trips_through_config_file(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    config = default_config()
    config.opencode_workspace_url = "https://opencode.ai/workspace/wrk_123/go"
    save_config(path, config)
    loaded = load_config(path)
    assert loaded.opencode_workspace_url == config.opencode_workspace_url
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL（`AttributeError: 'AppConfig' object has no attribute 'opencode_workspace_url'`）

- [ ] **Step 3: Write minimal implementation**

`src/aacc/models.py` 顶部 import 区加 `from urllib.parse import urlparse`；`AppConfig`（L121-126）改为：

```python
class AppConfig(BaseModel):
    config_version: int = Field(default=1, ge=1)
    app: AppSettings = Field(default_factory=AppSettings)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    hotkeys: dict[str, str] = Field(default_factory=dict)
    tasks: list[TaskConfig] = Field(default_factory=list)
    opencode_workspace_url: str = Field(default="", max_length=2048)

    @field_validator("opencode_workspace_url")
    @classmethod
    def validate_opencode_workspace_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        try:
            parsed = urlparse(value)
        except ValueError as error:
            raise ValueError("opencode_workspace_url must be a valid URL") from error
        if parsed.scheme != "https" or parsed.netloc != "opencode.ai":
            raise ValueError("opencode_workspace_url host must be opencode.ai")
        if not parsed.path.startswith("/workspace/"):
            raise ValueError(
                "opencode_workspace_url must point to an opencode.ai workspace page"
            )
        return value
```

注意：`config.py` 无需改动（`load_config` 已捕获 `ValidationError` 并包装为 `ValueError`）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS（原有用例 + 6 个新用例全过）

- [ ] **Step 5: Commit**

```bash
git add src/aacc/models.py tests/test_config.py
git commit -m "feat: add opencode workspace url config"
```

---

## Task 3: 错误归一化 `opencode_web_error.py` + i18n 键

**Files:**
- Create: `src/aacc/opencode_web_error.py`
- Modify: `src/aacc/i18n.py`
- Test: `tests/test_opencode_web_error.py`、`tests/test_i18n.py`（追加）

**Interfaces:**
- Consumes: `aacc.i18n.LanguageManager`
- Produces:
  - `OpenCodeQuotaErrorCategory(StrEnum)`：`UNAUTHORIZED = "unauthorized"`、`REFRESH_TIMEOUT = "refresh_timeout"`、`REFRESH_FAILED = "refresh_failed"`、`PARSE_FAILED = "parse_failed"`
  - `normalize_opencode_quota_error_category(value: object) -> OpenCodeQuotaErrorCategory`
  - `opencode_quota_error_text(category: object, language_manager: LanguageManager) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opencode_web_error.py
from __future__ import annotations

from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.opencode_web_error import (
    OpenCodeQuotaErrorCategory,
    normalize_opencode_quota_error_category,
    opencode_quota_error_text,
)


def test_normalize_accepts_enum_and_string() -> None:
    assert (
        normalize_opencode_quota_error_category(OpenCodeQuotaErrorCategory.UNAUTHORIZED)
        is OpenCodeQuotaErrorCategory.UNAUTHORIZED
    )
    assert (
        normalize_opencode_quota_error_category("refresh_timeout")
        is OpenCodeQuotaErrorCategory.REFRESH_TIMEOUT
    )


def test_normalize_unknown_falls_back_to_refresh_failed() -> None:
    assert (
        normalize_opencode_quota_error_category("bogus")
        is OpenCodeQuotaErrorCategory.REFRESH_FAILED
    )
    assert (
        normalize_opencode_quota_error_category(None)
        is OpenCodeQuotaErrorCategory.REFRESH_FAILED
    )


def test_error_text_maps_both_languages() -> None:
    zh = LanguageManager(ZH_CN)
    en = LanguageManager(EN_US)
    assert opencode_quota_error_text("unauthorized", zh) == "OpenCode 登录已过期，请重新授权"
    assert opencode_quota_error_text("unauthorized", en) == (
        "OpenCode sign-in expired. Please authorize again"
    )
    assert opencode_quota_error_text("refresh_timeout", zh) == "OpenCode 用量刷新超时"
```

同时追加 `tests/test_i18n.py`（沿文件现有结构；若为 catalog 完整性测试则追加键断言）：

```python
def test_opencode_web_keys_exist_in_both_catalogs() -> None:
    from aacc.i18n import CATALOGS, EN_US, ZH_CN

    keys = [
        "opencode.web_title",
        "opencode.web_starting",
        "opencode.web_need_config",
        "opencode.web_unauthorized",
        "opencode.web_refresh_timeout",
        "opencode.web_refresh_failed",
        "opencode.web_parse_failed",
        "opencode.quota",
        "settings.opencode_web_login",
        "settings.opencode_logout",
    ]
    for language in (ZH_CN, EN_US):
        for key in keys:
            assert key in CATALOGS[language], f"{key} missing in {language}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_opencode_web_error.py tests/test_i18n.py -q`
Expected: FAIL（module 不存在 / i18n 键缺失）

- [ ] **Step 3: Write minimal implementation**

```python
# src/aacc/opencode_web_error.py
"""Normalize opencode.ai workspace usage errors for display."""

from __future__ import annotations

from enum import StrEnum

from aacc.i18n import LanguageManager


class OpenCodeQuotaErrorCategory(StrEnum):
    UNAUTHORIZED = "unauthorized"
    REFRESH_TIMEOUT = "refresh_timeout"
    REFRESH_FAILED = "refresh_failed"
    PARSE_FAILED = "parse_failed"


_ERROR_KEYS: dict[OpenCodeQuotaErrorCategory, str] = {
    OpenCodeQuotaErrorCategory.UNAUTHORIZED: "opencode.web_unauthorized",
    OpenCodeQuotaErrorCategory.REFRESH_TIMEOUT: "opencode.web_refresh_timeout",
    OpenCodeQuotaErrorCategory.REFRESH_FAILED: "opencode.web_refresh_failed",
    OpenCodeQuotaErrorCategory.PARSE_FAILED: "opencode.web_parse_failed",
}


def normalize_opencode_quota_error_category(value: object) -> OpenCodeQuotaErrorCategory:
    if isinstance(value, OpenCodeQuotaErrorCategory):
        return value
    if isinstance(value, str):
        try:
            return OpenCodeQuotaErrorCategory(value)
        except ValueError:
            pass
    return OpenCodeQuotaErrorCategory.REFRESH_FAILED


def opencode_quota_error_text(category: object, language_manager: LanguageManager) -> str:
    normalized = normalize_opencode_quota_error_category(category)
    return language_manager.text(_ERROR_KEYS[normalized])
```

`src/aacc/i18n.py` 改动：
1. `LanguageSubscriberComponent` Literal 与 `LANGUAGE_SUBSCRIBER_COMPONENTS` frozenset（L12-28）各加 `"opencode_web_session"`。
2. ZH_CN catalog（L33 起，`"settings.kimi_logout"` 之后）追加：

```python
        "settings.opencode_web_login": "登录 OpenCode（同步 5H / WEEK / MONTH）",
        "settings.opencode_logout": "退出 OpenCode",
```

在 `"quota.last_update"` 之后追加：

```python
        "opencode.quota": "OpenCode 用量",
        "opencode.web_title": "OpenCode 工作区登录",
        "opencode.web_starting": "正在启动 OpenCode 登录页面，请稍候…",
        "opencode.web_need_config": "请先在 config.yaml 中配置 opencode_workspace_url",
        "opencode.web_unauthorized": "OpenCode 登录已过期，请重新授权",
        "opencode.web_refresh_timeout": "OpenCode 用量刷新超时",
        "opencode.web_refresh_failed": "OpenCode 用量刷新失败",
        "opencode.web_parse_failed": "OpenCode 用量数据解析失败",
```

3. EN_US catalog 对应追加：

```python
        "settings.opencode_web_login": "Sign in to OpenCode (sync 5H / WEEK / MONTH)",
        "settings.opencode_logout": "Sign out of OpenCode",
        "opencode.quota": "OpenCode usage",
        "opencode.web_title": "OpenCode workspace login",
        "opencode.web_starting": "Starting the OpenCode login page. Please wait…",
        "opencode.web_need_config": "Set opencode_workspace_url in config.yaml first",
        "opencode.web_unauthorized": "OpenCode sign-in expired. Please authorize again",
        "opencode.web_refresh_timeout": "OpenCode usage refresh timed out",
        "opencode.web_refresh_failed": "OpenCode usage refresh failed",
        "opencode.web_parse_failed": "OpenCode usage data could not be parsed",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_opencode_web_error.py tests/test_i18n.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aacc/opencode_web_error.py src/aacc/i18n.py tests/test_opencode_web_error.py tests/test_i18n.py
git commit -m "feat: add opencode usage error categories and i18n keys"
```

---

## Task 4: QtWebView 会话 `opencode_web_session.py`

**Files:**
- Create: `src/aacc/opencode_web_session.py`
- Test: `tests/test_opencode_web_session.py`

**Interfaces:**
- Consumes: `aacc.file_security.protect_directory`、`aacc.i18n.LanguageManager`；QtWebView 的 `QWebView`（macOS 由 app.py 在 QApplication 前统一初始化，本模块不重复调用 `QtWebView.initialize()`）
- Produces:
  - `BRIDGE_PREFIX = "AACC_OPENCODE_QUOTA:"`、`BRIDGE_PAYLOAD_KEY = "__AACC_OPENCODE_QUOTA_PAYLOAD__"`
  - `SERVER_FN_HASH = "7abeebee372f304e050aaaf92be863f4a86490e382f8c79db68fd94040d691b4"`、`REFRESH_TIMEOUT_MS = 60_000`、`LOGOUT_CLEANUP_TIMEOUT_MS = 10_000`
  - `opencode_webview_user_data_path(config_dir: Path) -> Path`
  - `workspace_id_from_url(url: str) -> str | None`
  - `opencode_usage_fetch_script(url: str, generation: int) -> str`
  - `OpenCodeWebSession(QObject)`：信号 `login_state_changed(bool)` / `quota_received(object)` / `error_occurred(str)`；方法 `set_workspace_url(url)` / `refresh()` / `open_login(parent=None)` / `logout() -> bool` / `close()` / `retranslate_ui()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opencode_web_session.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl

from aacc.opencode_web_session import (
    BRIDGE_PAYLOAD_KEY,
    BRIDGE_PREFIX,
    SERVER_FN_HASH,
    OpenCodeWebSession,
    opencode_usage_fetch_script,
    workspace_id_from_url,
)

WORKSPACE_URL = "https://opencode.ai/workspace/wrk_01KYVH7EJDHAAE4TZ51J3TX5CS/go"


class FakeWebView(QObject):
    def __init__(self) -> None:
        super().__init__()
        self._url = QUrl()
        self.scripts: list[str] = []
        self.script_result: object = None
        self.cookies_deleted = False
        self.deleted = False
        self.settings = lambda: _FakeSettings()

    def url(self) -> QUrl:
        return self._url

    def load(self, url: QUrl) -> None:
        self._url = url
        self.scripts = []

    def runJavaScript(self, script: str, callback=None) -> None:
        self.scripts.append(script)
        if callback is not None:
            callback(self.script_result)

    def deleteAllCookies(self) -> None:
        self.cookies_deleted = True

    def deleteLater(self) -> None:
        self.deleted = True


class _FakeSettings:
    class WebAttribute:
        JavaScriptEnabled = 0
        LocalStorageEnabled = 1

    def setAttribute(self, attribute: int, enabled: bool) -> None:
        pass


class FakeLoadingInfo:
    class LoadStatus:
        Succeeded = 0

    status = LoadStatus.Succeeded


def make_session(tmp_path: Path) -> OpenCodeWebSession:
    session = OpenCodeWebSession(tmp_path)
    session.view = FakeWebView()  # type: ignore[assignment]
    session.set_workspace_url(WORKSPACE_URL)
    return session


def test_workspace_id_from_url() -> None:
    assert workspace_id_from_url(WORKSPACE_URL) == "wrk_01KYVH7EJDHAAE4TZ51J3TX5CS"
    assert workspace_id_from_url("https://opencode.ai/zen") is None


def test_fetch_script_embeds_workspace_id_and_server_hash() -> None:
    script = opencode_usage_fetch_script(WORKSPACE_URL, 7)
    assert "wrk_01KYVH7EJDHAAE4TZ51J3TX5CS" in script
    assert SERVER_FN_HASH in script
    assert "X-Server-Id" in script
    assert "X-Server-Instance" in script
    assert "X-Server-Id" in script and "server-fn:1" in script
    assert "subscription" in script
    assert "rollingUsage" in script
    assert BRIDGE_PAYLOAD_KEY in script
    assert "AACC_OPENCODE_QUOTA:" in script
    assert opencode_usage_fetch_script("https://opencode.ai/zen", 1) == ""


def test_fetch_script_uses_json_content_type() -> None:
    script = opencode_usage_fetch_script(WORKSPACE_URL, 1)
    assert "Content-Type" in script and "application/json" in script


def test_session_refresh_runs_fetch_script(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    login_states: list[bool] = []
    quotas: list[object] = []
    errors: list[str] = []
    session.login_state_changed.connect(login_states.append)
    session.quota_received.connect(quotas.append)
    session.error_occurred.connect(errors.append)

    session.refresh()
    assert session.view.url().toString() == WORKSPACE_URL
    session._on_loading_changed(FakeLoadingInfo())
    assert session.view.scripts
    assert "_server" in session.view.scripts[-1]


def test_session_bridge_delivers_quota_payload(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    quotas: list[object] = []
    session.quota_received.connect(quotas.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    payload = {
        "kind": "quota",
        "generation": generation,
        "raw": {
            "subscription": {
                "rollingUsage": {"usagePercent": 0, "resetInSec": 17760},
                "weeklyUsage": {"usagePercent": 42, "resetInSec": 226800},
                "monthlyUsage": {"usagePercent": 100, "resetInSec": 2674800},
            }
        },
    }
    session.view.script_result = json.dumps(payload)
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
    assert len(quotas) == 1
    assert quotas[0]["subscription"]["rollingUsage"]["usagePercent"] == 0


def test_session_bridge_unauthorized_emits_login_state(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    login_states: list[bool] = []
    errors: list[str] = []
    session.login_state_changed.connect(login_states.append)
    session.error_occurred.connect(errors.append)
    session.refresh()
    generation = session._active_refresh_generation
    assert generation is not None
    session.view.script_result = json.dumps(
        {"kind": "unauthorized", "generation": generation, "message": "UNAUTHORIZED:401"}
    )
    session._on_title_changed(f"{BRIDGE_PREFIX}{generation}:ready:result")
    assert login_states == [False]
    assert errors == ["unauthorized"]


def test_session_bridge_stale_generation_ignored(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.refresh()
    session._on_title_changed(f"{BRIDGE_PREFIX}9999:ready:result")
    assert errors == []


def test_session_refresh_timeout_emits_error(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    errors: list[str] = []
    session.error_occurred.connect(errors.append)
    session.refresh()
    session._refresh_watchdog.timeout.emit()
    assert errors == ["refresh_timeout"]


def test_session_logout_clears_cookies(qapp, tmp_path: Path) -> None:
    del qapp
    session = make_session(tmp_path)
    session.view._url = QUrl(WORKSPACE_URL)
    assert session.logout() is True
    session._on_loading_changed(FakeLoadingInfo())
    assert session.view.cookies_deleted is True
    assert "localStorage.clear" in session.view.scripts[-1]
    session.close()
    assert session.view.deleted is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_opencode_web_session.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Write minimal implementation**

```python
# src/aacc/opencode_web_session.py
"""AACC-owned opencode.ai session using the operating system's native web view.

The workspace page renders Go-plan usage through the same-origin ``/_server``
RPC ``subscription.get``. Refreshes run a fetch script inside the page so the
session cookie authenticates the request; results arrive through the
title-bridge used by the Kimi session.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebView import QWebView, QWebViewLoadingInfo
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QVBoxLayout, QWidget

from aacc.file_security import FileProtectionError, protect_directory
from aacc.i18n import ZH_CN, LanguageManager

BRIDGE_PREFIX = "AACC_OPENCODE_QUOTA:"
BRIDGE_PAYLOAD_KEY = "__AACC_OPENCODE_QUOTA_PAYLOAD__"
SERVER_FN_HASH = "7abeebee372f304e050aaaf92be863f4a86490e382f8c79db68fd94040d691b4"
REFRESH_TIMEOUT_MS = 60_000
LOGOUT_CLEANUP_TIMEOUT_MS = 10_000
_workspace_id_pattern = re.compile(r"/workspace/([A-Za-z0-9_-]+)")
_logger = logging.getLogger("aacc.opencode_web_session")


def opencode_webview_user_data_path(config_dir: Path) -> Path:
    """Return AACC's writable opencode session directory."""

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise FileProtectionError("LOCALAPPDATA is unavailable")
        return Path(local_app_data) / "AACC" / "opencode-web-session"
    return config_dir / "opencode-web-session"


def workspace_id_from_url(url: str) -> str | None:
    match = _workspace_id_pattern.search(url)
    return match.group(1) if match else None


def opencode_usage_fetch_script(url: str, generation: int) -> str:
    """Return the same-origin usage request used by the native web view."""

    workspace_id = workspace_id_from_url(url)
    if workspace_id is None:
        return ""
    body = json.dumps(
        {
            "t": {"t": 15, "l": 1, "c": "Array", "a": [{"t": 2, "s": workspace_id}]},
            "f": 31,
            "m": [],
        },
        separators=(",", ":"),
    )
    return f"""
(() => {{
  const prefix = {json.dumps(BRIDGE_PREFIX)};
  const payloadKey = {json.dumps(BRIDGE_PAYLOAD_KEY)};
  const generation = {generation};
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 15000);
  const emit = (payload) => {{
    window[payloadKey] = JSON.stringify(payload);
    document.title = prefix + generation + ':ready:' + Date.now() + ':' + Math.random();
  }};
  const findSubscription = (node, depth) => {{
    if (!node || typeof node !== 'object' || depth > 6) return null;
    if (node.rollingUsage && node.weeklyUsage && node.monthlyUsage) return node;
    for (const key in node) {{
      if (Object.prototype.hasOwnProperty.call(node, key)) {{
        const found = findSubscription(node[key], depth + 1);
        if (found) return found;
      }}
    }}
    return null;
  }};
  const parse = (text) => {{
    const trimmed = String(text || '').trim();
    if (!trimmed) return null;
    if (trimmed.charAt(0) === '{{') {{
      try {{ return JSON.parse(trimmed); }} catch (_) {{ return null; }}
    }}
    const equals = trimmed.indexOf('=');
    const expression = equals === -1 ? trimmed : trimmed.slice(equals + 1);
    try {{ return eval(expression); }} catch (_) {{ return null; }}
  }};
  fetch('/_server', {{
    method: 'POST',
    headers: {{
      'Content-Type': 'application/json',
      'X-Server-Id': {json.dumps(SERVER_FN_HASH)},
      'X-Server-Instance': 'server-fn:1'
    }},
    credentials: 'include',
    signal: controller.signal,
    body: {json.dumps(body)}
  }}).then(async (response) => {{
    if (response.status === 401 || response.status === 403) {{
      throw new Error('UNAUTHORIZED:' + response.status);
    }}
    if (!response.ok) {{
      throw new Error('HTTP:' + response.status);
    }}
    const subscription = findSubscription(parse(await response.text()), 0);
    if (!subscription) {{
      throw new Error('PARSE_FAILED');
    }}
    emit({{kind: 'quota', generation, raw: {{subscription}}}});
  }}).catch((error) => {{
    controller.abort();
    const message = String(error && error.message || error);
    emit({{
      kind: message.startsWith('UNAUTHORIZED:') ? 'unauthorized' : 'error',
      generation,
      message: message.slice(0, 120)
    }});
  }}).finally(() => clearTimeout(deadline));
}})();
"""


class OpenCodeWebSession(QObject):
    """Keep opencode.ai cookies in the platform web view; never handle the password."""

    login_state_changed = Signal(bool)
    quota_received = Signal(object)
    error_occurred = Signal(str)

    def __init__(
        self,
        config_dir: Path,
        parent: QObject | None = None,
        *,
        language_manager: LanguageManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage_path = opencode_webview_user_data_path(config_dir)
        protect_directory(self.storage_path)
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self.workspace_url = ""
        self.view = QWebView()
        self.view.settings().setAttribute(self.view.settings().WebAttribute.JavaScriptEnabled, True)
        self.view.settings().setAttribute(
            self.view.settings().WebAttribute.LocalStorageEnabled, True
        )
        self.view.loadingChanged.connect(self._on_loading_changed)
        self.view.titleChanged.connect(self._on_title_changed)
        self._refreshing = False
        self._refresh_generation = 0
        self._active_refresh_generation: int | None = None
        self._refresh_watchdog = QTimer(self)
        self._refresh_watchdog.setSingleShot(True)
        self._refresh_watchdog.timeout.connect(self._refresh_watchdog_timeout)
        self._logout_after_load = False
        self._logout_cleanup_watchdog = QTimer(self)
        self._logout_cleanup_watchdog.setSingleShot(True)
        self._logout_cleanup_watchdog.timeout.connect(self._logout_cleanup_watchdog_timeout)
        self._login_dialog: QDialog | None = None
        self._login_container: QWidget | None = None
        self._login_explanation_label: QLabel | None = None
        self._login_dialog_open = False
        self._login_status_key = "opencode.web_starting"

    def set_workspace_url(self, url: str) -> None:
        self.workspace_url = url.strip()

    def _is_opencode_origin(self) -> bool:
        if not self.workspace_url:
            return False
        expected = QUrl(self.workspace_url).host()
        return bool(expected) and self.view.url().host() == expected

    def refresh(self) -> None:
        if not self.workspace_url:
            return
        self._refreshing = True
        self._start_refresh_generation()
        if (
            self._login_dialog_open
            or self.view.url().isEmpty()
            or not self._is_opencode_origin()
        ):
            self._load_workspace_url()
            return
        self._run_fetch_script()

    def open_login(self, parent: QWidget | None = None) -> None:
        if not self.workspace_url:
            if parent is not None:
                QMessageBox.information(
                    parent,
                    "AACC",
                    self.language_manager.text("opencode.web_need_config"),
                )
            return
        if self._login_dialog is None:
            dialog = QDialog(parent)
            dialog.resize(960, 720)
            layout = QVBoxLayout(dialog)
            explanation = QLabel(self.language_manager.text("opencode.web_starting"))
            explanation.setWordWrap(True)
            layout.addWidget(explanation)
            container = QWidget.createWindowContainer(self.view, dialog)
            layout.addWidget(container, 1)
            self._login_dialog = dialog
            self._login_container = container
            self._login_explanation_label = explanation
        self._login_explanation_label.setText(
            self.language_manager.text("opencode.web_starting")
        )
        self._login_dialog_open = True
        self._login_dialog.show()
        self._login_dialog.raise_()
        self._login_dialog.activateWindow()
        self._start_refresh_generation()
        self._load_workspace_url()

    def logout(self) -> bool:
        if not self.workspace_url:
            return True
        self._logout_after_load = True
        self._logout_cleanup_watchdog.start(LOGOUT_CLEANUP_TIMEOUT_MS)
        self.view.load(QUrl(self.workspace_url))
        self.login_state_changed.emit(False)
        return True

    def close(self) -> None:
        self._refreshing = False
        self._refresh_watchdog.stop()
        self._logout_cleanup_watchdog.stop()
        self._login_dialog_open = False
        if self._login_dialog is not None:
            self._login_dialog.close()
            self._login_dialog.deleteLater()
            self._login_dialog = None
        self._login_container = None
        self._login_explanation_label = None
        self.view.deleteLater()

    def retranslate_ui(self) -> None:
        if self._login_explanation_label is not None:
            self._login_explanation_label.setText(
                self.language_manager.text(self._login_status_key)
            )

    def _load_workspace_url(self) -> None:
        if not self.workspace_url:
            return
        self.view.load(QUrl(self.workspace_url))

    def _run_fetch_script(self) -> None:
        script = opencode_usage_fetch_script(self.workspace_url, self._refresh_generation)
        if not script:
            self._finish_refresh_with_error("refresh_failed")
            return
        self._start_refresh_watchdog()
        self.view.runJavaScript(script, lambda _result: None)

    def _start_refresh_generation(self) -> None:
        self._refresh_generation += 1
        self._active_refresh_generation = self._refresh_generation

    def _start_refresh_watchdog(self) -> None:
        self._refresh_watchdog.start(REFRESH_TIMEOUT_MS)

    def _refresh_watchdog_timeout(self) -> None:
        self._finish_refresh_with_error("refresh_timeout")

    def _finish_refresh_with_error(self, category: str) -> None:
        self._refreshing = False
        self._refresh_watchdog.stop()
        self.error_occurred.emit(category)

    def _on_loading_changed(self, info: QWebViewLoadingInfo) -> None:
        if info.status != QWebViewLoadingInfo.LoadStatus.Succeeded:
            return
        if self._logout_after_load:
            self._logout_after_load = False
            self._logout_cleanup_watchdog.stop()
            self._run_logout_cleanup()
            return
        if not self._is_opencode_origin():
            return
        self._run_fetch_script()

    def _on_title_changed(self, title: str) -> None:
        if not title.startswith(BRIDGE_PREFIX):
            return
        try:
            generation = int(title[len(BRIDGE_PREFIX) :].split(":", 1)[0])
        except ValueError:
            return
        if generation != self._active_refresh_generation:
            return

        def dispatch(payload_text: object) -> None:
            self._handle_bridge(payload_text)

        self.view.runJavaScript(f"window[{json.dumps(BRIDGE_PAYLOAD_KEY)}]", dispatch)

    def _handle_bridge(self, payload_text: object) -> None:
        try:
            payload = json.loads(payload_text) if isinstance(payload_text, str) else payload_text
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            self._finish_refresh_with_error("refresh_failed")
            return
        kind = payload.get("kind")
        if kind == "quota":
            self._refreshing = False
            self._refresh_watchdog.stop()
            self.quota_received.emit(payload.get("raw"))
            if self._login_dialog_open:
                self._close_login_dialog()
                self.login_state_changed.emit(True)
            return
        if kind == "unauthorized":
            self._refreshing = False
            self._refresh_watchdog.stop()
            self.login_state_changed.emit(False)
            self.error_occurred.emit("unauthorized")
            return
        self._finish_refresh_with_error("refresh_failed")

    def _close_login_dialog(self) -> None:
        self._login_dialog_open = False
        if self._login_dialog is not None:
            self._login_dialog.close()

    def _run_logout_cleanup(self) -> None:
        self.view.runJavaScript(
            "try { localStorage.clear(); sessionStorage.clear(); return true; } "
            "catch (_) { return false; }",
            lambda _result: None,
        )
        self.view.deleteAllCookies()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_opencode_web_session.py -q`
Expected: PASS（11 passed）

- [ ] **Step 5: Commit**

```bash
git add src/aacc/opencode_web_session.py tests/test_opencode_web_session.py
git commit -m "feat: add opencode web session with usage fetch bridge"
```

---

## Task 5: 轮询服务 `opencode_web_quota_service.py`

**Files:**
- Create: `src/aacc/opencode_web_quota_service.py`
- Test: `tests/test_opencode_web_quota_service.py`

**Interfaces:**
- Consumes: Task 1 的 `parse_opencode_quota`/`OpenCodeQuota`；Task 3 的错误归一化；会话协议（`_WebSessionLike`，与 Kimi 同构 + `set_workspace_url`）
- Produces: `OPENCODE_WEB_QUOTA_INTERVAL_MS = 300_000`；`OpenCodeWebQuotaService(QObject)`：信号 `quota_updated(object)` / `login_state_changed(bool)` / `error_occurred(str)`；方法 `set_workspace_url(url)` / `start()` / `stop()` / `refresh_now()` / `open_login(parent=None)` / `logout() -> bool`；属性 `last_quota`、`timer`、`workspace_url`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opencode_web_quota_service.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from aacc.kimi_quota import QuotaStatus
from aacc.opencode_web_quota_service import (
    OPENCODE_WEB_QUOTA_INTERVAL_MS,
    OpenCodeWebQuotaService,
)


class FakeSession(QObject):
    login_state_changed = Signal(bool)
    quota_received = Signal(object)
    error_occurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.refreshes = 0
        self.logins = 0
        self.logouts = 0
        self.closed = 0
        self.logout_result: bool | None = None

    def refresh(self) -> None:
        self.refreshes += 1

    def open_login(self, parent=None) -> None:
        del parent
        self.logins += 1

    def logout(self) -> bool | None:
        self.logouts += 1
        return self.logout_result

    def close(self) -> None:
        self.closed += 1

    def retranslate_ui(self) -> None:
        pass

    def set_workspace_url(self, url: str) -> None:
        del url


def test_service_starts_five_minute_timer(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = OpenCodeWebQuotaService(tmp_path, session=session)
    service.set_workspace_url("https://opencode.ai/workspace/wrk_1/go")
    service.start()
    assert OPENCODE_WEB_QUOTA_INTERVAL_MS == 300_000
    assert service.timer.interval() == 300_000
    assert service.timer.isActive()
    assert session.refreshes == 1
    service.timer.timeout.emit()
    assert session.refreshes == 2
    service.stop()


def test_service_noops_without_workspace_url(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = OpenCodeWebQuotaService(tmp_path, session=session)
    service.start()
    assert session.refreshes == 0
    service.stop()


def test_service_parses_quota_and_preserves_it_on_error(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = OpenCodeWebQuotaService(
        tmp_path,
        session=session,
        now=lambda: datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
    )
    updates: list[object] = []
    errors: list[str] = []
    service.quota_updated.connect(updates.append)
    service.error_occurred.connect(errors.append)

    session.quota_received.emit(
        {
            "subscription": {
                "rollingUsage": {"usagePercent": 0, "resetInSec": 17760},
                "weeklyUsage": {"usagePercent": 42, "resetInSec": 226800},
                "monthlyUsage": {"usagePercent": 100, "resetInSec": 2674800},
            }
        }
    )
    session.error_occurred.emit("refresh_timeout")

    assert len(updates) == 1
    assert updates[0].status is QuotaStatus.OK
    assert updates[0].rolling.percentage == 0
    assert service.last_quota is updates[0]
    assert errors == ["refresh_timeout"]


def test_service_emits_parse_failed_on_unknown(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = OpenCodeWebQuotaService(tmp_path, session=session)
    errors: list[str] = []
    service.error_occurred.connect(errors.append)
    session.quota_received.emit({"unrelated": {}})
    assert errors == ["parse_failed"]


def test_service_clears_snapshot_on_unauthorized(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = OpenCodeWebQuotaService(tmp_path, session=session)
    service.last_quota = object()  # type: ignore[assignment]
    login_states: list[bool] = []
    service.login_state_changed.connect(login_states.append)
    session.login_state_changed.emit(False)
    assert service.last_quota is None
    assert login_states == [False]


def test_service_logout_clears_snapshot(qapp, tmp_path: Path) -> None:
    session = FakeSession()
    service = OpenCodeWebQuotaService(tmp_path, session=session)
    service.last_quota = object()  # type: ignore[assignment]
    service.logout()
    assert session.logouts == 1
    assert service.last_quota is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_opencode_web_quota_service.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Write minimal implementation**

```python
# src/aacc/opencode_web_quota_service.py
"""Qt-timer orchestration for the cached opencode.ai web session."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QWidget

from aacc.i18n import ZH_CN, LanguageManager
from aacc.kimi_quota import QuotaStatus
from aacc.opencode_web_error import (
    OpenCodeQuotaErrorCategory,
    normalize_opencode_quota_error_category,
)
from aacc.opencode_web_quota import OpenCodeQuota, parse_opencode_quota

OPENCODE_WEB_QUOTA_INTERVAL_MS = 300_000


class _WebSessionLike(Protocol):
    login_state_changed: Any
    quota_received: Any
    error_occurred: Any

    def refresh(self) -> None: ...
    def open_login(self, parent: QWidget | None = None) -> None: ...
    def logout(self) -> bool | None: ...
    def close(self) -> None: ...
    def retranslate_ui(self) -> None: ...
    def set_workspace_url(self, url: str) -> None: ...


def _create_native_web_session(
    config_dir: Path,
    parent: QObject,
    *,
    language_manager: LanguageManager,
) -> _WebSessionLike:
    session_type: Any = import_module("aacc.opencode_web_session").OpenCodeWebSession
    return cast(
        _WebSessionLike,
        session_type(config_dir, parent, language_manager=language_manager),
    )


class OpenCodeWebQuotaService(QObject):
    quota_updated = Signal(object)
    login_state_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(
        self,
        config_dir: Path,
        *,
        session: _WebSessionLike | None = None,
        language_manager: LanguageManager | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_dir = config_dir
        self._session: _WebSessionLike | None = session
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self._now = now
        self.last_quota: OpenCodeQuota | None = None
        self.workspace_url = ""
        self.timer = QTimer(self)
        self.timer.setInterval(OPENCODE_WEB_QUOTA_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh_now)
        if self._session is not None:
            self._connect_session(self._session)
        self._stopped = False

    def set_workspace_url(self, url: str) -> None:
        self.workspace_url = url.strip()

    def start(self) -> None:
        self._ensure_session().set_workspace_url(self.workspace_url)
        if not self.timer.isActive():
            self.timer.start()
        self.refresh_now()

    def stop(self) -> None:
        self.timer.stop()
        if self._stopped:
            return
        self._stopped = True
        if self._session is not None:
            self._session.close()

    def refresh_now(self) -> None:
        if not self.workspace_url:
            return
        self._ensure_session().refresh()

    def open_login(self, parent: QWidget | None = None) -> None:
        self._ensure_session().open_login(parent)

    def logout(self) -> bool:
        result: bool | None = True
        try:
            if self._session is not None:
                result = self._session.logout()
        finally:
            self.last_quota = None
        return result is not False

    def _on_quota_received(self, raw: object) -> None:
        quota = parse_opencode_quota(raw, now=self._now())
        self.last_quota = quota
        self.quota_updated.emit(quota)
        if quota.status is QuotaStatus.UNKNOWN:
            self.error_occurred.emit(OpenCodeQuotaErrorCategory.PARSE_FAILED.value)

    def _on_error(self, category: object) -> None:
        normalized = normalize_opencode_quota_error_category(category)
        self.error_occurred.emit(normalized.value)

    def _on_login_state_changed(self, authorized: bool) -> None:
        if not authorized:
            self.last_quota = None
        self.login_state_changed.emit(authorized)

    def _ensure_session(self) -> _WebSessionLike:
        if self._session is None:
            self._session = _create_native_web_session(
                self._config_dir, self, language_manager=self.language_manager
            )
            self._connect_session(self._session)
        return self._session

    def _connect_session(self, session: _WebSessionLike) -> None:
        session.login_state_changed.connect(self._on_login_state_changed)
        session.quota_received.connect(self._on_quota_received)
        session.error_occurred.connect(self._on_error)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_opencode_web_quota_service.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add src/aacc/opencode_web_quota_service.py tests/test_opencode_web_quota_service.py
git commit -m "feat: add opencode web quota polling service"
```

---

## Task 6: GUI 额度条 + MainWindow 接线 + app.py 装配

**Files:**
- Modify: `src/aacc/gui.py`（`OpenCodeQuotaBar` 新类；MainWindow 参数/属性/接线/处理器/showEvent；SettingsDialog 按钮；retranslate_ui）
- Modify: `src/aacc/app.py`（Runtime 字段、close 阶段、工厂、build_runtime、MainWindow 装配）
- Test: `tests/test_opencode_quota_bar.py`（新文件）、`tests/test_gui_quota_wiring.py`（追加）、`tests/test_app.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `OpenCodeQuota`/`OpenCodeUsage`；Task 3 的 `normalize_opencode_quota_error_category`/`opencode_quota_error_text`/`OpenCodeQuotaErrorCategory`；Task 5 的 `OpenCodeWebQuotaService`；gui.py 现有 helper `_add_quota_metric_row`、`_set_quota_metric`、`format_reset_countdown`、`format_quota_reset`
- Produces:
  - `OpenCodeQuotaBar(QFrame)`：`clicked` 信号；`show_unauthorized()` / `show_pending()` / `show_quota(quota, *, preserve_errors=False)` / `show_error(category)` / `retranslate_ui()`；测试辅助 `metric_row_count()` / `period_labels()` / `percent_labels()` / `reset_labels()`；属性 `rolling_label` / `rolling_bar` / `weekly_label` / `weekly_bar` / `monthly_label` / `monthly_bar`
  - MainWindow：新参数 `opencode_web_quota_service: OpenCodeWebQuotaService | None = None`；新处理器 `_on_opencode_quota_bar_clicked` / `_on_opencode_quota_updated` / `_on_opencode_login_state` / `_on_opencode_quota_error` / `open_opencode_web_login` / `opencode_logout`
  - app.py：`Runtime.opencode_web_quota_service` 字段；`_default_opencode_web_quota_service_factory(config_dir, config, language_manager=None) -> OpenCodeWebQuotaService | None`（win32 返回 None）；`build_runtime` 新参数 `opencode_web_quota_service_factory`

- [ ] **Step 1: Write the failing test（额度条）**

```python
# tests/test_opencode_quota_bar.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aacc.gui import OpenCodeQuotaBar
from aacc.i18n import EN_US, ZH_CN, LanguageManager
from aacc.kimi_quota import QuotaStatus
from aacc.opencode_web_quota import OpenCodeQuota, OpenCodeUsage


def _quota() -> OpenCodeQuota:
    now = datetime.now(UTC)
    return OpenCodeQuota(
        rolling=OpenCodeUsage(0, 17760, now + timedelta(seconds=17760)),
        weekly=OpenCodeUsage(42, 226800, now + timedelta(seconds=226800)),
        monthly=OpenCodeUsage(100, 2674800, now + timedelta(seconds=2674800)),
        status=QuotaStatus.OK,
        fetched_at=now,
    )


def test_bar_shows_three_metric_rows() -> None:
    bar = OpenCodeQuotaBar()
    assert bar.metric_row_count() == 3
    assert bar.period_labels() == ["5H", "WEEK", "MONTH"]


def test_bar_renders_quota_percentages_and_resets() -> None:
    bar = OpenCodeQuotaBar()
    bar.show_quota(_quota())
    assert bar.percent_labels() == ["0%", "42%", "100%"]
    assert bar.rolling_label.text() == "0%"
    assert bar.monthly_label.text() == "100%"
    assert all(label.text() for label in bar.reset_labels())


def test_bar_unauthorized_state() -> None:
    bar = OpenCodeQuotaBar()
    bar.show_unauthorized()
    assert "点击授权" in bar.summary_label.text()
    assert bar.percent_labels() == ["--", "--", "--"]


def test_bar_error_preserves_last_quota_as_stale(qtbot) -> None:
    bar = OpenCodeQuotaBar()
    bar.show_quota(_quota())
    bar.show_error("refresh_timeout")
    assert bar.percent_labels() == ["0%", "42%", "100%"]
    assert "点击重试" in bar.toolTip()
    assert "刷新超时" in bar.toolTip()


def test_bar_retranslate_switches_language() -> None:
    bar = OpenCodeQuotaBar(LanguageManager(ZH_CN))
    bar.show_quota(_quota())
    bar.language_manager = LanguageManager(EN_US)
    bar.retranslate_ui()
    assert bar.summary_label.text() == "OpenCode usage"


def test_bar_click_emits_signal(qtbot) -> None:
    bar = OpenCodeQuotaBar()
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(0, 0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    with qtbot.waitSignal(bar.clicked, timeout=1000):
        bar.mouseReleaseEvent(event)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_opencode_quota_bar.py -q`
Expected: FAIL（`ImportError: cannot import name 'OpenCodeQuotaBar' from 'aacc.gui'`）

- [ ] **Step 3: Write minimal implementation（gui.py）**

3a. 文件头 import 区（`from aacc.kimi_web_quota import merge_kimi_quota` 附近）追加：

```python
from aacc.opencode_web_error import (
    OpenCodeQuotaErrorCategory,
    normalize_opencode_quota_error_category,
    opencode_quota_error_text,
)
from aacc.opencode_web_quota import OpenCodeQuota, OpenCodeUsage
```

3b. 在 `class CodexQuotaBar`（L582）之前插入 `OpenCodeQuotaBar`：

```python
class OpenCodeQuotaBar(QFrame):
    """OpenCode workspace usage strip (5H / WEEK / MONTH) from web session data."""

    clicked = Signal()

    def __init__(self, language_manager: LanguageManager | None = None) -> None:
        super().__init__()
        self.language_manager = language_manager or LanguageManager(ZH_CN)
        self._has_known_quota = False
        self._last_quota_tooltip = ""
        self._last_quota: OpenCodeQuota | None = None
        self._display_state = "unauthorized"
        self._last_error: OpenCodeQuotaErrorCategory | None = None
        self.setObjectName("quotaBar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(4)
        self.dot = QLabel("●")
        self.dot.setObjectName("quotaDot")
        layout.addWidget(self.dot, 0, 0, Qt.AlignmentFlag.AlignTop)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("quotaSummary")
        self.summary_label.setFixedWidth(98)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label, 0, 1, Qt.AlignmentFlag.AlignTop)
        metric_layout = QGridLayout()
        metric_layout.setContentsMargins(0, 0, 0, 0)
        metric_layout.setHorizontalSpacing(4)
        metric_layout.setVerticalSpacing(4)
        metric_layout.setColumnMinimumWidth(0, 40)
        metric_layout.setColumnMinimumWidth(1, 36)
        metric_layout.setColumnMinimumWidth(2, 16)
        metric_layout.setColumnMinimumWidth(3, 152)
        metric_layout.setColumnStretch(2, 1)
        layout.addLayout(metric_layout, 0, 2, 1, 1)
        layout.setColumnStretch(2, 1)
        self._rolling_row = _add_quota_metric_row(metric_layout, 0, "5H")
        self._weekly_row = _add_quota_metric_row(metric_layout, 1, "WEEK")
        self._monthly_row = _add_quota_metric_row(metric_layout, 2, "MONTH")
        self._metric_rows = [self._rolling_row, self._weekly_row, self._monthly_row]
        self.rolling_label = self._rolling_row.percent_label
        self.rolling_bar = self._rolling_row.progress_bar
        self.weekly_label = self._weekly_row.percent_label
        self.weekly_bar = self._weekly_row.progress_bar
        self.monthly_label = self._monthly_row.percent_label
        self.monthly_bar = self._monthly_row.progress_bar
        self.show_unauthorized()

    def period_labels(self) -> list[str]:
        return [row.period_label.text() for row in self._metric_rows]

    def percent_labels(self) -> list[str]:
        return [row.percent_label.text() for row in self._metric_rows]

    def reset_labels(self) -> list[str]:
        return [row.reset_label.text() for row in self._metric_rows]

    def metric_row_count(self) -> int:
        return len(self._metric_rows)

    def show_unauthorized(self) -> None:
        self._display_state = "unauthorized"
        self._last_quota = None
        self._last_error = None
        self._has_known_quota = False
        self._last_quota_tooltip = ""
        self.dot.setStyleSheet("color: #e06c75;")
        self.summary_label.setText(
            "OpenCode 用量\n点击授权"
            if self.language_manager.language == ZH_CN
            else "OpenCode usage\nAuthorize"
        )
        for row in self._metric_rows:
            _set_quota_metric(row, None, None, self.language_manager)
        self.setToolTip(
            "点击登录 opencode.ai 工作区，同步 Go 套餐用量"
            if self.language_manager.language == ZH_CN
            else "Sign in to the opencode.ai workspace to sync Go plan usage"
        )

    def show_pending(self) -> None:
        self._display_state = "pending"
        self._last_error = None
        self.dot.setStyleSheet("color: #e5c07b;")
        self.summary_label.setText(
            f"{self.language_manager.text('opencode.quota')}\n"
            f"{self.language_manager.text('quota.authorizing')}"
        )
        self.setToolTip(self.language_manager.text("quota.authorizing"))

    def show_quota(
        self,
        quota: OpenCodeQuota,
        *,
        preserve_errors: bool = False,
    ) -> None:
        self._last_quota = quota
        if not preserve_errors:
            self._last_error = None
        if self._last_error is not None:
            self._display_state = "error"
            self._render_error()
            return
        self._display_state = "quota"
        self._render_quota(quota)

    def show_error(self, category: object) -> None:
        self._display_state = "error"
        self._last_error = normalize_opencode_quota_error_category(category)
        self._render_error()

    def _render_quota(self, quota: OpenCodeQuota) -> None:
        self._has_known_quota = quota.status is not QuotaStatus.UNKNOWN
        if quota.status is QuotaStatus.UNKNOWN:
            self.dot.setStyleSheet("color: #8997aa;")
            self.summary_label.setText(
                "OpenCode 用量\n数据不可用"
                if self.language_manager.language == ZH_CN
                else "OpenCode usage\nUsage unavailable"
            )
        elif quota.status is QuotaStatus.PARTIAL:
            self.dot.setStyleSheet("color: #e5c07b;")
            self.summary_label.setText(
                "OpenCode 用量\n部分数据"
                if self.language_manager.language == ZH_CN
                else "OpenCode usage\nPartial usage data"
            )
        elif quota.status is QuotaStatus.STALE:
            self.dot.setStyleSheet("color: #8997aa;")
            self.summary_label.setText(
                f"{self.language_manager.text('opencode.quota')}\n"
                f"{self.language_manager.text('quota.stale')}"
            )
        else:
            self.dot.setStyleSheet("color: #98c379;")
            self.summary_label.setText(self.language_manager.text("opencode.quota"))
        self._show_detail(self._rolling_row, quota.rolling)
        self._show_detail(self._weekly_row, quota.weekly)
        self._show_detail(self._monthly_row, quota.monthly)
        tooltip_lines = [
            self._detail_tooltip("5H", quota.rolling),
            self._detail_tooltip("WEEK", quota.weekly),
            self._detail_tooltip("MONTH", quota.monthly),
        ]
        if quota.fetched_at is not None:
            tooltip_lines.append(
                self.language_manager.text(
                    "quota.last_update",
                    updated=quota.fetched_at.astimezone().strftime("%H:%M:%S"),
                )
            )
        tooltip_lines.append(self.language_manager.text("quota.refresh"))
        self._last_quota_tooltip = "\n".join(tooltip_lines)
        self.setToolTip(self._last_quota_tooltip)

    def _show_detail(self, row: _QuotaMetricRow, usage: OpenCodeUsage | None) -> None:
        if usage is None:
            _set_quota_metric(row, None, None, self.language_manager)
            return
        _set_quota_metric(row, usage.percentage, usage.reset_at, self.language_manager)

    def _detail_tooltip(self, name: str, usage: OpenCodeUsage | None) -> str:
        if usage is None:
            unknown = "未知" if self.language_manager.language == ZH_CN else "Unknown"
            return f"{name}: {unknown}"
        reset = (
            format_reset_countdown(usage.reset_at)
            if self.language_manager.language == ZH_CN
            else format_quota_reset(usage.reset_at, self.language_manager)
        )
        return f"{name}: {usage.percentage}% ({reset})"

    def _render_error(self) -> None:
        if self._last_quota is not None:
            self._render_quota(self._last_quota)
        self.dot.setStyleSheet("color: #8997aa;")
        if self._has_known_quota:
            state_text = (
                "数据过期"
                if self.language_manager.language == ZH_CN
                else self.language_manager.text("quota.stale")
            )
        else:
            state_text = (
                "数据不可用"
                if self.language_manager.language == ZH_CN
                else self.language_manager.text("quota.unavailable")
            )
        self.summary_label.setText(
            f"{self.language_manager.text('opencode.quota')}\n{state_text}"
        )
        previous = f"{self._last_quota_tooltip}\n" if self._last_quota_tooltip else ""
        retry = "点击重试" if self.language_manager.language == ZH_CN else "Click to retry"
        error_text = opencode_quota_error_text(self._last_error, self.language_manager)
        self.setToolTip(f"{previous}{error_text}\n{retry}")

    def retranslate_ui(self) -> None:
        if self._display_state == "pending":
            self.show_pending()
        elif self._display_state == "quota" and self._last_quota is not None:
            self._render_quota(self._last_quota)
        elif self._display_state == "error":
            self._render_error()
        else:
            self.show_unauthorized()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)
```

3c. MainWindow `__init__` 参数（`codex_quota_service` 之后 L1405）加：

```python
        opencode_web_quota_service: OpenCodeWebQuotaService | None = None,
```

属性区（`self.codex_quota_service = codex_quota_service` 附近）加：

```python
        self.opencode_web_quota_service = opencode_web_quota_service
        self._latest_opencode_quota: OpenCodeQuota | None = None
        self._opencode_authorized = False
```

`self.codex_quota_bar: CodexQuotaBar | None = None` 之后加：

```python
        self.opencode_quota_bar: OpenCodeQuotaBar | None = None
```

3d. 额度条创建区（kimi web 接线块 L1654-1658 之后）加：

```python
        if self.opencode_web_quota_service is not None:
            self.opencode_quota_bar = OpenCodeQuotaBar(self.language_manager)
            self.opencode_quota_bar.clicked.connect(self._on_opencode_quota_bar_clicked)
            layout.addWidget(self.opencode_quota_bar)
            self.opencode_web_quota_service.quota_updated.connect(self._on_opencode_quota_updated)
            self.opencode_web_quota_service.login_state_changed.connect(
                self._on_opencode_login_state
            )
            self.opencode_web_quota_service.error_occurred.connect(self._on_opencode_quota_error)
```

3e. 处理器（`_on_kimi_web_quota_error` L2175-2177 之后）加：

```python
    def _on_opencode_quota_bar_clicked(self) -> None:
        if self.opencode_web_quota_service is None:
            return
        if not self._opencode_authorized:
            self.opencode_web_quota_service.open_login(self)
            return
        self.opencode_web_quota_service.refresh_now()

    def _on_opencode_quota_updated(self, quota: object) -> None:
        if not isinstance(quota, OpenCodeQuota):
            return
        self._latest_opencode_quota = quota
        self._opencode_authorized = True
        if self.opencode_quota_bar is not None:
            self.opencode_quota_bar.show_quota(quota)

    def _on_opencode_login_state(self, authorized: bool) -> None:
        self._opencode_authorized = authorized
        if authorized:
            return
        self._latest_opencode_quota = None
        if self.opencode_quota_bar is not None:
            self.opencode_quota_bar.show_unauthorized()

    def _on_opencode_quota_error(self, category: str) -> None:
        if self.opencode_quota_bar is not None:
            self.opencode_quota_bar.show_error(category)

    def open_opencode_web_login(self) -> None:
        if self.opencode_web_quota_service is not None:
            self.opencode_web_quota_service.open_login(self)

    def opencode_logout(self) -> None:
        if self.opencode_web_quota_service is None:
            return
        self.opencode_web_quota_service.logout()
        self._latest_opencode_quota = None
        self._opencode_authorized = False
        if self.opencode_quota_bar is not None:
            self.opencode_quota_bar.show_unauthorized()
```

3f. `_request_quota_refresh_on_restore`（L2757-2764）改为：

```python
    def _request_quota_refresh_on_restore(self) -> None:
        if (
            self.kimi_web_quota_service is None
            and self.opencode_web_quota_service is None
        ):
            return
        now = time.monotonic()
        if now - self._last_restore_quota_refresh < RESTORE_QUOTA_REFRESH_INTERVAL_SECONDS:
            return
        self._last_restore_quota_refresh = now
        if self.kimi_web_quota_service is not None:
            self.kimi_web_quota_service.refresh_now()
        if self.opencode_web_quota_service is not None:
            self.opencode_web_quota_service.refresh_now()
```

3g. `retranslate_ui`（`self.codex_quota_bar` 块 L1800-1801 之后）加：

```python
        if self.opencode_quota_bar is not None:
            self.opencode_quota_bar.retranslate_ui()
```

3h. `SettingsDialog`（kimi logout 按钮 L1192-1194 之后）加：

```python
        if window.opencode_web_quota_service is not None:
            opencode_login = QPushButton(language.text("settings.opencode_web_login"))
            opencode_login.clicked.connect(window.open_opencode_web_login)
            layout.addWidget(opencode_login)
            opencode_logout = QPushButton(language.text("settings.opencode_logout"))
            opencode_logout.clicked.connect(window.opencode_logout)
            layout.addWidget(opencode_logout)
```

- [ ] **Step 4: Write the failing test（MainWindow 接线）**

在 `tests/test_gui_quota_wiring.py` 末尾追加：

```python
class FakeOpenCodeWebQuotaService(QObject):
    quota_updated = Signal(object)
    login_state_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.refreshes = 0
        self.logins = 0
        self.logouts = 0

    def refresh_now(self) -> None:
        self.refreshes += 1

    def open_login(self, parent=None) -> None:
        del parent
        self.logins += 1

    def logout(self) -> None:
        self.logouts += 1


def test_opencode_quota_bar_wired_and_signals_flow(qtbot, tmp_path):
    from aacc.opencode_web_quota import OpenCodeQuota, OpenCodeUsage

    service = FakeOpenCodeWebQuotaService()
    window = make_window(
        qtbot,
        tmp_path,
        with_service=False,
        opencode_web_quota_service=service,
    )
    assert window.opencode_quota_bar is not None
    assert window.opencode_quota_bar.metric_row_count() == 3

    window.opencode_quota_bar.clicked.emit()
    assert service.logins == 1

    now = datetime.now(UTC)
    quota = OpenCodeQuota(
        rolling=OpenCodeUsage(10, 3600, now),
        weekly=OpenCodeUsage(20, 3600, now),
        monthly=OpenCodeUsage(30, 3600, now),
        status=__import__("aacc.kimi_quota", fromlist=["QuotaStatus"]).QuotaStatus.OK,
        fetched_at=now,
    )
    service.quota_updated.emit(quota)
    assert window.opencode_quota_bar.rolling_label.text() == "10%"

    window.opencode_quota_bar.clicked.emit()
    assert service.refreshes == 1

    service.error_occurred.emit("refresh_timeout")
    assert "点击重试" in window.opencode_quota_bar.toolTip()

    window.opencode_logout()
    assert service.logouts == 1
    assert window.opencode_quota_bar.rolling_label.text() == "--"
```

`make_window` 加参数 `opencode_web_quota_service=None` 并透传给 `MainWindow(...)`。

- [ ] **Step 5: Write the failing test（app.py 装配）**

在 `tests/test_app.py` 追加：

```python
def test_default_opencode_factory_skips_windows(monkeypatch) -> None:
    import aacc.app as app_module
    from aacc.config import default_config

    monkeypatch.setattr(app_module.sys, "platform", "win32")
    service = app_module._default_opencode_web_quota_service_factory(
        Path("."), default_config()
    )
    assert service is None


def test_default_opencode_factory_uses_configured_url(monkeypatch) -> None:
    import aacc.app as app_module
    from aacc.config import default_config

    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    config = default_config()
    config.opencode_workspace_url = (
        "https://opencode.ai/workspace/wrk_01KYVH7EJDHAAE4TZ51J3TX5CS/go"
    )
    service = app_module._default_opencode_web_quota_service_factory(
        Path("."), config
    )
    assert service is not None
    assert service.workspace_url == config.opencode_workspace_url
    service.stop()
```

（若 `tests/test_app.py` 已有 import `Path`/`monkeypatch` 则复用；`_default_opencode_web_quota_service_factory` 仅做 url 注入 + win32 门控，不启动会话，测试安全。）

- [ ] **Step 6: Write minimal implementation（app.py）**

6a. import（L45 `from aacc.kimi_web_quota_service import KimiWebQuotaService` 之后）加：

```python
from aacc.opencode_web_quota_service import OpenCodeWebQuotaService
```

6b. Runtime dataclass（L69 后）加字段，close() 元组（L88 后）加阶段：

```python
    opencode_web_quota_service: OpenCodeWebQuotaService | None = None
```

```python
            (
                "opencode-web-quota",
                self.opencode_web_quota_service.stop
                if self.opencode_web_quota_service is not None
                else lambda: None,
            ),
```

6c. `_default_kimi_web_quota_service_factory` 之后加工厂：

```python
def _default_opencode_web_quota_service_factory(
    config_dir: Path,
    config: AppConfig,
    language_manager: LanguageManager | None = None,
) -> OpenCodeWebQuotaService | None:
    if sys.platform == "win32":
        return None
    service = OpenCodeWebQuotaService(
        config_dir,
        language_manager=language_manager,
    )
    service.set_workspace_url(config.opencode_workspace_url)
    return service
```

6d. `build_runtime`：参数（`codex_quota_service_factory` 后）加

```python
    opencode_web_quota_service_factory: (
        Callable[[Path], OpenCodeWebQuotaService | None] | None
    ) = None,
```

工厂默认值 + 调用（`kimi_web_quota_factory` 块之后）：

```python
    opencode_web_quota_factory = opencode_web_quota_service_factory or (
        lambda config_dir: _default_opencode_web_quota_service_factory(
            config_dir,
            config,
            language_manager,
        )
    )
    ...
    opencode_web_quota_service = opencode_web_quota_factory(config_path.parent)
```

`Runtime(...)` 加 `opencode_web_quota_service=opencode_web_quota_service,`。

6e. `_run_application` 的 `MainWindow(...)`（L413 `kimi_web_quota_service=...` 之后）加：

```python
        opencode_web_quota_service=runtime.opencode_web_quota_service,
```

- [ ] **Step 7: Run all new tests**

Run: `.venv/bin/python -m pytest tests/test_opencode_quota_bar.py tests/test_gui_quota_wiring.py tests/test_app.py -q`
Expected: PASS

- [ ] **Step 8: Full gate + Commit**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/mypy src/aacc`
Expected: 全绿

```bash
git add src/aacc/gui.py src/aacc/app.py tests/test_opencode_quota_bar.py tests/test_gui_quota_wiring.py tests/test_app.py
git commit -m "feat: show opencode go plan usage in quota bar"
```

---

## Task 7: 手动验收（无法自动化）

**Files:**
- Modify: `docs/KNOWN_LIMITATIONS.md`（中英条目）
- Modify: `docs/release-notes-*.md` 或 CHANGELOG（随下个版本发布时处理，不在本任务）

- [ ] **Step 1: 配置**

本机 `config.yaml`（`~/.aacc/config.yaml` 或 `--config` 指定路径）追加：

```yaml
opencode_workspace_url: https://opencode.ai/workspace/wrk_01KYVH7EJDHAAE4TZ51J3TX5CS/go
```

- [ ] **Step 2: 首次登录**

1. 运行 AACC → 面板出现「OpenCode 用量/点击授权」条。
2. 点击 → QtWebView 登录窗口打开（opencode.ai OpenAuth）→ 选 GitHub 或 Google 登录。
3. 重定向回工作区页 → 三行 5H/WEEK/MONTH 显示真实百分比与重置倒计时（对照
   https://opencode.ai/workspace/wrk_01KYVH7EJDHAAE4TZ51J3TX5CS/go 页面的
   滚动用量/每周用量/每月用量数值一致）→ 登录窗口自动关闭。

- [ ] **Step 3: 会话持久化**

重启 AACC → 无需重新登录（cookie 在 `{config_dir}/opencode-web-session/`）→
额度条自动出数（5 分钟内，点击立即刷新）。

- [ ] **Step 4: 错误路径**

1. 设置 → 退出 OpenCode → 条回「点击授权」。
2. 设置 → 登录 OpenCode → 重新授权恢复。
3. 断网点刷新 → 条保留旧数据 + tooltip「刷新超时/点击重试」；恢复网络 → 点击重试恢复。

- [ ] **Step 5: 更新 KNOWN_LIMITATIONS（双语）**

追加条目：OpenCode 用量依赖 opencode.ai 工作区网页结构（`/_server` RPC 与
seroval 载荷），网页改版可能使取数失效，需随版本跟进；仅 macOS 真机验证，
Windows（Edge CDP）为后续迭代，不宣称消费级 Windows 真机验证。

- [ ] **Step 6: Commit**

```bash
git add docs/KNOWN_LIMITATIONS.md
git commit -m "docs: note opencode usage web scraping limits"
```

---

## Self-Review

**1. Spec coverage（对照设计文档逐节）：**
- 架构/数据流 → Task 1（解析）、Task 4（会话+桥接）、Task 5（服务）、Task 6（GUI/app 装配）。✓
- 登录与会话（会话目录、登录窗口自动关闭、unauthorized 语义）→ Task 4。✓
- 错误处理与刷新（OK/PARTIAL/UNKNOWN/STALE、四类错误、300s + showEvent 60s 节流）→ Task 1/3/5/6（3f）。✓
- 测试策略（解析器/会话/额度条/配置/服务五类测试文件）→ Task 1-6。✓
- 实施顺序 5 步 → Task 1-7。✓
- 设计修正：取数方式改「页面内直接 fetch」（QtWebView 无 DocumentCreation），设计文档已同步。✓

**2. Placeholder scan：** 无 TBD/TODO；每个代码步骤含完整代码与精确路径。

**3. Type consistency：**
- `OpenCodeQuota`（rolling/weekly/monthly + status + fetched_at）在 Task 1 定义，Task 5/6 一致使用。✓
- `OpenCodeUsage.percentage/reset_seconds/reset_at` 在 Task 1 定义，Task 6 `_show_detail` 用 `usage.reset_at` 喂 `_set_quota_metric`（与 QuotaBar 同型）。✓
- 信号名：会话 `quota_received(object)`（Task 4）↔ 服务 `_on_quota_received(raw)`（Task 5）↔ 服务 `quota_updated(object)` ↔ MainWindow `_on_opencode_quota_updated`。✓
- `normalize_opencode_quota_error_category`/`opencode_quota_error_text`/`OpenCodeQuotaErrorCategory` 在 Task 3 定义，Task 5/6 引用一致。✓
- i18n 键 `opencode.web_*`/`settings.opencode_*`/`opencode.quota` 在 Task 3 定义，Task 4（`opencode.web_need_config`/`opencode.web_starting`/`opencode.web_title`）、Task 6（`settings.opencode_web_login`/`settings.opencode_logout`/`opencode.quota`）引用一致。✓
- `OpenCodeWebQuotaService` 方法集在 Task 5 定义，Task 6 调用 `open_login(self)`/`refresh_now()`/`logout()` 一致；`FakeOpenCodeWebQuotaService`（Task 6 测试）方法集与之一致。✓
- `set_workspace_url` 在 Task 4（会话）与 Task 5（服务）都有，Task 5 `start()` 透传 URL，Task 6 app.py 工厂注入 URL。✓
