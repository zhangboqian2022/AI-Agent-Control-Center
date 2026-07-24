# Kimi Web Relay Spike — Findings (M3)

状态：Task 1（环境探测 + REST/AsyncAPI 捕获）、Task 2（WS 实测）、
Task 3（结论补全 + 决策）均已完成。**决策：1.4.0 不实现子系统 C
（No-go for 1.4.0 / Go for later milestone）**，见 §8。

## 1. 环境事实（2026-07-24）

- Kimi Code CLI：`0.29.0`，位于 `~/.kimi-code/bin/kimi`。
- `kimi web` 子命令存在，默认端口 58627；本 spike 用 **58699** 避免与
  用户可能在跑的实例冲突。
- spike 服务器（`kimi web --no-open --port 58699`）已完成使命并停止
  （2026-07-24 复查 PID 70147 已不存在）。
- `~/.kimi-code/server.token`：**之前不存在，首次 `kimi web` 启动时自动
  生成**（持久 token，`kimi web rotate-token` 可轮换）。
- 实例注册表：`~/.kimi-code/server/instances/<server_id>.json`，纯 JSON，
  字段 `server_id / pid / host / port / started_at / heartbeat_at /
  host_version`——**不含 token**（token 只在 server.token 和启动横幅里）。
  这给了 AACC 一条零配置的本地发现路径：扫 instances 目录 + 读
  server.token 即可对接任意在跑的 `kimi web`。
- 所有 REST 与 WS 路由都要 `Authorization: Bearer <token>`；无 token
  返回 401（已验证 `/openapi.json` 与 `/api/v1/sessions`）。
- 信封格式统一：`{"code":0,"msg":"success","data":{...},"request_id":...}`。

## 2. 捕获的协议文档

- `tests/fixtures/kimi_web/openapi.json`（1.2 MB，OpenAPI，30 个 session
  子路由）
- `tests/fixtures/kimi_web/asyncapi.json`（402 KB，AsyncAPI 3.1.0，
  "Kimi Code WebSocket API"）

## 3. 关键 REST 路由

| 路由 | 用途 |
|---|---|
| `GET /api/v1/sessions` | 会话列表；每项含 `busy`、`main_turn_active`、`pending_interaction`、`usage{input/output/cache...}`、`message_count`、**`last_seq`**；支持 `busy`、`before_id/after_id/page_size` 等过滤 |
| `GET /api/v1/sessions/{id}` | 会话详情 |
| `GET /api/v1/sessions/{id}/snapshot` | **原子快照**：`{as_of_seq, epoch, session{...}}`——客户端重建状态 + 续传水位线（已实测，返回 `as_of_seq:0, epoch:"ep_..."`) |
| `GET /api/v1/sessions/{id}/status` | 轻量状态：`busy / model / thinking_level / context_tokens / max_context_tokens / context_usage`（实测返回 `kimi-code/k3`） |
| `GET /api/v1/sessions/{id}/messages` | 消息分页（`before_id/after_id/page_size/role`） |
| `GET /api/v1/sessions/{id}/transcript` | 完整 transcript |
| `GET /api/v1/oauth/usage` | 配额/用量（M1 已走别的路径，待对照） |

## 4. AsyncAPI（WebSocket）要点

- 单 channel：`/api/v1/ws`。**实测握手顺序**（ws-events-sample.jsonl L2–L4）：
  服务端先推 `server_hello{ws_connection_id, protocol_version:2,
  max_event_buffer_size:1000, capabilities}` → 客户端回
  `client_hello{id, client_id, subscriptions[], cursors{sid:{seq,epoch}}}`
  → 服务端 `ack{id, code:0, accepted_subscriptions[], resync_required[],
  cursors}`。WS 鉴权用子协议 `kimi-code.bearer.<token>`（不走
  Authorization 头）。
- 服务端事件统一封装为 `session_event`：
  `{type, seq, epoch, session_id, timestamp, payload, volatile?, offset?}`，
  `type` 即 payload 事件类型。**`volatile` 与 `offset` 的精确语义
  （Task 2 实测修正）**：
  - volatile 帧与持久帧**共用同一条 seq 序列**（如 L7–L9 的 seq 5 上
    既有持久 `turn.started` 又有 volatile `agent.status.updated`）。
  - **只有 delta 类流式帧**（`thinking.delta`、`assistant.delta`）额外
    携带 `offset`（L13/L15/L16/L18，offset 0→6、0→2 递增）；
    volatile 非 delta 帧（如 `agent.status.updated`，L8/L9/L19/L20）
    **不带 offset**。（Task 2 报告一度表述为"volatile 帧都带 offset"，
    以本结论为准。）
  - 断线重放时 volatile 帧不重发：phase B 以 seq=0 重放（L29–L40），
    seq 5 只回放了 `turn.started`，其后的 volatile
    `agent.status.updated` 未出现。
