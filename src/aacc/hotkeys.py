from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

FUNCTION_KEYCODES = {
    "F13": 105,
    "F14": 107,
    "F15": 113,
    "F16": 106,
    "F17": 64,
    "F18": 79,
    "F19": 80,
    "F20": 90,
}


def hotkey_keycode(name: str) -> int:
    normalized = name.strip().upper()
    try:
        return FUNCTION_KEYCODES[normalized]
    except KeyError as error:
        raise ValueError(f"Unsupported global hotkey: {name}") from error


class GlobalHotkeys:
    def __init__(self, bindings: dict[str, str], actions: dict[str, Callable[[], None]]) -> None:
        self._actions_by_code = {
            hotkey_keycode(hotkey): actions[action]
            for action, hotkey in bindings.items()
            if action in actions
        }
        self._thread: threading.Thread | None = None
        self._run_loop: Any = None
        self._ready = threading.Event()
        self.error: str | None = None

    def start(self) -> bool:
        if self._thread is not None:
            return self.error is None
        self._thread = threading.Thread(target=self._run, name="aacc-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2)
        return self.error is None

    def _run(self) -> None:
        try:
            import Quartz  # type: ignore[import-untyped]

            def callback(_proxy: Any, event_type: int, event: Any, _refcon: Any) -> Any:
                if event_type == Quartz.kCGEventKeyDown:
                    code = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                    action = self._actions_by_code.get(code)
                    if action is not None:
                        action()
                        return None
                return event

            mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,
                mask,
                callback,
                None,
            )
            if tap is None:
                raise RuntimeError("Accessibility permission is required for global hotkeys")
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            self._run_loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(
                self._run_loop, source, Quartz.kCFRunLoopCommonModes
            )
            Quartz.CGEventTapEnable(tap, True)
            self._ready.set()
            Quartz.CFRunLoopRun()
        except Exception as error:
            self.error = str(error)
            logging.getLogger("aacc.hotkeys").warning("Global hotkeys unavailable: %s", error)
            self._ready.set()

    def stop(self) -> None:
        if self._run_loop is not None:
            try:
                import Quartz  # type: ignore[import-untyped]

                Quartz.CFRunLoopStop(self._run_loop)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._thread = None
        self._run_loop = None
