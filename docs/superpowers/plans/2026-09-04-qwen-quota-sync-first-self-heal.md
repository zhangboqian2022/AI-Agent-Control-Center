# Qwen 百炼额度：同步优先登录与受保护自愈 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Qwen 额度「点击授权先自动同步日常 Chrome 会话、同步不到才弹居中登录窗」，刷新失效时按会话来源受保护地自愈（绝不误覆盖新登录缓存），会话确死时自动弹登录窗，并修掉登录窗 14 秒误关、窗口不居中、Chrome 退出不干净三类实锤缺陷。

**Architecture:** 全部改动集中在 Qwen Chrome 会话栈：`kimi_web_login_state.py`（状态存储加来源/时间戳）→ `qwen_chrome_cdp.py`(操作层的来源感知自愈、急同步、健壮取消、居中+可见反检测、退出校验) → `qwen_chrome_session.py`（同步优先编排、自动弹窗信号）→ `qwen_web_quota_service.py`/`gui.py`/`i18n.py`（信号转发与状态文案）。探针实验（spec §3.3）是代码合入后的真机验证，见文末「真机探针」节。

**Tech Stack:** Python 3.12 / PySide6 / pytest / ruff / mypy；macOS Chrome CDP（实现全部可注入 fake，跨平台单测）。

**Spec:** `docs/superpowers/specs/2026-09-04-qwen-quota-sync-first-self-heal-design.md`

## Global Constraints

- 环境准备与 CI 一致：`uv sync --locked --extra dev`；测试命令一律
  `.venv/bin/python -m pytest -q`（`tests/conftest.py` 已设 `QT_QPA_PLATFORM=offscreen`）。
- 改动后必须全绿：`.venv/bin/ruff check src tests`、`.venv/bin/ruff format --check src tests`、
  `.venv/bin/mypy src/aacc`、全量 pytest；CI diff-cover 改动行覆盖率 ≥90%（新代码必须带测试）。
- 提交信息格式：`feat: ...` / `fix: ...` / `docs: ...`，英文；每个任务一个提交。
- 版本号改动顺序（`tests/test_packaging.py` 强制）：`src/aacc/__init__.py::__version__` →
  `pyproject.toml` → `uv lock` → 双语 CHANGELOG 最新段标题用 `public_version()` →
  `docs/release-notes-<__version__>.md` 存在。`__version__` 用 PEP 440（`1.4.6rc1`）。
- **测试时间造假必须相对当前时刻回拨**（`time.monotonic() - INTERVAL - 1`），
  不能写 0 或固定时间点（`monotonic()` 是开机秒数，CI 全新 VM 会红）。
- 文档中英双语成对（CHANGELOG、release-notes）。
- 不宣称消费级 Windows 真机验证；Windows Edge 路径本次不动。
- 工作分支：`fix/qwen-quota-sync-first-selfheal`（自 main 开出，Task 0 创建）。

---

### Task 0: 开分支

**Files:** 无（本计划文档已先行提交到 main）

- [ ] **Step 1: 从 main 开分支**

```bash
cd /Users/zhangboqian/Desktop/codelight
git switch -c fix/qwen-quota-sync-first-selfheal
```

---

### Task 1: 状态存储记录会话来源与成功时间

背景：`KimiWebLoginStateStore`（kimi/opencode/qwen 共用）目前只存
`reuse_native_session` 与 `logged_out_by_user`。本任务加 `session_origin`
（`daily_recopy`/`manual_login`）与 `last_success_epoch`。**不升 version
字段**：新字段按缺省宽容读取（旧文件没有字段 = 迁移），避免破坏共用此存储的
kimi/opencode（spec 里"版本升到 2"的意图由缺省迁移等价实现）。

**Files:**
- Modify: `src/aacc/kimi_web_login_state.py`
- Test: `tests/test_kimi_web_login_state.py`

**Interfaces:**
- Produces:
  - `KimiWebLoginStateStore.SESSION_ORIGIN_DAILY_RECOPY = "daily_recopy"`
  - `KimiWebLoginStateStore.SESSION_ORIGIN_MANUAL_LOGIN = "manual_login"`
  - `session_origin() -> str`（缺失/损坏 → `"manual_login"`，保守迁移）
  - `last_success_epoch() -> int | None`（缺失/损坏 → `None`）
  - `set_may_reuse(value, *, logged_out_by_user=None, session_origin=None, last_success_epoch=None)`
    （新参数缺省时保留旧值，与 `logged_out_by_user` 同款语义）

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_kimi_web_login_state.py`）

```python
def test_session_origin_defaults_to_manual_login_when_missing(tmp_path):
    store = KimiWebLoginStateStore(tmp_path)
    assert store.session_origin() == KimiWebLoginStateStore.SESSION_ORIGIN_MANUAL_LOGIN
    assert store.last_success_epoch() is None


def test_set_may_reuse_records_origin_and_success_epoch(tmp_path):
    store = KimiWebLoginStateStore(tmp_path)
    store.set_may_reuse(
        True,
        session_origin=KimiWebLoginStateStore.SESSION_ORIGIN_DAILY_RECOPY,
        last_success_epoch=1725424224,
    )
    assert store.may_reuse() is True
    assert store.session_origin() == KimiWebLoginStateStore.SESSION_ORIGIN_DAILY_RECOPY
    assert store.last_success_epoch() == 1725424224


def test_set_may_reuse_preserves_origin_when_omitted(tmp_path):
    store = KimiWebLoginStateStore(tmp_path)
    store.set_may_reuse(
        True,
        session_origin=KimiWebLoginStateStore.SESSION_ORIGIN_DAILY_RECOPY,
        last_success_epoch=1725424224,
    )
    store.set_may_reuse(False)
    assert store.session_origin() == KimiWebLoginStateStore.SESSION_ORIGIN_DAILY_RECOPY
    assert store.last_success_epoch() == 1725424224
    assert store.may_reuse() is False


def test_qwen_state_file_accepts_origin_fields(tmp_path):
    store = KimiWebLoginStateStore(tmp_path, state_file_name="qwen-web-session-state.json")
    store.set_may_reuse(
        True,
        session_origin=KimiWebLoginStateStore.SESSION_ORIGIN_MANUAL_LOGIN,
        last_success_epoch=1725424224,
    )
    fresh = KimiWebLoginStateStore(tmp_path, state_file_name="qwen-web-session-state.json")
    assert fresh.session_origin() == KimiWebLoginStateStore.SESSION_ORIGIN_MANUAL_LOGIN
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_kimi_web_login_state.py -q`
Expected: 4 个新测试 FAIL（`AttributeError: session_origin`）

- [ ] **Step 3: 实现**

`src/aacc/kimi_web_login_state.py` 类体内加常量：

```python
    SESSION_ORIGIN_DAILY_RECOPY = "daily_recopy"
    SESSION_ORIGIN_MANUAL_LOGIN = "manual_login"
    _KNOWN_SESSION_ORIGINS = frozenset({SESSION_ORIGIN_DAILY_RECOPY, SESSION_ORIGIN_MANUAL_LOGIN})
```

`logged_out_by_user` 方法后新增两个读取方法：

```python
    def session_origin(self) -> str:
        """Return how the most recent successful session was established.

        Missing or unreadable state fails closed to the manual-login origin:
        unknown provenance must take the protected (no immediate recopy)
        path, never the destructive one.
        """

        raw = self._read_state()
        if raw is None:
            return self.SESSION_ORIGIN_MANUAL_LOGIN
        origin = raw.get("session_origin")
        if origin in self._KNOWN_SESSION_ORIGINS:
            return origin
        return self.SESSION_ORIGIN_MANUAL_LOGIN

    def last_success_epoch(self) -> int | None:
        """Return the local epoch seconds of the most recent success."""

        raw = self._read_state()
        if raw is None:
            return None
        value = raw.get("last_success_epoch")
        return value if type(value) is int else None

    def _read_state(self) -> dict[str, Any] | None:
        path = self._path()
        try:
            self._reject_unsafe_path(path)
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None
```

`set_may_reuse` 签名与落盘字典改为：

```python
    def set_may_reuse(
        self,
        value: bool,
        *,
        logged_out_by_user: bool | None = None,
        session_origin: str | None = None,
        last_success_epoch: int | None = None,
    ) -> None:
        """Atomically persist a reuse decision using AACC's file protections.

        ``logged_out_by_user`` records whether the logged-out state is an
        explicit user logout; ``session_origin`` and ``last_success_epoch``
        record how and when the newest successful session was established.
        Omitted fields preserve the previously persisted values.
        """

        if not isinstance(value, bool):
            raise ValueError("Kimi web session reuse value must be boolean")
        marker = self.logged_out_by_user() if logged_out_by_user is None else logged_out_by_user
        origin = self.session_origin() if session_origin is None else session_origin
        if origin not in self._KNOWN_SESSION_ORIGINS:
            raise ValueError("unknown web session origin")
        success_epoch = self.last_success_epoch() if last_success_epoch is None else last_success_epoch
        if success_epoch is not None and type(success_epoch) is not int:
            raise ValueError("last success epoch must be an integer")
        path = self._path()
