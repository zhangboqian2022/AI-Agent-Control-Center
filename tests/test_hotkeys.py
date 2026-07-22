import pytest

from aacc.hotkeys import AccessibilityHotkeySync, GlobalHotkeys, hotkey_keycode


@pytest.mark.parametrize(
    ("name", "code"),
    [("F13", 105), ("F14", 107), ("F15", 113), ("F16", 106), ("F20", 90)],
)
def test_function_hotkeys_map_to_macos_virtual_codes(name: str, code: int) -> None:
    assert hotkey_keycode(name) == code


def test_unknown_hotkey_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        hotkey_keycode("COMMAND+Q")


@pytest.mark.parametrize(
    "reason",
    ["kCGEventTapDisabledByTimeout", "kCGEventTapDisabledByUserInput"],
)
def test_disabled_event_tap_is_reenabled(reason: str) -> None:
    enabled: list[tuple[object, bool]] = []

    class Quartz:
        kCGEventTapDisabledByTimeout = 1
        kCGEventTapDisabledByUserInput = 2
        kCGEventKeyDown = 3

        @staticmethod
        def CGEventTapEnable(tap: object, value: bool) -> None:
            enabled.append((tap, value))

    hotkeys = GlobalHotkeys({}, {})
    hotkeys._quartz = Quartz()  # type: ignore[assignment]
    hotkeys._tap = object()
    event = object()

    assert hotkeys._callback(None, getattr(Quartz, reason), event, None) is event
    assert enabled == [(hotkeys._tap, True)]


def test_keydown_dispatches_bound_action_and_consumes_event() -> None:
    actions: list[str] = []

    class Quartz:
        kCGEventTapDisabledByTimeout = 1
        kCGEventTapDisabledByUserInput = 2
        kCGEventKeyDown = 3
        kCGKeyboardEventKeycode = 4

        @staticmethod
        def CGEventGetIntegerValueField(_event: object, _field: int) -> int:
            return 105

    hotkeys = GlobalHotkeys({"focus": "F13"}, {"focus": lambda: actions.append("focus")})
    hotkeys._quartz = Quartz()  # type: ignore[assignment]

    assert hotkeys._callback(None, Quartz.kCGEventKeyDown, object(), None) is None
    assert actions == ["focus"]

    Quartz.CGEventGetIntegerValueField = staticmethod(lambda _event, _field: 999)  # type: ignore[method-assign]
    unmatched = object()
    assert hotkeys._callback(None, Quartz.kCGEventKeyDown, unmatched, None) is unmatched


def test_callback_without_quartz_returns_event_and_stop_clears_state() -> None:
    hotkeys = GlobalHotkeys({}, {})
    event = object()
    hotkeys._tap = object()

    assert not hotkeys.available
    assert hotkeys._callback(None, 0, event, None) is event
    hotkeys.stop()
    assert hotkeys._tap is None
    assert hotkeys._quartz is None


def test_start_retries_after_a_failed_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    hotkeys = GlobalHotkeys({}, {})
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    hotkeys._thread = dead
    hotkeys.error = "Accessibility permission is required for global hotkeys"

    reran: list[bool] = []

    def fake_run() -> None:
        reran.append(True)
        hotkeys._ready.set()

    monkeypatch.setattr(hotkeys, "_run", fake_run)

    assert hotkeys.start() is True
    assert reran == [True]
    assert hotkeys.error is None
    hotkeys.stop()


class FakeHotkeys:
    def __init__(self, start_result: bool = True) -> None:
        self.start_result = start_result
        self.starts = 0
        self.stops = 0

    def start(self) -> bool:
        self.starts += 1
        return self.start_result

    def stop(self) -> None:
        self.stops += 1


def test_sync_starts_hotkeys_once_when_trust_appears() -> None:
    hotkeys = FakeHotkeys()
    sync = AccessibilityHotkeySync(hotkeys)  # type: ignore[arg-type]

    sync.sync(False)
    assert hotkeys.starts == 0
    sync.sync(True)
    sync.sync(True)
    assert hotkeys.starts == 1
    assert sync.running


def test_sync_stops_hotkeys_when_trust_is_removed() -> None:
    hotkeys = FakeHotkeys()
    sync = AccessibilityHotkeySync(hotkeys)  # type: ignore[arg-type]

    sync.sync(True)
    sync.sync(False)
    assert hotkeys.stops == 1
    assert not sync.running
    sync.sync(False)
    assert hotkeys.stops == 1


def test_sync_retries_when_start_fails() -> None:
    hotkeys = FakeHotkeys(start_result=False)
    sync = AccessibilityHotkeySync(hotkeys)  # type: ignore[arg-type]

    sync.sync(True)
    assert not sync.running
    hotkeys.start_result = True
    sync.sync(True)
    assert hotkeys.starts == 2
    assert sync.running
