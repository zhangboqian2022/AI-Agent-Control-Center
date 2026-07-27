# AACC 1.4.2（发布候选说明草案）

本文件记录 1.4.2 候选改动与发版门禁。`v1.4.2` 尚未创建，也没有发布资产；只有
Windows 真机与多用户权限验证完成后，才能把本草案转为正式发布说明。

## 中文

### 额度显示

- Codex 额度条只保留一行 `WEEK`。AACC 优先通过本机已安装 Codex 的只读
  `account/rateLimits/read` app-server 方法读取当前账户周额度，因此无需先启动
  Codex 任务；方法不可用时回退到有界的本机会话元数据。
- Kimi 按 `5H`、`WEEK`、`MONTH` 三行显示。月额度来自官方
  `/coding/v1/usages` 响应的 `totalQuota`；空对象或缺失值诚实显示 `--`，
  不从订阅日期或其他窗口推断。
- 每行把百分比、进度条和本地绝对重置时间分列显示。数字与日期在默认面板宽度
  下不会互相遮挡；Codex 不显示五小时窗口。

### Windows 与 CI 加固

- 修复 Windows Actions 在 PowerShell 中执行 POSIX 环境变量赋值导致 pytest
  无法启动的问题。
- Windows 构建脚本安装 dev 依赖；macOS 与 Windows 均执行严格 mypy、阻塞式
  `pip-audit` 和原生 PyInstaller 构建回归。
- Windows 构建后递归检查 PyInstaller archive，确认 `aacc.win32`、
  `aacc.automation_windows` 与 `aacc.hotkeys_windows` 已被收集。
- `config.yaml` 与 `kimi-credentials.json` 在 Windows 使用 `icacls` 移除继承，
  仅向当前用户 SID、Local System 与本机 Administrators 授予完全控制。ACL
  失败发生在原子替换之前，旧文件保留，新明文文件不会发布。
- 未知任务品牌的移除请求继续记录错误，并在面板显示通用“操作未生效”反馈。

### 评审结论

- 已实施经代码或 CI 日志确认的 #1、#2、#5、#6、#7、#8、#9、#12、#14、
  #15、#17。
- 未添加评审建议的三个 hidden imports：隔离 PyInstaller 分析已经证明它们会被
  静态收集；CI 现在直接检查最终 archive。
- 保留原占位 Token 前缀防御；GUI 布局重建保护与 Windows 能力限制文档原本已经
  存在；`AGENTS.md` 历史整理和前景锁 workaround 不属于本次加固。

## English

### Quota display

- Codex keeps one `WEEK` row. AACC first uses the installed Codex app-server's
  read-only `account/rateLimits/read` method, so no Codex task must be started;
  bounded local session metadata remains the fallback.
- Kimi renders `5H`, `WEEK`, and `MONTH`. The monthly row maps only the official
  `/coding/v1/usages` `totalQuota` object; missing or empty data stays `--`.
- Percentage, progress, and absolute local reset time occupy separate columns
  without overlap at the default panel width. No Codex five-hour row exists.

### Windows and CI hardening

- Fix Windows Actions pytest startup under PowerShell, install build
  dependencies, and run strict mypy, blocking dependency audit, and native
  package regression builds on both platforms.
- Recursively inspect the Windows PyInstaller archive for all three platform
  modules instead of adding redundant hidden imports.
- Restrict `config.yaml` and `kimi-credentials.json` with Windows ACLs before
  atomic replacement. Protection failure preserves the prior file and aborts
  the write.
- Surface a generic visible failure when a task-removal prefix is unknown.

## Automated verification

The final test count, Ruff results, both mypy platform results, macOS package
build/codesign result, local Codex read-only probe, and hosted Windows Actions
URL will be recorded after the implementation suite finishes.

## Manual release gates

- [ ] Complete every item in
  `docs/windows-verification-checklist.zh-CN.md` on a real Windows 10/11
  machine.
- [ ] Confirm a separate unprivileged Windows account cannot read either
  sensitive file.
- [ ] Attach the completed checklist evidence to the release PR or notes.
- [ ] Only then create tag and release `v1.4.2`.
