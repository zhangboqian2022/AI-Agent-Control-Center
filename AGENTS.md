# AGENTS.md

给 AI 助手的工作记忆文件：项目怎么构建测试、关键约定、当前进度。

## 项目概览

AACC（AI Agent Control Center）：macOS 菜单栏面板应用，监控本机运行的
Agent CLI 任务（Codex、Kimi Code），支持状态展示、窗口聚焦、按键/语音注入。
Python 3.12+ / PySide6，src 布局，包名 `aacc`。

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

## 约定

- 新功能/修 bug 先写失败测试（TDD），测试放 `tests/`。
- 文档中英双语（如 README.md / README.zh-CN.md）。
- 提交信息格式：`feat: ...` / `fix: ...` / `docs: ...`，英文。
- 不要提交 `.venv`、`dist/`、`build/`、缓存目录；送审副本用
  `git archive HEAD | tar -x -C <目标目录>` 导出。
- `scripts/install.sh` 的 wheel 版本用 `uv version --short` 动态获取，
  不要硬编码版本号。

## 当前进度（2026-07-22）

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
