from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_windows_compatibility_contract_script_has_explicit_target_allowlist() -> None:
    script = (ROOT / "scripts" / "test_windows_compatibility_contract.ps1").read_text(
        encoding="utf-8"
    )

    assert "param" in script
    assert "windows-10" in script
    assert "windows-11" in script
    assert "Windows Server compatibility evidence only" in script
    assert "consumer Windows 10/11 hardware" in script


def test_ci_labels_consumer_targets_without_claiming_consumer_runners() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "windows-consumer-compatibility-contract" in workflow
    assert "target: [windows-10, windows-11]" in workflow
    assert "runs-on: windows-2022" in workflow
    assert "Hosted Windows Server compatibility evidence only" in workflow
    assert "runs-on: windows-10" not in workflow
    assert "runs-on: windows-11" not in workflow
