# AGENTS.md

给 AI 助手的工作记忆文件：项目怎么构建测试、关键约定、当前进度。

## 项目概览

AACC（AI Agent Control Center）：macOS 菜单栏 / Windows 托盘面板应用，
监控本机运行的 Agent CLI 任务（Codex、Kimi Code），支持状态展示、窗口聚焦、
按键/语音注入。Python 3.12+ / PySide6，src 布局，包名 `aacc`，uv 管理依赖。

## 常用命令

```bash
# 环境准备（与 CI 完全一致）
uv sync --locked --extra dev
# 测试（tests/conftest.py 已自动设 QT_QPA_PLATFORM=offscreen，直接跑即可）
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_kimi_quota.py -q            # 单个文件
.venv/bin/python -m pytest tests/test_kimi_quota.py::test_x -q    # 单个用例
# 改动后必须全过（与 CI 一致）
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/aacc
# 构建 macOS app（PyInstaller；钥匙串存在 "AACC Local Development" 自签名证书时
# 自动用它签名——稳定身份让辅助功能授权跨构建保持；否则回退 ad-hoc，
# 也可用 AACC_CODESIGN_IDENTITY 显式指定。版本号取 pyproject.toml）
scripts/build_app.sh
# 安装到 ~/Applications 并启动（SKIP_BUILD=1 复用已有 dist 只重装 runtime）
scripts/install.sh
# Windows onedir + 原生 broker / 当前用户 Setup（须在 Windows 上跑）
scripts\build_windows.ps1
scripts\build_windows_installer.ps1
```

CI 是 `.github/workflows/ci.yml`：macOS + Windows Server 2022/2025 三腿都跑
ruff/mypy/pytest（`QT_QPA_PLATFORM=offscreen`）；mac 腿另跑 diff-cover
**改动行覆盖率 ≥90%**（新代码不带测试必红）。pip-audit 出 JSON 报告并上传，
不设 allow-failure。

## 架构要点

- 发现源：`codex_discovery.py`（`~/.codex`）、`kimi_discovery.py`（`~/.kimi-code`）、
  `kimi_desktop_discovery.py`（Kimi 桌面版 daimon 的 `conversations.sqlite`，只读；
  **Chat 标签页是网页套壳无本地记录，无法监控，仅 Agent/Work 会写库**）。
- `discovery_service.py`：`LocalDiscoveryService` / `KimiDesktopDiscoveryService`
  后台轮询。核心语义：manual/retained/muted/auto-active 四个集合；**运行中的任务
  每次轮询自动解除 muted**（muted 只对不活跃任务生效）。
- `gui.py`：`MainWindow` / `TaskCard`。QSettings("AACC","AACC") 持久化
  `codex_/kimi_ manual|retained|muted _tasks`、`custom_task_names`（自定义卡片名，
  JSON，按 task id 存储）。GUI 每次 refresh 从服务同步 retained 和 muted。
- `task_manager.py` + `persistence.py` + `state_machine.py`：任务状态机与 SQLite。
- 额度栈：`kimi_oauth.py`（设备授权）、`kimi_web_session.py`（macOS Qt WebView）、
  `kimi_edge_session.py` + `kimi_edge_cdp.py`（Windows Edge 专用 profile +
  CDP/WebSocket；Cookie 与 token 留在页面上下文，Python 只收归一化数值）、
  `kimi_web_quota_service.py` / `codex_quota_service.py`（60s 轮询）。Codex 只
  接受 10080 分钟周窗口，忽略旧 300 分钟窗口。
- `app.py`：组装 Runtime（三个发现服务 + GUI + 可选 API server）。
- 平台抽象：`win32.py`（ctypes Win32 绑定）、`automation_windows.py` /
  `hotkeys_windows.py`；`automation.py::create_automation` 与 app.py 热键装配按
  `sys.platform` 工厂分发——macOS 走 AppleScript/Quartz，Windows 走窗口标题
  匹配 + Win+H 语音，无需辅助功能授权。
- Windows 敏感目录/文件用 pywin32 原生精确 DACL（`file_security_windows.py`），
  冻结运行时不依赖 whoami/icacls/taskkill；Codex 只读 app-server 由旁置
  `/MT` 静态 `aacc-spawn` broker 与 Job Object 管理。

