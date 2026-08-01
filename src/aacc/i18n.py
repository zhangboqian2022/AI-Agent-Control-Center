from __future__ import annotations

import logging
import string
from collections.abc import Callable
from contextlib import suppress
from typing import Literal

from PySide6.QtCore import QLocale, QSettings

Language = Literal["zh_CN", "en_US"]
LanguageSubscriberComponent = Literal[
    "main_window",
    "kimi_oauth_dialog",
    "kimi_web_session",
    "opencode_web_session",
    "test",
]
ZH_CN: Language = "zh_CN"
EN_US: Language = "en_US"
SUPPORTED_LANGUAGES = frozenset({ZH_CN, EN_US})
LANGUAGE_SUBSCRIBER_COMPONENTS = frozenset(
    {
        "main_window",
        "kimi_oauth_dialog",
        "kimi_web_session",
        "opencode_web_session",
        "test",
    }
)
SAFE_ENGLISH_TEXT_FALLBACK = "Interface text unavailable"
_logger = logging.getLogger("aacc.i18n")

CATALOGS: dict[Language, dict[str, str]] = {
    ZH_CN: {
        "language.switch_to_english": "切换到英文界面",
        "language.switch_to_chinese": "切换到中文界面",
        "status.unconfigured": "未配置",
        "status.idle": "空闲",
        "status.starting": "启动中",
        "status.thinking": "思考中",
        "status.running": "执行中",
        "status.waiting_input": "等待输入",
        "status.waiting_approval": "等待批准",
        "status.completed": "已完成",
        "status.warning": "警告",
        "status.error": "失败",
        "status.paused": "已暂停",
        "status.cancelled": "已取消",
        "status.stopped": "已停止",
        "status.unknown": "状态未知",
        "time.total": "总用时 {elapsed}",
        "quota.reset": "{month}月{day}日 {hour:02d}:{minute:02d} 重置",
        "header.about": "关于",
        "header.settings": "设置",
        "header.hide": "隐藏",
        "header.quit": "退出 AACC",
        "summary.tasks.one": "{count} 个任务",
        "summary.tasks.other": "{count} 个任务",
        "group.running": "运行中",
        "group.completed": "已完成",
        "group.clear_all": "清除全部",
        "empty.no_tasks": "暂无任务",
        "quota.kimi": "Kimi 额度",
        "quota.codex": "Codex 额度",
        "quota.authorize": "授权",
        "quota.authorizing": "授权中…",
        "quota.unavailable": "额度不可用",
        "quota.partial": "部分额度可用",
        "quota.stale": "额度信息已过期",
        "quota.refresh": "刷新额度",
        "quota.five_hour": "5H",
        "quota.week": "WEEK",
        "quota.month": "MONTH",
        "quota.membership": "会员额度",
        "quota.booster": "加油包",
        "quota.last_update": "上次更新 {updated}",
        "opencode.quota": "OpenCode 额度",
        "opencode.web_title": "OpenCode 工作区登录",
        "opencode.web_starting": "正在启动 OpenCode 登录页面，请稍候…",
        "opencode.web_need_config": "请先在 config.yaml 中配置 opencode_workspace_url",
        "opencode.web_unauthorized": "OpenCode 登录已过期，请重新授权",
        "opencode.web_refresh_timeout": "OpenCode 额度刷新超时",
        "opencode.web_refresh_failed": "OpenCode 额度刷新失败",
        "opencode.web_parse_failed": "OpenCode 额度数据解析失败",
        "task.switch": "切换到任务",
        "task.manual_status": "手动状态",
        "task.copy": "复制",
        "task.rename": "重命名",
        "task.remove": "移除",
        "task.no_message": "暂无消息",
        "task.message.running": "正在运行",
        "task.message.analyzing": "正在分析任务",
        "task.message.generating": "正在生成回复",
        "task.message.testing": "正在运行测试",
        "task.message.building": "正在构建程序",
        "task.message.inspecting": "正在检查代码",
        "task.message.executing": "正在执行命令",
        "task.message.editing": "正在修改代码",
        "task.message.researching": "正在查询资料",
        "task.message.waiting_input": "等待输入",
        "task.message.waiting_approval": "等待同意",
        "task.message.idle": "空闲",
        "task.message.completed": "已完成",
        "task.message.turn_completed": "回合已完成",
        "task.message.no_process": "未检测到运行进程",
        "task.message.recent_no_process": "最近更新，未检测到运行进程",
        "task.last_activity": "最后活动 {elapsed}",
        "task.updated": "更新于 {updated}",
        "compact.toggle": "紧凑模式",
        "topmost.toggle": "始终置顶",
        "dock.top_right": "停靠到右上角",
        "tray.show_hide": "显示/隐藏 AACC",
        "tray.quit": "退出 AACC",
        "settings.title": "AACC 设置",
        "settings.opacity": "面板透明度",
        "settings.config_file": "配置文件\n{path}",
        "settings.accessibility": "打开辅助功能设置",
        "settings.compact": "切换紧凑 / 展开模式",
        "settings.topmost": "切换始终置顶",
        "settings.dock": "停靠到桌面右上角",
        "settings.select_codex": "选择监控的 Codex 任务",
        "settings.select_kimi_code": "选择监控的 Kimi Code 任务",
        "settings.select_kimi_desktop": "选择监控的 Kimi Desktop 任务",
        "settings.select_opencode": "选择监控的 OpenCode 任务",
        "settings.selected_counts": "（{selected} 已选 · {automatic} 自动运行）",
        "settings.rotate_api": "重置 API 凭证",
        "settings.kimi_fallback": "Kimi Code 备用授权（可用 API Key 替代 OAuth）",
        "settings.save_kimi_key": "保存 Kimi API Key",
        "settings.kimi_key_required": "API Key 不能为空",
        "settings.kimi_web_login": "登录 Kimi 会员（同步 5H / WEEK / MONTH）",
        "settings.kimi_edge_login": "使用专用 Edge 登录 Kimi（同步 5H / WEEK / MONTH）",
        "settings.kimi_logout": "退出 Kimi 登录",
        "settings.opencode_web_login": "登录 OpenCode（同步 5H / WEEK / MONTH）",
        "settings.opencode_logout": "退出 OpenCode",
        "settings.visible_agents": "显示哪些程序",
        "settings.generic_cli": "Z Code / 通用 CLI",
        "selector.running_hint": "运行中的任务会自动勾选；取消勾选可停止自动监控该任务。",
        "selector.auto_running": "\n自动监控 · 运行中",
        "selector.select_all": "全选",
        "selector.clear_all": "全部取消",
        "selector.restore_auto": "恢复自动识别",
        "selector.start_monitoring": "开始监控",
        "rename.title": "重命名任务",
        "rename.prompt": "任务名称（留空恢复默认）：",
        "clear_completed.title": "清除已完成任务",
        "clear_completed.prompt.one": "确定从面板移除 {count} 个已完成任务吗？",
        "clear_completed.prompt.other": "确定从面板移除 {count} 个已完成任务吗？",
        "credentials.reset_title": "重置凭证",
        "credentials.reset_prompt": "旧凭证会立即失效，是否继续？",
        "credentials.reset_done_title": "凭证已重置",
        "credentials.reset_done_text": "旧凭证已失效。新凭证如下（不会自动写入剪贴板）：",
        "credentials.copy": "复制",
        "accessibility.enabled": "辅助功能权限：已开启",
        "accessibility.disabled": "辅助功能权限：未开启；全局热键与键盘输入不可用",
        "accessibility.title": "需要辅助功能权限",
        "accessibility.prompt": (
            "AACC 需要辅助功能权限才能使用全局热键和键盘输入。是否打开系统设置？"
        ),
        "accessibility.do_not_remind": "不再提示",
        "about.title": "关于 AACC",
        "about.body.macos": "AI Agent Control Center\n版本 {version}\nmacOS DMG AACC-{version}.dmg",
        "about.body.windows": (
            "AI Agent Control Center\n版本 {version}\nWindows Setup AACC-{version}-Setup.exe"
        ),
        "kimi.device_title": "Kimi 授权",
        "kimi.device_opened": "浏览器已打开 Kimi 授权页面，请确认以下验证码：",
        "kimi.device_finished": "授权完成后此窗口会自动关闭",
        "kimi.device_cancel": "取消授权",
        "kimi.web_title": "Kimi 会员网页登录",
        "kimi.web_explanation": (
            "请直接在 Kimi 官网完成登录。AACC 只复用系统 WebView 会话，"
            "不保存账号密码；登录成功后会自动同步 5H、WEEK 和 MONTH。"
        ),
        "kimi.web_starting": "正在启动 Kimi 登录页面，请稍候…",
        "kimi.web_diagnostic": (
            "无法启动 Kimi 登录页面。请检查网络或修复 Microsoft Edge WebView2 Runtime，然后重试。"
        ),
        "kimi.web_repair": "修复 Microsoft Edge WebView2",
        "kimi.web_load_failed": "Kimi 官网加载失败",
        "kimi.web_refresh_timeout": "Kimi 会员额度刷新超时",
        "kimi.web_refresh_failed": "Kimi 会员额度刷新失败",
        "kimi.web_state_save_failed": "Kimi 网页登录状态保存失败",
        "kimi.code_refresh_failed": "Kimi Code 额度刷新失败",
        "kimi.code_oauth_failed": "Kimi Code 授权失败",
        "kimi.code_fallback_refresh_failed": "Kimi Code 备用额度刷新失败",
        "kimi.oauth_failed": "KIMI 授权失败",
        "kimi.logout_partial": "Kimi 退出登录未完全完成",
        "feedback.operation_failed": "操作未生效",
        "feedback.task_selected": "已选择 {name}",
        "feedback.manual_update": "手动更新",
        "feedback.status_marked": "已标记为 {status}",
        "feedback.task_copied": "任务信息已复制",
        "feedback.automation_queued": "自动操作已排队",
        "usage.cache": "缓存",
        "automation.focused": "已聚焦 {name}",
        "automation.key_sent": "已发送 {key}",
        "automation.text_sent": "文本已发送",
        "automation.voice_started": "已触发系统语音输入",
        "automation.timeout": "桌面自动化超时",
        "automation.unavailable": "桌面自动化不可用",
        "automation.cancelled": "桌面自动化已取消",
        "automation.unsupported_operation": "不支持的桌面自动化操作",
        "automation.executor_closed": "桌面自动化执行器已关闭",
        "automation.queue_full": "桌面自动化队列已满",
        "automation.window_not_found": "未找到目标窗口",
        "automation.window_focus_failed": "无法将目标窗口置前",
        "automation.app_unconfigured": "未配置目标应用",
        "automation.injection_disabled": "AACC 设置中已停用键盘输入",
        "automation.accessibility_required": "需要辅助功能权限",
        "automation.key_not_allowed": "不允许发送该按键",
        "automation.text_invalid": "文本长度必须为 1 到 2000 个字符",
        "automation.text_nul": "文本不能包含 NUL 字符",
        "automation.voice_hotkey_unsupported": "不支持当前语音热键",
        "common.cancel": "取消",
        "common.ok": "确定",
        "common.yes": "是",
        "common.close": "关闭",
        "common.done": "完成",
        "common.apply": "应用",
    },
    EN_US: {
        "language.switch_to_english": "Switch to English",
        "language.switch_to_chinese": "Switch to Chinese",
        "status.unconfigured": "Not configured",
        "status.idle": "Idle",
        "status.starting": "Starting",
        "status.thinking": "Thinking",
        "status.running": "Running",
        "status.waiting_input": "Waiting for input",
        "status.waiting_approval": "Pending",
        "status.completed": "Completed",
        "status.warning": "Warning",
        "status.error": "Failed",
        "status.paused": "Paused",
        "status.cancelled": "Cancelled",
        "status.stopped": "Stopped",
        "status.unknown": "Unknown",
        "time.total": "Total {elapsed}",
        "quota.reset": "Resets {month}/{day} {hour:02d}:{minute:02d}",
        "header.about": "About",
        "header.settings": "Settings",
        "header.hide": "Hide",
        "header.quit": "Quit AACC",
        "summary.tasks.one": "{count} task",
        "summary.tasks.other": "{count} tasks",
        "group.running": "Running",
        "group.completed": "Completed",
        "group.clear_all": "Clear all",
        "empty.no_tasks": "No tasks",
        "quota.kimi": "Kimi quota",
        "quota.codex": "Codex quota",
        "quota.authorize": "Authorize",
        "quota.authorizing": "Authorizing…",
        "quota.unavailable": "Quota unavailable",
        "quota.partial": "Partial quota data",
        "quota.stale": "Quota data is stale",
        "quota.refresh": "Refresh quota",
        "quota.five_hour": "5H",
        "quota.week": "WEEK",
        "quota.month": "MONTH",
        "quota.membership": "Membership quota",
        "quota.booster": "Booster pack",
        "quota.last_update": "Last updated {updated}",
        "opencode.quota": "OpenCode quota",
        "opencode.web_title": "OpenCode workspace login",
        "opencode.web_starting": "Starting the OpenCode login page. Please wait…",
        "opencode.web_need_config": "Set opencode_workspace_url in config.yaml first",
        "opencode.web_unauthorized": "OpenCode sign-in expired. Please authorize again",
        "opencode.web_refresh_timeout": "OpenCode quota refresh timed out",
        "opencode.web_refresh_failed": "OpenCode quota refresh failed",
        "opencode.web_parse_failed": "OpenCode quota data could not be parsed",
        "task.switch": "Switch to task",
        "task.manual_status": "Manual status",
        "task.copy": "Copy",
        "task.rename": "Rename",
        "task.remove": "Remove",
        "task.no_message": "No message",
        "task.message.running": "Running",
        "task.message.analyzing": "Analyzing task",
        "task.message.generating": "Generating a response",
        "task.message.testing": "Running tests",
        "task.message.building": "Building the program",
        "task.message.inspecting": "Inspecting code",
        "task.message.executing": "Running a command",
        "task.message.editing": "Modifying code",
        "task.message.researching": "Researching",
        "task.message.waiting_input": "Waiting for input",
        "task.message.waiting_approval": "Waiting for approval",
        "task.message.idle": "Idle",
        "task.message.completed": "Completed",
        "task.message.turn_completed": "Turn completed",
        "task.message.no_process": "No running process detected",
        "task.message.recent_no_process": "Recently updated; no running process detected",
        "task.last_activity": "Last activity {elapsed}",
        "task.updated": "Updated {updated}",
        "compact.toggle": "Compact mode",
        "topmost.toggle": "Always on top",
        "dock.top_right": "Dock to top right",
        "tray.show_hide": "Show/Hide AACC",
        "tray.quit": "Quit AACC",
        "settings.title": "AACC Settings",
        "settings.opacity": "Panel opacity",
        "settings.config_file": "Configuration file\n{path}",
        "settings.accessibility": "Open Accessibility settings",
        "settings.compact": "Compact / expanded mode",
        "settings.topmost": "Toggle always on top",
        "settings.dock": "Dock to top right",
        "settings.select_codex": "Select Codex tasks to monitor",
        "settings.select_kimi_code": "Select Kimi Code tasks to monitor",
        "settings.select_kimi_desktop": "Select Kimi Desktop tasks to monitor",
        "settings.select_opencode": "Select OpenCode tasks to monitor",
        "settings.selected_counts": " ({selected} selected · {automatic} running automatically)",
        "settings.rotate_api": "Reset API credentials",
        "settings.kimi_fallback": "Kimi Code fallback authorization (API Key instead of OAuth)",
        "settings.save_kimi_key": "Save Kimi API Key",
        "settings.kimi_key_required": "API Key is required",
        "settings.kimi_web_login": "Sign in to Kimi membership (sync 5H / WEEK / MONTH)",
        "settings.kimi_edge_login": (
            "Sign in to Kimi with dedicated Edge (sync 5H / WEEK / MONTH)"
        ),
        "settings.kimi_logout": "Sign out of Kimi",
        "settings.opencode_web_login": "Sign in to OpenCode (sync 5H / WEEK / MONTH)",
        "settings.opencode_logout": "Sign out of OpenCode",
        "settings.visible_agents": "Visible applications",
        "settings.generic_cli": "Z Code / Generic CLI",
        "selector.running_hint": (
            "Running tasks are selected automatically; clear one to stop monitoring it "
            "automatically."
        ),
        "selector.auto_running": "\nAutomatic monitoring · Running",
        "selector.select_all": "Select all",
        "selector.clear_all": "Clear all",
        "selector.restore_auto": "Restore automatic detection",
        "selector.start_monitoring": "Start monitoring",
        "rename.title": "Rename task",
        "rename.prompt": "Task name (leave blank to restore the default):",
        "clear_completed.title": "Clear completed tasks",
        "clear_completed.prompt.one": "Remove {count} completed task from the panel?",
        "clear_completed.prompt.other": "Remove {count} completed tasks from the panel?",
        "credentials.reset_title": "Reset credentials",
        "credentials.reset_prompt": (
            "The old credentials will become invalid immediately. Continue?"
        ),
        "credentials.reset_done_title": "Credentials reset",
        "credentials.reset_done_text": (
            "The old credentials are invalid. The new credentials are shown below "
            "(they were not copied to the clipboard):"
        ),
        "credentials.copy": "Copy",
        "accessibility.enabled": "Accessibility permission: Enabled",
        "accessibility.disabled": (
            "Accessibility permission: Disabled; global hotkeys and keyboard input are unavailable"
        ),
        "accessibility.title": "Accessibility permission required",
        "accessibility.prompt": (
            "AACC needs Accessibility permission for global hotkeys and keyboard input. "
            "Open System Settings?"
        ),
        "accessibility.do_not_remind": "Do not remind me again",
        "about.title": "About AACC",
        "about.body.macos": (
            "AI Agent Control Center\nVersion {version}\nmacOS DMG AACC-{version}.dmg"
        ),
        "about.body.windows": (
            "AI Agent Control Center\nVersion {version}\nWindows Setup AACC-{version}-Setup.exe"
        ),
        "kimi.device_title": "Kimi authorization",
        "kimi.device_opened": (
            "The Kimi authorization page is open in your browser. Confirm this code:"
        ),
        "kimi.device_finished": "This window closes automatically after authorization",
        "kimi.device_cancel": "Cancel authorization",
        "kimi.web_title": "Kimi membership login",
        "kimi.web_explanation": (
            "Sign in directly on the Kimi website. AACC only reuses the system WebView "
            "session and never stores your account or password. After sign-in, AACC "
            "automatically syncs 5H, WEEK and MONTH."
        ),
        "kimi.web_starting": "Starting the Kimi login page. Please wait…",
        "kimi.web_diagnostic": (
            "Kimi login could not start. Check your network or repair Microsoft Edge "
            "WebView2 Runtime, then try again."
        ),
        "kimi.web_repair": "Repair Microsoft Edge WebView2",
        "kimi.web_load_failed": "The Kimi website failed to load",
        "kimi.web_refresh_timeout": "Kimi membership quota refresh timed out",
        "kimi.web_refresh_failed": "Kimi membership quota refresh failed",
        "kimi.web_state_save_failed": "Kimi web login state could not be saved",
        "kimi.code_refresh_failed": "Kimi Code quota refresh failed",
        "kimi.code_oauth_failed": "Kimi Code authorization failed",
        "kimi.code_fallback_refresh_failed": "Kimi Code fallback quota refresh failed",
        "kimi.oauth_failed": "KIMI authorization failed",
        "kimi.logout_partial": "Kimi sign-out did not fully complete",
        "feedback.operation_failed": "Operation had no effect",
        "feedback.task_selected": "Selected {name}",
        "feedback.manual_update": "Manually updated",
        "feedback.status_marked": "Marked as {status}",
        "feedback.task_copied": "Task information copied",
        "feedback.automation_queued": "Automation queued",
        "usage.cache": "Cache",
        "automation.focused": "Focused {name}",
        "automation.key_sent": "Sent {key}",
        "automation.text_sent": "Text sent",
        "automation.voice_started": "Started system voice input",
        "automation.timeout": "Desktop automation timed out",
        "automation.unavailable": "Desktop automation is unavailable",
        "automation.cancelled": "Desktop automation was cancelled",
        "automation.unsupported_operation": "Unsupported desktop automation operation",
        "automation.executor_closed": "Desktop automation executor is closed",
        "automation.queue_full": "Desktop automation queue is full",
        "automation.window_not_found": "Target window was not found",
        "automation.window_focus_failed": "Target window could not be brought to the front",
        "automation.app_unconfigured": "Target application is not configured",
        "automation.injection_disabled": "Keyboard input is disabled in AACC settings",
        "automation.accessibility_required": "Accessibility permission is required",
        "automation.key_not_allowed": "That key is not allowed",
        "automation.text_invalid": "Text must contain 1 to 2000 characters",
        "automation.text_nul": "Text must not contain NUL",
        "automation.voice_hotkey_unsupported": "The configured voice hotkey is unsupported",
        "common.cancel": "Cancel",
        "common.ok": "OK",
        "common.yes": "Yes",
        "common.close": "Close",
        "common.done": "Done",
        "common.apply": "Apply",
    },
}


