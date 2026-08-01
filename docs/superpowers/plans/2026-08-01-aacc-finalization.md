# AACC Finalization Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 修复 OpenCode 完成态误报、补齐 Codex 工作目录展示、统一 OpenCode 额度命名，并更新双语 GitHub 界面截图。

**Architecture:** 保留现有只读本地发现架构。OpenCode 只读取最近有限数量的 part 元数据，比较当前 step 的开始/结束边界；Codex 只从 rollout 文件头部的 \`payload.cwd\`/\`payload.directory\` 读取目录元数据。GUI 复用现有 \`TaskCard\` 工作目录展示，额度文案通过稳定 i18n key 统一，截图继续由合成数据脚本生成。

**Tech Stack:** Python 3.12+ / PySide6 / SQLite read-only discovery / pytest / ruff / mypy / tracked PNG screenshot fixtures。

## Global Constraints

- 所有行为修复必须先写失败测试并观察到正确失败，再写生产代码。
- OpenCode SQLite 只读取 part 的类型、工具状态、更新时间；不得读取或保存 prompt、回复、命令或 reasoning 内容。
- Codex rollout 只读取有限的元数据行；工作目录之外的 payload 不得进入 \`TaskState\`、日志或 UI。
- OpenCode 用户可见名称统一为中文 \`OpenCode 额度\`、英文 \`OpenCode quota\`；历史设计文档中的技术术语不强行重写。
- 双语 README、CHANGELOG、KNOWN_LIMITATIONS、1.4.3-rc.3 发布说明中的 OpenCode 用户可见“用量/usage”改为“额度/quota”；修复 README English 中的 \`monしゅs\` 拼写错误。
- 不改版本号、不推送 GitHub、不替换 GitHub release 资产；只更新仓库内代码、测试、文档与截图。
- 保留当前工作区已有未提交的 \`AGENTS.md\` 和 \`.superpowers/sdd/task-2-report.md\`、\`.superpowers/sdd/task-3-report.md\`。
- 每个任务单独提交，提交信息使用英文 \`fix:\`、\`feat:\` 或 \`docs:\` 前缀。
- 最终必须运行 \`.venv/bin/python -m pytest -q\`、\`.venv/bin/ruff check src tests\`、\`.venv/bin/ruff format --check src tests\`、\`.venv/bin/mypy src/aacc\` 和 \`git diff --check\`。

---

### Task 1: Keep completed OpenCode turns green

**Files:**
- Modify: \`src/aacc/opencode_discovery.py\`
- Test: \`tests/test_opencode_discovery.py\`

**Interfaces:**
- Consumes: existing \`part\` rows with JSON \`type\`, optional \`state.status\`, and \`time_updated\`.
- Produces: \`OpenCodePartSnapshot\` with optional \`completed_at\` and \`step_started_at\` metadata used only by \`evaluate_opencode_session_status\`.

- [ ] **Step 1: Write the failing regression tests**

Add integration tests beside the existing SQLite discovery tests. Build a session with chronological \`step-start\`, \`step-finish\`, then final \`text\`; assert \`TaskStatus.COMPLETED\`. Add a second test with \`step-start\` after the old \`step-finish\` and a fresh \`text\`; assert \`TaskStatus.RUNNING\`.

\`\`\`python
def test_final_text_after_step_finish_is_completed(tmp_path: Path) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    path, connection = _make_db(tmp_path)
    now = datetime.now(UTC)
    _add_session(connection, "ses_finished", updated=now)
    _add_part(connection, "ses_finished", "start", {"type": "step-start"}, now - timedelta(seconds=30))
    _add_part(connection, "ses_finished", "finish", {"type": "step-finish"}, now - timedelta(seconds=10))
    _add_part(connection, "ses_finished", "final-text", {"type": "text", "text": "secret"}, now)
    connection.commit()
    connection.close()

    task = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True).discover()[0]

    assert task.state.status is TaskStatus.COMPLETED
    assert "secret" not in str(task.state.metadata)


def test_new_step_after_previous_finish_remains_running(tmp_path: Path) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    path, connection = _make_db(tmp_path)
    now = datetime.now(UTC)
    _add_session(connection, "ses_running", updated=now)
    _add_part(connection, "ses_running", "old-start", {"type": "step-start"}, now - timedelta(seconds=40))
    _add_part(connection, "ses_running", "old-finish", {"type": "step-finish"}, now - timedelta(seconds=25))
    _add_part(connection, "ses_running", "new-start", {"type": "step-start"}, now - timedelta(seconds=5))
    _add_part(connection, "ses_running", "new-text", {"type": "text"}, now)
    connection.commit()
    connection.close()

    task = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True).discover()[0]

    assert task.state.status is TaskStatus.RUNNING
\`\`\`

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

\`\`\`bash
.venv/bin/python -m pytest -q tests/test_opencode_discovery.py -k 'final_text or new_step_after'
\`\`\`

Expected: the final-text test fails because the recent \`text\` part is classified as \`RUNNING\`; the new-step test protects against making every text part green.

- [ ] **Step 3: Implement bounded part-history inference**

In \`src/aacc/opencode_discovery.py\`:

1. Add \`_PART_HISTORY_LIMIT = 64\` and change \`_LATEST_PART_QUERY\` to return the newest 64 rows ordered by \`time_updated DESC, id DESC\`.
2. Extend \`OpenCodePartSnapshot\` with \`completed_at: datetime | None = None\` and \`step_started_at: datetime | None = None\`, preserving existing three-argument construction.
3. In \`_latest_part_snapshots\`, parse only the existing safe fields from each returned row. Set \`step_started_at\` to the newest \`step-start\` timestamp and \`completed_at\` to the newest \`step-finish\` or \`tool\` with \`state.status == "completed"\` timestamp. Keep the newest row as the primary \`part_type\`, \`state_status\`, and \`time_updated\`.
4. In \`evaluate_opencode_session_status\`, keep \`tool/pending\` and \`tool/running\` checks first. Before the streaming activity fallback, return \`COMPLETED\` when both boundary timestamps exist and \`completed_at >= step_started_at\`. Keep the existing fallback when no current step boundary can be established.
5. Do not include parsed part text or unrecognized JSON fields in returned state or logs.

- [ ] **Step 4: Run focused and related tests**

\`\`\`bash
.venv/bin/python -m pytest -q tests/test_opencode_discovery.py tests/test_discovery_service.py
\`\`\`

Expected: all focused OpenCode/discovery tests pass, including pending/running/finished behavior.

- [ ] **Step 5: Commit**

\`\`\`bash
git add src/aacc/opencode_discovery.py tests/test_opencode_discovery.py
git commit -m "fix: keep finished opencode turns green"
\`\`\`

### Task 2: Carry and display Codex working directories

**Files:**
- Modify: \`src/aacc/codex_discovery.py\`
- Modify: \`src/aacc/gui.py\`
- Test: \`tests/test_codex_discovery.py\`
- Test: \`tests/test_gui.py\`

**Interfaces:**
- Consumes: bounded first metadata records from Codex rollout JSONL.
- Produces: optional \`TaskState.metadata["work_dir"]\` and the existing \`TaskCard.workdir_label\` basename/full-path tooltip presentation.

- [ ] **Step 1: Write failing discovery and GUI tests**

Add a discovery test whose first metadata record contains \`payload.cwd\` and an unrelated secret field. Assert \`work_dir\` is present and the secret is absent from state. Add a \`payload.directory\` case if the helper supports it. Replace \`test_codex_card_hides_work_dir_label\` with a test supplying \`metadata={"work_dir": "/Users/test/Desktop/codelight"}\` and asserting \`· codelight\`, visible label, and full-path tooltip.

\`\`\`python
def test_discover_carries_payload_cwd_without_other_payload_data(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    session_id = "codex-dir-0001"
    (sessions / f"rollout-{session_id}.jsonl").write_text(
        '{"type":"session_meta","payload":{"cwd":"/work/aacc","secret":"do-not-copy"}}\n',
        encoding="utf-8",
    )
    index = tmp_path / "session_index.jsonl"
    index.write_text(
        '{"id":"codex-dir-0001","thread_name":"Directory task","updated_at":"2026-08-01T08:00:00Z"}\n',
        encoding="utf-8",
    )

    tasks = CodexLocalDiscovery(
        index,
        tmp_path / "missing-processes.json",
        session_directory=sessions,
        now=lambda: datetime(2026, 8, 1, 8, 0, 30, tzinfo=UTC),
    ).discover()

    assert tasks[0].state.metadata["work_dir"] == "/work/aacc"
    assert "do-not-copy" not in str(tasks[0].state)
\`\`\`

- [ ] **Step 2: Run focused tests and verify RED**

\`\`\`bash
.venv/bin/python -m pytest -q tests/test_codex_discovery.py -k 'work_dir or directory' tests/test_gui.py -k 'codex_card'
\`\`\`

Expected: discovery fails because no \`work_dir\` metadata is emitted, and the GUI test fails because Codex is excluded from the display condition.

- [ ] **Step 3: Implement bounded Codex metadata extraction**

In \`CodexLocalDiscovery.discover\`, resolve the latest rollout path for each selected conversation and call \`_read_session_work_dir(path)\`. The helper reads at most the first 64 lines, caps each line at \`MAX_SESSION_METADATA_LINE_BYTES\`, parses valid JSON only, inspects only \`item["payload"]\`, and returns the first non-empty string from \`payload["cwd"]\` or \`payload["directory"]\`. Add the value to the existing metadata dict only when valid.

In \`TaskCard._render_state\`, change the work-directory agent condition from \`("kimi_code", "opencode_cli")\` to \`("codex_cli", "kimi_code", "opencode_cli")\`. Keep \`PurePath(work_dir).name\`, the full path tooltip, and hide behavior for missing values.

- [ ] **Step 4: Run focused and related tests**

\`\`\`bash
.venv/bin/python -m pytest -q tests/test_codex_discovery.py tests/test_gui.py -k 'codex or work_dir'
\`\`\`

Expected: new and existing Codex discovery/status tests pass without exposing payload content.

- [ ] **Step 5: Commit**

\`\`\`bash
git add src/aacc/codex_discovery.py src/aacc/gui.py tests/test_codex_discovery.py tests/test_gui.py
git commit -m "feat: show Codex working directories"
\`\`\`

### Task 3: Normalize OpenCode quota terminology and tooltip output

**Files:**
- Modify: \`src/aacc/i18n.py\`
- Modify: \`src/aacc/gui.py\`
- Modify: \`README.md\`
- Modify: \`README.zh-CN.md\`
- Modify: \`CHANGELOG.md\`
- Modify: \`CHANGELOG.zh-CN.md\`
- Modify: \`KNOWN_LIMITATIONS.md\`
- Modify: \`KNOWN_LIMITATIONS.zh-CN.md\`
- Modify: \`docs/release-notes-1.4.3rc3.md\`
- Test: \`tests/test_i18n.py\`
- Test: \`tests/test_opencode_quota_bar.py\`
- Test: \`tests/test_opencode_web_error.py\`

**Interfaces:**
- Consumes: stable \`opencode.quota\` and \`opencode.web_*\` translation keys.
- Produces: \`OpenCode quota\`/\`OpenCode 额度\` in current user-visible OpenCode quota surfaces; no \`None%\` tooltip.

- [ ] **Step 1: Write failing terminology and tooltip tests**

Update/add assertions before changing catalogs:

\`\`\`python
def test_opencode_quota_bar_uses_quota_wording_and_never_none_percent(qtbot) -> None:
    zh = OpenCodeQuotaBar(LanguageManager(ZH_CN))
    zh.show_quota(
        OpenCodeQuota(
            rolling=OpenCodeUsage(None, 60, datetime.now(UTC) + timedelta(seconds=60)),
            weekly=None,
            monthly=None,
            status=QuotaStatus.PARTIAL,
            fetched_at=None,
        )
    )
    assert "OpenCode 额度" in zh.summary_label.text()
    assert "None%" not in zh.toolTip()
    assert "5H: --" in zh.toolTip()

    en = OpenCodeQuotaBar(LanguageManager(EN_US))
    assert en.language_manager.text("opencode.quota") == "OpenCode quota"
\`\`\`

Change existing English expected label from \`OpenCode usage\` to \`OpenCode quota\`, and error-category expectation from \`OpenCode usage refresh timed out\` to \`OpenCode quota refresh timed out\`.

- [ ] **Step 2: Run focused tests and verify RED**

\`\`\`bash
.venv/bin/python -m pytest -q tests/test_opencode_quota_bar.py tests/test_opencode_web_error.py tests/test_i18n.py -k 'opencode or quota_wording'
\`\`\`

Expected: terminology assertions fail on old catalog values and partial tooltip fails on \`None%\`.

- [ ] **Step 3: Implement minimal wording/render fixes**

Change both catalog values:

\`\`\`python
"opencode.quota": "OpenCode 额度"       # ZH_CN
"opencode.quota": "OpenCode quota"     # EN_US
"opencode.web_refresh_timeout": "OpenCode 额度刷新超时"
"opencode.web_refresh_failed": "OpenCode 额度刷新失败"
"opencode.web_parse_failed": "OpenCode 额度数据解析失败"
"opencode.web_refresh_timeout": "OpenCode quota refresh timed out"
"opencode.web_refresh_failed": "OpenCode quota refresh failed"
"opencode.web_parse_failed": "OpenCode quota data could not be parsed"
\`\`\`

In \`OpenCodeQuotaBar\`, use \`opencode.quota\` for unauthorized/unknown/partial summaries and replace hard-coded \`usage\` wording in tooltips with \`quota\`. In \`_detail_tooltip\`, render \`percentage_text = "--" if usage.percentage is None else f"{usage.percentage}%"\`.

Update current user-facing OpenCode references in README, changelog, known limitations, and \`docs/release-notes-1.4.3rc3.md\` from usage/用量 to quota/额度. Correct \`monしゅs status\` to \`monitored status\` in \`README.md\`.

- [ ] **Step 4: Run focused tests and user-facing search**

\`\`\`bash
.venv/bin/python -m pytest -q tests/test_opencode_quota_bar.py tests/test_opencode_web_error.py tests/test_i18n.py
rg -n -i 'OpenCode (usage|用量)|用量.*OpenCode|usage.*OpenCode|OpenCode.*usage' README.md README.zh-CN.md CHANGELOG.md CHANGELOG.zh-CN.md KNOWN_LIMITATIONS.md KNOWN_LIMITATIONS.zh-CN.md docs/release-notes-1.4.3rc3.md src/aacc tests
\`\`\`

Expected: focused tests pass; the search returns no current user-visible OpenCode usage wording. Technical \`usage\` identifiers in Python model names may remain outside prose/catalog surfaces.

- [ ] **Step 5: Commit**

\`\`\`bash
git add src/aacc/i18n.py src/aacc/gui.py README.md README.zh-CN.md CHANGELOG.md CHANGELOG.zh-CN.md KNOWN_LIMITATIONS.md KNOWN_LIMITATIONS.zh-CN.md docs/release-notes-1.4.3rc3.md tests/test_i18n.py tests/test_opencode_quota_bar.py tests/test_opencode_web_error.py
git commit -m "fix: call OpenCode limits quota"
\`\`\`

### Task 4: Regenerate bilingual screenshots with OpenCode quota

**Files:**
- Modify: \`scripts/capture_panel_screenshot.py\`
- Modify: \`tests/test_release_docs.py\`
- Modify: \`docs/images/panel-overview.png\`
- Modify: \`docs/images/panel-overview.en.png\`

**Interfaces:**
- Consumes: synthetic \`OpenCodeQuota\`, \`OpenCodeUsage\`, and a fake Qt signal service.
- Produces: fixed-size 420x650 bilingual PNGs showing Codex, Kimi, and OpenCode quota bars and a Codex working-directory label.

- [ ] **Step 1: Write failing screenshot-contract assertions**

Extend \`test_screenshot_fixture_is_fixed_and_privacy_safe\` to require \`OPENCODE_5H\`, \`OPENCODE_WEEK\`, \`OPENCODE_MONTH\`, \`OpenCodeQuota\`, and \`opencode_web_quota_service\` in the capture script. Change expected PNG size to \`(420, 650)\`.

- [ ] **Step 2: Run contract test and verify RED**

\`\`\`bash
.venv/bin/python -m pytest -q tests/test_release_docs.py -k 'screenshot_fixture'
\`\`\`

Expected: the test fails because the current script has no OpenCode fixture and current PNGs are 420x577.

- [ ] **Step 3: Extend the synthetic capture script**

Add imports for \`OpenCodeQuota\` and \`OpenCodeUsage\`, constants \`OPENCODE_5H = 12\`, \`OPENCODE_WEEK = 44\`, \`OPENCODE_MONTH = 68\`, and a \`_DemoOpenCodeWebQuotaService(QObject)\` with \`quota_updated\`, \`login_state_changed\`, and \`error_occurred\` signals plus no-op login/refresh/logout methods. Construct the quota using \`DEMO_NOW + timedelta(...)\`, pass the fake service as \`opencode_web_quota_service\`, emit authorized state and quota after \`window.show()\`, set demo Codex \`work_dir="C:/AACC-Demo/sample-project"\`, and change fixed window size/assertion to 420x650. Keep all data synthetic and free of real home paths/tokens.

- [ ] **Step 4: Generate, inspect, and verify both images**

\`\`\`bash
.venv/bin/python scripts/capture_panel_screenshot.py docs/images/panel-overview.png
AACC_SCREENSHOT_LANG=en_US .venv/bin/python scripts/capture_panel_screenshot.py docs/images/panel-overview.en.png
.venv/bin/python -m pytest -q tests/test_release_docs.py -k 'screenshot_fixture'
\`\`\`

Use the image viewer to confirm both images visibly contain the OpenCode quota strip, Codex directory basename, and no clipped critical labels. Expected: both PNGs are valid 420x650 synthetic fixtures and the contract passes.

- [ ] **Step 5: Commit**

\`\`\`bash
git add scripts/capture_panel_screenshot.py tests/test_release_docs.py docs/images/panel-overview.png docs/images/panel-overview.en.png
git commit -m "docs: refresh bilingual quota screenshots"
\`\`\`

### Task 5: Final verification and independent review

**Files:**
- Inspect: all task commits and final working-tree diff.

- [ ] **Step 1: Run complete project gates**

\`\`\`bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/aacc
git diff --check 840a2ee..HEAD
\`\`\`

Expected: pytest has zero failures, ruff/mypy exit 0, formatting and diff check are clean. The diff check starts at the committed design ```840a2ee``` and excludes the pre-existing unrelated working-tree edits.

- [ ] **Step 2: Review final diff for scope and privacy**

Confirm only design/plan docs, targeted source/tests/docs, and two synthetic screenshots changed relative to the pre-existing unrelated worktree edits. Confirm no prompt, response, command, token, cookie, or real home path was added to source, tests, logs, or PNGs.

- [ ] **Step 3: Request independent code review**

Use the requesting-code-review workflow with the base commit immediately before \`fix: keep finished opencode turns green\` and final HEAD. Fix Critical/Important findings, rerun all final gates, then report exact evidence and remaining limitations.