- 与 relay 直接相关的事件类型（共 51 种 payload 分支）：
  - 回合：`turn.started{turnId,prompt,origin}`、
    `turn.ended{turnId,reason,durationMs,error}`
  - 步骤/用量：**`turn.step.completed{stepId,usage{inputCacheCreation,
    inputCacheRead,inputOther,output},finishReason,llm*延迟字段}`**——
    实时 token 用量从这里拿
  - 流式：`assistant.delta`、`thinking.delta`、`tool.call.*`
  - 会话元：`session.meta.updated{title,patch}`、
    `event.session.status_changed`、`event.session.work_changed`
  - 子代理：`subagent.spawned/started/completed/failed/suspended`
- 另有 terminal_* / watch_fs_* 帧族（relay 大概率不需要）。

## 5. 对 AACC 的判断（Task 2/3 已验证，结论见 §7–§8）

- REST 轮询 `GET /api/v1/sessions` 已能覆盖 M2 的大部分指标（busy、
  usage、last_seq），但**前提是用户本机跑着 `kimi web`**——这是 subsystem C
  最大的前提成本。
- WS 推送（subscribe + session_event）比轮询多给出：实时增量、
  turn.step.completed 的逐步用量、epoch 断点续传。
- `instances/*.json` + `server.token` 让"自动发现并接入本机 kimi web"
  可行，无需用户手填地址/token。

## 6. Task 1 开放问题的答案（Task 2 实测）

1. **WS 鉴权**：走 WebSocket 子协议 `kimi-code.bearer.<token>`（脚本客户端
   在 `Sec-WebSocket-Protocol` 里带上即可），不需要 `#token=` fragment。
2. **订阅语义**：订阅在 `client_hello` 的 `subscriptions[]` 里声明
   （L3），每会话游标在 `cursors{sid:{seq,epoch}}`；ack 回显
   `accepted_subscriptions` 与生效游标（L4）。"省略 cursors 是否订阅全部/
   从何起始"未单独探测，实现时一律显式传游标即可规避。
3. **`turn.step.completed` 频率与 volatile 语义**：每个 LLM step 完成时
   触发一次（样例单步回合 L11 step.started → L21 step.completed）；
   volatile 帧语义见 §4——共享持久 seq、重放时不重发、仅 delta 帧带
   `offset`。
4. **epoch 变化与 resync**：phase C 用伪造 epoch 订阅（L44），服务端即推
   `resync_required{session_id, reason:"epoch_changed", current_seq:11,
   epoch:<当前>}`（L45），ack 的 `resync_required[]` 也列出该会话（L46）。
   恢复路径：REST 重新 `GET /sessions/{id}/snapshot` 拿 `(as_of_seq,
   epoch)`，再以新游标重新订阅。
5. **stale instances 文件清理**：未探测——列为未来实现的跟进项
   （实现时按 pid 存活 + heartbeat_at 时效双重判断）。

## 7. Task 3 决策问题（brief 五问）的答复

证据均指 `tests/fixtures/kimi_web/ws-events-sample.jsonl`（46 行，下称
"样例"）与 `tests/fixtures/kimi_web/openapi.json` / `asyncapi.json`。

**Q1 哪些 WS channel 携带回合生命周期与用量事件？消息形状？**
唯一 channel `/api/v1/ws`。回合生命周期：`turn.started{turnId, prompt,
origin}`（样例 L7）→ `turn.step.started`（L11）→ `turn.step.completed`
（L21）→ `turn.ended{turnId, reason, durationMs}`（L23）→
`prompt.completed{promptId, finishedAt, reason}`（L26），辅以持久帧
`event.session.work_changed{busy, main_turn_active, pending_interaction}`
（L6/L24）与 volatile 帧 `agent.status.updated`（phase/usage/context，
L8/L9/L19/L20/L25）。用量：`turn.step.completed` 携带逐步
`usage{inputOther, output, inputCacheRead, inputCacheCreation}` 与 LLM 延迟
字段（`llmFirstTokenLatencyMs`、`llmStreamDurationMs` 等，L21）；
`agent.status.updated` 的 volatile 帧携带累计 `usage{byModel, total,
currentTurn}` + `contextTokens/maxContextTokens`（L19/L20）。
共 51 种 `session_event` payload 分支（AsyncAPI 统计），relay 只需其中
约 10 种。

**Q2 `last_seq` 快照 + 增量订阅是否如 kimi-code-monitor 假设的那样工作？**
是。REST `GET /api/v1/sessions` 每项含 `last_seq`（§3），
`GET /sessions/{id}/snapshot` 原子返回 `{as_of_seq, epoch, session}`；
把 `(seq, epoch)` 作为 cursor 传入 `client_hello` 后只收到 seq 之后的
事件（样例 L3 传 seq=2，首个事件为 seq=3 的 `session.meta.updated`，L5）。
ack 回显生效游标，可用于校验（L4）。

