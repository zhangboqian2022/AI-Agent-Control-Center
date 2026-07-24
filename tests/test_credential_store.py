from __future__ import annotations

from aacc.credential_store import CredentialStore
from aacc.kimi_oauth import load_credentials, save_credentials


def test_late_replace_is_rejected_after_generation_invalidated(tmp_path):
    store = CredentialStore(tmp_path)
    store.replace({"auth_method": "api_key", "api_key": "old"})
    old = store.snapshot()

    store.invalidate()

    assert store.replace_if_current(old, {"auth_method": "api_key", "api_key": "late"}) is None
    assert load_credentials(tmp_path) == {
        "auth_method": "api_key",
        "api_key": "old",
    }


def test_external_disk_change_invalidates_snapshot(tmp_path):
    store = CredentialStore(tmp_path)
    store.replace({"auth_method": "api_key", "api_key": "old"})
    old = store.snapshot()

    save_credentials(tmp_path, {"auth_method": "api_key", "api_key": "external"})

    assert not store.is_current(old)
    assert store.clear_if_current(old) is False
    assert load_credentials(tmp_path) == {
        "auth_method": "api_key",
        "api_key": "external",
    }


def test_current_snapshot_can_conditionally_replace_and_clear(tmp_path):
    store = CredentialStore(tmp_path)
    first = store.replace({"auth_method": "api_key", "api_key": "first"})

    second = store.replace_if_current(first, {"auth_method": "api_key", "api_key": "second"})

    assert second is not None
    assert store.is_current(second)
    assert store.clear_if_current(second) is True
    assert load_credentials(tmp_path) is None
    assert not store.is_current(second)


def test_snapshot_isolated_from_caller_mutation(tmp_path):
    store = CredentialStore(tmp_path)
    credentials = {
        "auth_method": "oauth",
        "token": {"access_token": "one"},
    }
    store.replace(credentials)

    snapshot = store.snapshot()
    credentials["token"]["access_token"] = "mutated"
    assert snapshot.credentials is not None
    snapshot.credentials["token"]["access_token"] = "also-mutated"

    assert load_credentials(tmp_path) == {
        "auth_method": "oauth",
        "token": {"access_token": "one"},
    }
