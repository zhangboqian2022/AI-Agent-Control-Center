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
# 构建 app（PyInstaller + ad-hoc 签名，版本号取 pyproject.toml）
scripts/build_app.sh
# 安装到 ~/Applications 并启动（SKIP_BUILD=1 复用已有 dist 只重装 runtime）
scripts/install.sh
```

## 架构要点

- `src/aacc/codex_discovery.py` / `kimi_discovery.py`：从 `~/.codex`、`~/.kimi-code`
  读取本地会话元数据，判定运行/等待/完成。
- `src/aacc/discovery_service.py`：`LocalDiscoveryService` 后台轮询。
  核心语义：manual/retained/muted/auto-active 四个集合；**运行中的任务每次
  轮询自动解除 muted**（muted 只对不活跃任务生效）。
- `src/aacc/gui.py`：`MainWindow` / `TaskCard`。QSettings 持久化：
  `codex_/kimi_ manual|retained|muted _tasks`、`custom_task_names`（自定义卡片名，
  JSON，按 task id 存储）。GUI 每次 refresh 从服务同步 retained 和 muted。
- `src/aacc/task_manager.py` + `persistence.py` + `state_machine.py`：任务状态机
  与 SQLite 持久化。
- `src/aacc/app.py`：组装 Runtime（两个 discovery 服务 + GUI + 可选 API server）。

## 约定

- 新功能/修 bug 先写失败测试（TDD），测试放 `tests/`。
- 文档中英双语（如 README.md / README.zh-CN.md）。
- 提交信息格式：`feat: ...` / `fix: ...` / `docs: ...`，英文。
- 不要提交 `.venv`、`dist/`、`build/`、缓存目录；送审副本用
  `git archive HEAD | tar -x -C <目标目录>` 导出。
- `scripts/install.sh` 的 wheel 版本用 `uv version --short` 动态获取，
  不要硬编码版本号。

## 当前进度（2026-07-20）

- `main` @ `ff95347`：1.3.0-rc.4 已发布（tag `v1.3.0-rc.4` + GitHub
  Prerelease 附 DMG）。内容：Kimi wire 三态反向扫描 + 隐私哨兵测试、
  版本/文档统一 + 链接一致性测试、install/uninstall 安全修复、GUI 订阅
  Kimi health、SECURITY.md 修正、Event Tap 测试参数化、CI 工作流。
- 已部署 rc.4：`~/Applications/AACC.app`（运行中）与 `/Applications/AACC.app`，
  健康接口返回 1.3.0rc4。DMG：`~/Desktop/AACC-1.3.0-rc.4.dmg`，
  SHA-256 `ba940a28c4ea2ad5322441be8e4df8a55d40d69b2a6b612fcc432d8b9a373567`。
- 送审副本：`~/Desktop/summit01`（rc.3 时的 git archive；如需 rc.4 要重新导出）。
- 新工作直接在 main 上开新分支。