def detect_system_language(locale_name: str | None = None) -> Language:
    name = locale_name or QLocale.system().name()
    return ZH_CN if name.casefold().replace("-", "_").startswith("zh_") else EN_US


def load_language(settings: QSettings, locale_name: str | None = None) -> Language:
    value = settings.value("ui_language")
    if isinstance(value, str) and value in SUPPORTED_LANGUAGES:
        return value
    return detect_system_language(locale_name)


def other_language(language: Language) -> Language:
    return EN_US if language == ZH_CN else ZH_CN


def catalog_keys(language: Language) -> frozenset[str]:
    return frozenset(CATALOGS[language])


def placeholder_names(language: Language, key: str) -> frozenset[str]:
    template = CATALOGS[language][key]
    return frozenset(
        field_name.split(".", maxsplit=1)[0].split("[", maxsplit=1)[0]
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name is not None and field_name
    )


class LanguageManager:
    def __init__(self, language: Language, settings: QSettings | None = None) -> None:
        self.language = language
        self._settings = settings
        self._subscribers: list[tuple[str, Callable[[], None]]] = []

    def text(self, translation_key: str, **values: object) -> str:
        template = CATALOGS[self.language].get(translation_key)
        if template is None:
            _logger.warning("Unknown translation key key=%s", translation_key)
            return SAFE_ENGLISH_TEXT_FALLBACK
        try:
            return template.format(**values)
        except Exception:  # noqa: BLE001 - a formatter value may raise arbitrary exceptions
            _logger.warning("Translation formatting failed key=%s", translation_key)
            return CATALOGS[EN_US][translation_key]

    def set_language(self, language: Language) -> None:
        if language == self.language:
            return

        self.language = language
        if self._settings is not None:
            try:
                self._settings.setValue("ui_language", language)
                self._settings.sync()
                if self._settings.status() != QSettings.Status.NoError:
                    _logger.warning("Language preference persistence failed")
            except Exception:  # noqa: BLE001 - persistence must not block live retranslation
                _logger.warning("Language preference persistence failed")

        for component, callback in tuple(self._subscribers):
            try:
                callback()
            except Exception:
                _logger.warning("Language subscriber failed component=%s", component)

    def subscribe(
        self,
        callback: Callable[[], None],
        *,
        component: LanguageSubscriberComponent,
    ) -> Callable[[], None]:
        safe_component = component if component in LANGUAGE_SUBSCRIBER_COMPONENTS else "unknown"
        subscription = (safe_component, callback)
        self._subscribers.append(subscription)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._subscribers.remove(subscription)

        return unsubscribe
