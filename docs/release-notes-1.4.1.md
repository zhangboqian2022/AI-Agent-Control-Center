# AACC 1.4.1

Codex 周额度 + Kimi 授权链路可靠性修复。  
Codex weekly quota plus Kimi authorization reliability fixes.

## 中文

- 新增只读 Codex 周额度条：仅从本机结构化元数据读取当前 10080 分钟周窗口，
  明确忽略旧 300 分钟窗口，不显示已经取消的 Codex 五小时限制，也不读取提示词
  或回答正文。
- Kimi 凭据写入加入 generation/fingerprint 条件更新；延迟刷新、OAuth、退出登录、
  API Key 修改和外部文件改写不能再覆盖更新一代凭据或让状态停留在 pending。
- OAuth 对话框的关闭、Esc 与取消按钮统一终止后台授权；设备轮询最长 15 分钟，
  所有 HTTP Client 确定关闭，异常信息统一脱敏。
- Kimi 额度区分正常、部分、未知与过期，不再把缺失或畸形响应误报为 `0%`；
  Kimi Desktop 只读 SQLite 连接增加 5 秒 busy timeout。
- CI 强制锁文件、ruff format、mypy strict、全量测试、改动行覆盖率不低于 90%，
  并以非空 JSON 报告阻塞已知依赖漏洞；新增正式 Release 资产校验脚本。

## English

- Add a read-only Codex weekly quota strip. It accepts only the current
  10080-minute window from bounded local structured metadata, ignores legacy
  300-minute windows, exposes no obsolete five-hour Codex field, and never
  reads prompt or response bodies.
- Protect Kimi credential writes with generation/fingerprint conditional
  updates. Delayed refreshes, OAuth, logout, API-key changes, and external file
  changes cannot overwrite newer credentials or leave authorization pending.
- Cancel background OAuth on dialog close, Escape, or the cancel button; cap
  device polling at 15 minutes, close every HTTP client deterministically, and
  redact surfaced errors.
- Distinguish normal, partial, unknown, and stale Kimi quota data instead of
  rendering malformed responses as `0%`; add a five-second busy timeout to the
  read-only Kimi Desktop SQLite connection.
- Enforce locked dependencies, ruff formatting, mypy strict, the full test
  suite, at least 90% changed-line coverage, and a non-empty blocking
  dependency-audit report in CI; add a formal Release asset verifier.

## 安装 / Install

下载 `AACC-1.4.1.dmg` 并把 AACC.app 拖入“应用程序”。本版本使用本地自签名证书，
未经过 Apple 公证；首次启动被拦截时，请先核对附带的 `.sha256` 文件，再到
“系统设置 → 隐私与安全性”选择“仍要打开”。

Download `AACC-1.4.1.dmg` and drag AACC.app to Applications. This build uses a
local self-signed certificate and is not notarized by Apple. If macOS blocks
the first launch, verify the attached `.sha256` file before choosing
**System Settings → Privacy & Security → Open Anyway**.

SHA-256:
`fda8131f359f55dccca3a64a125aaf59377322a479d4f9934db15e53d2713d94`
