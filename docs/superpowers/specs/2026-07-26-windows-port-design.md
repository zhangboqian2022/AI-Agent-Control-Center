# AACC Windows 移植设计（2026-07-26）

目标：在**同一代码库**内提供与 macOS 版功能完全一致的 Windows 版本，
运行时按平台选择实现；macOS 行为零回归（全套测试 + ruff + mypy strict 保持绿）。

## 方案对比与决策

- **A. 单代码库 + 平台抽象层（采纳）**：`AutomationController` Protocol 已存在，
  平台差异集中在少数模块，运行时工厂选择实现。长期维护一份代码，
  "功能完全一样"可持续。
- B. Fork 独立仓库：两份代码必然漂移，拒绝。
- C. 阉割版（只监控、不注入/聚焦）：不满足需求，拒绝。

## 平台差异清单与 Windows 实现策略

| 能力 | macOS 现状 | Windows 实现 |
|---|---|---|
| 窗口聚焦/恢复最小化 | AppleScript（automation.py） | ctypes Win32：EnumWindows 按标题匹配 + ShowWindow(SW_RESTORE) + SetForegroundWindow |
| 按键/文本注入 | osascript keystroke | SendInput（ctypes），文本走 Unicode KEYEVENTF_UNICODE |
| 语音注入 | 双击 Fn 触发系统听写 | Win+H 触发 Windows 语音输入（SendInput） |
| 全局热键 | Quartz CGEventTap（hotkeys.py） | RegisterHotKey + QAbstractNativeEventFilter 收 WM_HOTKEY |
| 辅助功能权限 | AXIsProcessTrusted | 无对应概念，`is_accessibility_trusted()` 恒 True，设置页跳转 no-op |
| 进程探测 | psutil（已跨平台） | 复用；匹配模式按平台调整（`kimi.exe`、路径分隔符） |
| 单实例锁 | fcntl.flock（instance_guard.py） | msvcrt.locking；激活已有实例改 Win32 前景窗口 |
| 应用支持目录 | ~/Library/Application Support/AACC | %APPDATA%/AACC（constants.py 平台分支） |
| Kimi Desktop daimon | ~/Library/Application Support/kimi-desktop/... | 候选路径列表（%APPDATA%/%LOCALAPPDATA% 下 kimi-desktop），找不到则该发现源静默停用 |
| 终端默认配置 | com.apple.Terminal bundle id | 新增 terminal type `windows_terminal`，按窗口标题聚焦（window_title 字段复用） |
| 文件缓存指纹 | st_dev/st_ino（codex_discovery） | st_ino 为 0 时降级为 path+mtime+size 指纹 |
| chmod 0600/目录 fsync | POSIX | 平台守卫（Windows 跳过，ACL 默认仅当前用户可读） |
| OAuth 设备头 | platform.mac_ver() | 按平台生成（Windows 用 platform.version()） |
| 托盘/Dock 恢复 | applicationStateChanged workaround | Qt 跨平台 API，保留即可，Windows 下无害 |

## 架构改动（最小侵入）

1. **新增 `src/aacc/win32.py`**：全部 ctypes Win32 调用的薄封装
   （EnumWindows/ShowWindow/SetForegroundWindow/SendInput/RegisterHotKey/
   msvcrt 锁）。所有 Windows 专属测试通过替换该层 fake，无需 Windows 机器。
2. **`automation.py`**：保留 `MacAutomation`；新增 `automation_windows.py`
   `WindowsAutomation`（实现既有 `AutomationController` Protocol 四方法）；
   新增 `create_automation()` 工厂按 `sys.platform` 分发，`app.py` 装配处改调工厂。
3. **`hotkeys.py`**：保留 macOS 实现；新增 `hotkeys_windows.py`
   （QAbstractNativeEventFilter + RegisterHotKey）；`app.py` 按平台装配。
4. **`accessibility.py`**：平台分支，Windows 恒 True / no-op。
5. **`instance_guard.py`**：锁与激活按平台分支。
6. **`constants.py`**：`app_support_dir()` 平台感知；`gui.py` 默认日志路径改用它。
7. **发现服务微调**：进程匹配正则平台化（kimi_discovery/adapters/
   kimi_desktop_discovery）；kimi_desktop_discovery 加 Windows 候选路径；
   codex_discovery 的 st_ino 缓存加 Windows 降级。
8. **配置**：`TerminalConfig.type` 增加 `windows_terminal`；`config.py`
   默认终端按平台生成；`examples/config.example.yaml` 补 Windows 示例。
9. **打包**：新增 `AACC-windows.spec`（无 BUNDLE，windowed 单 exe）+
   `scripts/build_windows.ps1`；mac 构建脚本不动。
10. **CI**：`.github/workflows` 测试矩阵加 `windows-latest`
    （offscreen pytest；mac 专属断言已靠 fake 隔离，预期可直接跑）。
11. **文档**：README 双语补 Windows 构建/安装/冒烟章节；KNOWN_LIMITATIONS
    补 Windows 侧已知差异（终端聚焦依赖窗口标题、无公证签名等）。

## 不做（YAGNI / 与 macOS 版对齐）

- `start_at_login`：macOS 版本身未实现，Windows 也不做。
- Windows 安装器（Inno Setup/MSI）：先提供 exe + 说明，后续再议。
- 代码签名/公证：Windows 侧无证书，跳过并在文档说明 SmartScreen 警告。
- Kimi Desktop Windows 路径未经真机验证：按候选路径 best-effort，找不到即停用。

## 测试策略

- 每个 Windows 实现模块配单元测试，win32 层全 fake，在本机（macOS）即可跑。
- 全量 pytest（offscreen）+ ruff + mypy strict 在 mac 上保持绿——零回归硬门槛。
- 新增测试覆盖：平台工厂分发、Windows 路径分支、进程正则、终端聚焦
  脚本生成（fake win32 断言调用序列）、单实例锁 Windows 分支。
- 真机验证清单写入文档：Windows 机器上 build_windows.ps1 → 冒烟
  （发现任务、聚焦、注入、热键、托盘）需用户执行，本仓库不宣称已覆盖。

## 风险

- SetForegroundWindow 在 Windows 有前景锁限制（需 AttachThreadInput
  或先模拟一次输入）；实现时按惯用 workaround 处理并在 KNOWN_LIMITATIONS 记录。
- Windows Terminal 窗口标题随 shell 变化，标题匹配可能失准——降级为
  激活最近匹配窗口并记录日志。
- 无法在本机构建/运行 Windows 产物：所有 Windows 代码路径以 fake 单测
  验证逻辑，真机冒烟留给用户按清单执行。
