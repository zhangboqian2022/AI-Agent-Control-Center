import subprocess
import sys

import aacc.accessibility as accessibility


class FakeQuartz:
    kAXTrustedCheckOptionPrompt = "prompt"

    def __init__(self, trusted: bool) -> None:
        self.trusted = trusted
        self.options: dict[str, bool] | None = None

    def AXIsProcessTrustedWithOptions(self, options: dict[str, bool]) -> bool:
        self.options = options
        return self.trusted


def test_accessibility_trust_uses_quartz_options(monkeypatch: object) -> None:
    quartz = FakeQuartz(True)
    monkeypatch.setattr(accessibility, "_load_quartz", lambda: quartz)  # type: ignore[attr-defined]

    assert accessibility.is_accessibility_trusted(prompt=True)
    assert quartz.options == {"prompt": True}


def test_open_accessibility_settings_uses_system_deep_link(monkeypatch: object) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", runner)  # type: ignore[attr-defined]
    accessibility.open_accessibility_settings()

    assert calls[0][0][0:2] == [
        "/usr/bin/open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    ]
    assert calls[0][1]["timeout"] == 5


def test_accessibility_failures_are_safe(monkeypatch: object) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        accessibility, "_load_quartz", lambda: (_ for _ in ()).throw(ImportError())
    )
    assert not accessibility.is_accessibility_trusted()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError())
    )
    assert accessibility.open_accessibility_settings() is None


def test_accessibility_always_trusted_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    from aacc.accessibility import is_accessibility_trusted

    assert is_accessibility_trusted() is True
    assert is_accessibility_trusted(prompt=True) is True


def test_open_settings_noop_on_windows(monkeypatch) -> None:
    import subprocess

    monkeypatch.setattr(sys, "platform", "win32")
    called: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a) or None)
    from aacc.accessibility import open_accessibility_settings

    open_accessibility_settings()
    assert called == []
