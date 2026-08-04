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

## 当前进度（2026-08-04）

- **1.4.5-rc.2**（feat 分支 `feat/qwen-quota`，未发布，未合并 main）。
  rc.1 实测暴露的四处问题已修复：
  1. **Qwen 授权浏览器改走真实 Chrome（CDP）**：阿里云 RAM 登录需
     新窗口/跨域导航，QtWebView 无 `createWindow` 弹窗被丢弃。沿用
     Windows Edge CDP 范式新增 `qwen_chrome_cdp.py` / `qwen_chrome_session.py`：
     darwin 且装有 Chrome 时 service 选 Chrome 会话（可见窗口登录、
     headless 定时刷新，cookie 在 `qwen-chrome-profile/`，登录态持久化
     `qwen-web-session-state.json`）；未装 Chrome 回退原生 QtWebView 会话
     （`qwen_web_session.py`，同为 Windows native 路径）。不打包浏览器内核。
  2. **未登录误判修复**：匿名页「5 小时/7 天」介绍文案曾被当成额度数据，
     自动关登录框显示假 0%；现在提取上抛原始文本片段，无百分比即
     `unauthorized`，Python 解析器（`qwen_web_quota.py`）要求百分比存在。
  3. **小数百分比**：0.04% 不再被抹成 0%（`QuotaDetail.percentage`
     int → float | None，GUI `format_quota_percentage`）；重置倒计时按窗口
     切片防串窗。
  4. **qwen/opencode refresh 整页重载**：此前只重读旧 DOM，5 分钟轮询数值
     恒定；现在 `_reload_workspace_url()` 后 load 完成再提脚本。kimi 不变。
  另修复 service 在 win32 导入不存在的 `aacc.qwen_edge_session`（回退 native）；
  `websocket-client` 依赖改为跨平台（macOS CDP 传输）。
- `config.app.qwen_quota_enabled`（默认 True）+ `qwen_workspace_url`
  （默认百炼 personal 页，host 白名单校验）不变。
- **待用户实测**：Chrome（CDP）窗口内完成阿里云 RAM 登录、0.04% 显示、
  5 分钟刷新见新值；若提取正则与真实渲染文字不符，看 `Qwen quota raw=...`
  日志（现为原始文本片段）继续收紧。
- Windows Edge CDP 专属会话仍未实现（native 回退）。
- 本机全量测试绿、ruff、format、mypy 全绿。
- 不宣称：消费级 Windows 10/11 真机验证。