```

同一方法里 `json.dump` 的字典改为：

```python
                json.dump(
                    {
                        "version": _STATE_VERSION,
                        "reuse_native_session": value,
                        "logged_out_by_user": marker,
                        "session_origin": origin,
                        "last_success_epoch": success_epoch,
                    },
                    handle,
                    separators=(",", ":"),
                )
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_kimi_web_login_state.py -q`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/aacc/kimi_web_login_state.py tests/test_kimi_web_login_state.py
git commit -m "feat: track Qwen session origin and last success in the state store"
```

---

### Task 2: 操作层按来源自愈（手动登录先复检、绝不立即覆盖）

背景：`ManagedQwenChromeOperation.run` 现在撞横幅就立即重复制——今天 11:55
毁掉 9 分钟前新登录的根源。改为：来源=`manual_login` 先睡 60 秒复检一次，
仍死才允许重复制；来源=`daily_recopy` 保持立即重复制（现行为，缺省值，
现存测试不受影响）。

**Files:**
- Modify: `src/aacc/qwen_chrome_cdp.py`
- Test: `tests/test_qwen_chrome_cdp.py`

**Interfaces:**
- Consumes: Task 1 的起源常量字符串值（`"daily_recopy"`/`"manual_login"`；
  cdp 模块不依赖状态存储，用字符串常量）
- Produces:
  - 模块常量 `QWEN_SESSION_ORIGIN_DAILY_RECOPY = "daily_recopy"`、
    `QWEN_SESSION_ORIGIN_MANUAL_LOGIN = "manual_login"`、
    `_QWEN_MANUAL_RECHECK_DELAY_SECONDS = 60.0`
  - `ManagedQwenChromeOperation.__init__` 新增关键字参数
    `session_origin: str = QWEN_SESSION_ORIGIN_DAILY_RECOPY`、
    `recheck_delay_seconds: float = _QWEN_MANUAL_RECHECK_DELAY_SECONDS`
  - 实例属性 `self.recopy_performed: bool`（本次 run 是否调用过
    `session_recopy`；Task 7 的 session 层用它落来源）
  - 私有方法 `_recover_expired_session(cancel: Event) -> dict[str, object]`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_qwen_chrome_cdp.py`；
  复用现有 `_make_chrome_profile`、`_fake_process_factory`、`FakeProcess`、
  `_quota_page_target`、`_quota_payload`、`WORKSPACE_URL`）

```python
def _make_recovery_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    evaluations: list[object],
    session_origin: str,
    recopied: list[Path],
    recopy_raises: bool = False,
    session_recopy=True,
    recheck_delay_seconds: float = 0.0,
):
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    cursor = iter(evaluations)

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            return next(cursor)

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)

    def recopy(config_dir: Path) -> None:
        if recopy_raises:
            raise OSError("daily profile unreadable")
        recopied.append(config_dir)

    clock = iter([0.0] * 64)
    return ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=lambda _origin: [_quota_page_target()],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
        session_recopy=recopy if session_recopy else None,
        session_origin=session_origin,
        recheck_delay_seconds=recheck_delay_seconds,
    )


def test_manual_origin_banner_rechecks_before_recopy_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recopied: list[Path] = []
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[{"kind": "unauthorized"}, _quota_payload()],
        session_origin=QWEN_SESSION_ORIGIN_MANUAL_LOGIN,
        recopied=recopied,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert recopied == []  # the recheck recovered; nothing was overwritten
    assert operation.recopy_performed is False


def test_manual_origin_confirmed_dead_falls_through_to_recopy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recopied: list[Path] = []
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[
            {"kind": "unauthorized"},
            {"kind": "unauthorized"},
            _quota_payload(),
        ],
        session_origin=QWEN_SESSION_ORIGIN_MANUAL_LOGIN,
        recopied=recopied,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert recopied == [tmp_path / "config"]
    assert operation.recopy_performed is True


def test_manual_origin_without_recopy_surfaces_unauthorized_after_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[{"kind": "unauthorized"}, {"kind": "unauthorized"}],
        session_origin=QWEN_SESSION_ORIGIN_MANUAL_LOGIN,
        recopied=[],
        session_recopy=False,
    )

    with pytest.raises(QwenChromeUnauthorizedError):
        operation.run(visible=False, cancel=Event())


def test_daily_origin_banner_recopies_immediately_without_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recopied: list[Path] = []
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[{"kind": "unauthorized"}, _quota_payload()],
        session_origin=QWEN_SESSION_ORIGIN_DAILY_RECOPY,
        recopied=recopied,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert recopied == [tmp_path / "config"]
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_cdp.py -k "origin" -q`
Expected: FAIL（`TypeError: unexpected keyword argument 'session_origin'`）

- [ ] **Step 3: 实现**

`src/aacc/qwen_chrome_cdp.py` 常量区（`_QWEN_LOGIN_WINDOW_MISSING_POLLS` 附近）加：

```python
QWEN_SESSION_ORIGIN_DAILY_RECOPY = "daily_recopy"
QWEN_SESSION_ORIGIN_MANUAL_LOGIN = "manual_login"
# A manual-login session hit by one login-banner observation is rechecked
# once before any recopy may overwrite it: the 2026-09-04 incident destroyed
# a nine-minute-old fresh login on a single banner reading.
_QWEN_MANUAL_RECHECK_DELAY_SECONDS = 60.0
```

`ManagedQwenChromeOperation.__init__` 增加参数（`session_recopy` 之后）：

```python
        session_origin: str = QWEN_SESSION_ORIGIN_DAILY_RECOPY,
        recheck_delay_seconds: float = _QWEN_MANUAL_RECHECK_DELAY_SECONDS,
```

构造体尾部（`self._session_recopy = session_recopy` 之后）加：

```python
        self._session_origin = session_origin
        self._recheck_delay_seconds = max(0.0, recheck_delay_seconds)
        self.recopy_performed = False
```

用下面的实现整体替换现有 `run` 方法，并在其后新增 `_recover_expired_session`：

```python
    def run(self, *, visible: bool, cancel: Event) -> dict[str, object]:
        """Run one operation with origin-aware session recovery.

        Visible logins never recopy. Hidden refreshes that hit the rendered
        login banner recover by origin: a daily-recopy session is recopied
        immediately (idempotent), while a manual-login session is rechecked
        once first so a transient banner can never overwrite a fresh login.
        """

        if cancel.is_set():
            raise QwenChromeCancelledError
        if visible:
            return self._run_once(visible=True, cancel=cancel, fail_fast_unauthorized=False)
        fail_fast_unauthorized = self._session_recopy is not None
        try:
            return self._run_once(
                visible=False,
                cancel=cancel,
                fail_fast_unauthorized=fail_fast_unauthorized,
            )
        except QwenChromeUnauthorizedError:
            return self._recover_expired_session(cancel)

    def _recover_expired_session(self, cancel: Event) -> dict[str, object]:
        if self._session_origin == QWEN_SESSION_ORIGIN_MANUAL_LOGIN:
            _logger.warning(
                "Qwen hidden refresh saw a login banner on a manual-login session; "
                "rechecking before any recopy"
            )
            self._sleep(self._recheck_delay_seconds)
            if cancel.is_set():
                raise QwenChromeCancelledError
            try:
                return self._run_once(visible=False, cancel=cancel, fail_fast_unauthorized=False)
            except QwenChromeUnauthorizedError:
                _logger.warning("Qwen manual-login session confirmed expired")
        if self._session_recopy is None:
            raise QwenChromeUnauthorizedError
        _logger.warning(
            "Qwen hidden refresh found an expired session; "
            "recopying the daily Chrome session before retrying"
        )
        try:
            self._session_recopy(self.config_dir)
        except Exception:
            _logger.warning("Qwen daily Chrome session recopy failed", exc_info=True)
            # Surface the logout: the retry never happened, so the session
            # layer must prompt for a visible re-login.
            raise QwenChromeUnauthorizedError from None
        self.recopy_performed = True
        return self._run_once(visible=False, cancel=cancel, fail_fast_unauthorized=False)
