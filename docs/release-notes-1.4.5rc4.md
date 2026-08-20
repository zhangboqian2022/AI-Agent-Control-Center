# AACC 1.4.5-rc.4 Release Notes / 发布说明

## English

1.4.5-rc.4 is a macOS-focused prerelease that fixes repeated Chrome Dock
icons during Qwen Code (Bailian token-plan) hidden quota refreshes. It does
not claim consumer Windows 10/11 hardware validation.

- **Hidden Qwen refreshes no longer register foreground Dock instances.**
  rc.3 directly executed the Chrome binary. On macOS, each isolated
  `--user-data-dir` was still registered by LaunchServices as a separate
  foreground Google Chrome application, so repeated refreshes could create
  multiple Dock instances even though no browser window was visible. rc.4
  launches the same headed Chrome through `open -j -g -n -b com.google.Chrome`.
  `-j` keeps each isolated instance hidden, while `-g` avoids focus stealing
  and `-n` preserves the AACC-owned profile isolation. The quota page is
  still created by CDP in a background window and remains headed rather than
  headless for Bailian risk-control compatibility.
- **Launch handoff and shutdown are tracked separately.** The short-lived
  `open` process is no longer mistaken for Chrome. AACC waits for the
  AACC-owned profile's DevTools endpoint and profile-scoped Chrome processes,
  then closes the browser and terminates only matching profile processes if
  graceful shutdown does not finish.
- **Regression coverage and evidence.** Unit tests cover the hidden
  LaunchServices command, successful/failed opener handoff, CDP background
  target creation, and shutdown escalation. On this Mac, two simultaneous
  temporary-profile `open -j -g -n` launches were observed by `lsappinfo` as
  `hidden=true`, while the prior direct-executable A/B launches were
  `Foreground`; the temporary profile processes were then removed.

## 中文

1.4.5-rc.4 是一次重点修复 macOS 的预发布版本，解决 Qwen Code（百炼
token-plan）隐藏刷新反复产生 Chrome 程序坞图标的问题。本版本不宣称已完成
消费级 Windows 10/11 真机验证。

- **Qwen 隐藏刷新不再注册前台程序坞实例。** rc3 直接执行 Chrome 二进制；
  在 macOS 上，即使没有可见窗口，每个隔离的 `--user-data-dir` 仍会被
  LaunchServices 注册为独立的前台 Google Chrome 应用，连续刷新可能因此
  出现多个程序坞实例。rc4 改为通过
  `open -j -g -n -b com.google.Chrome` 启动同一个有头 Chrome：`-j` 将隔离
  实例保持隐藏，`-g` 避免抢焦点，`-n` 保留 AACC 专属 profile 隔离。额度页
  仍由 CDP 在后台窗口创建，并保持有头模式以兼容百炼风控，不改用
  headless。
- **启动交接与关闭生命周期分离跟踪。** 短生命周期的 `open` 进程退出不再
  被误认为 Chrome 已退出。AACC 等待 AACC 专属 profile 的 DevTools 端点和
  profile 范围内的 Chrome 进程，先请求浏览器正常关闭；若仍未退出，只终止
  带有该 profile 参数的 Chrome 进程。
- **回归测试与实机证据。** 单元测试覆盖隐藏 LaunchServices 命令、启动器成功/
  失败交接、CDP 后台目标创建和关闭升级路径。本机使用两个临时 profile 做
  对照：`open -j -g -n` 启动的两个实例由 `lsappinfo` 观测为 `hidden=true`；
  之前直接执行二进制的对照实例为 `Foreground`；实验结束后已清理临时
  profile 进程。

## Verification boundary / 验证边界

The release artifact must be verified locally with the repository's targeted
Qwen tests, full pytest suite, ruff, mypy, bundle signature check, and a fresh
launch of the replacement app. Existing AACC user data and the user's normal
Chrome profile are out of scope and are not modified by the fix.

发布产物须在本机完成 Qwen 针对性测试、全量 pytest、ruff、mypy、应用签名检查，
并启动替换后的应用进行新鲜验证。现有 AACC 用户数据与用户日常 Chrome profile
不在修改范围内，本修复不会触碰它们。
