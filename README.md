# AI Agent Control Center（AACC）

AACC 是一个仅在本机运行的 macOS 多 AI Coding Agent 悬浮状态与控制中心。它默认自动识别并显示 Codex 任务，可在设置中筛选 Codex CLI、Claude Code、Kimi Code 和 Z Code / 通用 CLI，提供状态灯、菜单栏、窗口聚焦、白名单按键、系统听写、本地 API 与命令行工具。

## 直接安装

要求 macOS 13+，并已安装 [uv](https://docs.astral.sh/uv/)。在本目录执行：

```bash
./scripts/install.sh
```

安装脚本会解析依赖、运行测试、构建 `AACC.app`、安装到 `~/Applications/AACC.app`，并把 `aacc`、`aacc-run` 放到 `~/.local/bin`。完成后程序自动启动。若终端找不到 `aacc`，把下面一行加入 `~/.zshrc`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

需要发给其他 Mac 或留存安装包时，执行 `./scripts/build_dmg.sh`；它会在桌面生成 `AACC-1.0.0.dmg`。

开发模式可执行：

```bash
./scripts/start.sh
```

## 第一次使用

1. 启动后，桌面右上区域出现自动发现的 Codex 任务卡片，菜单栏也会出现 AACC 图标。
2. 打开 `~/Library/Application Support/AACC/config.yaml`，为任务填写稳定的 `window_title`、`tab_title` 或 App `bundle_id`。
3. 在“系统设置 → 隐私与安全性 → 辅助功能”中允许 AACC；窗口切换还可能触发“自动化”授权。
4. 用包装器启动 Agent，或从 CLI/API 更新状态。

```bash
aacc-run --task task-1 -- codex
aacc status task-1 running --message "正在分析代码"
aacc status task-1 waiting-approval --message "等待批准 npm test"
aacc status task-1 completed --message "修改完成"
aacc list
aacc doctor
```

API 默认地址为 `http://127.0.0.1:17650`，随机 Token 在配置文件的 `app.api.token`。示例：

```bash
TOKEN=$(python3 -c 'import pathlib,yaml; print(yaml.safe_load((pathlib.Path.home()/"Library/Application Support/AACC/config.yaml").read_text())["app"]["api"]["token"])')
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:17650/api/v1/tasks
```

## 安全边界

- API 只允许 `127.0.0.1`，且必须使用随机 Bearer Token。
- API 不接受 shell 命令；按键只允许 Enter、Esc、方向键、Ctrl+C、1、2。
- 所有进程调用都使用参数数组与超时，不使用 `shell=True`。
- 发送按键前必须成功激活目标应用/窗口；失败即停止。
- 模糊日志不会被伪装成确定状态，正则匹配有行长限制与执行超时。
- 日志会脱敏 Token、密码、Authorization 和常见 API Key。

## 文档

- [用户指南](docs/user-guide.md)
- [Adapter 开发](docs/adapter-development.md)
- [故障排查](docs/troubleshooting.md)
- [测试报告](docs/test-report.md)
- [执行规格](AI-Agent-Control-Center-Specification.md)

## 已知限制

各 Agent 没有统一可靠事件接口。结构化 Hook 可通过本地 API 接入；否则使用 `aacc-run`、手动状态或保守正则。Terminal/iTerm2 的精确标签定位依赖稳定标题。首次使用键盘注入和全局快捷键必须由用户授予 macOS 辅助功能权限。本地构建使用 ad-hoc 签名，不包含 Apple Developer 公证。

## 卸载

```bash
./scripts/uninstall.sh
```

卸载脚本将 App、命令链接和数据移动到废纸篓内带时间戳的备份目录，不直接永久删除。