```

- [ ] **Step 4: 运行确认通过（新测试 + 既有重复制测试一起跑）**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_cdp.py -q`
Expected: 全 PASS（既有 `test_hidden_refresh_recopies_and_retries_after_unauthorized`
等依赖缺省来源 = `daily_recopy`，行为不变）

- [ ] **Step 5: 提交**

```bash
git add src/aacc/qwen_chrome_cdp.py tests/test_qwen_chrome_cdp.py
git commit -m "fix: recheck manual-login sessions before recopy overwrites them"
```

---

### Task 3: 操作层急同步（同步优先登录的原语）

背景：同步优先登录需要「先重复制、紧接着隐藏取数」的急切模式，区别于刷新的
懒重复制。失败（重复制抛错或取数仍未登录）直接向上抛，由 session 层转可见
登录兜底。

**Files:**
- Modify: `src/aacc/qwen_chrome_cdp.py`
- Test: `tests/test_qwen_chrome_cdp.py`

**Interfaces:**
- Produces: `ManagedQwenChromeOperation.__init__` 新增 `eager_recopy: bool = False`；
  `eager_recopy=True` 且 `session_recopy` 存在时，`run(visible=False)` 先
  `session_recopy(config_dir)`（异常原样冒泡）再 `_run_once`，且**不做**二次
  重复制兜底。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_qwen_chrome_cdp.py`；
  先给 `_make_recovery_operation` 工厂加 `eager_recopy: bool = False` 参数，
  透传给构造器的同名关键字——构造器参数在本任务 Step 3 加入）

```python
def test_eager_recopy_copies_before_refresh_and_reports_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recopied: list[Path] = []
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[_quota_payload()],
        session_origin=QWEN_SESSION_ORIGIN_DAILY_RECOPY,
        recopied=recopied,
        eager_recopy=True,
    )

    result = operation.run(visible=False, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert recopied == [tmp_path / "config"]
    assert operation.recopy_performed is True


def test_eager_recopy_failure_propagates_for_visible_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[_quota_payload()],
        session_origin=QWEN_SESSION_ORIGIN_DAILY_RECOPY,
        recopied=[],
        recopy_raises=True,
        eager_recopy=True,
    )

    with pytest.raises(QwenChromeQuotaError):
        operation.run(visible=False, cancel=Event())


def test_eager_recopy_unauthorized_does_not_recopy_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recopied: list[Path] = []
    operation = _make_recovery_operation(
        tmp_path,
        monkeypatch,
        evaluations=[{"kind": "unauthorized"}],
        session_origin=QWEN_SESSION_ORIGIN_DAILY_RECOPY,
        recopied=recopied,
        eager_recopy=True,
    )

    with pytest.raises(QwenChromeUnauthorizedError):
        operation.run(visible=False, cancel=Event())
    assert recopied == [tmp_path / "config"]  # exactly once
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_cdp.py -k "eager" -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`ManagedQwenChromeOperation.__init__` 加参数（`session_origin` 之后）：

```python
        eager_recopy: bool = False,
```

构造体：`self._eager_recopy = eager_recopy`。

`run` 方法 `if visible:` 分支之后、`fail_fast_unauthorized = ...` 之前插入：

```python
        if self._eager_recopy and self._session_recopy is not None:
            _logger.info("Qwen sync login recopying the daily Chrome session before refresh")
            self._session_recopy(self.config_dir)  # errors bubble to the caller's fallback
            self.recopy_performed = True
            return self._run_once(visible=False, cancel=cancel, fail_fast_unauthorized=False)
```

说明：急切分支失败（recopy 抛错 / `_run_once` 抛 unauthorized）都直接冒泡，
不进 `_recover_expired_session`——同一份 cookie 再复制一次毫无意义，交由
session 层转可见登录。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_cdp.py -q`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/aacc/qwen_chrome_cdp.py tests/test_qwen_chrome_cdp.py
git commit -m "feat: eager daily-session recopy primitive for sync-first login"
```

---

### Task 4: 健壮取消规则（跨域跳转不再 14 秒误关登录窗）

背景：现行「3×2s 没看到百炼页面 = 用户关窗」会把短信/风控验证的跨域跳转
误判为关窗（12:07:33 实锤，14 秒被自动关掉）。新规则：只有「一个页面目标都
没有」（真关窗）才计数取消；页面存在但不在百炼域 = 等，不计数。

**Files:**
- Modify: `src/aacc/qwen_chrome_cdp.py`
- Test: `tests/test_qwen_chrome_cdp.py`

**Interfaces:**
- Produces:
  - 模块函数 `count_qwen_page_targets(targets: object) -> int`
  - 常量 `_QWEN_LOGIN_STARTUP_GRACE_SECONDS = 30.0`
  - `_run_once` 可见分支取消判定按新规则重写

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_qwen_chrome_cdp.py`）

```python
def test_count_page_targets_counts_any_origin() -> None:
    targets = [
        {"type": "page", "url": "https://signin.aliyun.com/login"},
        {"type": "page", "url": WORKSPACE_URL},
        {"type": "iframe", "url": "https://example.com"},
        "garbage",
    ]
    assert count_qwen_page_targets(targets) == 2
    assert count_qwen_page_targets(None) == 0
    assert count_qwen_page_targets("not a list") == 0


def test_visible_login_survives_cross_origin_redirect_without_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    listings = iter(
        [
            # Poll 1..2: the page sits on the SMS origin — not Bailian, but a
            # page target exists, so this must never count as a user cancel.
            [{"type": "page", "url": "https://signin.aliyun.com/sms",
              "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/sms"}],
            [{"type": "page", "url": "https://signin.aliyun.com/sms",
              "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/sms"}],
            # Poll 3: back on Bailian with the quota rendered.
            [_quota_page_target()],
        ]
    )
    evaluations = {"count": 0}

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            evaluations["count"] += 1
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    clock = iter([0.0] * 64)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=lambda _origin: next(listings),
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
    )

    result = operation.run(visible=True, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert evaluations["count"] == 1


def test_visible_login_cancels_when_window_truly_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    seen_page = {"flag": False}

    def target_loader(_origin: str) -> list[object]:
        if not seen_page["flag"]:
            seen_page["flag"] = True
            return [_quota_page_target()]
        return []  # the user closed the window: zero page targets

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            return {"kind": "unauthorized"}  # keep polling: not yet logged in

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    clock = iter([0.0] * 64)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=target_loader,
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
    )

    with pytest.raises(QwenChromeLoginCancelledError):
        operation.run(visible=True, cancel=Event())
```

注意：第二个测试里可见路径的 unauthorized 评估不再立即超时——现行代码可见
路径撞 `QwenChromeUnauthorizedError` 是继续等登录（`login_deadline` 内），这里
`clock` 恒 0 不会超时，循环会一直走直到窗口目标清零 3 轮后抛取消。若实现里
可见路径对评估异常的处理有变，请保持「评估出的未登录 = 继续等用户登录」语义。

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_cdp.py -k "count_page_targets or cross_origin or truly_closed" -q`
Expected: FAIL

- [ ] **Step 3: 实现**

模块函数区加：

```python
def count_qwen_page_targets(targets: object) -> int:
    """Count page targets of any origin (a closed window has zero)."""

    if not isinstance(targets, list):
        return 0
    return sum(1 for item in targets if isinstance(item, dict) and item.get("type") == "page")
```

常量：`_QWEN_LOGIN_STARTUP_GRACE_SECONDS = 30.0`。

`_run_once` 里把循环状态初始化改为（替换 `target_requested = visible` /
`missing_target_polls = 0` 那一段）：

```python
            target_requested = visible
            missing_page_polls = 0
            saw_page_target = False
            endpoint_ready_at = self._monotonic()
```

把 `targets = self._target_loader(...)` 之后、`return self._evaluate_page_candidates(...)`
之前的整段 `try/except QwenChromeUnauthorizedError` 换成：

