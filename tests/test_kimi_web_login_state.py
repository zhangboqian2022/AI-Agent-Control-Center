from __future__ import annotations

import json

import pytest

from aacc.kimi_web_login_state import KimiWebLoginStateStore


def test_gate_defaults_false_and_survives_new_store_instance(tmp_path):
    first = KimiWebLoginStateStore(tmp_path)

    assert first.may_reuse() is False

    first.set_may_reuse(True)

    assert KimiWebLoginStateStore(tmp_path).may_reuse() is True


def test_corrupt_gate_fails_closed(tmp_path):
    path = tmp_path / "kimi-web-session-state.json"
    path.write_text("{", encoding="utf-8")

    assert KimiWebLoginStateStore(tmp_path).may_reuse() is False


@pytest.mark.parametrize(
    "state",
    [
        {"version": True, "reuse_native_session": True},
        {"version": 2, "reuse_native_session": True},
        {"version": 1, "reuse_native_session": 1},
    ],
)
def test_invalid_gate_schema_fails_closed(tmp_path, state):
    path = tmp_path / "kimi-web-session-state.json"
    path.write_text(json.dumps(state), encoding="utf-8")

    assert KimiWebLoginStateStore(tmp_path).may_reuse() is False


def test_symlink_gate_fails_closed_and_is_never_replaced(tmp_path):
    target = tmp_path / "target.json"
    target.write_text('{"version":1,"reuse_native_session":true}', encoding="utf-8")
    path = tmp_path / "kimi-web-session-state.json"
    path.symlink_to(target)
    store = KimiWebLoginStateStore(tmp_path)

    assert store.may_reuse() is False
    with pytest.raises(ValueError, match="symbolic link"):
        store.set_may_reuse(False)
    assert target.read_text(encoding="utf-8") == '{"version":1,"reuse_native_session":true}'


def test_logout_marker_defaults_false_and_round_trips(tmp_path):
    store = KimiWebLoginStateStore(tmp_path)
    assert store.logged_out_by_user() is False

    store.set_may_reuse(False, logged_out_by_user=True)
    assert KimiWebLoginStateStore(tmp_path).logged_out_by_user() is True
    assert store.may_reuse() is False

    store.set_may_reuse(True, logged_out_by_user=False)
    assert store.logged_out_by_user() is False


def test_logout_marker_preserved_when_unspecified(tmp_path):
    store = KimiWebLoginStateStore(tmp_path)
    store.set_may_reuse(False, logged_out_by_user=True)
    store.set_may_reuse(False)
    assert store.logged_out_by_user() is True


def test_opencode_state_file_is_isolated_from_kimi_state(tmp_path):
    store = KimiWebLoginStateStore(
        tmp_path,
        state_file_name="opencode-web-session-state.json",
    )

    store.set_may_reuse(True)

    assert store.may_reuse() is True
    assert (tmp_path / "opencode-web-session-state.json").is_file()
    assert not (tmp_path / "kimi-web-session-state.json").exists()


def test_qwen_state_file_is_isolated_from_kimi_state(tmp_path):
    store = KimiWebLoginStateStore(
        tmp_path,
        state_file_name="qwen-web-session-state.json",
    )

    store.set_may_reuse(True)

    assert store.may_reuse() is True
    assert (tmp_path / "qwen-web-session-state.json").is_file()
    assert not (tmp_path / "kimi-web-session-state.json").exists()


def test_unknown_state_file_name_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        KimiWebLoginStateStore(tmp_path, state_file_name="other.json")
