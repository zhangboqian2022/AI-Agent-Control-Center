# AACC 1.4.2（发布候选说明草案）

本文件记录 1.4.2 候选改动与发版门禁。`v1.4.2` 尚未创建，也没有发布资产；只有
Windows 真机与多用户权限验证完成后，才能把本草案转为正式发布说明。

## 中文

### 额度显示

- Codex 额度条只保留一行 `WEEK`。AACC 优先通过本机已安装 Codex 的只读
  `account/rateLimits/read` app-server 方法读取当前账户周额度，因此无需先启动
  Codex 任务；方法不可用时回退到有界的本机会话元数据。
- Kimi 按 `5H`、`WEEK`、`MONTH` 三行显示。AACC 新增独立的 Kimi 官网会员
  登录视图：首次由用户直接在官网登录，此后系统原生 WebView 会话只缓存在
  AACC 本地，直到用户在 AACC 中退出登录。AACC 每五分钟同时刷新官网的三个
  窗口，并仅在官网窗口暂缺时用 Kimi Code 的 `5H` / `WEEK` 补位；不再把
  `/coding/v1/usages` 的空 `totalQuota` 当作月额度来源。
- 每行把百分比、进度条和本地绝对重置时间分列显示。数字与日期在默认面板宽度
  下不会互相遮挡，并增大额度摘要、百分比和重置时间字号；Codex 不显示五小时
  窗口。
- Kimi Code 与官网会员刷新都是只读额度元数据请求，不提交模型推理任务，因此
  不消耗模型 Token 或额度。

### Windows 与 CI 加固

- 修复 Windows Actions 在 PowerShell 中执行 POSIX 环境变量赋值导致 pytest
  无法启动的问题。
- Windows 构建脚本安装 dev 依赖；macOS 与 Windows 均执行严格 mypy、阻塞式
  `pip-audit` 和原生 PyInstaller 构建回归。
- Windows 构建后递归检查 PyInstaller archive，确认 `aacc.win32`、
  `aacc.automation_windows` 与 `aacc.hotkeys_windows` 已被收集。
- `config.yaml` 与 `kimi-credentials.json` 在 Windows 使用 `icacls` 移除继承，
  仅向当前用户 SID、Local System 与本机 Administrators 授予完全控制。ACL
  在写入敏感内容前先施加到空临时文件；旧文件也通过受保护的新文件原子重发，
  从而移除遗留的无关显式 ACE。ACL 失败时旧文件保留，新明文文件不会发布。
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
- Kimi renders `5H`, `WEEK`, and `MONTH`. A new AACC-owned native web view lets
  the user sign in directly on Kimi's site once and keeps that session local to
  AACC until explicit logout. Every five minutes AACC refreshes all three web
  membership windows together; Kimi Code can fill only a temporarily missing
  `5H` or `WEEK`, never `MONTH`.
- Percentage, progress, and absolute local reset time occupy separate columns
  with larger, more legible text and no overlap at the default panel width. No
  Codex five-hour row exists.
- Both refresh paths read quota metadata without submitting a model inference
  request, so polling does not consume model tokens or quota.

### Windows and CI hardening

- Fix Windows Actions pytest startup under PowerShell, install build
  dependencies, and run strict mypy, blocking dependency audit, and native
  package regression builds on both platforms.
- Recursively inspect the Windows PyInstaller archive for all three platform
  modules instead of adding redundant hidden imports.
- Restrict `config.yaml` and `kimi-credentials.json` with Windows ACLs before
  writing sensitive content. Legacy files are atomically re-published through
  a newly protected file so unrelated explicit ACEs do not survive. Protection
  failure preserves the prior file and aborts the write.
- Surface a generic visible failure when a task-removal prefix is unknown.

## Automated verification

- 2026-07-27 本机自动验证：`544 passed, 4 skipped`；Ruff check 与 format
  全部通过；mypy strict 为 42 个源码文件零错误。
- changed-line coverage 为 94%（门槛 90%）；锁定依赖审计覆盖 75 个依赖，
  未发现已知漏洞。
- macOS `dist/AACC.app` 构建成功，Info.plist 版本为 `1.4.2`，
  `codesign --verify --deep --strict` 通过。原生 WKWebView 插件已打包，
  QtWebEngine 未进入 115 MB 应用包，8 秒启动冒烟通过。420×640 离屏截图已
  人工检查，额度数字、进度条与完整重置日期无重叠。
- 本机只读 Codex probe 找到已安装可执行文件，在没有启动 Codex 任务的情况下
  返回 `status=ok`、周额度 14%、重置时间 `2026-08-02T00:40:57+00:00`、
  方案 `prolite`。
- Hosted macOS/Windows Actions：
  [run 30234298287](https://github.com/zhangboqian2022/AI-Agent-Control-Center/actions/runs/30234298287)
  双平台成功。Windows 实际执行结果为 `522 passed, 10 skipped`，严格 mypy、
  零已知漏洞依赖审计、`AACC.exe` 构建、三个平台模块 archive 检查，以及
  ACL/Windows 字体布局回归均通过。

## Manual release gates

- [ ] Complete every item in
  `docs/windows-verification-checklist.zh-CN.md` on a real Windows 10/11
  machine.
- [ ] Confirm a separate unprivileged Windows account cannot read either
  sensitive file.
- [ ] Sign in to a real Kimi membership account from AACC and confirm `5H`,
  `WEEK`, and `MONTH` refresh together and survive an app restart.
- [ ] Attach the completed checklist evidence to the release PR or notes.
- [ ] Only then create tag and release `v1.4.2`.
