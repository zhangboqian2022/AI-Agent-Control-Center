# OpenCode 任务状态检测设计（2026-07-31）

## 背景

AACC 已监控 Codex / Kimi Code / Kimi Desktop 任务。用户要求为 **opencode CLI** 增加任务
状态检测（参照 Kimi Code 做法）：

1. 当需要用户判断或同意时，任务暂停（等待审批状态）
2. 任务完成和进行中的状态指示灯颜色不同（均为圆形）

opencode 官方提供运行时状态模型 `SessionStatus = idle | retry | busy`（`GET /session/status`
与 `session.status` SSE 事件），但该状态是**运行时事件，不落库**。opencode 本地 SQLite
（`~/.local/share/opencode/opencode.db`）包含会话与消息部件数据，可只读推断：

- `part` 表是实时权威快照：`tool` part 的 `state.status` = `running | pending | completed | error`，
  其中 **`pending` 正是"等待用户同意"**（权限审批挂起）；流式 `text`/`reasoning`/`patch` part
  持续刷新 `time_updated`。
- `session` 表：id、title、directory、agent、model、time_created/time_updated、time_archived、
  parent_id（子会话）。无显式"完成"标记。

## 范围

- 平台：macOS + Windows（SQLite 只读，跨平台同实现；进程模式按平台区分）。
- 监控方式：**只读轮询 db**（用户确认，与 Kimi Desktop 监控同模式，不侵入 opencode、
  不改用户工作流）。
- 状态显示：等待同意（黄）/ 进行中（蓝）/ 等待输入（黄）/ 已完成（绿）/ 空闲（灰）。
- 不做：权限响应（需 opencode serve HTTP，超出只读范围）、会话控制。

## 架构

新增模块（与 Kimi Desktop 链路并列）：

| 模块 | 职责 |
|---|---|
| `opencode_discovery.py` | 只读 SQLite 发现 + 状态决策树：`OpenCodeLocalDiscovery` |
| `discovery_service.py` 扩展 | `OpenCodeDiscoveryService`：后台轮询、manual/retained/muted 语义 |
| `processes.py` 复用 | `CachedProcessAlive("name", opencode 进程模式)` |
| `gui.py` 扩展 | agent 类型 `opencode_cli`（显示名 "OpenCode"）、设置对话框选择按钮 |
| `app.py` 扩展 | Runtime 装配 + 启动 |

数据流：db 轮询（默认 5s，与 Kimi Desktop 一致）→ 每会话决策树判定 → `DiscoveredTask`
（task_id = `opencode:<session_id>`）→ 面板卡片（TaskCard 复用：状态灯已是圆形、
颜色已按状态区分）。

## 会话发现

```sql
SELECT id, title, directory, agent, model, time_created, time_updated
FROM session
WHERE time_archived IS NULL AND parent_id IS NULL
ORDER BY time_updated DESC
LIMIT 50
```

- 过滤 `parent_id` 非空的子会话（subagent 派发，避免与主会话重复）。
- 最多 20 个任务卡片（运行中优先排序，与 Kimi 一致）；标题截断 20 字符（同 Kimi）。
- 会话标题取自 `session.title`（用户可见元数据，同 Kimi）。

## 状态决策树

每会话查询 `part` 表最新一条快照（`ORDER BY time_updated DESC, id DESC LIMIT 1`），
只解析 `type` / `state.status` / `time_updated` 三字段，不读取文本内容：

| 最新 part | 条件 | 状态 | 灯色 |
|---|---|---|---|
| `tool` + `state.status=pending` | 无条件 | 等待同意（暂停） | 黄 |
| `tool` + `state.status=running` | 无条件 | 进行中 | 蓝 |
| `text` / `reasoning` / `patch` | age ≤ 90s（活动窗口） | 进行中 | 蓝 |
| `step-finish` / `tool` completed/error / 任意 part | age > 90s 且进程在 | 等待输入（官方 idle） | 黄 |
| 任意 | age > 90s 且进程不在 | 已完成 | 绿 |
| 无任何 part（仅创建未对话） | 进程在 | 空闲 | 灰 |
| 无任何 part | 进程不在 | 未检测到 | 灰 |

官方模型映射：`busy` → 进行中（蓝）；`idle` → 等待输入（黄）；`retry` → 进行中（蓝，重试也是活动）。

判定细节：

- 活动窗口 90s（与 Kimi 一致）；流式回复/工具执行持续刷新 part `time_updated`。
- 进程检测：`CachedProcessAlive("name", ...)`，macOS 匹配 `opencode` 进程名，
  Windows 匹配 `opencode(.exe)`；每轮轮询只查一次（Kimi 同款缓存语义）。
- 完成判定不依赖显式标记（官方无此字段）：进程退出 + 90s 缓冲 → 已完成（绿）；
  opencode 开着但 idle → 等待输入（黄）。

## 安全与边界

- SQLite 以 URI 只读模式打开（`sqlite3.connect("file:...?mode=ro", uri=True)`）；
  WAL 模式下只读连接可安全并发读（opencode 写入不受影响）；连接每次轮询短开短关。
- **内容边界**：`part.data` 只提取 `type` / `state.status` / `time_updated`；
  绝不读取/存储/展示 prompt、回复文本、工具命令、reasoning 内容（对齐 Kimi wire 扫描原则）。
- db 缺失/损坏/不可读 → 静默降级（空列表 + 健康状态标记），不阻塞面板。
- db 路径可注入（测试用临时 db）。
- 多 opencode 实例同时运行 → 进程存活为全局判断（任一实例在即"进程在"，与 Kimi 同语义）。

## 测试策略（TDD）

- `tests/test_opencode_discovery.py`（核心，临时 SQLite db 注入）：
  决策树全分支（pending/running/流式/step-finish 停滞/进程退出/无 part）、会话发现
  （排序/parent_id/archived/max_tasks/标题截断）、只读模式断言（注入 connect 工厂）、
  损坏 db 降级、内容边界（含敏感文本的 part 不进入状态）、时间造假相对当前时刻回拨。
- `tests/test_opencode_discovery_service.py`：轮询间隔、manual/retained/muted 语义、
  auto-active 解除 muted、health（仿 KimiDesktopDiscoveryService 测试）。
- `tests/test_gui_*.py` 追加：visible_agent_types 默认含 `opencode_cli`、设置对话框按钮。
- `tests/test_app.py` 追加：Runtime 装配、启动/关闭。
- 门禁：CI diff-cover 改动行覆盖率 ≥90%；本机 pytest/ruff/mypy 全绿。

## 实施顺序

1. 纯状态决策 + 会话发现（`opencode_discovery.py`）+ 测试。
2. 轮询服务 `OpenCodeDiscoveryService` + 测试。
3. GUI（agent 类型、设置对话框）+ app.py 装配 + 测试。
4. 手动验收（本机真实 opencode 会话状态核对）。
