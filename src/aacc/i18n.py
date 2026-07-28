from __future__ import annotations

import logging
import string
from collections.abc import Callable
from contextlib import suppress
from typing import Literal

from PySide6.QtCore import QLocale, QSettings

Language = Literal["zh_CN", "en_US"]
ZH_CN: Language = "zh_CN"
EN_US: Language = "en_US"
SUPPORTED_LANGUAGES = frozenset({ZH_CN, EN_US})
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
        "summary.tasks": "{count} 个任务",
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
        "task.switch": "切换到任务",
        "task.manual_status": "手动状态",
        "task.copy": "复制",
        "task.rename": "重命名",
        "task.remove": "移除",
        "task.no_message": "暂无消息",
        "task.last_activity": "最后活动 {elapsed}",
        "task.updated": "更新于 {updated}",
        "compact.toggle": "紧凑模式",
        "topmost.toggle": "始终置顶",
        "dock.top_right": "停靠到右上角",
        "tray.show_hide": "显示/隐藏 AACC",
        "tray.quit": "退出 AACC",
        "common.cancel": "取消",
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
        "status.waiting_approval": "Waiting for approval",
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
        "summary.tasks": "{count} tasks",
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
        "task.switch": "Switch to task",
        "task.manual_status": "Manual status",
        "task.copy": "Copy",
        "task.rename": "Rename",
        "task.remove": "Remove",
        "task.no_message": "No message",
        "task.last_activity": "Last activity {elapsed}",
        "task.updated": "Updated {updated}",
        "compact.toggle": "Compact mode",
        "topmost.toggle": "Always on top",
        "dock.top_right": "Dock to top right",
        "tray.show_hide": "Show/Hide AACC",
        "tray.quit": "Quit AACC",
        "common.cancel": "Cancel",
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
        self._subscribers: list[Callable[[], None]] = []

    def text(self, key: str, **values: object) -> str:
        template = CATALOGS[self.language].get(key)
        if template is None:
            return key
        return template.format(**values)

    def set_language(self, language: Language) -> None:
        if language == self.language:
            return

        self.language = language
        if self._settings is not None:
            self._settings.setValue("ui_language", language)

        for callback in tuple(self._subscribers):
            try:
                callback()
            except Exception:
                _logger.warning("Language subscriber failed")

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._subscribers.remove(callback)

        return unsubscribe
