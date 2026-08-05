# Qwen 额度：有头隐藏 Chrome 刷新（macOS）设计

日期：2026-08-05
分支：feat/qwen-quota
状态：设计已定稿（用户已批准全部决策），待转入实现计划

## 1. 背景与问题

AACC 的 Qwen Code（阿里云百炼 token-plan）额度目前由一个 AACC 自管的隔离
Chrome（CDP 驱动）抓取。`refresh` 走 headless（`--headless=new --disable-gpu`）、
每 5 分钟一次。2026-08-05 实测确认：阿里云风控（baxia）**按浏览器指纹区别
对待同一张会话票据**——

- 同一张 `login_aliyunid_ticket`，日常 Chrome 出示持续有效；
- 从 AACC 的 profile（headless UA + 当天大量失败/重登记录）出示即被服务端
  拒绝并以 Set-Cookie 删除。

headless 是最强的机器人指纹。而当天两次**可见（有头）**登录（15:25、16:06）
都成功取到了额度。因此本方案把刷新从 headless 改为「有头但完全隐藏」的
Chrome，用真实浏览器指纹换取会话存活。

> 注意：会话票据本身约 5.5 小时过期、且可能随时被风控升级拒绝，这是服务端
> 行为，本方案不解决；兜底仍依赖上午已上线的「未登录检测」修复
> （提交 1b6efe3）——一旦被拒，额度栏如实显示「点击授权」，不再死循环假数据。

## 2. 已批准的需求决策

| 决策点 | 结论 |
|---|---|
| 窗口呈现 | 完全后台隐藏（不抢焦点、不 Dock 弹跳、用户无感） |
| 指纹加固 | 中度：去 headless + 注入 JS 隐藏 `navigator.webdriver` + 伪装屏外负坐标 |
| 刷新频率 | 5 分钟 → **15 分钟**（`QWEN_WEB_QUOTA_INTERVAL_MS` = 900_000） |
| 平台 | 仅 macOS（Chrome-CDP 路径）；Windows 维持 native 回退 |
| 启动机制 | 方案 A：`open -g -n` 系统级后台启动 |
| 失败兜底 | 复用 1b6efe3 的未登录检测，栏变「点击授权」 |

## 3. 经实测修正后的启动与隐藏方案

macOS/Chrome 专家以 Chrome 150.0.7871.189 做了 7 次真机实测（/tmp 临时
profile + 中立页），修正了原设计两处错误：

1. **`--window-position=-32000,-32000` 会被 macOS 钳回屏内**（Cocoa
   `constrainFrameRect`）。启动参数无法直接把窗口放到屏外；CDP 编程移动
   也会被钳到「屏外只留 40px 可抓细条」。因此改为「先小窗启动、再用 CDP
   推出屏外」。
2. **`Page.addScriptToEvaluateOnNewDocument` 必须先 `Page.enable` 才生效**；
   且只对注册之后创建的新文档生效，故注册后必须 `Page.reload`。Chrome 150
   下挂了 CDP 的页面 `navigator.webdriver` 恒为 true，掩盖是必须的。

**最终启动/隐藏序列：**

第一步——启动（`open -g -n` 保证不抢焦点、强制新实例）：

```
open -g -n -b com.google.Chrome --args \
  --user-data-dir=<AACC 受管 profile> \
  --remote-debugging-address=127.0.0.1 --remote-debugging-port=0 \
  --no-first-run --no-default-browser-check \
  --disable-background-timer-throttling \
  --disable-renderer-backgrounding \
  --disable-backgrounding-occluded-windows \
  --window-position=0,0 --window-size=500,375 \
  <workspace_url>
```

- 不带 `--headless=new` / `--disable-gpu`（真实渲染、真实 UA/WebGL）。
- 500×375 是 Chrome 最小窗尺寸；三个 `--disable-*` flag 经 A/B 实测确认能在
  窗口被遮挡/最小化时保 `setTimeout` 全速，作为保险。
- 自定义 profile 不受 Chrome 136+「默认目录禁远程调试」限制（已实测确认）。

第二步——启动后 1~2s（读到 `DevToolsActivePort` 后），通过 CDP：

1. `Page.enable` → `Page.addScriptToEvaluateOnNewDocument`（注入 stealth：
   `Object.defineProperty` 掩盖 `navigator.webdriver`、把负的 `screenX/screenY`
   伪装成正常值）；
2. `Page.reload`（让重载后的新文档在首帧脚本前应用 stealth——**必要环节，
   非优化**，否则 baxia 风控的首次出示就漏了）；
3. `Browser.getWindowForTarget` 取 `windowId` → `Browser.setWindowBounds
   {left:-32000, top:-32000}`（被钳成屏外 40px 细条，`visibilityState` 保持
   `"visible"`、定时器全速）；
