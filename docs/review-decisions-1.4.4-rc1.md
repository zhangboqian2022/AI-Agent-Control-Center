# AACC 1.4.4-rc.1 评审裁决记录 / Review Decision Record

基线 / Baseline: `main@d50271133e9903e8efc10320da273fa46dd4e622`  
范围 / Scope: 用户提供的最终统一意见 P1-1、P1-2、P2-3、P2-4、P2-5、P2-6、P2-7。  
方法 / Method: 代码、现有测试、构建脚本和 CI 配置核对；另由 Windows 文件安全、API/产品安全、Broker/发布工程三个独立角色复核。

## 裁决总表 / Decision Summary

| 项目 | 裁决 | 本轮处理边界 |
|---|---|---|
| P1-1 Windows `save_config` / `save_credentials` TOCTOU | 部分接受 / Partially accepted | 风险成立；补齐凭据路径校验，并使用 Windows 句柄相对重命名失败关闭。仅做目录身份前后检查不足以证明竞态免疫。 |
| P1-2 `/send-text` 风险警示 | 接受 / Accepted | API 行为保持不变；补 GUI 红色警示、双语安全文档和发布说明。 |
| P2-3 Uvicorn 回环运行时断言 | 接受 / Accepted | 增加显式运行时检查，允许 `127.0.0.1` / `::1`，拒绝其他地址；不使用可被优化掉的裸 `assert`。 |
| P2-4 Broker 8.3 长路径 | 部分接受 / Partially accepted | 做长路径规范化以防止前缀比较不一致；当前代码未证明存在可利用越权，因此不把它升级为 P1。转换失败按安全边界失败关闭。 |
| P2-5 临时配置文件 `fchmod` | 拒绝该具体建议 / Reject as written | Windows 分支在写入前关闭 POSIX fd，并已先应用原生 DACL；`fchmod` 不能加固该分支。POSIX 临时文件已用 `0600` 创建并在写入前收紧权限。保留现有保护流程，不加入无效的跨平台伪修复。 |
| P2-6 coverage / pip-audit 发布证据 | 部分接受 / Partially accepted | CI 已生成并上传覆盖率、JUnit、pip-audit；补充逐腿 provenance JSON 和证据说明。Release 页面资产仍须在对应 RC 构建通过后由发布流程上传，不能用空壳报告冒充。 |
| P2-7 未签名/未公证分发引导 | 接受 / Accepted | 补齐双平台精确 SHA-256 命令和拦截处理路径；继续明确未签名、未公证限制，不暗示绕过安全检查等于签名。 |

## 技术依据 / Technical Basis

### P1-1

`save_config` 的 POSIX 路径已经用打开的父目录和 `dir_fd` 发布；Windows 和
`save_credentials` 原先存在路径式临时文件创建与 `os.replace`。评审提出的“替换前后
比较父目录句柄”只能在事后发现一部分变化，不能阻止比较之后的竞态，因此本轮采用
Windows 原生、父目录句柄相对重命名路径：真实 Windows 路径保留最初写入临时文件的
句柄，验证父目录最终身份与长路径一致性，并且失败时不回退到路径式替换。临时文件的
首次创建仍是系统路径 API，故本轮仍不宣称对具备同用户写权限的竞态攻击完全免疫。

### P1-2

`SECURITY.md` 已经写明 `/send-text` 加 Enter 在终端目标上等效交互式输入，但 GUI 重置凭证对话框和 README 的使用引导不够醒目。风险本身属于本地高权限控制 token 的使用语义，不需要在本 RC 中重做 Token 能力分级。

### P2-3

Pydantic 校验是正常配置路径的第一层；`APIServerThread` 是最终启动边界，必须再次检查。运行时检查使用显式异常而非 `assert`，确保优化运行时也存在。

### P2-4

`GetFullPathNameW` 不会把所有 8.3 别名统一为长路径，可能造成 rooted-prefix 比较不一致。
规范化用于一致性和失败关闭，不代表当前已复现权限绕过；不改变 Broker 只允许既定
目标和扩展名的边界。此修改不宣称启用 `\\?\` 超长路径或替代 Windows
`longPathAware` manifest。

### P2-5

`mkstemp` 在 POSIX 上以 `0600` 创建，现有代码在写入前 `fchmod`；Windows 使用原生 DACL，且在打开临时文件写入前已调用 `protect_file`。在 Windows 关闭 fd 后调用 `os.fchmod` 既不适用也不能消除路径竞态。

### P2-6 / P2-7

当前 CI 配置已证明“会生成”这些证据；本轮进一步让每个质量矩阵腿的 JSON 记录实际
命令、真实文件名、任务状态和证据文件 SHA-256。Windows 腿明确把 diff-cover 标记为
不适用，避免把 macOS-only 检查伪装成全平台证据。单次发布仍需要把对应 run 的证据
artifact 和产物建立 Release 级 provenance 关联，不能用 CI artifact 自动推断 Release
资产。文档只提供校验和系统拦截处理，不把托管 Windows Server 结果包装成消费级
Windows 10/11 真机验证。

## 未纳入本轮 / Explicitly Not Claimed

- 没有真实消费级 Windows 10/11 机器的 Junction 对抗测试证据，因此不宣称该人工门禁已完成。
- 没有 Developer ID、Apple notarization 或 Windows Authenticode，因此不宣称已签名发布。
- 没有实施 Token capability split、速率限制或 API 行为变更；这些是后续独立设计，不应在本轮安全警示修复中顺带引入。

## 验证状态 / Verification Status

- 基线隔离工作树：已建立，未修改用户的主工作树及 `summit202608-1`、`summit02`、
  `release-output`。
- 基线测试：`1207 passed, 7 skipped, 1 warning`。
- 修复后全量测试：`1218 passed, 7 skipped, 1 warning`。
- Ruff、格式检查、mypy、`git diff --check` 和 CI YAML 解析：通过。
- macOS PyInstaller 构建：通过，生成 `dist/AACC.app`；本机未宣称 Windows 原生构建、
  Windows 10/11 真机或 Junction 对抗测试。
- P2-6 当前状态：CI provenance 与证据 artifact 已补齐并经本地脚本测试；Release 页面
  的最终资产挂载仍是发布流程步骤，未在本轮伪造完成证明。
