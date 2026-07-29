# AGENTS.md

给 AI 助手的工作记忆文件：项目怎么构建测试、关键约定、当前进度。

## 项目概览

AACC（AI Agent Control Center）：macOS 菜单栏 / Windows 托盘面板应用，
监控本机运行的 Agent CLI 任务（Codex、Kimi Code），支持状态展示、窗口聚焦、
按键/语音注入。Python 3.12+ / PySide6，src 布局，包名 `aacc`。

## 常用命令

```bash
# 测试（GUI 测试需要 offscreen）
.venv/bin/python -m pytest -q
# lint 与类型检查（改动后必须都过）
.venv/bin/ruff check src tests
.venv/bin/mypy src/aacc
# 构建 app（PyInstaller；钥匙串里存在 "AACC Local Development" 自签名证书时
# 自动用它签名——稳定身份让辅助功能授权跨构建保持；否则回退 ad-hoc，
# 也可用 AACC_CODESIGN_IDENTITY 显式指定。版本号取 pyproject.toml）
scripts/build_app.sh
# 安装到 ~/Applications 并启动（SKIP_BUILD=1 复用已有 dist 只重装 runtime）
scripts/install.sh
# Windows onedir + 原生 broker
scripts\build_windows.ps1
# Windows 当前用户 Setup（输出 AACC-<version>-Setup.exe + .sha256）
scripts\build_windows_installer.ps1
```

## 架构要点

- `src/aacc/codex_discovery.py` / `kimi_discovery.py`：从 `~/.codex`、`~/.kimi-code`
  读取本地会话元数据，判定运行/等待/完成。
- `src/aacc/kimi_desktop_discovery.py`：第三发现源，读取 Kimi 桌面版
  daimon 的 sqlite 会话目录（只读），Agent 任务状态复用 kimi 的回合判定。
- `src/aacc/discovery_service.py`：`LocalDiscoveryService` /
  `KimiDesktopDiscoveryService` 后台轮询。
  核心语义：manual/retained/muted/auto-active 四个集合；**运行中的任务每次
  轮询自动解除 muted**（muted 只对不活跃任务生效）。
- `src/aacc/gui.py`：`MainWindow` / `TaskCard`。QSettings 持久化：
  `codex_/kimi_ manual|retained|muted _tasks`、`custom_task_names`（自定义卡片名，
  JSON，按 task id 存储）。GUI 每次 refresh 从服务同步 retained 和 muted。
- `src/aacc/task_manager.py` + `persistence.py` + `state_machine.py`：任务状态机
  与 SQLite 持久化。
- `src/aacc/app.py`：组装 Runtime（三个 discovery 服务 + GUI + 可选 API server）。
- 平台抽象：`src/aacc/win32.py`（ctypes Win32 绑定）、
  `automation_windows.py` / `hotkeys_windows.py`（Windows 桌面自动化与全局
  热键）；`automation.py` 的 `create_automation` 与 `app.py` 的热键装配按
  `sys.platform` 工厂分发——macOS 走 AppleScript/Quartz，Windows 走窗口
  标题匹配 + Win+H 语音，无需辅助功能授权。

## 约定

- 新功能/修 bug 先写失败测试（TDD），测试放 `tests/`。
- 文档中英双语（如 README.md / README.zh-CN.md）。
- 提交信息格式：`feat: ...` / `fix: ...` / `docs: ...`，英文。
- 不要提交 `.venv`、`dist/`、`build/`、缓存目录；送审副本用
  `git archive HEAD | tar -x -C <目标目录>` 导出。
- `scripts/install.sh` 的 wheel 版本用 `uv version --short` 动态获取，
  不要硬编码版本号。

## 当前进度（2026-07-29）

- `codex/codex-dynamic-quota-tray-exit@5f7966b`：Windows Codex 实时额度源
  改为每次 60 秒轮询或点击额度条时重新发现；AACC 先启动、之后再打开或重启
  ChatGPT/Codex 也能恢复只读 `account/rateLimits/read`，没有安全实时来源时
  继续使用有界本地会话元数据。Windows 还会在受信 OpenAI/ChatGPT 安装路径中
  发现正在运行的 `codex.exe`，拒绝任意同名进程。