4. 之后照旧轮询提取（沿用现有 `qwen_dom_extract_expression()`）。

**残留风险（如实记录）**：启动瞬态约 1~2s 一个 500×375 窗口出现在 (0,30)
后台层（在用户窗口之后）；屏外细条会在屏幕左缘菜单栏下留 40px 宽条，若用户
窗口未盖住该角落则可见。均为外观问题，不抢焦点。

## 4. 代码集成方案（集成评审专家结论）

现有 DI 缝隙（`process_factory` / `process_tree_terminator` /
`socket_factory` / `target_loader`）足以承载全部改动，不伤筋动骨。

### 4.1 启动层 `build_qwen_chrome_launch`
- hidden 产物：`executable` 变为 `/usr/bin/open`，`arguments` 为
  `(-g, -n, -b, com.google.Chrome, --args, *chrome_flags, url)`。
- `find_qwen_chrome_executable()` 保留为**安装探测/gating**（不参与 exec）。
- 加 `platform_name` 参数：非 darwin 请求 hidden 时 fail-closed 抛错
  （不回退 headless——本方案明确移除）。
- `/usr/bin/open` 调用参照现有先例 `accessibility.py::open_accessibility_settings`
  与 `instance_guard.py::activate_existing_instance`（`subprocess.run`，带
  win32 分支保护）。

### 4.2 进程生命周期（关键改动）
`open -g -n` 移交 LaunchServices 后立刻以 0 退出，Popen 拿到的是 `open` 的
pid 而非 Chrome 的。现有三处依赖 `process` 的逻辑（`_wait_for_endpoint` 判死、
主循环判死、`_shutdown_process`）都会失真。解法：引入实现 `_ProcessLike`
协议的「分离句柄」，由 `process_factory` 在 hidden 模式返回：
- `poll()`：`open` 运行中或**以 0 退出**时返回 `None`（Chrome 存活交给 endpoint
  探测）；仅当 `open` **非 0 退出**（如 bundle 找不到）时透传非 0。三处判死代码
  一行不改。
- `wait(timeout)`：不信 `open` 退出码，轮询「该 profile 对应的 Chrome 是否已
  消失」直到超时。
- `terminate()`：委托给按 `--user-data-dir` 的杀进程函数，经现有
  `process_tree_terminator` 构造参数注入。
- 代价：`open` 以 0 退出后失去「Chrome 启动即崩」的快速感知，只能等满
  `QWEN_STARTUP_TIMEOUT_SECONDS`（90s）。可接受，需测试钉住该语义。

### 4.3 按 user-data-dir 找/杀进程
- **psutil 已是核心依赖**（`pyproject.toml` `psutil>=7,<8`，src 下 8 处已用），
  无需新增。项目约定不用 shell/pgrep/pkill。
- 新增 `_find_qwen_chrome_processes_for_profile(profile, *,
  process_iter=psutil.process_iter)`：**精确 argv 元素相等**匹配
  `--user-data-dir={profile}`（不要子串匹配），并附加进程名含 "Chrome" 校验
  （`open` 启动器 argv 在 `--args` 后也含该字样，需排除）；每进程异常
  fail-closed，照抄 `codex_app_server.py::find_running_desktop_codex` 模式。
- 杀树梯次复用 `kimi_edge_cdp.py::_terminate_process_tree`（children 逆序
  terminate → 父 terminate → wait_procs → 幸存 kill）。
- **启动前清理**：fire-and-forget 模式下，launch 之前（`_remove_stale_active_port`
  旁）也要跑一次按 user-data-dir 的清理，防止僵尸实例锁住 profile。

### 4.4 模式参数
- **保留 `visible` 布尔，不升枚举**。headless 移除后 `visible=False` 语义从
  "headless" 变为 "hidden 屏外有头"，调用方/协议/测试签名全不动，侵入最小。
- 改名：`run()` 内 `headless_auth_deadline` → `refresh_auth_deadline`
  （`EDGE_HEADLESS_AUTH_GRACE_SECONDS` 来自 kimi_edge_cdp，跨模块改名会波及
  Kimi 侧，qwen 侧起本地别名并注释）。
- 同步改写模块/类 docstring（去掉 "headless runs for the 5-minute refreshes"
  等表述）。

### 4.5 stealth 注入（CdpConnection 扩展）
- 新增公开方法（与 `evaluate`/`close_browser` 并列），经 `_request` 发送
  `Page.enable`、`Page.addScriptToEvaluateOnNewDocument`、`Page.reload`、
  `Browser.getWindowForTarget`、`Browser.setWindowBounds`。id 序列、事件过滤、
  `MAX_CDP_MESSAGE_BYTES` 自动生效；stealth 脚本很短，不触上限。
- **位置**：page socket，重试循环内、`page.evaluate(...)` 之前：连 page →
  `Page.enable` → addScript → `Page.reload` → setWindowBounds → evaluate。