```python
                targets = self._target_loader(endpoint.http_origin)
                page_count = count_qwen_page_targets(targets)
                try:
                    page_sockets = select_qwen_page_sockets(targets, expected_port=port)
                except QwenChromeUnauthorizedError:
                    if visible:
                        if page_count > 0:
                            # Cross-origin login hops (SMS, baxia verification)
                            # temporarily leave no Bailian target while a page
                            # target still exists: wait, never count a cancel.
                            saw_page_target = True
                            missing_page_polls = 0
                            self._sleep(2.0)
                            continue
                        # Zero page targets: either the user closed the window
                        # or the instance never opened one. After the startup
                        # grace both count toward a clean cancel.
                        if saw_page_target or (
                            self._monotonic() - endpoint_ready_at
                            >= _QWEN_LOGIN_STARTUP_GRACE_SECONDS
                        ):
                            missing_page_polls += 1
                            if missing_page_polls >= _QWEN_LOGIN_WINDOW_MISSING_POLLS:
                                _logger.info(
                                    "Qwen login window closed by the user; shutting Chrome down"
                                )
                                raise QwenChromeLoginCancelledError from None
                        self._sleep(2.0)
                        continue
                    # A missing Bailian page is a startup race, never proof of
                    # an expired session (hidden path stays unchanged).
                    raise QwenChromeQuotaError(QwenQuotaErrorCategory.REFRESH_FAILED) from None
                if visible:
                    saw_page_target = True
                missing_page_polls = 0
                if visible and not visible_setup_done:
                    visible_setup_done = True
                    self._setup_visible_login_page(page_sockets)
                return self._evaluate_page_candidates(page_sockets, visible=visible)
```

并在循环状态初始化处加 `visible_setup_done = False`（Task 5 会用到
`_setup_visible_login_page`；本任务先加一个空实现占位，Task 5 补全）：

```python
    def _setup_visible_login_page(self, page_sockets: Sequence[str]) -> None:
        del page_sockets
```

同时删除旧的 `missing_target_polls` 变量及其旧注释块。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_cdp.py -q`
Expected: 全 PASS（既有 `test_visible_login_window_close_cancels_cleanly_without_recopy`
若依赖旧计数语义，按新规则调整其 fake 目标序列：先给一个页面目标再清零）

- [ ] **Step 5: 提交**

```bash
git add src/aacc/qwen_chrome_cdp.py tests/test_qwen_chrome_cdp.py
git commit -m "fix: cancel visible Qwen login only when every page target is gone"
```

---

### Task 5: 登录窗居中 + 可见路径反检测脚本

背景：可见登录窗现在开在 Chrome 默认位置（用户要求屏幕正中）；且可见路径
从未装反检测脚本（隐藏路径有），疑似手动登录会话被污染的帮凶（探针 T1 前提）。

**Files:**
- Modify: `src/aacc/qwen_chrome_cdp.py`
- Test: `tests/test_qwen_chrome_cdp.py`

**Interfaces:**
- Produces:
  - 纯函数 `centered_window_bounds(screen_width: int, screen_height: int,
    window_width: int = _QWEN_HIDDEN_WINDOW_WIDTH,
    window_height: int = _QWEN_HIDDEN_WINDOW_HEIGHT) -> tuple[int, int, int, int]`
    （返回 left, top, width, height；坐标不小于 0）
  - `install_qwen_visible_login_page(page: CdpConnection,
    bounds: tuple[int, int, int, int]) -> None`（Page.enable + stealth 脚本 +
    setWindowBounds 到 bounds + Page.reload；异常由调用方 try 住）
  - `ManagedQwenChromeOperation.__init__` 新增
    `visible_window_bounds: tuple[int, int, int, int] | None = None`
  - `_setup_visible_login_page` 补全（用第一个百炼页面 socket 建连接、调用
    `install_qwen_visible_login_page`、失败 WARNING 不中断登录）

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_qwen_chrome_cdp.py`）

```python
def test_centered_window_bounds_centers_on_screen() -> None:
    assert centered_window_bounds(2560, 1440, 1100, 700) == (730, 370, 1100, 700)
    # A screen smaller than the window clamps to the origin, never negative.
    assert centered_window_bounds(800, 600, 1100, 700) == (0, 0, 1100, 700)


def test_install_visible_login_page_masks_webdriver_and_centers() -> None:
    commands: list[tuple[str, object]] = []

    class Page:
        def send_command(self, method: str, params: object) -> dict[str, object]:
            commands.append((method, params))
            if method == "Browser.getWindowForTarget":
                return {"result": {"windowId": 7}}
            return {"result": {}}

    install_qwen_visible_login_page(Page(), (100, 50, 1100, 700))

    methods = [method for method, _ in commands]
    assert "Page.enable" in methods
    assert "Page.addScriptToEvaluateOnNewDocument" in methods
    assert "Page.reload" in methods
    bounds_commands = [params for method, params in commands if method == "Browser.setWindowBounds"]
    assert bounds_commands == [
        {"windowId": 7, "bounds": {"left": 100, "top": 50, "width": 1100, "height": 700}}
    ]
    script = dict(
        (method, params) for method, params in commands
        if method == "Page.addScriptToEvaluateOnNewDocument"
    )["Page.addScriptToEvaluateOnNewDocument"]
    assert "webdriver" in script["source"]


def test_visible_login_applies_bounds_and_stealth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    commands: list[str] = []

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, method: str, _params: object) -> dict[str, object]:
            commands.append(method)
            if method == "Browser.getWindowForTarget":
                return {"result": {"windowId": 3}}
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)
    clock = iter([0.0] * 64)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, FakeProcess()),
        target_loader=lambda _origin: [_quota_page_target()],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=lambda _profile: [],
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
        visible_window_bounds=(730, 370, 1100, 700),
    )

    result = operation.run(visible=True, cancel=Event())

    assert result["personalFiveHourText"] == "5小时限额\n0.04%已用"
    assert "Page.addScriptToEvaluateOnNewDocument" in commands
    assert "Browser.setWindowBounds" in commands
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_cdp.py -k "centered or visible_login_page or applies_bounds" -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`install_qwen_hidden_page_stealth` 之后加：

```python
def centered_window_bounds(
    screen_width: int,
    screen_height: int,
    window_width: int = _QWEN_HIDDEN_WINDOW_WIDTH,
    window_height: int = _QWEN_HIDDEN_WINDOW_HEIGHT,
) -> tuple[int, int, int, int]:
    """Return (left, top, width, height) centering the window on the screen."""

    left = max(0, (screen_width - window_width) // 2)
    top = max(0, (screen_height - window_height) // 2)
    return (left, top, window_width, window_height)


def install_qwen_visible_login_page(
    page: CdpConnection, bounds: tuple[int, int, int, int]
) -> None:
    """Center the visible login window and mask automation fingerprints.

    The same stealth script as the hidden path is installed before the first
    baxia read of the login page: a CDP-attached Chrome reports
    ``navigator.webdriver`` as true, and masking it on the visible path keeps
    the manually created session from being flagged at creation time. The
    reload applies the script to the current document.
    """

    left, top, width, height = bounds
    page.send_command("Page.enable", {})
    page.send_command(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": qwen_hidden_page_stealth_script()},
    )
    response = page.send_command("Browser.getWindowForTarget", {})
    result = response.get("result")
    window_id = result.get("windowId") if isinstance(result, dict) else None
    if window_id is not None:
        page.send_command(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {"left": left, "top": top, "width": width, "height": height},
            },
        )
    page.send_command("Page.reload", {})
```

构造器加参数 `visible_window_bounds: tuple[int, int, int, int] | None = None`，
构造体 `self._visible_window_bounds = visible_window_bounds`。

把 Task 4 的 `_setup_visible_login_page` 占位实现替换为：

```python
    def _setup_visible_login_page(self, page_sockets: Sequence[str]) -> None:
        """Center the login window and install stealth before the user types.

        Best-effort like the hidden stealth installation: a failure degrades
        to a plain visible login, it must never abort the flow.
        """

        if self._visible_window_bounds is None or not page_sockets:
            return
        page: CdpConnection | None = None
        try:
            page = CdpConnection(self._socket_factory(page_sockets[0]))  # type: ignore[arg-type]
            install_qwen_visible_login_page(page, self._visible_window_bounds)
        except Exception:
            _logger.warning("Qwen visible login page setup failed", exc_info=True)
        finally:
            if page is not None:
                with suppress(Exception):
                    page.close()
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_cdp.py -q`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/aacc/qwen_chrome_cdp.py tests/test_qwen_chrome_cdp.py
git commit -m "feat: center visible Qwen login and install stealth before login"
```

---

### Task 6: 用完必退——操作结束强制校验零残留进程

背景：今天实锤登录成功后 Chrome 进程滞留 6+ 分钟（Dock 小圆点残留的直接
来源）。怀疑 Chrome re-exec 使「Popen 子进程已退出」的判定失真。无论哪种
机制，操作结束后按 profile 枚举进程做硬校验+强杀都能兜住，并补日志。

**Files:**
- Modify: `src/aacc/qwen_chrome_cdp.py`
- Test: `tests/test_qwen_chrome_cdp.py`

**Interfaces:**
- Produces: `_run_once` 的 finally 尾部追加 `self._verify_profile_processes_gone()`；
  新增私有方法 `_verify_profile_processes_gone()`（发现残留 → 调
  `terminate_qwen_chrome_profile_processes` 强杀 → 再查一次 → 仍非空记
  ERROR）；`_shutdown_process` 三条路径各补 DEBUG/WARNING/INFO 日志。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_qwen_chrome_cdp.py`；
  用可见路径避免隐藏路径 `_DetachedQwenChromeHandle` 的等待循环干扰计数）

```python
def test_operation_force_kills_surviving_profile_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    import aacc.qwen_chrome_cdp as module

    profile = _make_chrome_profile(tmp_path)
    survivor = FakeChromeProcess(stubborn=False, pid=9001)
    state = {"shutdown_done": False, "forced": False}

    def finder(_profile: Path) -> list[FakeChromeProcess]:
        # Graceful shutdown has exited but one process is still alive; it
        # disappears only once the forced-termination pass has run.
        if state["shutdown_done"] and not state["forced"]:
            return [survivor]
        return []

    class FakeCdp:
        def __init__(self, _socket: object) -> None:
            pass

        def send_command(self, _method: str, _params: object) -> dict[str, object]:
            return {"result": {}}

        def evaluate(self, _expression: str) -> object:
            return _quota_payload()

        def close_browser(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "CdpConnection", FakeCdp)

    def forced_terminator(_profile: Path, process_finder=None) -> None:
        del process_finder
        # The pre-launch cleanup also calls the terminator while the finder
        # is empty; only mark the forced pass once a survivor is actually
        # present (post-shutdown).
        if finder(_profile):
            state["forced"] = True

    monkeypatch.setattr(module, "terminate_qwen_chrome_profile_processes", forced_terminator)

    process = FakeProcess()

    def marking_wait(timeout: float | None = None) -> int:
        del timeout
        state["shutdown_done"] = True
        return 0

    process.wait = marking_wait  # graceful shutdown reports success
    clock = iter([0.0] * 64)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(profile, process),
        target_loader=lambda _origin: [_quota_page_target()],
        socket_factory=lambda _url: object(),
        expression_factory=lambda: "return quota",
        chrome_process_finder=finder,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock, 61.0),
    )

    with caplog.at_level(logging.WARNING, logger="aacc.qwen_chrome_cdp"):
        operation.run(visible=True, cancel=Event())

    assert state["forced"] is True
    assert any("remained" in record.message.casefold() for record in caplog.records)