- 托盘右键不再触发面板显隐，菜单由主窗口持有避免被回收；托盘“退出 AACC”
  与头部电源按钮都会隐藏托盘、关闭窗口并进入完整 runtime cleanup。最新本机
  948 passed、7 skipped，ruff、format、mypy 全绿；hosted 全量运行
  `https://github.com/zhangboqian2022/AI-Agent-Control-Center/actions/runs/30436090922`
  在 macOS、Windows Server 2022/2025 全绿，包含冻结包、broker、Setup、
  安装/重装/卸载、ACL、进程清理和产物严格校验。
- `main@4f1a7f2`：Windows Kimi 登录已从不稳定的内嵌
  WebView2 改为 AACC 管理的 Microsoft Edge 专用会话。配置目录固定为
  `%LOCALAPPDATA%\AACC\kimi-edge-profile`，使用原生精确受保护 DACL；正常退出
  AACC 或重启电脑均保留登录态，只有人工退出、Kimi 鉴权失效或安全故障才撤销
  复用。每五分钟通过 loopback 随机端口 CDP/WebSocket 刷新 5H/WEEK/MONTH，
  Cookie 与 access token 始终留在页面上下文，Python 只接收归一化百分比、
  重置时间和会员级别。Windows 包不再依赖 WebView2 Runtime。
- 最新加固提交序列已关闭：登录/退出 worker 重叠、Edge 子进程树回收、
  不安全 profile fail-closed、原始会员文档越界、CDP target 启动竞态；并新增
  安装后 `AACC.exe --smoke-edge-cdp` 产品冒烟，验证真实 Edge/CDP/WebSocket、
  profile 精确 ACL 和无孤儿 Edge。macOS 继续使用原生 Qt WebView。当前本机
  930 passed、7 skipped，ruff、format、mypy、`uv lock --check` 全绿。专家
  终审无 Critical/Important，结论 APPROVED。
- `main@4f1a7f2` 的 hosted 全量运行
  `https://github.com/zhangboqian2022/AI-Agent-Control-Center/actions/runs/30424147975`
  已在 macOS、Windows Server 2022/2025 全绿；包含 Windows 双版本完整 pytest、
  依赖审计、冻结包与 Setup 构建、真实安装后 Edge/CDP/WebSocket、profile 精确
  ACL、无孤儿 Edge、broker、重装、锁目标拒绝、卸载、进程清理及产物严格校验。
- `main`：**1.4.2 已按维护者决定正式发布**。主 Windows
  产物为 `AACC-1.4.2-Setup.exe`，当前用户、
  无需提权，默认安装到 `%LocalAppData%\Programs\AACC`；开始菜单必建、桌面
  快捷方式可选、不添加启动项，升级/卸载保留 `%APPDATA%\AACC`。
  正式 DMG SHA-256 为
  `aa60665e9808e3c0067b2db761893acb2911d1f2600085821cb46c1ef7e03822`；
  本机构建使用 ad-hoc 签名、未经过 Apple 公证。
  GitHub Release：
  `https://github.com/zhangboqian2022/AI-Agent-Control-Center/releases/tag/v1.4.2`
  （Latest、非草稿、非预发布）；正式提交 `c0aaa07` 的 CI
  `https://github.com/zhangboqian2022/AI-Agent-Control-Center/actions/runs/30440258506`
  全绿，四项 Release 资产的名称、大小、摘要与下载 URL 已通过发布后校验。
- 已移除冻结运行时对 `whoami.exe`、`icacls.exe`、`taskkill.exe` 的依赖：
  Windows 敏感目录/文件使用 pywin32 原生精确受保护 DACL（当前用户、System、
  Administrators），Codex 只读 app-server 由旁置 `/MT` 静态
  `aacc-spawn.exe` 与 Job Object 管理。SQLite database/WAL/SHM 也走同一保护
  门面。