## 约定

- 新功能/修 bug 先写失败测试（TDD），测试放 `tests/`。
- 文档中英双语成对（README、CHANGELOG、KNOWN_LIMITATIONS、release-notes 等）。
- 提交信息格式：`feat: ...` / `fix: ...` / `docs: ...`，英文；新工作直接在
  main 上开新分支。
- **版本号改动顺序**（`tests/test_packaging.py` 强制一致）：
  `src/aacc/__init__.py::__version__` → `pyproject.toml` → **`uv lock`**（漏同步
  uv.lock 会让 CI `uv sync --locked` 全红）→ 双语 CHANGELOG 最新段标题用
  `public_version()` → `docs/release-notes-<__version__>.md` 存在。
  `__version__` 用 PEP 440（`1.4.3rc2`），`public_version()` 输出 dash 形式
  （`1.4.3-rc.2`）；Windows Setup 产物名用 PEP 440（`AACC-1.4.3rc2-Setup.exe`）。
  `scripts/verify_release.sh` 只服务正式版（显式拒绝 prerelease），rc 验收用
  `gh release view` + 下载回环手动完成。
- **测试时间造假必须相对当前时刻回拨**（`time.monotonic() - INTERVAL - 1`），
  不能写 0 或固定时间点：`monotonic()` 是开机秒数，CI 全新 VM 开机不足 1 小时时
  绝对时间断言必红（曾致 CI 全红本地全绿的"幽灵"失败）。
- 签名：自签名 "AACC Local Development" 让辅助功能授权（TCC）跨构建保持；
  ad-hoc 每次构建哈希都变会失效；自签名开 hardened runtime 启动即崩，勿开。
  Gatekeeper 警告仍需付费 Developer ID + 公证才能消除（未购买）。
- 不要提交 `.venv`、`dist/`、`build/`、缓存目录；送审副本用
  `git archive HEAD | tar -x -C <目标目录>` 导出并剔除 `docs/superpowers` 与
  `tests/fixtures`（HEAD 之后有未提交改动时用 `git ls-files` 按工作区导出）。
- `scripts/install.sh` 的 wheel 版本用 `uv version --short` 动态获取，不硬编码。
- 证据边界：托管 Windows Server CI ≠ 消费级 Windows 10/11 真机验证，**不得宣称
  真机验证**（KNOWN_LIMITATIONS 双语有此声明，`test_packaging.py` 校验条目数对齐）。

## 当前进度（2026-08-05）

- **Qwen 额度 hidden 有头刷新落地并真机验证通过（macOS）**：headless UA 被
  阿里云 baxia 风控按指纹作废同一票据 → 定时刷新改为「有头但完全隐藏」的
  Chrome：`open -g -n -b com.google.Chrome` 不抢焦点启动（fire-and-forget；
  `_DetachedQwenChromeHandle` 掩盖 open 的 0 退出码，判死改走 DevTools
  endpoint + 按 profile 进程探测）、按 `--user-data-dir=` 精确 argv 找/杀
  进程（名字过滤排除 `open` 启动器）+ 启动前僵尸清理、CDP stealth 注入
  （Page.enable → addScript 掩盖 `navigator.webdriver`/屏外负坐标 →
  Emulation.setDeviceMetricsOverride → Page.reload → setWindowBounds 推出
  屏外；注入失败不致命，继续走原提取路径）。**实测坑**：500×375 小窗触发
  百炼「移动端体验」拦截页（额度 DOM 完全不出现 → DOM_TIMEOUT），改
  `--window-size=1100,700` + 视口覆盖双保险后 T+2s 渲染出额度。刷新间隔
  5→15 分钟（`QWEN_WEB_QUOTA_INTERVAL_MS=900_000`）；hidden 模式非 darwin
  fail-closed（不回退 headless）；`--disable-extensions` 防 profile 副本按
  Preferences 回装 ~241MB 日常扩展。新构建两轮连续周期（启动即刷 + 15 分钟
  定时）真机取数成功、无告警、进程干净收尾、不抢焦点。会话副本 ~5.5h 过期
  与风控升级风险仍在（服务端行为；兜底=未登录检测，栏如实显示「点击授权」）。
