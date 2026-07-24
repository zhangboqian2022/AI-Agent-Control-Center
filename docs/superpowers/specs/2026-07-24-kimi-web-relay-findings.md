# Kimi Web Relay Spike — Findings (M3)

状态：Task 1（环境探测 + REST/AsyncAPI 捕获）已完成；Task 2（WS 实测）与
Task 3（结论补全）待续。

## 1. 环境事实（2026-07-24）

- Kimi Code CLI：`0.29.0`，位于 `~/.kimi-code/bin/kimi`。
- `kimi web` 子命令存在，默认端口 58627；本 spike 用 **58699** 避免与
  用户可能在跑的实例冲突。
- **spike 服务器仍在运行**：PID **70147**（`kimi` 进程，父进程 bash 包装
  70146），监听 `127.0.0.1:58699`，日志 `/tmp/aacc-kimi-web-spike.log`。
  停止方式：`kill 70147`。
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

- 单 channel：`/api/v1/ws`；客户端发 `client_hello` → 服务端 `server_hello`，
  然后 `subscribe{session_ids[], cursors{sid:{seq,epoch}}}` 按会话订阅，
  支持用 snapshot 的 `(seq, epoch)` 断点续传；对不上会收到
  `resync_required`。
- 服务端事件统一封装为 `session_event`：
  `{type, seq, epoch, volatile, offset, session_id, timestamp, payload}`，
  `type` 即 payload 事件类型。
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

## 5. 对 AACC 的初步判断（待 Task 2/3 验证）

- REST 轮询 `GET /api/v1/sessions` 已能覆盖 M2 的大部分指标（busy、
  usage、last_seq），但**前提是用户本机跑着 `kimi web`**——这是 subsystem C
  最大的前提成本。
- WS 推送（subscribe + session_event）比轮询多给出：实时增量、
  turn.step.completed 的逐步用量、epoch 断点续传。
- `instances/*.json` + `server.token` 让"自动发现并接入本机 kimi web"
  可行，无需用户手填地址/token。

## 6. 留给 Task 2 的开放问题

1. WS 握手是否也走 `Authorization` 头（浏览器用 `#token=` fragment，
   脚本客户端要用头或 query）？
2. `subscribe` 不带 `session_ids` 是否订阅全部？`cursors` 省略时的起始点？
3. 真实回合中 `turn.step.completed` 的触发频率与 `volatile` 标志语义。
4. `epoch` 何时变化（服务器重启？会话重置？），`resync_required` 后如何
   用 snapshot 重建。
5. 服务器空闲超时/崩溃时 instances 文件是否自动清理（stale 文件检测）。
