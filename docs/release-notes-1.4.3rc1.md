# AACC 1.4.3-rc.1 Release Notes / 发布说明

## English

1.4.3-rc.1 is a release candidate on top of 1.4.2. It applies the accepted items
from the external joint review (Gemini × DeepSeek × HubChat) after each claim was
verified against the source; review items that were already mitigated by existing
defenses were rejected with evidence.

- **Fix — Windows second-instance activation.** Launching a second copy now
  searches for the real main-window title (`AI Agent Control Center`, via the
  shared `AACC_WINDOW_TITLE` constant) instead of the `"AACC"` substring that
  never matched, and focuses the existing panel.
- **Security — log redaction defense in depth.** The sink-level
  `RedactingFormatter` pattern list now also covers `device_code`, `user_code`,
  `api_key`, and `apikey` fields. Redaction stays centralized at the logging
  sink; no call-site `redact()` calls were added.
- **Diagnostics — Kimi Desktop discovery.** When none of the candidate daimon
  roots exists, one INFO log entry lists every probed path so the disabled
  discovery source is traceable.
- **Docs — honesty boundaries.** KNOWN_LIMITATIONS (bilingual) now declares
  that hosted Windows Server CI does not equal consumer Windows 10/11
  verification, and a CI assertion keeps the bilingual entry counts aligned.

Evidence boundary: macOS-hosted pytest (957+), ruff, ruff format, and mypy pass
locally; hosted CI runs on push. Real-machine verification of the Windows
second-instance activation fix still requires a consumer Windows 10/11 machine
and is not claimed here.

## 中文

1.4.3-rc.1 是基于 1.4.2 的候选发布版。它应用了外部联合评审
（Gemini × DeepSeek × HubChat）中经逐条源码核实后接受的项；已被现有防御
机制覆盖的评审项均附证据驳回。

- **修复 — Windows 二次实例激活。** 二次启动改为按真实主窗口标题
  （`AI Agent Control Center`，经共享常量 `AACC_WINDOW_TITLE`）搜索，取代
  永不命中的 `"AACC"` 子串，并把已有面板提到前台。
- **安全 — 日志脱敏纵深防御。** sink 级 `RedactingFormatter` 的模式列表
  新增 `device_code`、`user_code`、`api_key`、`apikey` 字段。脱敏仍集中在
  日志 sink，未在业务代码中插入调用点级 `redact()`。
- **诊断 — Kimi Desktop 发现。** 所有候选 daimon 根目录都不存在时，记录
  一条列出全部探测路径的 INFO 日志，停用的发现源可追溯。
- **文档 — 诚实边界。** KNOWN_LIMITATIONS（双语）新增声明：托管
  Windows Server CI 不等于消费级 Windows 10/11 验证；并新增 CI 断言强制
  中英条目数对齐。

证据边界：本机 macOS 全量 pytest（957+）、ruff、ruff format、mypy 通过；
托管 CI 随推送运行。Windows 二次实例激活修复的真机验证仍需消费级
Windows 10/11 设备，本说明不宣称已完成。
