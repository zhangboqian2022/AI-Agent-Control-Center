# Kimi Web Relay (Subsystem C) Implementation Plan — 骨架（推迟至 post-1.4.0）

> **For agentic workers:** 本文件是 M3 spike 决策（defer）后的实施计划**骨架**，
> 供未来里程碑的 brainstorming/planning 周期充实为完整计划。任务级拆解与接口
> 已给出，完整代码刻意从略。所有协议事实均有 fixture 证据，见
> `docs/superpowers/specs/2026-07-24-kimi-web-relay-findings.md`（下称 findings）。

**Goal:** 新增第四个发现源 `KimiWebRelayService`：自动发现并接入本机在跑的
`kimi web` 实例，经 WebSocket 推送获取实时会话状态（含真·等待输入检测
`pending_interaction`）与逐步 token 用量，对齐 AACC 现有 `TaskStatus` 与
`kimi_metrics` 管线。默认关闭（实验特性）。

**Architecture:** 服务发现（instances/*.json + server.token + 端口兜底）→
httpx REST 快照（`(as_of_seq, epoch)` 游标）→ websockets 订阅
`/api/v1/ws`（子协议 `kimi-code.bearer.<token>`）→ 事件映射进
`discovery_service.py` 既有的 manual/retained/muted/auto-active 语义。

**Tech Stack:** Python 3.12+ / PySide6 现有栈；`httpx`（M1 已用）、
`websockets`（b2998fa 已 pin）；TDD，fixtures 已在
`tests/fixtures/kimi_web/`（`ws-events-sample.jsonl` 46 行实测样例 +
`openapi.json` / `asyncapi.json`）。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-three-in-one-integration-design.md`
  子系统 C 节（含 M3 结论段）。
- 只读 `~/.kimi-code/`（instances、server.token）；绝不写入、绝不把 token
  写进日志/测试/提交物。
- 遵循 findings §9 已知坑：`agent_config.model` 创建时静默忽略（relay 只读，
  不创建会话，仅记录）；volatile 非 delta 帧无 `offset`；重放窗口上限
  `max_event_buffer_size:1000`。
- TDD：先写失败测试；质量门 `pytest -q` / `ruff check src tests` /
  `mypy src/aacc` 全绿。
- 隐私边界与 M2 wire 尾随一致：只消费状态/用量字段，不读取 prompt/response
  内容（样例中 prompt/delta 已 redact，映射层不得依赖其值）。

---

### Task 1: 配置开关与数据模型

**Files:**
- Modify: `src/aacc/models.py`（`AppSettings` 加字段）
- Test: `tests/test_kimi_web_discovery.py`（新建，先写配置解析失败测试）

- [ ] `AppSettings` 增加 `kimi_web_relay_enabled: bool = False`
  （默认关，实验特性；配置加载/保存往返测试）。
- [ ] 定义内部数据结构（可放新模块内）：
  - `KimiWebInstance(server_id, pid, host, port, started_at, heartbeat_at)`
    —— 对应 `~/.kimi-code/server/instances/<server_id>.json` 字段
    （findings §1）。
  - `SessionCursor(seq: int, epoch: str)` —— 每会话水位线。

### Task 2: 服务发现 `discover_kimi_web_instances()`

**Files:**
- Create: `src/aacc/kimi_web_discovery.py`
- Test: `tests/test_kimi_web_discovery.py`

- [ ] 扫 `~/.kimi-code/server/instances/*.json`（纯 JSON，无 token），
  过滤 stale 条目：**pid 存活 + `heartbeat_at` 时效双重判断**（stale 清理
  行为未实测，findings §6.5，需按保守策略实现并用假 instances 目录测试）。
- [ ] 读 `~/.kimi-code/server.token`（0600，只读）作为 bearer；缺失/不可读
  时记 DEBUG 并返回空（不报错——用户没跑 `kimi web` 是常态）。
- [ ] 兜底：instances 为空时探测 58627 起连续端口（官方默认 58627，
  冲突 +1 重试），用带 token 的 `GET /api/v1/sessions` 200/401 判定存活。
- [ ] 接口：`discover_kimi_web_instances(kimi_home: Path) ->
  list[KimiWebInstance]`，纯函数、可注入路径，测试用 tmp_path。

### Task 3: REST 快照客户端

**Files:**
- Modify: `src/aacc/kimi_web_discovery.py`
- Test: `tests/test_kimi_web_discovery.py`（httpx MockTransport）

- [ ] `KimiWebRestClient(base_url, token)`：`Authorization: Bearer <token>`，
  信封统一 `{code, msg, data, request_id}`（findings §1）。
  - `list_sessions()` → `GET /api/v1/sessions`：每项含 `busy`、
    `main_turn_active`、`pending_interaction`、`usage`、`last_seq`。
  - `snapshot(session_id)` → `GET /api/v1/sessions/{id}/snapshot`：
    原子 `{as_of_seq, epoch, session}`，产出 `SessionCursor`。
- [ ] 错误处理：401 → 记 WARNING（token 可能已轮换，提示
  `kimi web rotate-token` 场景）；连接失败 → 沿用指纹冷却日志（60s，
  与 `aacc.discovery` 一致）。

### Task 4: WebSocket 中继核心

**Files:**
- Modify: `src/aacc/kimi_web_discovery.py`
- Test: `tests/test_kimi_web_discovery.py`（fixture 驱动，见 Task 6）

- [ ] `KimiWebRelay`：连接 `ws://<host>:<port>/api/v1/ws`，子协议
  `kimi-code.bearer.<token>`（findings §6.1）。
- [ ] 握手状态机（样例 L2–L4）：收 `server_hello` → 发
  `client_hello{client_id:"aacc", subscriptions[], cursors}` → 等 `ack`
  校验 `accepted_subscriptions` 与回显游标。
- [ ] 游标管理：每会话从 REST snapshot 拿 `(as_of_seq, epoch)` 起步；
  每收一帧推进 seq；掉线重连先尝试同 epoch seq 续传（重放不重发
  volatile 帧，样例 L27–L41）。
- [ ] `resync_required{reason:"epoch_changed", current_seq, epoch}`（样例
  L45–L46）→ 重新 REST snapshot → 新游标重订阅；掉线过久超出
  `max_event_buffer_size:1000` 时同样走此路径。
- [ ] 解析纪律：volatile 帧与持久帧共享 seq；**仅 delta 帧**（
  `thinking.delta`/`assistant.delta`）带 `offset`，`agent.status.updated`
  等 volatile 非 delta 帧**无 offset**（findings §4 修正，勿过度假设）。

### Task 5: 事件 → TaskStatus / metrics 映射

**Files:**
- Modify: `src/aacc/kimi_web_discovery.py`
- Test: `tests/test_kimi_web_discovery.py`

- [ ] 状态映射（对齐 `src/aacc/models.py` `TaskStatus`，findings §7 Q3）：
  - `turn.started` / `event.session.work_changed{busy:true}` → `RUNNING`
  - `agent.status.updated.payload.pending_interaction != "none"` →
    `WAITING_INPUT`（relay 独有增量；`event.session.work_changed` 同名
    字段作佐证）
  - `turn.ended{reason:"completed"}` / `prompt.completed` → `COMPLETED`
    （两帧相邻到达，映射须幂等：先到定状态，后到有重复抑制）
  - `turn.ended{reason:"cancelled"|"error"}` → `CANCELLED` / `ERROR`
- [ ] 用量：`turn.step.completed` 的
  `usage{inputOther, output, inputCacheRead, inputCacheCreation}` + LLM 延迟
  字段（样例 L21）喂给子系统 B 的 `kimi_metrics` 同一管线（产出与
  `SessionUsage` 对齐的结构）；volatile `agent.status.updated` 的
  `usage{total, currentTurn}`/`contextTokens`（样例 L19/L20）可作卡片
  实时补充。
- [ ] 产出 `DiscoveredTask`（task id 前缀建议 `kimi_web_`，与 remove 卡片
  单一分发入口的前缀约定一致），`TaskState.metadata["usage"]` 约定与
  M2 相同。
- [ ] 51 种 payload 分支中未识别类型：DEBUG 略过，不报错。

### Task 6: fixture 驱动测试套件

**Files:**
- Test: `tests/test_kimi_web_discovery.py`

- [ ] 解析 `tests/fixtures/kimi_web/ws-events-sample.jsonl` 全 46 行：
  握手三帧、phase A 单回合全事件序列、phase B seq=0 重放（验证 volatile
  帧缺席）、phase C bogus epoch → `resync_required`。
- [ ] 状态映射表逐行断言（样例 L7/L23/L24/L26 → RUNNING/COMPLETED）。
- [ ] 用量提取断言：L21 的逐步 usage 数值与延迟字段。
- [ ] 游标推进与 resync 恢复路径（模拟 epoch 失配）。
- [ ] token 零泄漏：测试中 token 用假值，提交前 grep fixtures/测试目录
  验证无真实 token（spike 的 token hygiene 纪律延续）。

### Task 7: 组装进 Runtime

**Files:**
- Modify: `src/aacc/app.py`、`src/aacc/discovery_service.py`（如需基类/模式对齐）
- Test: `tests/test_app.py`（flag 开关两态）

- [ ] `kimi_web_relay_enabled=True` 时组装 `KimiWebRelayService` 为第四个
  discovery 服务，生命周期/轮询节奏对齐 `LocalDiscoveryService` /
  `KimiDesktopDiscoveryService` 模式；WS 长连断开时退化为 REST 5s 轮询
  并指数退避重连。
- [ ] 与磁盘 `kimi_discovery` 去重策略：同会话双源时 relay 数据优先
  （更新 metadata/状态），不重复出卡——复用 discovery_service 既有集合
  语义，具体合并点在此任务设计时定。
- [ ] GUI 无需改动（走现有 TaskCard 通道；`kimi_web_` 前缀如需品牌名/
  右键菜单适配，归入 GUI 小改）。

### Task 8: 文档与发布

- [ ] README（中英）实验特性段：前置条件（`kimi web` 常驻）、开关方式、
  与磁盘发现的关系。
- [ ] KNOWN_LIMITATIONS 补：relay 依赖前台 `kimi web` 进程；重放窗口
  1000 事件上限；stale instances 判定策略。
- [ ] CHANGELOG（中英）。

---

## Self-Review Notes

- 骨架刻意不含完整代码：状态机细节、去重合并点、GUI 适配待未来
  brainstorming/planning 周期定稿。
- 全部协议断言可回溯到 findings 文档与 fixtures 行号；若未来 `kimi web`
  协议升级（protocol_version > 2），需重跑一次 spike 校准。