- **1.4.5-rc.2**（feat 分支 `feat/qwen-quota`，未发布，未合并 main）。
  本轮在本机实测驱动下完成三块：
  1. **Qwen 额度对齐真实控制台**：百炼 token-plan 页实为个人版（5小时限额/
     7天限额，`X%已用` + `将于 <绝对时间> 重置刷新`）+ 团队版（路由 hash
     `token-plan/enterprise`，仅一个总额度，`重置时间 <绝对时间>`）；每个表盘
     后跟 0%/50%/90%/100% 刻度噪声。提取 JS 改两阶段（先等个人版 `%已用`，
     再跳 enterprise 抓总额度，团队版可 null）；payload 键改
     personalFiveHourText/personalWeeklyText/teamTotalText；解析器支持
     绝对重置时间（本地时区，此前只认相对倒计时正是"重置不显示"根因）、
     按"刻度序列之前"位置剔除噪声；QwenQuotaBar 三行（5 小时/7 天/团队）。
  2. **登录风控绕过（本机方案，未产品化）**：专属空白 profile 反复被阿里云
     baxia 拦截；已按用户批准把日常 Chrome 会话最小集复制进托管
     `qwen-chrome-profile/`（Cookies 用 `sqlite3 .backup` 在线备份、Local
     State、Preferences、Local/Session Storage；不复制 Login Data 密码库；
     目录 700；旧空白 profile 隔离为 `.qwen-chrome-profile.pre-dailycopy-*`）。
     副本继承受信会话后 headless 刷新免验证直取数。**待产品化**：把复制流
     程纳入 qwen_chrome_session 登录流（需用户同意 UI）；无团队版订阅账户
     的降级未实测。
  3. **传输层关键修复**：共享 WebSocket `recv` 5s 超时把两阶段 evaluate
     拦腰截断、被截的页内异步脚本并发重跑互相干扰 → headless refresh 永不
     收敛（此前 17~18s 必败的另一根因是复用 Edge 的 15s 启动预算）。现在
     Qwen 页面 socket 90s（`QWEN_PAGE_SOCKET_TIMEOUT_SECONDS`，Kimi Edge
     路径不变）、`QWEN_STARTUP_TIMEOUT_SECONDS=90`；冷启动实测 ~29s 端到端
     成功。教训：长 JS 等待必须给足 socket 预算，别让 transport 截断重试。
- **Qwen Code 任务发现（运行监控）**：新增 `qwen_discovery.py`——会话在
  `~/.qwen/projects/<路径编码>/chats/<uuid>.jsonl`；`<uuid>.runtime.json`
  给 pid/work_dir（退出后残留，必须 pid 存活判定 + 回读 cmdline 含
  `qwen-code` 防 PID 复用——进程名是 node 不是 qwen）；无 runtime 时按
  jsonl mtime 90s 窗。→ `QwenDiscoveryService`（state_source=qwen_local）→
  app.py 装配 → gui.py `qwen_manual/retained/muted_tasks`、`qwen:` 前缀过滤、
  任务选择对话框、`agent_visibility_migrated_v4`、agent type `qwen_cli`。
- **input/output/cache 用量行推广到全部 provider**：TaskCard 不再限
  kimi_code；Codex 读 rollout `event_msg.token_count` 的累计
  `total_token_usage`（input 已含 cached，拆出 cache_read/write）；OpenCode
  SUM `message.data.tokens`（assistant）；Qwen 按 sessionId 聚合
  `~/.qwen/usage_record.jsonl` 的 models 计数（每回合落一条，回合未结束无行
  属正常）。work_dir 显示同步取消 agent 类型限制。
- **额度展板开关**：设置内 Codex/Kimi/OpenCode/Qwen 四开关，QSettings
  `visible_quota_panels`。
- 全量 1491 测试 + ruff + format + mypy 全绿，diff-cover 改动行覆盖率 92%
  （CI 门槛 ≥90%）；已构建安装本机实测（Qwen 任务 RUNNING/STOPPED 正常显示、
  Qwen 额度 hidden 刷新两轮周期成功）。
- Windows Edge CDP 专属会话仍未实现（native 回退）。
- 不宣称：消费级 Windows 10/11 真机验证。