- Setup 在升级/卸载前使用 `--shutdown-for-update` 请求 20 秒内优雅退出；
  `main@b7be123` 的 hosted 全量运行
  `https://github.com/zhangboqian2022/AI-Agent-Control-Center/actions/runs/30412622959`
  已在 macOS、Windows Server 2022/2025 全绿，包括冻结包启动、broker、安装、
  重装、写入前锁目标拒绝、卸载、ACL、进程清理与原生 WebView2 产品冒烟。
  Setup、SHA-256 与便携 ZIP 通过严格内容校验并上传；本机同一提交 885 passed、
  7 skipped，ruff、format、mypy 全绿。该证据不替代 Windows 10/11 真机门禁。
- 最新 Codex 同步/退出与额度保留修复为 `5f7966b`，hosted 全量运行
  `https://github.com/zhangboqian2022/AI-Agent-Control-Center/actions/runs/30436090922`
  全绿；正式 Setup SHA-256
  `139f45214362dd084aebe4b833d80dd344703491fbaf1f400482107759f4662a`，
  同名 `.sha256` 已同步。
- 额度最终布局：Codex 仅一行 `WEEK`；Kimi 为 `5H`、`WEEK`、`MONTH`，行内
  显示百分比、进度条和完整本地重置日期时间。Kimi 会员网页会话在 AACC 本地
  缓存到明确退出，并每五分钟一起刷新三窗口；真实 `0%` 不得显示为 `--`，
  临时刷新失败保留最后一次可验证值并标记过期；额度元数据查询不消耗模型 Token。
- 正式说明明确保留证据边界：托管 Windows Server 自动化不等于消费级
  Windows 10/11 标准用户、另一无特权账户拒读、真实 Kimi/Codex、SmartScreen、
  托盘、聚焦/热键与长时间运行的人工验证；这些项目未被虚构为已完成。

### 历史基线（2026-07-26）

- `main`：**Windows 移植已合并（merge `a47196e`，未发版）**。另含
  `1eb8a58` 修复：取消的回合（turn.cancel 无 usage.record）与从未有回合
  事件的 kimi 会话不再误报"正在运行"（曾致 1 个终端显示 3 个运行中）。
  移植内容（8 个 SDD 任务，分支 feat/windows-port 已删）：
  代码层（Task 1–7）：平台化发现源/配置/路径、`win32.py` +
  `automation_windows.py` + `hotkeys_windows.py` 工厂分发（聚焦=窗口标题
  匹配，语音=Win+H，无需辅助功能授权）。收尾（Task 8）：
  `AACC-windows.spec` + `scripts/build_windows.ps1`（windowed 单目录
  `dist/AACC/AACC.exe`，未签名有 SmartScreen 提示）；CI matrix 增加
  `windows-latest`：两条腿都跑 ruff check / ruff format --check / pytest，
  mypy 因跨平台 typeshed 差异保持 `macos-latest` 单点（diff-cover /
  pip-audit / upload 同为 mac 单点）；POSIX 权限位断言的测试以
  `skipif(sys.platform == "win32")` 跳过（Windows 不强制权限位），
  `os.fchmod`（Unix-only）在 `save_config`/`save_credentials` 内按
  `sys.platform != "win32"` 守卫（Windows ACL 默认仅当前用户可读，
  规格已认可此降级）；pyproject 描述改跨平台；README 双语 Windows 章节、
  KNOWN_LIMITATIONS 双语 5 条 Windows 差异、
  `docs/windows-verification-checklist` 双语冒烟清单。475 测试 + ruff +
  mypy strict 全绿；Windows 真机构建与冒烟未执行（按清单待验）。
  **已推送 origin/main（fb20bc5）；`wincode/` 送审副本已建**（HEAD
  `git archive` 快照，剔除 docs/superpowers 与 tests/fixtures，已入
  .gitignore；代码再改需重新导出：`git archive HEAD | tar -x -C wincode`）。
  **明日接续点**：① Windows 真机跑 `scripts\build_windows.ps1` 构建 +
  按 `docs/windows-verification-checklist.zh-CN.md` 逐项冒烟；
  ② 遗留 Minor 排期（真机里程碑）：SetForegroundWindow 前景锁
  workaround、user32 argtypes、_send_input GetLastError 诊断、win32
  指纹降级对增长文件的缓存失效（性能）、msvcrt type: ignore 在真
  Windows mypy 下需移除；③ 若冒烟通过可考虑发 Windows 版 release。
