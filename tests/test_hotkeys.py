import pytest

from aacc.hotkeys import hotkey_keycode


@pytest.mark.parametrize(
    ("name", "code"),
    [("F13", 105), ("F14", 107), ("F15", 113), ("F16", 106), ("F20", 90)],
)
def test_function_hotkeys_map_to_macos_virtual_codes(name: str, code: int) -> None:
    assert hotkey_keycode(name) == code


def test_unknown_hotkey_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        hotkey_keycode("COMMAND+Q")