**Q3 WS 事件能否无歧义映射到 AACC 现有 `TaskStatus`？**
可以。映射表（对齐 `src/aacc/models.py` 的 `TaskStatus`）：
- `turn.started` / `event.session.work_changed{busy:true}` → `RUNNING`
- `agent.status.updated.payload.pending_interaction != "none"`（Task 2 实测
  该字段存在于 agent.status.updated；`event.session.work_changed` 亦携带
  同名字段，样例 L6/L24 值为 `"none"`）→ `WAITING_INPUT`——这是 relay
  相对磁盘轮询的**独有增量**：真正的"等待输入"检测。
- `turn.ended{reason:"completed"}` / `prompt.completed` → `COMPLETED`
- `turn.ended{reason:"cancelled"|"error"}` → `CANCELLED` / `ERROR`
唯一需要注意的歧义：`turn.ended` 与 `prompt.completed` 先后到达（L23→L26，
同 seq 相邻），映射层应幂等，先到者定状态、后到者不重复触发。

**Q4 重连时 seq 续传是否足够，还是需要重新快照？**
两者都要，分工明确：同 epoch 下 seq 续传足够——phase B 以 seq=0 重连
（L29），服务端按序重放全部持久事件 seq 1–11（L30–L40，volatile 帧
不重发），ack 把游标推进到 seq=11（L41）。epoch 失配则必须重新快照：
服务端推 `resync_required{reason:"epoch_changed", current_seq, epoch}`
（L45），客户端走 REST snapshot → 新游标重订阅。另注意重放是
`max_event_buffer_size:1000` 上限内的（L2），掉线过久可能也需要快照兜底
（未实测，实现时按 resync_required 处理即可）。

**Q5 实现工作量估计（模块清单 + 测试面）？**
- `src/aacc/kimi_web_discovery.py`（新，约 350 行）：服务发现
  （instances/*.json + server.token + 端口兜底探测）、httpx REST 快照、
  websockets 客户端（握手/游标/resync）、事件→`DiscoveredTask` 映射。
- `src/aacc/models.py`：`AppSettings` 加 `kimi_web_relay_enabled: bool =
  False`（约 5 行）。
- `src/aacc/app.py`：按 flag 条件组装第四个 discovery 服务（约 15 行）。
- 测试：`tests/test_kimi_web_discovery.py`（约 250 行，fixture 驱动：
  解析样例 jsonl、握手/ack、游标推进、resync_required→重快照、状态映射、
  per-step usage 提取；REST 部分用 httpx MockTransport）。
- 依赖均已就位：`websockets`（b2998fa 已 pin）、`httpx`（M1 已用）。
- 估计：**2–3 个工作日**（含 TDD 与质量门），三个子系统中最大的一个。

## 8. 决策（2026-07-24，controller 裁定）

**1.4.0 不实现子系统 C；推迟到下一里程碑（post-1.4.0）。** 按 brief 决策
门定义为 "No-go for 1.4.0 / Go for later milestone"：本 release 以本
findings 文档作为子系统 C 的交付物关闭，未来里程碑从
`docs/superpowers/plans/2026-07-24-kimi-web-relay.md` 的实施计划骨架直接
启动。理由：

1. **增量价值窄**：M1+M2 已交付的能力覆盖了大部分诉求——磁盘发现
   （5s 轮询）已覆盖会话状态，M2 的 wire 尾随已交付 token 指标且零服务
   端依赖。WS relay 的独有增量只有推送级延迟与 `pending_interaction`
   （真·等待输入检测）——真实存在，但不值得为 1.4.0 背上三个子系统中
   最大的一个。
2. **采用前提**：relay 只在用户本机跑着 `kimi web`（前台进程）时可用，
   多数 AACC 用户不会跑。
3. **spike 目的已达成**：产出"决策级协议事实"的目标完成；实现可随时从
   已捕获的 fixtures 冷启动，无信息损失。

## 9. 已知坑（实现时必读）

- **`agent_config.model` 在创建会话时被静默忽略**：`POST /sessions` 传
  model 不生效，需创建后调 `POST /sessions/{id}/profile` 修改（Task 1
  实测；样例 L30 可见 `agent_config: {"model": ""}`）。
- volatile 非 delta 帧（`agent.status.updated`）不带 `offset`——不要按
  "volatile 必有 offset" 写解析器（§4 修正）。
- 重放窗口受 `max_event_buffer_size:1000` 限制（L2），长时间掉线后应
  预期 `resync_required` 并走快照恢复。
- `server.token` 只存于 `~/.kimi-code/server.token` 与启动横幅，
  instances/*.json 不含 token；AACC 只读，绝不写入或外传。