- `main`：**1.4.1 正式版已发布**（tag `v1.4.1` + GitHub Release（Latest，非
  Prerelease）附 DMG 与 `.sha256`，SHA-256
  `fda8131f359f55dccca3a64a125aaf59377322a479d4f9934db15e53d2713d94`）。
  本版完成 Kimi OAuth/轮询凭据 generation + fingerprint 条件写入、pending
  隔离、所有 HTTP Client 确定关闭、OAuth 全关闭路径取消、
  UNKNOWN/PARTIAL/STALE 诚实展示、Kimi Desktop SQLite 5 秒 busy timeout，
  以及 Codex 只读周额度条。Codex 额度**只接受 10080 分钟周窗口**，明确
  忽略旧 300 分钟窗口，不显示五小时字段；本机真实元数据读取已验证。
  GitHub Actions 的全量测试、ruff format、mypy strict、changed-line
  覆盖率 ≥90%、非空阻塞式 pip-audit JSON 报告均通过；
  `scripts/verify_release.sh 1.4.1` 已确认正式 Release 状态、资产与下载 URL。
  1.4.1 App 已覆盖安装到 `~/Applications/AACC.app` 并通过
  `codesign --verify --deep --strict`。macOS 13/15 真机矩阵仍未签字，
  不宣称已经覆盖这些组合。
- **已修复：CI 全红但本地全绿的"幽灵"失败**（`67b83be`）。
  `test_expired_history_cleanup_is_throttled_between_updates` 把
  `_last_history_cleanup` 置 `0.0` 来伪造"距上次清理已过 1 小时"，而
  `time.monotonic()` 是**开机秒数**——开机不足 1 小时的机器上
  `monotonic() - 0.0 < 3600`，节流不触发、测试必败。CI 运行器每次都是
  全新 VM（开机几分钟）所以 GitHub 上全红并触发失败通知邮件；本机开机
  超过 1 小时就通过，表现为间歇性。修复：测试改为
  `time.monotonic() - HISTORY_CLEANUP_INTERVAL_SECONDS - 1`（相对当前
  时间回拨），与机器 uptime 无关。
  **教训（后续开发遵守）**：凡涉及 `time.monotonic()`/`time.time()` 的
  测试，时间造假必须相对"当前时刻"偏移，不能写绝对值（0、固定时间点）；
  依赖开机时长、系统时钟、时区的断言都是 CI 地雷。GitHub Actions 失败
  邮件是推送给 commit 作者的默认通知（Settings → Notifications → Actions
  可调）。
- `main`：**1.4.0 正式版已发布**（tag `v1.4.0` + GitHub Release（Latest，非
  Prerelease）附 DMG 与 `.sha256`，SHA-256
  `0974f394fbc1272100b51c0352e473c0a747d7b40ec2e9f09508e5f4d544c909`）。
  内容即三合一整合（M1 额度监控 + M2 会话指标 + rc.2 的 5h 窗口解析修复）。
  本机 `~/Applications/AACC.app` 已对齐 1.4.0。送审副本为仓库内 `code/`
  子目录（`git archive HEAD` 导出，剔除 `docs/superpowers/` 与
  `tests/fixtures/`，1.1MB；该目录已入 .gitignore）。