def test_verify_profile_processes_logs_error_for_stubborn_survivors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    import aacc.qwen_chrome_cdp as module

    stubborn = FakeChromeProcess(stubborn=True, pid=1)

    def no_op_terminator(_profile: Path, process_finder=None) -> None:
        del process_finder

    monkeypatch.setattr(module, "terminate_qwen_chrome_profile_processes", no_op_terminator)
    operation = ManagedQwenChromeOperation(
        WORKSPACE_URL,
        config_dir=tmp_path / "config",
        executable=Path("chrome"),
        platform_name="darwin",
        protector=lambda _profile: None,
        process_factory=_fake_process_factory(
            _make_chrome_profile(tmp_path), FakeProcess()
        ),
        target_loader=lambda _origin: [],
        socket_factory=lambda _url: object(),
        chrome_process_finder=lambda _profile: [stubborn],
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    with caplog.at_level(logging.ERROR, logger="aacc.qwen_chrome_cdp"):
        operation._verify_profile_processes_gone()

    assert any("survived" in record.message.casefold() for record in caplog.records)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_cdp.py -k "surviving or stubborn_survivors" -q`
Expected: FAIL（`AttributeError: _verify_profile_processes_gone`）

- [ ] **Step 3: 实现**

`ManagedQwenChromeOperation` 新增方法：

```python
    def _verify_profile_processes_gone(self) -> None:
        """Hard-verify the owned profile has no Chrome left after shutdown.

        A browser that re-execs itself defeats the Popen-child exit check;
        the profile process enumeration is the authoritative ownership
        boundary, so re-scan it and force-terminate survivors.
        """

        try:
            remaining = list(self._chrome_process_finder(self.profile))
        except Exception:
            _logger.warning("Qwen Chrome post-shutdown scan failed", exc_info=True)
            return
        if not remaining:
            return
        _logger.warning(
            "Qwen Chrome processes remained after shutdown; forcing termination count=%d",
            len(remaining),
        )
        try:
            terminate_qwen_chrome_profile_processes(
                self.profile, process_finder=self._chrome_process_finder
            )
        except Exception:
            _logger.error("Qwen Chrome forced termination failed", exc_info=True)
            return
        try:
            survivors = list(self._chrome_process_finder(self.profile))
        except Exception:
            return
        if survivors:
            _logger.error(
                "Qwen Chrome processes survived forced termination count=%d",
                len(survivors),
            )
```

`_run_once` 的 finally 末尾（`if not self._shutdown_process(process):` 块后）加：

```python
            self._verify_profile_processes_gone()
```

`_shutdown_process` 加日志（三条路径）：

```python
    def _shutdown_process(self, process: _ProcessLike) -> bool:
        try:
            process.wait(timeout=EDGE_SHUTDOWN_TIMEOUT_SECONDS)
            _logger.debug("Qwen Chrome exited cleanly")
            return True
        except Exception:
            pass
        _logger.warning(
            "Qwen Chrome did not exit within the shutdown window; terminating the tree"
        )
        try:
            self._process_tree_terminator(process)
        except Exception:
            _logger.error("Qwen Chrome process tree termination failed", exc_info=True)
            return False
        try:
            process.wait(timeout=EDGE_SHUTDOWN_TIMEOUT_SECONDS)
            _logger.info("Qwen Chrome terminated after escalation")
            return True
        except Exception:
            _logger.error("Qwen Chrome process remained alive after termination", exc_info=True)
            return False
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_cdp.py -q`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/aacc/qwen_chrome_cdp.py tests/test_qwen_chrome_cdp.py
git commit -m "fix: verify and force-terminate Qwen Chrome after every operation"
```

---

### Task 7: session 层同步优先编排与来源落盘

背景：`open_login` 改为「有同步源 → 先静默急同步（隐藏）→ 失败再可见登录」；
每次成功按来源与是否发生重复制落 `session_origin`/`last_success_epoch`。

**Files:**
- Modify: `src/aacc/qwen_chrome_session.py`
- Test: `tests/test_qwen_chrome_session.py`

**Interfaces:**
- Consumes: Task 1 状态存储方法；Task 2/3 操作参数
  （`session_origin`/`eager_recopy`/`visible_window_bounds`）与属性 `recopy_performed`
- Produces:
  - `QwenChromeSession.__init__` 新增注入参数：
    `daily_source_probe: Callable[[], Path | None] = daily_chrome_session_source`、
    `visible_bounds_provider: Callable[[], tuple[int, int, int, int] | None] = _default_visible_window_bounds`、
    `success_clock: Callable[[], int] = <int(time.time)>`
  - 新信号 `sync_started = Signal()`
  - 内部状态 `self._login_phase: str | None`（`"sync"`/`"visible"`/`None`）与
    `self._active_operation`
  - `open_login` 同步优先；`_on_operation_finished` 处理同步失败回落可见、
    成功落来源

模块级新增（`_make_thread` 附近）：

```python
def _default_visible_window_bounds() -> tuple[int, int, int, int] | None:
    """Center a 1100x700 login window on the primary screen, Qt-side.

    Computed here (Qt thread) instead of inside the CDP module so the
    operation stays platform-neutral and unit-testable.
    """

    try:
        from PySide6.QtGui import QGuiApplication

        from aacc.qwen_chrome_cdp import centered_window_bounds

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        geometry = screen.availableGeometry()
        return centered_window_bounds(geometry.width(), geometry.height())
    except Exception:
        return None
```

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_qwen_chrome_session.py`；
  先给 `make_session` 工厂透传新注入参数）

`make_session` 的 `session = QwenChromeSession(...)` 调用体追加三行
（保持 `assert not kwargs` 兜底）：

```python
        daily_source_probe=kwargs.pop("daily_source_probe", lambda: None),
        visible_bounds_provider=kwargs.pop("visible_bounds_provider", lambda: None),
        success_clock=kwargs.pop("success_clock", lambda: 1725424224),
```

新测试：

```python
def test_open_login_syncs_first_when_daily_source_exists(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n0.04%"})
    session = make_session(
        tmp_path,
        operation,
        auto_session_recopy=True,
        daily_source_probe=lambda: tmp_path / "daily-chrome",
    )

    session.open_login()

    assert operation.calls == [False]  # hidden sync attempt, no window
    store = KimiWebLoginStateStore(tmp_path, state_file_name="qwen-web-session-state.json")
    assert store.may_reuse() is True
    assert store.session_origin() == KimiWebLoginStateStore.SESSION_ORIGIN_DAILY_RECOPY
    assert store.last_success_epoch() == 1725424224


def test_open_login_falls_back_to_visible_when_sync_unauthorized(qapp, tmp_path):
    del qapp

    class ScriptedOperation:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def run(self, *, visible: bool, cancel: Event):
            self.calls.append(visible)
            if visible:
                return {"fiveHourText": "5 小时\n0.04%"}
            raise QwenChromeUnauthorizedError()

    operation = ScriptedOperation()
    session = make_session(
        tmp_path,
        operation,
        auto_session_recopy=True,
        daily_source_probe=lambda: tmp_path / "daily-chrome",
    )

    session.open_login()

    assert operation.calls == [False, True]
    store = KimiWebLoginStateStore(tmp_path, state_file_name="qwen-web-session-state.json")
    assert store.session_origin() == KimiWebLoginStateStore.SESSION_ORIGIN_MANUAL_LOGIN


def test_open_login_without_daily_source_goes_straight_to_visible(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n0.04%"})
    session = make_session(tmp_path, operation, auto_session_recopy=True)

    session.open_login()

    assert operation.calls == [True]
    store = KimiWebLoginStateStore(tmp_path, state_file_name="qwen-web-session-state.json")
    assert store.session_origin() == KimiWebLoginStateStore.SESSION_ORIGIN_MANUAL_LOGIN


def test_open_login_emits_sync_started_for_sync_phase(qapp, tmp_path):
    del qapp
    operation = FakeOperation({"fiveHourText": "5 小时\n0.04%"})
    session = make_session(
        tmp_path,
        operation,
        auto_session_recopy=True,
        daily_source_probe=lambda: tmp_path / "daily-chrome",
    )
    hints: list[None] = []
    session.sync_started.connect(lambda: hints.append(None))

    session.open_login()

    assert hints == [None]


def test_refresh_success_after_recopy_records_daily_origin(qapp, tmp_path):
    del qapp

    class RecopyingOperation:
        recopy_performed = True

        def run(self, *, visible: bool, cancel: Event):
            assert visible is False
            return {"fiveHourText": "5 小时\n0.04%"}

    session = make_session(tmp_path, RecopyingOperation())
    session.login_state.set_may_reuse(
        True,
        session_origin=KimiWebLoginStateStore.SESSION_ORIGIN_MANUAL_LOGIN,
    )

    session.refresh()

    store = KimiWebLoginStateStore(tmp_path, state_file_name="qwen-web-session-state.json")
    assert store.session_origin() == KimiWebLoginStateStore.SESSION_ORIGIN_DAILY_RECOPY
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_session.py -q`
Expected: 新测试 FAIL（`TypeError: unexpected keyword argument` / 行为不符）

- [ ] **Step 3: 实现**

`qwen_chrome_session.py` 顶部导入补充：

```python
import time
from aacc.qwen_chrome_cdp import (
    ...,
    QWEN_SESSION_ORIGIN_DAILY_RECOPY,
    QWEN_SESSION_ORIGIN_MANUAL_LOGIN,
    daily_chrome_session_source,
)
```

`QwenChromeSession` 类体：

信号区加 `sync_started = Signal()`。

`__init__` 新增参数（`auto_session_recopy` 之后）：

```python
        daily_source_probe: Callable[[], Path | None] = daily_chrome_session_source,
        visible_bounds_provider: Callable[[], tuple[int, int, int, int] | None] = _default_visible_window_bounds,
        success_clock: Callable[[], int] = lambda: int(time.time()),
```

构造体对应：

```python
        self._daily_source_probe = daily_source_probe
        self._visible_bounds_provider = visible_bounds_provider
        self._success_clock = success_clock
        self._login_phase: str | None = None
        self._active_operation: object | None = None
```

用下面的实现替换 `open_login`：

```python
    def open_login(self, parent: QWidget | None = None) -> None:
        del parent
        if self._closed or self._busy:
            if self._busy:
                self.error_occurred.emit(QwenQuotaErrorCategory.REFRESH_FAILED.value)
            return
        if not self.workspace_url:
            self.error_occurred.emit(QwenQuotaErrorCategory.REFRESH_FAILED.value)
            return
        if self.auto_session_recopy and self._daily_source_probe() is not None:
            # Sync-first: silently recopy the daily Chrome session and fetch
            # the quota hidden. Only a failure opens the visible login.
            self._login_phase = "sync"
            self.sync_started.emit()
            _logger.info("Qwen login attempting silent daily-session sync first")
            self._start(visible=False, eager_recopy=True)
            return
        self._login_phase = "visible"
        self._start(visible=True)
```

`_start` 签名改为 `def _start(self, *, visible: bool, eager_recopy: bool = False) -> None:`，
构造操作处改为：

```python
            try:
                operation = ManagedQwenChromeOperation(
                    self.workspace_url,
                    config_dir=self.config_dir,
                    session_recopy=(
                        recopy_qwen_daily_chrome_session if self.auto_session_recopy else None
                    ),
                    session_origin=self.login_state.session_origin(),
                    eager_recopy=eager_recopy,
                    visible_window_bounds=(
                        self._visible_bounds_provider() if visible else None
                    ),
                )
            except Exception:
```

`self._thread = thread` 之前加 `self._active_operation = operation`。

用下面的完整实现替换 `_on_operation_finished`（自动弹窗行由 Task 8 补入）：

```python
    def _on_operation_finished(self, generation: int, outcome: object) -> None:
        if self._cleanup_after_worker:
            self._cleanup_after_worker = False
            self._busy = False
            self._thread = None
            self._cancel = None
            self._finish_logout_cleanup(True)
            return
        if generation != self._generation or self._closed:
            return
        self._busy = False
        self._thread = None
        self._cancel = None
        if isinstance(outcome, dict):
            self._persist_success_origin()
            self._login_phase = None
            self.login_state_changed.emit(True)
            self.quota_received.emit(outcome)
            return
        if self._login_phase == "sync" and not isinstance(
            outcome, (QwenChromeCancelledError, QwenChromeLoginCancelledError)
        ):
            # The silent sync attempt failed (dead daily session, missing
            # source files, or a failed hidden fetch): open the visible login.
            _logger.info("Qwen silent sync failed; falling back to the visible login window")
            self._login_phase = "visible"
            self._start(visible=True)
            return
        self._login_phase = None
        if isinstance(outcome, QwenChromeUnauthorizedError):
            _logger.warning(
                "Qwen Chrome session is logged out; quota refresh paused until re-login"
            )
            self._persist_reuse(False, logged_out_by_user=False)
            self.login_state_changed.emit(False)
            return
        if isinstance(outcome, QwenChromeLoginCancelledError):
            _logger.info("Qwen Chrome login window closed by the user; login abandoned")
            return
        if isinstance(outcome, QwenChromeCancelledError):
            return
        category = (
            outcome.category
            if isinstance(outcome, QwenChromeQuotaError)
            else QwenQuotaErrorCategory.REFRESH_FAILED
        )
        _logger.warning("Qwen Chrome operation completed category=%s", category.value)
        self.error_occurred.emit(category.value)
```

新增 `_persist_success_origin` 方法：

```python
    def _persist_success_origin(self) -> None:
        if self._login_phase == "visible":
            origin = KimiWebLoginStateStore.SESSION_ORIGIN_MANUAL_LOGIN
        elif self._login_phase == "sync":
            origin = KimiWebLoginStateStore.SESSION_ORIGIN_DAILY_RECOPY
        else:
            recopy_performed = bool(
                getattr(self._active_operation, "recopy_performed", False)
            )
            origin = (
                KimiWebLoginStateStore.SESSION_ORIGIN_DAILY_RECOPY
                if recopy_performed
                else self.login_state.session_origin()
            )
        if not self._persist_reuse(
            True,
            logged_out_by_user=False,
            session_origin=origin,
            last_success_epoch=self._success_clock(),
        ):
            self.error_occurred.emit("state_save_failed")
```

（成功分支里原来的 `persisted = self._persist_reuse(True, logged_out_by_user=False)`
与 `state_save_failed` 两行由 `_persist_success_origin()` 调用取代。）

`_persist_reuse` 签名扩展：

```python
    def _persist_reuse(
        self,
        value: bool,
        *,
        logged_out_by_user: bool | None = None,
        session_origin: str | None = None,
        last_success_epoch: int | None = None,
    ) -> bool:
        try:
            self.login_state.set_may_reuse(
                value,
                logged_out_by_user=logged_out_by_user,
                session_origin=session_origin,
                last_success_epoch=last_success_epoch,
            )
        except Exception:
            _logger.error("Qwen web session state update failed")
            return False
        return True
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_session.py tests/test_qwen_chrome_cdp.py -q`
Expected: 全 PASS（既有 session 测试走 `daily_source_probe=lambda: None` 缺省，
行为不变）

- [ ] **Step 5: 提交**

```bash
git add src/aacc/qwen_chrome_session.py tests/test_qwen_chrome_session.py
git commit -m "feat: sync-first Qwen login with origin-aware state persistence"
```

---

### Task 8: 自动弹窗自愈信号与限频

背景：刷新确认会话死亡且同步救不回时，自动弹出登录窗（用户选定的失效体验），
30 分钟限频；用户主动登出永不触发。

**Files:**
- Modify: `src/aacc/qwen_chrome_session.py`、`src/aacc/qwen_web_quota_service.py`、
  `src/aacc/gui.py`
- Test: `tests/test_qwen_chrome_session.py`、`tests/test_qwen_web_quota_service.py`

**Interfaces:**
- Produces:
  - `QwenChromeSession.auto_login_requested = Signal()`；`__init__` 新增
    `auto_login_clock: Callable[[], float] = time.monotonic`；模块常量
    `QWEN_AUTO_LOGIN_MIN_INTERVAL_SECONDS = 1800.0`
  - `QwenWebQuotaService.auto_login_requested = Signal()`（`_connect_session`
    用 getattr 守卫转发，native 会话无此信号）
  - GUI `_on_qwen_auto_login_requested()`：栏切自动恢复态 + `open_login`

- [ ] **Step 1: 写失败测试**

`tests/test_qwen_chrome_session.py` 追加：

```python
def test_unauthorized_refresh_requests_auto_login_when_rate_limit_allows(qapp, tmp_path):
    del qapp
    import time as _time

    operation = FakeOperation(QwenChromeUnauthorizedError())
    now = _time.monotonic()
    session = make_session(
        tmp_path,
        operation,
        auto_session_recopy=True,
        auto_login_clock=lambda: now,
    )
    session.login_state.set_may_reuse(True)
    requests: list[None] = []
    session.auto_login_requested.connect(lambda: requests.append(None))

    session.refresh()
    assert requests == [None]

    # Second exhaustion within the window stays silent.
    session.login_state.set_may_reuse(True)
    session.refresh()
    assert requests == [None]


def test_user_logout_never_requests_auto_login(qapp, tmp_path):
    del qapp
    operation = FakeOperation(QwenChromeUnauthorizedError())
    session = make_session(tmp_path, operation, auto_session_recopy=True)
    session.logout()
    requests: list[None] = []
    session.auto_login_requested.connect(lambda: requests.append(None))

    session.refresh()  # logged_out_by_user=True: refresh is skipped entirely

    assert requests == []
```

`tests/test_qwen_web_quota_service.py` 追加（仿照该文件现有
`_FakeSession`/服务构造模式；若现有 fake 无该信号，加一个带
`auto_login_requested = Signal()` 的变体）：

```python
def test_service_forwards_auto_login_requested(qapp, tmp_path):
    del qapp
    from PySide6.QtCore import QObject, Signal

    from aacc.qwen_web_quota_service import QwenWebQuotaService

    class FakeSession(QObject):
        login_state_changed = Signal(bool)
        quota_received = Signal(object)
        error_occurred = Signal(str)
        auto_login_requested = Signal()

        def refresh(self) -> None: ...
        def open_login(self, parent=None) -> None: ...
        def logout(self) -> bool:
            return True
        def close(self) -> None: ...
        def retranslate_ui(self) -> None: ...
        def set_workspace_url(self, url: str) -> None: ...

    session = FakeSession()
    service = QwenWebQuotaService(tmp_path, session=session)
    received: list[None] = []
    service.auto_login_requested.connect(lambda: received.append(None))

    session.auto_login_requested.emit()

    assert received == [None]
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_session.py tests/test_qwen_web_quota_service.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`qwen_chrome_session.py`：

```python
QWEN_AUTO_LOGIN_MIN_INTERVAL_SECONDS = 1800.0
```

信号区加 `auto_login_requested = Signal()`；`__init__` 加参数
`auto_login_clock: Callable[[], float] = time.monotonic`，构造体
`self._auto_login_clock = auto_login_clock`、`self._last_auto_login_at: float | None = None`。

`_on_operation_finished` 的未登录分支（Task 7 版本）按下面补一行：

```python
        if isinstance(outcome, QwenChromeUnauthorizedError):
            _logger.warning(
                "Qwen Chrome session is logged out; quota refresh paused until re-login"
            )
            self._persist_reuse(False, logged_out_by_user=False)
            self.login_state_changed.emit(False)
            self._maybe_request_auto_login()
            return
```

新方法：

```python
    def _maybe_request_auto_login(self) -> None:
        """Ask the GUI to open the login automatically, rate-limited.

        Recovery is exhausted when this fires: the refresh hit the login
        banner past every recheck and recopy chance. An explicit user logout
        must never resurrect a popup.
        """

        if self.login_state.logged_out_by_user():
            return
        now = self._auto_login_clock()
        if (
            self._last_auto_login_at is not None
            and now - self._last_auto_login_at < QWEN_AUTO_LOGIN_MIN_INTERVAL_SECONDS
        ):
            _logger.debug("Qwen auto-login popup suppressed by the rate limit")
            return
        self._last_auto_login_at = now
        _logger.warning("Qwen quota session expired; requesting an automatic login window")
        self.auto_login_requested.emit()
```

`qwen_web_quota_service.py`：类体信号区加
`auto_login_requested = Signal()`；`_connect_session` 末尾加：

```python
        auto_login = getattr(session, "auto_login_requested", None)
        if auto_login is not None:
            auto_login.connect(self.auto_login_requested.emit)
```

`gui.py`：qwen 服务接线块（`self.qwen_web_quota_service.error_occurred.connect(...)`
行后）加：

```python
            self.qwen_web_quota_service.auto_login_requested.connect(
                self._on_qwen_auto_login_requested
            )
```

`_on_qwen_quota_error` 方法后新增：

```python
    def _on_qwen_auto_login_requested(self) -> None:
        if self.qwen_web_quota_service is None:
            return
        if self.qwen_quota_bar is not None:
            self.qwen_quota_bar.show_auto_recovering()
        self.qwen_web_quota_service.open_login(self)
```

`_on_qwen_quota_bar_clicked` 的 `if not self._qwen_authorized:` 分支与
`open_qwen_web_login` 里，`open_login` 调用前加
`if self.qwen_quota_bar is not None: self.qwen_quota_bar.show_pending()`
（点击后立即给「授权中…」反馈，覆盖同步与弹窗两条路径）。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_qwen_chrome_session.py tests/test_qwen_web_quota_service.py -q`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/aacc/qwen_chrome_session.py src/aacc/qwen_web_quota_service.py src/aacc/gui.py \
        tests/test_qwen_chrome_session.py tests/test_qwen_web_quota_service.py
git commit -m "feat: auto-open the centered Qwen login when recovery is exhausted"
```

---

### Task 9: 状态文案（中英双语）

背景：额度条新增「正在同步日常 Chrome 会话…」「会话已过期，正在自动恢复…」
两态（spec §3.6；「登录窗已打开」复用既有「授权中…」态）。

**Files:**
- Modify: `src/aacc/i18n.py`、`src/aacc/gui.py`
- Test: `tests/test_qwen_quota_bar.py`

**Interfaces:**
- Produces: i18n 键 `qwen.syncing`、`qwen.auto_recovering`（双语）；
  `QwenQuotaBar.show_syncing()`、`QwenQuotaBar.show_auto_recovering()`；
  session `sync_started` → 服务 → GUI 显示同步态。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_qwen_quota_bar.py`，
  按该文件既有的构造/断言风格）

```python
def test_show_syncing_renders_sync_text():
    bar = QwenQuotaBar()
    bar.show_syncing()
    assert "正在同步" in bar.summary_label.text()
    assert bar.toolTip() != ""


def test_show_auto_recovering_renders_recovery_text():
    bar = QwenQuotaBar()
    bar.show_auto_recovering()
    assert "自动恢复" in bar.summary_label.text()
```

`tests/test_qwen_web_quota_service.py` 追加（与 Task 8 的
`test_service_forwards_auto_login_requested` 同款 FakeSession，
FakeSession 类体加 `sync_started = Signal()`）：

```python
def test_service_forwards_sync_started(qapp, tmp_path):
    del qapp
    session = FakeSession()  # 含 sync_started 信号的变体
    service = QwenWebQuotaService(tmp_path, session=session)
    received: list[None] = []
    service.sync_started.connect(lambda: received.append(None))

    session.sync_started.emit()

    assert received == [None]
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_qwen_quota_bar.py -q`
Expected: FAIL（`AttributeError: show_syncing`）

- [ ] **Step 3: 实现**

`i18n.py` 的 `CATALOGS[ZH_CN]` 里 `qwen.quota` 附近加：

```python
        "qwen.syncing": "正在同步日常 Chrome 会话…",
        "qwen.auto_recovering": "会话已过期，正在自动恢复…",
```

`CATALOGS[EN_US]` 对应位置加：

```python
        "qwen.syncing": "Syncing the daily Chrome session…",
        "qwen.auto_recovering": "Session expired; recovering automatically…",
```

`gui.py` `QwenQuotaBar.show_pending` 方法后加：

```python
    def show_syncing(self) -> None:
        self._display_state = "pending"
        self._last_error = None
        self.dot.setStyleSheet("color: #e5c07b;")
        self.summary_label.setText(
            f"{self.language_manager.text('qwen.quota')}\n"
            f"{self.language_manager.text('qwen.syncing')}"
        )
        self.setToolTip(self.language_manager.text("qwen.syncing"))

    def show_auto_recovering(self) -> None:
        self._display_state = "pending"
        self._last_error = None
        self.dot.setStyleSheet("color: #e5c07b;")
        self.summary_label.setText(
            f"{self.language_manager.text('qwen.quota')}\n"
            f"{self.language_manager.text('qwen.auto_recovering')}"
        )
        self.setToolTip(self.language_manager.text("qwen.auto_recovering"))
```

服务转发 `sync_started`（与 auto_login 同款 getattr 守卫）：
`qwen_web_quota_service.py` 信号区加 `sync_started = Signal()`，
`_connect_session` 加：

```python
        sync_started = getattr(session, "sync_started", None)
        if sync_started is not None:
            sync_started.connect(self.sync_started.emit)
```

GUI 接线块加：

```python
            self.qwen_web_quota_service.sync_started.connect(self._on_qwen_sync_started)
```

新增处理方法（`_on_qwen_auto_login_requested` 旁）：

```python
    def _on_qwen_sync_started(self) -> None:
        if self.qwen_quota_bar is not None:
            self.qwen_quota_bar.show_syncing()
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_qwen_quota_bar.py tests/test_qwen_web_quota_service.py -q`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/aacc/i18n.py src/aacc/gui.py src/aacc/qwen_web_quota_service.py tests/test_qwen_quota_bar.py
git commit -m "feat: bilingual quota bar states for session sync and auto recovery"
```

---

### Task 10: 全量门槛验证

**Files:** 无新增（可能含微调）

- [ ] **Step 1: 静态与格式**

Run: `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/mypy src/aacc`
Expected: 全过；有违例就地修正（保持既有风格）

- [ ] **Step 2: 全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全过

- [ ] **Step 3: diff-cover 改动行覆盖率（模拟 CI）**

Run: `.venv/bin/python -m pytest -q --cov=src/aacc --cov-report=xml && .venv/bin/diff-cover coverage.xml --compare-branch=origin/main --fail-under=90`
Expected: ≥90%；不足则给薄弱分支补单测（重点：`_recover_expired_session`
各分支、`_on_operation_finished` 同步回落分支、`_maybe_request_auto_login`）

- [ ] **Step 4: 如有修正，提交**

```bash
git add -u && git commit -m "test: cover sync-first recovery branches"
```

---

### Task 11: 版本号、双语 CHANGELOG、release notes

**Files:**
- Modify: `src/aacc/__init__.py`、`pyproject.toml`、`uv.lock`、
  `CHANGELOG.md`、`CHANGELOG.zh-CN.md`
- Create: `docs/release-notes-1.4.6rc1.md`

- [ ] **Step 1: 版本三件套（顺序不可乱）**

`src/aacc/__init__.py`：`__version__ = "1.4.6rc1"`
`pyproject.toml`：`version = "1.4.6rc1"`
然后：

```bash
cd /Users/zhangboqian/Desktop/codelight && uv lock
```

- [ ] **Step 2: 双语 CHANGELOG**

两份 CHANGELOG 顶部（最新段之上）加新段，标题必须用 `public_version()`
输出：`## 1.4.6-rc.1`。内容中英对应，覆盖：同步优先登录、来源感知自愈
（不再误覆盖新登录缓存）、自动弹窗限频、登录窗居中+可见反检测、14 秒误关
修复、操作后零残留进程校验、额度条新状态文案。

- [ ] **Step 3: release notes**

Create `docs/release-notes-1.4.6rc1.md`（双语；对照
`docs/release-notes-1.4.5rc4.md` 的结构；含验证记录占位——构建后回填）。

- [ ] **Step 4: packaging 测试确认一致**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -q`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/aacc/__init__.py pyproject.toml uv.lock CHANGELOG.md CHANGELOG.zh-CN.md docs/release-notes-1.4.6rc1.md
git commit -m "chore: release 1.4.6rc1 with sync-first Qwen quota recovery"
```

---

### Task 12: 构建、安装、真机冒烟

- [ ] **Step 1: 构建**

```bash
cd /Users/zhangboqian/Desktop/codelight && scripts/build_app.sh
```

- [ ] **Step 2: 安装并启动**

```bash
scripts/install.sh
```

- [ ] **Step 3: 冒烟核对（对应 spec §6）**

1. 日常 Chrome 已登录百炼时点「授权」：应不弹登录窗、~60 秒内出额度
   （日志出现 `sync login recopying`）；
2. 面板显示「正在同步日常 Chrome 会话…」过渡态；
3. 登录窗弹出时位于屏幕正中（可临时把日常 Chrome 登出百炼制造兜底路径）；
4. 登录成功后 `ps aux | grep qwen-chrome-profile` 应在 10 秒内零结果；
5. 观察日志不再出现「登录后几分钟缓存被重复制覆盖」。

- [ ] **Step 4: 把验证结果回填到 release notes 与 CHANGELOG，提交**

```bash
git add docs/release-notes-1.4.6rc1.md CHANGELOG.md CHANGELOG.zh-CN.md
git commit -m "docs: record 1.4.6rc1 local verification results"
```

---

## 真机探针（代码合入后，按 spec §3.3；非 TDD，需用户配合）

前置：退出 AACC（避免 profile 竞争）；探针脚本沿用仓库根 `_qwen_probe.py`
风格（直 exec Chrome + CDP dump，跑前 `pgrep` 确认无 AACC 受管 Chrome，
跑完杀净受管 profile 进程）。

- **T1（手动登录耐久性）**：把受管 profile 改名隔离（`mv qwen-chrome-profile
  qwen-chrome-profile.pre-t1`）得到全新 profile → 用可见登录（已装反检测
  脚本）登录一次 → 每 5 分钟隐藏取数，记录首次出现横幅的时刻。
- **T2（并发假设）**：恢复/重建同步会话后，请用户**退出日常 Chrome** →
  每 5 分钟取数测存活。
- **T3（对照）**：日常 Chrome 保持打开（复现 <15 分钟）。
- **T4（启动方式 A/B）**：若 T1–T3 均 <15 分钟——直接 exec
  `--no-startup-window`（8 月方式）vs 现行 NSWorkspace 隐藏实例，以及
  「常驻单实例」变体；同时在 Chrome 152 上复核隐藏实例无 Dock 磁贴。

判定：存活 ≥2 小时 = 找到可活配置，另开实施计划把结论并入主代码；
全败 = 接受降级稳态（spec §3.6），把结论写回设计文档状态行。

## 验收对照（spec §6 → 任务）

| spec 验收 | 覆盖任务 |
|---|---|
| 1 同步取数不弹窗 | Task 3/7 + Task 12 冒烟 |
| 2 居中+登录+零残留 | Task 5/6 + Task 12 冒烟 |
| 3 manual 横幅不立即重复制 | Task 2 单测 |
| 4 daily 横幅立即重复制 | Task 2 单测（含既有用例） |
| 5 自动弹窗限频+登出不弹 | Task 8 单测 |
| 6 跨域跳转不再 14 秒误关 | Task 4 单测 |
| 7 全门槛绿 | Task 10/11 |
