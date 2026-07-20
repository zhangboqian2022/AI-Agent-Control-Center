import pytest

from aacc.hotkeys import GlobalHotkeys, hotkey_keycode


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
