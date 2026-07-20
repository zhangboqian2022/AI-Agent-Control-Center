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


def test_disabled_event_tap_is_reenabled() -> None:
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

    assert hotkeys._callback(None, Quartz.kCGEventTapDisabledByTimeout, event, None) is event
    assert enabled == [(hotkeys._tap, True)]