- `main`：**1.4.0-rc.2 已发布**（tag `v1.4.0-rc.2` + GitHub Prerelease 附 DMG 与
  `.sha256`，SHA-256 `3d8c5847404fbfb218fb91d4c3eda1cd1d4a10ae8b58a29573579d872ff595ed`）。
  rc.1 发布后因真机反馈发现 5h 额度解析 bug（API 窗口单位拼写
  `TIME_UNIT_MINUTE` 未被 `startswith("m")` 匹配，5h 恒显 0%；周额度 64%
  解析正常），TDD 修复后以 rc.2 替换（rc.1 发布与 tag 已删除）。
  本机 `~/Applications/AACC.app` 已对齐 1.4.0-rc.2。
- `main`：**三合一整合已合并（未发版，目标 1.4.0）**（merge `73a648e`，365 测试
  + ruff + mypy strict 全绿；15 个任务评审 + 全分支终审均通过）。
  - M1 Kimi 账户额度监控：`kimi_oauth.py`（官方 packages/oauth Device Flow
    移植，client_id 17e5f671，凭据存 AACC 配置目录 `kimi-credentials.json`
    0600，绝不碰 CLI 凭据）+ `kimi_quota.py`（`/coding/v1/usages`，宽松解析，
    加油包余额仅 ACTIVE/ENABLED 时取 amountLeft/1e8）+ `quota_service.py`
    （60s 轮询/30s TTL/single-flight 刷新）+ 面板顶部 QuotaBar + 设备授权
    对话框 + 设置页 API Key/退出登录。配置项 `app.kimi_quota_enabled`。
  - M2 会话 token 指标：`kimi_metrics.py`（kimi-code-monitor metrics.js
    移植）+ `kimi_wire_usage.py`（wire.jsonl 字节偏移增量尾随，截断重置、
    半行留待下轮）；Kimi 卡片新增 `↑输入 ↓输出 缓存% · tok/s` 行（仅
    累计非零时显示）。
  - M3 kimi web relay：spike 完成（协议 fixture 在 `tests/fixtures/kimi_web/`，
    结论 `docs/superpowers/specs/2026-07-24-kimi-web-relay-findings.md`），
    **决策：子系统 C 推迟到 post-1.4.0**，实施骨架
    `docs/superpowers/plans/2026-07-24-kimi-web-relay.md` 已备好
    （pending_interaction 在 `event.session.work_changed`，不在
    agent.status.updated）。
  - 合规：三方 MIT 来源（MoonshotAI/kimi-code、KimiCodeBar ©xifandev、
    kimi-code-monitor ©十叶）已在 `NOTICE` + 双语 README 致谢段声明。
  - 1.4.0-rc.2 跟进项（终审 Minor）：OAuth 对话框 X 关闭应触发取消；
    轮询 deadline 取 min(expires_in, 15min)；test_quota_bar 的
    QMouseEvent 弃用警告；quota_service 三项线程边界（见
    `.superpowers/sdd/progress.md` 清单）。
  - 发版前必做：真机冒烟——构建安装后点 QuotaBar 完成一次真实设备授权，
    确认额度渲染（M1 Task 7 Step 5 推迟项）。
- `main`：**1.3.3-rc.1 已发布**（tag `v1.3.3-rc.1` + GitHub Prerelease 附
  DMG 与 `.sha256`，SHA-256 `64e2f5d8288fe5d40a37cbc8cbf639a25b2468a208f067abfcb8cec9c5d4a43f`）。
  内容即第二轮评审接受项（下条）。本机 `~/Applications/AACC.app` 已对齐；
  送审副本 `~/Desktop/summit01` 已同步本版。
