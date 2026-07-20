import subprocess

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
