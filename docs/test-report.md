# AACC v1.3.0-rc.1 测试报告

测试环境为 Apple Silicon macOS 26.5.2（build 25F84）、Python 3.13.11、PySide6 6.11.1。

## 最终结果

- `pytest`：163 项通过，0 项失败。
- 全包行覆盖率 83%；相对 `6167267`（本次发布基线）的 666 行可执行变更覆盖 600 行，changed-line 覆盖率为 90%（缺失 66 行），达到发布门槛。复现命令：`uv run pytest --cov=src/aacc --cov-report=xml:coverage.xml -q`，随后执行 `uvx diff-cover coverage.xml --compare-branch=61672673d326796ad2631a0a59a39b8e5545ce45 --fail-under=90`。
- Ruff：0 个问题；strict mypy：23 个源码模块全部通过。
- wheel 已包含 `aacc/styles.qss`；安装器从 `uv.lock` 导出生产依赖并以 `--no-deps` 安装本地 wheel，独立 runtime 不含 pytest、mypy、Ruff 或 PyInstaller。
- `~/Applications/AACC.app` 版本为 `1.3.0-rc.1`，约 110 MB；ad-hoc 深度签名严格验证通过，进程保持运行，RSS 约 71 MB。
- 桌面 DMG 为 `/Users/zhangboqian/Desktop/AACC-1.3.0-rc.1.dmg`，约 49 MB；`hdiutil verify` 通过；SHA-256 为 `10f6c4fed8ee4fff4cf3b9fb708fd7197e8647adb8ddd6e771808f2a3fedd9f3`。
- 安装后配置与 SQLite 权限均为 `0600`；本地健康接口返回 `1.3.0rc1`；`aacc doctor` 通过；重复启动后仍只有一个进程。
- 真实 Codex 冒烟测试同时识别到本次运行任务和另一个已完成任务；脱敏的 2026-07 当前格式 index/session 夹具验证运行与完成事件解析；完成保留、移除与恢复由 Qt/发现回归测试覆盖。

当前机器没有 Developer ID Application 身份，也没有配置 Apple 公证 profile，因此本产物明确是 ad-hoc GitHub 预发布版，不是已公证稳定版。macOS 13/14/15 尚未完成真机矩阵，不作已实测承诺。
