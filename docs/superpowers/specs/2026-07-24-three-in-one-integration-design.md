# 三合一整合设计：额度监控 + 会话指标 + kimi web 实时通道

日期：2026-07-24
分支：`feat/kimi-quota-integration`
目标版本：1.4.0
参考项目（均为 MIT）：[kimi-code-monitor](https://github.com/bfjnbvf/kimi-code-monitor)（© 十叶）、
[KimiCodeBar](https://github.com/xifandev/KimiCodeBar)（© xifandev）
厂商标准：[MoonshotAI/kimi-code](https://github.com/MoonshotAI/kimi-code) `packages/oauth`（MIT）

## 背景与结论

三个项目能力互补、几乎零重叠。codelight（AACC）有最广的会话发现与控制能力
（CLI/Desktop 会话、窗口聚焦、注入），缺账户额度、每会话 token 指标、kimi web 实时通道。
本设计把另外两个项目的能力**移植**进 AACC（Python），不合并其代码仓库。

关键事实（已用官方文档与官方源码核实）：

- OAuth：RFC 8628 Device Code Flow，host `https://auth.kimi.com`，
  client_id `17e5f671-d194-4dfb-9706-5516cb48c098`（**CLI 官方 client**，
  见 `packages/oauth/src/constants.ts`），端点 `/api/oauth/device_authorization`
  与 `/api/oauth/token`。环境变量覆盖：`KIMI_CODE_OAUTH_HOST` / `KIMI_OAUTH_HOST`。
- 额度 API：`https://api.kimi.com/coding/v1/usages`（覆盖：`KIMI_CODE_BASE_URL`），
  Bearer 携带 OAuth access token 或 `sk-kimi-` API Key，服务端不区分。
- 设备头：`X-Msh-Platform: kimi_code_cli` + `X-Msh-Version/Device-Id/Device-Name/Device-Model/Os-Version`。
- `kimi web`：前台进程，REST+WS+Web UI 同源；默认端口 58627（占用则 +1 递增）；
  实例注册在 `~/.kimi-code/server/instances/`；持久 bearer token 在
  `~/.kimi-code/server.token`；`GET /openapi.json` / `GET /asyncapi.json`
  提供权威协议文档。
- **kimi web 会话与 CLI 共享 `~/.kimi-code/sessions/` 存储**——磁盘发现已覆盖，
  实时通道的增量价值是：秒级状态/指标（WS 推送）与"server 是否在跑"的感知。

## 范围

### 子系统 A：Kimi 账户额度监控（P0）

**新模块 `src/aacc/kimi_oauth.py`**（移植官方 `oauth.ts` + `identity.ts`）：

- `KimiOAuthToken`（dataclass）：`access_token / refresh_token / expires_at / scope /
  token_type`；`needs_refresh`（剩余 < 300s）。
- `request_device_authorization(client) -> DeviceAuthorization`：POST 表单，
  校验 `user_code / device_code / verification_uri_complete` 必填。
- `poll_device_token(client, device_code, interval, timeout=15min)`：处理
  `authorization_pending`（继续）、`slow_down`（interval+5s）、
  `expired_token` / `access_denied`（终止）。
- `refresh_access_token(client, token)`：401/403/`invalid_grant` →
  `KimiOAuthUnauthorizedError`；响应缺 refresh_token 时沿用旧值。
- `device_headers(version)`：X-Msh-* 头；device_id 存 **AACC 自己的配置目录**
  （`device_id` 文件，0600；不读写 `~/.kimi-code/device_id`，避免改动 CLI 状态）。
- 凭据持久化：`<aacc 配置目录>/kimi-credentials.json`，目录 0700、文件 0600，
  原子写（tmp → fsync → rename），与官方 `credentials/` 约定一致。
  **绝不读写 CLI 的 `~/.kimi-code/credentials/`**（refresh_token 服务端轮换会把
  CLI 挤掉线——两个参考项目共同的教训）。
- HTTP 用已有依赖 `httpx`；测试用 `httpx.MockTransport`，不引新依赖。

**新模块 `src/aacc/kimi_quota.py`**（解析规则综合官方 `managed-usage.ts` 的宽松解析
与 KimiCodeBar 已踩过的坑）：

- `KimiQuota` dataclass：`weekly / five_hour / total_quota: QuotaDetail
  (used/limit/remaining/reset_at/percentage)`、`membership_level`、
  `booster: BoosterWallet | None`。
- 解析规则：字段名宽松匹配（`used`/`remaining` 互补推算；`resetTime`/`reset_at`；
  ISO8601 可带小数秒）；5h 档取 `limits[]` 中 `window.duration == 300`；
  加油包余额 = `balance.amountLeft / 1e8` 元，**仅** `STATUS_ACTIVE/STATUS_ENABLED`
  时采用（未启用时该字段是月度上限相关值，必须显示 0）；`priceInCents` 为字符串分。
- `fetch_quota(client, token)`：401/403 → 调用方负责刷新后重试一次。

**新模块 `src/aacc/quota_service.py`**（GUI 侧服务，QObject）：

- 后台线程轮询（60s），结果经 Qt Signal 回 GUI 线程；30s 内重复请求用缓存。
- 状态机：`unauthorized → pending（device flow 进行中）→ authorized`；`error`
  为附带最近错误信息的装饰状态，不改变授权状态。
- access token 临期自动 single-flight 刷新；刷新失败（unauthorized）→ 清除凭据、
  回到 `unauthorized`。
- 支持两种凭据：OAuth token 或用户填入的 `sk-kimi-` API Key（同一凭据文件，
  `auth_method: "oauth" | "api_key"`）。

**GUI**（`src/aacc/gui.py`）：

- 面板顶部任务列表之上新增 `QuotaBar` 控件：状态点 + `周 42% ▓▓░ · 5h 10% ░░░
  · 余额 ¥3.15`，进度条着色（<50% 低 / 50-80% 中 / ≥80% 高），tooltip 显示精确
  重置时间与会员等级；点击手动刷新。
- 未授权时 QuotaBar 显示「点击授权 Kimi 额度」，点击弹出授权对话框：显示
  user code，`QDesktopServices.openUrl` 打开 `verification_uri_complete`，
  后台轮询直至完成/超时/取消。
- SettingsDialog 增加：额度开关、`sk-kimi-` API Key 输入（可选替代 OAuth）、
  「退出 Kimi 登录」按钮。
- 配置项（`AppSettings`）：`kimi_quota_enabled: bool = True`。

### 子系统 B：每会话 token 指标（P1）

**新模块 `src/aacc/kimi_metrics.py`**（移植 kimi-code-monitor `metrics.js`）：

- `normalize_usage(raw)`：多名字段归一（`inputOther`/`input_tokens`/`prompt_tokens`、
  `output`/`output_tokens`、`inputCacheRead`/`cache_read_input_tokens`、
  `inputCacheCreation`/`cache_creation_input_tokens`），负值/非数归零。
- `total_input`、`cache_read_pct`（无输入返回 None）、`decode_speed`
  （时长 <100ms 或 0 输出 → None）、`SpeedTracker`（滑窗 5，取中位数）。

**新模块 `src/aacc/kimi_wire_usage.py`**：

- `WireUsageTracker`：对每个 Kimi 会话的 `agents/main/wire.jsonl` 做**增量尾随**
  ——记录字节偏移，每次轮询只读新增行；文件变小（轮换/截断）则偏移归零重扫。
  累计 `usage.record`（`usageScope == "turn"`）事件的 token 与流式耗时，
  产出 `SessionUsage(cumulative: NormalizedUsage, speed: SpeedTracker,
  last_duration_ms)`。
- 只解析 usage 相关字段，不读取 prompt/response 内容（与现有 wire 扫描的
  隐私边界一致）；单行超 64KB 跳过（复用现有限制）。
- 接入点：`KimiLocalDiscovery.discover()` 为每个返回的 `DiscoveredTask` 在
  `TaskState.metadata` 附 `usage` 字典（仅当累计 token 非零——wire 存在但尚无
  已记录回合的会话不附加该键）；`TaskCard` 对 `kimi_code` 类型卡片
  渲染指标行：`↑12.3k ↓1.2k 缓存68% · 42 tok/s`（无数据时不渲染该行）。

### 子系统 C：kimi web 实时通道（P2，实验性，默认关闭）

- **新模块 `src/aacc/kimi_web_discovery.py`**：
  - 服务发现：读 `~/.kimi-code/server/instances/` 拿 live 实例（pid+port），
    兜底探测 58627 起连续端口；鉴权读 `~/.kimi-code/server.token`（只读，
    0600 已由其产品保证）。
  - 协议自描述：启动时拉 `GET /asyncapi.json` 解析 WS 通道与消息 schema，
    据 fixtures 写适配层；WS 连接用 `kimi-code.bearer.<token>` 子协议，
    先取会话 `last_seq` 快照再只消费后续事件（kimi-code-monitor 的防重复设计）。
  - 事件→`TaskState` 映射复用现有回合判定语义；usage 事件喂给子系统 B 的
    同一 `kimi_metrics` 管线。
- **实施门禁**：先跑 spike——在本机真实 `kimi web` 实例上抓取
  `/openapi.json`、`/asyncapi.json` 与 WS 消息样例存入 `tests/fixtures/`；
  若 WS 协议与预期偏差大，降级为 5s REST 轮询会话状态，仍交付"server 感知"。
- 配置项：`kimi_web_relay_enabled: bool = False`（实验特性，默认关）。

### 非目标（YAGNI）

- 不合并 Swift/.NET 代码；不做 Sparkle 自更新、会话归档、`kimi web` 进程启停、
  CLI 更新检测（后续单独立项再评估）。
- 不做菜单栏文本常驻（AACC 是面板应用，非 MenuBarExtra）。
- 不改写 codex 通道；额度仅支持 Kimi（Codex 无公开额度 API）。

## 错误处理

- 网络错误：额度轮询失败沿用上次数据 + 状态点变灰，连续失败不弹窗；
  日志走 `aacc.discovery` 同款指纹冷却（60s）。
- OAuth 轮询超时/取消/拒绝：对话框显示对应文案，回到 `unauthorized`。
- token 刷新 401/403：清凭据回 `unauthorized`，QuotaBar 提示重新授权。
- wire 尾随读文件竞争（写入中途的半行）：只消费以 `\n` 结尾的完整行，
  半行留待下一轮（复用 `_reverse_complete_lines` 的完整行纪律）。

## 测试

- TDD：每个模块先写失败测试。`tests/test_kimi_oauth.py`（MockTransport 覆盖
  device flow 全分支：pending/slow_down/expired/denied/刷新/缺字段）、
  `tests/test_kimi_quota.py`（官方样例 + KimiCodeBar 边界：未启用加油包、
  字符串数字、缺字段）、`tests/test_kimi_metrics.py`（移植 metrics.js 的
  行为用例）、`tests/test_kimi_wire_usage.py`（增量偏移、截断重扫、半行）、
  `tests/test_quota_service.py`（状态机、缓存 TTL、single-flight 刷新）、
  GUI 测试走 pytest-qt offscreen（QuotaBar 三态、卡片指标行有无）。
- 质量门：`pytest -q`、`ruff check src tests`、`mypy src/aacc`（strict）全绿。

## 合规与署名

- 两个参考项目与官方 `packages/oauth` 均为 MIT。仓库根新增 `NOTICE` 文件，
  移植文件头部保留对应版权声明与来源链接：
  - `kimi_oauth.py` / `kimi_quota.py`：© MoonshotAI（packages/oauth）、© xifandev
  - `kimi_metrics.py`：© 十叶
- README（中英）加致谢段。

## 里程碑

1. M1 = 子系统 A（额度监控端到端可用）→ 可独立发 1.4.0-rc.1
2. M2 = 子系统 B（卡片指标）
3. M3 = 子系统 C spike → 决策 → 实现或降级