- **注入失败不得升级为刷新失败**：stealth 是优化项，`_request` 抛错时
  suppress/log 后继续走原路径（今天没有 stealth 也能取数）。
- 二阶段用 `location.hash` 跳转不产生新文档，stealth 在阶段二仍有效，无需
  重复注入。

### 4.6 刷新间隔与文档
- `QWEN_WEB_QUOTA_INTERVAL_MS` 300_000 → 900_000。
- 唯一会红的测试：`tests/test_qwen_web_quota_service.py::
  test_service_starts_five_minute_timer`（断言 300_000）→ 改 900_000 并改名
  `..._fifteen_minute_...`。
- 文档同步（双语约定）：CHANGELOG 新版本段（旧文 "background refreshes run
  headless" / "refresh every 5 minutes" 需更新）、
  `docs/superpowers/specs/2026-08-04-qwen-quota-design.md` 的 300_000 对齐。

## 5. 测试策略（照抄现有 fake/DI 模式）

参照 `tests/test_kimi_edge_cdp.py`（FakeSocket/FakeProcess/StubbornProcess/
全量 DI + fake clock）、`tests/test_qwen_chrome_cdp.py`（_make_chrome_profile/
_fake_process_factory/FakeCdp）、`tests/test_qwen_chrome_session.py`
（FakeOperation.calls + 线程 fake）。新增：

1. **hidden launch spec**：断言无 `--headless=new`/`--disable-gpu`，有
   `--window-position=0,0`、`--window-size=500,375`、三个 `--disable-*`；
   open 包装 argv 首元素 `/usr/bin/open`、`-g -n -b com.google.Chrome --args`
   顺序正确；参数化 win32 → 抛错。
2. **fire-and-forget 句柄语义**：poll 立刻返回 0 的 fake → run() 仍推进到
   endpoint 并成功；poll 返回非 0 → 快速 REFRESH_FAILED。（最易写错，必测）
3. **按 user-data-dir 杀进程**：`process_iter` 注入，fake 进程断言精确 argv
   匹配 + 名称过滤（含「`open` argv 也含 flag 但不被选中」用例）；接入层仿
   `test_managed_edge_terminates_owned_tree_when_browser_close_does_not_exit`
   + StubbornProcess 验证 Browser.close 失败后兜底被调用。
4. **stealth 注入 CDP 序列**：FakeSocket 断言 sent 序列（Page.enable →
   addScript → reload → [setWindowBounds] → evaluate），中间事件被忽略；
   「注入收到 error 响应 → 不致命、evaluate 照常」。
5. **启动前清理**：launch 前调用按 user-data-dir 清理一次。
6. **间隔**：15 分钟断言。

CI 提醒：diff-cover 改动行覆盖率 ≥90%，上述每个新分支（open 退出码分支、
平台守卫、注入容错）都要有测试覆盖。

## 6. 风险与开放问题

按严重度（集成评审专家）：

1. **崩溃恢复弹窗**：兜底杀树后下次启动可能弹"恢复页面?"，出现在屏外隐藏窗
   口、可能阻塞 SPA 渲染 → DOM_TIMEOUT。现有代码无处理。需调研
   `--hide-crash-restore-bubble` 类 flag 并本机验证。
2. **僵尸实例堆积 / 启动前清理**：见 4.3，fire-and-forget 引入的新需求。
3. **`open` 静默移交失败**：bundle id 拼错/LaunchServices 异常时 `open` 以 0
   退出但 Chrome 没起，endpoint 永不出现 → 每次白等满 90s。日志要能区分
   "endpoint 超时" 与 "进程早夭"。
4. **stealth 必须 reload 才生效**：见 3/4.5，必要环节。
5. **屏外钳制**：40px 细条 + 启动瞬态小窗，外观问题（见 3 残留风险）。
6. **visible 登录与 hidden 刷新并发**：`_busy` 已挡 AACC 侧并发；低概率下
   endpoint 可能读到登录实例，记录即可。
7. **信任锚转移**：hidden 实际 exec 硬编码 `/usr/bin/open`（Apple 签名，可
   接受）+ bundle id；保留 `find_qwen_chrome_executable` gating，勿省。
8. **会话 ~5.5h 过期 / 风控升级**：服务端行为，本方案不解决；兜底=未登录检测。

## 7. 下一步

1. （可选）本机验证风险 1（崩溃恢复 flag）与残留风险的可视接受度。
2. 转入 writing-plans，把第 4/5 节拆成可执行任务（TDD：先写失败测试）。
3. 实现后全绿（pytest + ruff + mypy）→ `scripts/build_app.sh` +
   `scripts/install.sh` 真机实测隐藏刷新能否稳定取数、不抢焦点。
4. 双语 CHANGELOG / KNOWN_LIMITATIONS 同步。
