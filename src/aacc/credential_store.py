from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aacc.kimi_oauth import clear_credentials, load_credentials, save_credentials


@dataclass(frozen=True)
class CredentialSnapshot:
    generation: int
    fingerprint: str
    credentials: dict[str, Any] | None


class CredentialStore:
    """Coordinates conditional writes to AACC-owned Kimi credentials."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._lock = threading.RLock()
        self._generation = 0
        self._fingerprint = self._digest(load_credentials(config_dir))

    def snapshot(self) -> CredentialSnapshot:
        with self._lock:
            credentials = self._sync_from_disk()
            return CredentialSnapshot(
                generation=self._generation,
                fingerprint=self._fingerprint,
                credentials=copy.deepcopy(credentials),
            )

    def invalidate(self) -> None:
        with self._lock:
            self._sync_from_disk()
            self._generation += 1

    def replace(self, data: dict[str, Any]) -> CredentialSnapshot:
        with self._lock:
            save_credentials(self._config_dir, data)
            self._generation += 1
            self._fingerprint = self._digest(data)
            return CredentialSnapshot(
                generation=self._generation,
                fingerprint=self._fingerprint,
                credentials=copy.deepcopy(data),
            )

    def replace_if_current(
        self,
        expected: CredentialSnapshot,
        data: dict[str, Any],
    ) -> CredentialSnapshot | None:
        with self._lock:
            if not self._matches(expected):
                return None
            save_credentials(self._config_dir, data)
            self._generation += 1
            self._fingerprint = self._digest(data)
            return CredentialSnapshot(
                generation=self._generation,
                fingerprint=self._fingerprint,
                credentials=copy.deepcopy(data),
            )

    def clear_if_current(self, expected: CredentialSnapshot) -> bool:
        with self._lock:
            if not self._matches(expected):
                return False
            clear_credentials(self._config_dir)
            self._generation += 1
            self._fingerprint = self._digest(None)
            return True

    def is_current(self, expected: CredentialSnapshot) -> bool:
        with self._lock:
            return self._matches(expected)

    def _matches(self, expected: CredentialSnapshot) -> bool:
        self._sync_from_disk()
        return expected.generation == self._generation and expected.fingerprint == self._fingerprint

    def _sync_from_disk(self) -> dict[str, Any] | None:
        credentials = load_credentials(self._config_dir)
        fingerprint = self._digest(credentials)
        if fingerprint != self._fingerprint:
            self._generation += 1
            self._fingerprint = fingerprint
        return credentials

    @staticmethod
    def _digest(data: dict[str, Any] | None) -> str:
        encoded = json.dumps(
            {"credentials": data},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()
