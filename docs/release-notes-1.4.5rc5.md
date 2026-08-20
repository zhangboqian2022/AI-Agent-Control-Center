# AACC 1.4.5-rc.5 Release Notes / 发布说明

## English

1.4.5-rc.5 is a macOS-focused corrective prerelease for the Qwen Code
(Bailian token-plan) hidden Chrome refresh path.

- **Chrome no longer repopulates Dock with hidden-refresh entries.** rc.4
  changed direct execution to `open -j -g -n`, which hid the separate headed
  Chrome instance but did not suppress LaunchServices' `recent-apps` records.
  Repeated refreshes could therefore recreate multiple Chrome icons after the
  user manually removed them. rc.5 uses the modern
  `NSWorkspaceOpenConfiguration` API with `addsToRecentItems=False`,
  `activates=False`, `createsNewApplicationInstance=True`, and `hides=True`.
  The browser remains headed and keeps the AACC-owned profile, while the
  quota page is still created in a background CDP window.
- **Lifecycle tracking remains profile-scoped.** The returned
  `NSRunningApplication` is used as the launch handoff handle, while cleanup
  continues to terminate only Chrome processes carrying the exact AACC-owned
  `--user-data-dir` argument. A normal user Chrome profile is not touched.
- **Packaging includes AppKit explicitly.** The Cocoa runtime dependency and
  PyInstaller hidden import are declared so the same boundary is present in
  the distributed macOS app.
- **Shutdown reaps a slow hidden refresh.** If a page evaluation outlives the
  bounded worker join during application exit, the session synchronously
  terminates only its owned Chrome profile before the Qt process returns.
- **Late LaunchServices callbacks are contained.** A pending asynchronous
  launch is cancelled and drained per owned profile; if macOS returns an
  `NSRunningApplication` after shutdown has begun, the callback terminates it
  immediately instead of creating an orphan Chrome after AACC exits.

## 中文

1.4.5-rc.5 是针对 macOS Qwen Code（百炼 token-plan）隐藏 Chrome 刷新路径的
修正版预发布版本。

- **隐藏刷新不再重新生成 Dock Chrome 图标。** rc4 从直接执行改为
  `open -j -g -n`，虽然隐藏了独立的有头 Chrome 实例，但没有禁止
  LaunchServices 写入 `recent-apps`。因此连续刷新后，用户手动移除图标仍会
  再次出现多个 Chrome。rc5 改用现代 `NSWorkspaceOpenConfiguration` API，
  设置 `addsToRecentItems=False`、`activates=False`、
  `createsNewApplicationInstance=True` 和 `hides=True`。浏览器仍保持有头
  模式并使用 AACC 专属 profile，额度页仍由 CDP 在后台窗口创建。
- **生命周期仍按专属 profile 隔离。** `NSRunningApplication` 只负责跟踪
  LaunchServices 启动交接，关闭时继续只处理带有精确 AACC 专属
  `--user-data-dir` 参数的 Chrome 进程，不会触碰用户日常 Chrome profile。
- **显式补齐 AppKit 打包依赖。** Cocoa 运行时依赖和 PyInstaller 隐式导入已
  加入，确保分发的 macOS 应用包含同一启动边界。
- **关闭时清理慢速隐藏刷新。** 如果页面评估在应用退出时超过有界 worker
  等待，session 会在 Qt 进程返回前同步终止自己专属 profile 的 Chrome，
  不留下后台浏览器。
- **延迟 LaunchServices 回调被封闭。** 待处理的异步启动会按专属 profile
  取消并排空；如果 macOS 在关闭开始后才返回 `NSRunningApplication`，回调会
  立即终止它，不会在 AACC 退出后重新生成孤儿 Chrome。

## Verification boundary / 验证边界

The rc.5 fix is verified with Qwen unit tests, a real temporary Chrome profile
on this macOS machine, and a Dock preference check: the temporary hidden
Chrome reached `DevToolsActivePort` while the Chrome `recent-apps` count stayed
unchanged. Existing AACC data, the pinned normal Chrome Dock item, and the
user's normal Chrome profile are out of scope and were not modified.

本版本已通过 Qwen 单元测试、当前 macOS 上的真实临时 Chrome profile 启动验证及
Dock 偏好检查：临时隐藏 Chrome 成功生成 `DevToolsActivePort`，Chrome
`recent-apps` 数量保持不变。现有 AACC 数据、Dock 中固定的正常 Chrome 项目及
用户日常 Chrome profile 均未修改。
