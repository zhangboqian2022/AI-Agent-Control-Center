# AI Agent Control Center（AACC）

> 面向本机 AI Coding Agent 的 macOS 桌面状态与控制中心。

[English README](README.md) · [下载 AACC 1.0.0](https://github.com/zhangboqian2022/AI-Agent-Control-Center/releases/download/v1.0.0/AACC-1.0.0.dmg) · [发布说明](https://github.com/zhangboqian2022/AI-Agent-Control-Center/releases/tag/v1.0.0) · [产品设计](docs/product-design.zh-CN.md)

AACC 是一个本机优先的 macOS 悬浮面板，用于查看你选择监控的 AI 编程任务。它从本机 Codex 元数据自动发现对话，让你筛选需要展示的任务，并通过醒目的大状态灯快速显示运行、等待、完成、告警、错误或未知状态。它还提供本地 API、`aacc` 命令行、`aacc-run` 生命周期包装器和可配置的 Agent Adapter。

## 核心能力

- **任务自主选择：** 只勾选想展示的 Codex 对话；未勾选任务不会显示，也不会被轮询监控。
- **醒目状态灯：** 通过大尺寸灯光快速识别任务状态，避免错过正在等待处理的任务。
- **本机优先：** 只读取判断状态所需的本机任务元数据，不上传对话内容。
- **可靠的完成判断：** 优先依据 Codex `task_started` 与 `task_complete` 会话事件，避免任务完成后仍错误显示“执行中”。
- **克制的桌面控制：** 单击卡片只选中任务；只有右键菜单的“切换到任务”才会聚焦 Codex。按键输入仅允许白名单按键。
- **可扩展接入：** 支持 Codex CLI/App、Claude Code、Kimi Code、通用 CLI，以及本地 API、CLI 和包装器接入。

## 安装

### 推荐：下载 DMG

下载 [AACC-1.0.0.dmg](https://github.com/zhangboqian2022/AI-Agent-Control-Center/releases/download/v1.0.0/AACC-1.0.0.dmg)，打开后把 `AACC.app` 拖入“应用程序”文件夹。

公开构建使用 ad-hoc 签名，尚未经过 Apple 公证。若首次启动被 macOS 拦截，请先确认 DMG 来自本 Release 页面，再在“系统设置 → 隐私与安全性”选择“仍要打开”。

### 从源码构建

要求 macOS 13+ 与 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/zhangboqian2022/AI-Agent-Control-Center.git
cd AI-Agent-Control-Center
./scripts/install.sh
```

安装脚本会解析依赖、运行测试、构建 `AACC.app`、安装到 `~/Applications/AACC.app`，并将 `aacc` 与 `aacc-run` 写入 `~/.local/bin`。

制作分发镜像：

```bash
./scripts/build_dmg.sh
```

## 用 Codex 任务

1. 启动 AACC，点击右上角齿轮打开设置。
2. 点击“选择监控的 Codex 任务”，勾选需要展示的对话。
3. 点击“开始监控”。只有勾选任务会显示并被检查状态。
4. 将面板拖到固定位置；在设置中选择是否始终置顶，或恢复到桌面右上角。

单击卡片只会选中任务，不会隐藏 AACC。需要切换到 Codex 时，使用卡片右键菜单的“切换到任务”。

对已选择的 Codex 会话，AACC 只读取任务 ID、标题、更新时间、会话文件修改时间、事件名和匹配的进程标识；不会读取提示词、代码、命令或对话正文。详见[中文用户指南](docs/user-guide.md)或 [English user guide](docs/user-guide.en.md)。

## CLI 与本地 API

可用包装器报告进程生命周期，或直接更新任务：

```bash
aacc-run --task task-1 -- codex
aacc status task-1 running --message "正在分析仓库"
aacc status task-1 waiting-approval --message "等待批准"
aacc status task-1 completed --message "修改完成"
aacc list
aacc doctor
```

API 只绑定在 `http://127.0.0.1:17650`，使用写入本机配置的随机 Token；它不是远程控制 API。

## 架构与隐私

```text
已选择的本机 Agent 任务
          ↓
任务发现 / Adapter / CLI 包装器
          ↓
状态管理器 + SQLite 历史 + 可信度规则
          ↓
PySide6 悬浮面板 · 菜单栏 · localhost API
```

任务发现、Adapter、状态管理、GUI、API 与 macOS 自动化彼此隔离。AACC 优先使用结构化本机事件；可信度不足时会显示 `UNKNOWN` 或 `WARNING`，不会虚构结果。

安全边界：

- API 只允许 `127.0.0.1`，并使用随机 Bearer Token。
- 不提供任意 shell 命令接口，子进程不使用 `shell=True`。
- 注入按键仅限 Enter、Esc、方向键、Ctrl+C、`1`、`2`。
- 发送按键前必须成功激活目标 App/窗口。
- 日志会脱敏常见 Token、密码和 Authorization 头。

参阅完整[产品设计](docs/product-design.zh-CN.md)、[安全策略](SECURITY.md)和[故障排查](docs/troubleshooting.md)。

## 开发

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
./scripts/start.sh
```

新增 Agent 时请阅读 [Adapter 开发指南](docs/adapter-development.md) / [Adapter development](docs/adapter-development.en.md)。

## 贡献与社区

欢迎提交 Issue 和 Pull Request。参与前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)、[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 与 [SECURITY.md](SECURITY.md)。

作者与维护者：**zhangboqian** · <zhangboqian@hotmail.com> · [更新日志](CHANGELOG.zh-CN.md)

## 许可证

Copyright © 2026 zhangboqian。项目以 [MIT License](LICENSE) 开源。