- 第二轮评审整改（1.3.3-rc.1）：
  **接受 7 条**：remove 卡片改单一分发入口+未知前缀记 ERROR（P1-8 防御
  部分，按实际代码结构修正——本无中央 dispatch）；`save_config` 拒绝
  符号链接父目录（P2-7）；进程存活探测改 PID 缓存 `CachedProcessAlive`
  （N-P2-5，新模块 `src/aacc/processes.py`）；CI 加 pip-audit allow-failure
  （N-P2-2）；AGENTS.md 发现服务数量更正（N-P2-1）；连接处注释+KNOWN_
  LIMITATIONS 澄清 `mode=ro` 刻意非 `immutable=1`（N-P2-4/6，**评审驳回
  存档中"源码已含 immutable=1"系事实错误，特此纠正**）；KNOWN_LIMITATIONS
  补 daimon 路径 TCC 预案（N-P2-7）。
  **排期不动**：BrandHandler 重构（1.4.0 立项决策）、gui.py 拆包（P2-11）、
  docs 归档（P2-12）、全局日志冷却（P2-13，按指纹冷却已限频）。
- `main`：**1.3.2 安全 hotfix 已发布**（tag `v1.3.2` + GitHub Release 附
  DMG 与 `.sha256` 资产，SHA-256 `bb3d49d5aea5c3e92c4f8e3ed806a035065202c8932a75ab953a388662928967`）。
  内容即评审接受项（见下）。本机 `~/Applications/AACC.app` 已对齐 1.3.2。
- 评审整改（1.3.2）：第三方评审
  （P0×1/P1×5/P2×8）逐条对照代码验证后：**接受 9 条已修**——示例配置
  公开占位 token 前缀拒识+置空（P0-1）；Agent 品牌隐藏持久化一次性
  迁移键（P1-1）；doctor 与 app 共用 `resolve_database_path`（P1-4）；
  reload-config 返回 501（P1-5）；历史清理节流+索引、卡片布局按需重建、
  订阅者异常记日志、token 轮换不自动写剪贴板、README 安装器措辞
  （P2-1/2/3/5/8）；补 1.3.1 双语测试报告（P1-2 部分）。
  **驳回 4 条**：CI 已存在（P1-3）；`release_env.sh` 已入库（P2-7）；
  规格无 immutable 声称（P2-9）；版本一致性测试已存在（P1-2 另一半）。
  P2-10 以手动上传 `.dmg.sha256` 资产了结（1.3.1/1.3.2 均已附）。
- `main`：**1.3.1 已发布**（tag `v1.3.1` + GitHub Release 附 DMG）。
  在 1.3.0 基础上：切换到任务时恢复目标应用已最小化的窗口（终端走
  AppleScript `set miniaturized of windows to false`，mac_app 焦点也从
  `open -b` 改为 AppleScript）；卡片右键菜单移除语音/按键注入项。
  1.3.0 正式版新增：Kimi Code 卡片显示工作目录名；
  面板最小化/隐藏后经托盘、Dock 图标或 Cmd-Tab 都能恢复；辅助功能授权
  5 秒内生效、热键免重启启停、引导弹窗可加"不再提示"；构建自动使用
  钥匙串里的稳定自签名 "AACC Local Development" 身份（TCC 授权跨构建
  保持；hardened runtime 仅限 Developer ID，自签名开了会启动即崩）。
- 1.3.1 DMG：`~/Desktop/AACC-1.3.1.dmg`，
  SHA-256 `c748a726441334ba24d3537050ce6a7c4b32fa176808910db9f516da8a231df9`。
- 签名背景：辅助功能授权按签名身份匹配，ad-hoc 每次构建哈希都变导致
  授权失效；稳定自签名解决本机与分发拷贝的重复授权，但 Gatekeeper
  "不明开发者"警告仍需付费 Developer ID + 公证才能消除（用户已知购买
  流程，暂未购买）。
- 已确认的数据源限制：Kimi Desktop 的 Chat 标签页是 kimi.com 网页套壳，
  会话在云端，本地 daimon 无记录，AACC 无法监控；仅 Agent/Work 标签页
  会话会写入 daimon `conversations.sqlite`。2026-07-22 已用数据级仿真
  端到端验证 Agent 会话"进行中→完成"显示链路正常。
- 送审副本：`~/Desktop/summit01`（rc.4 之后 HEAD `6369ba6` 的导出；如需
  1.3.0 要重新导出）。
- 新工作直接在 main 上开新分支。
