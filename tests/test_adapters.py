from aacc.adapters import AdapterRegistry, GenericCLIAdapter, strip_ansi
from aacc.config import default_config
from aacc.models import AgentConfig, TaskStatus


def test_strip_ansi_removes_terminal_color_sequences() -> None:
    assert strip_ansi("\x1b[31mfailed\x1b[0m") == "failed"


def test_generic_adapter_classifies_configured_patterns() -> None:
    adapter = GenericCLIAdapter(
        "task-1",
        AgentConfig(
            type="generic_cli",
            running_patterns=[r"^working$"],
            waiting_approval_patterns=[r"^approve command: .+$"],
            completed_patterns=[r"^task completed$"],
            error_patterns=[r"^fatal error:"],
        ),
    )
    assert adapter.classify("working") is TaskStatus.RUNNING
    assert adapter.classify("approve command: npm test") is TaskStatus.WAITING_APPROVAL
    assert adapter.classify("task completed") is TaskStatus.COMPLETED
    assert adapter.classify("fatal error: no file") is TaskStatus.ERROR


def test_ambiguous_or_oversized_lines_do_not_fabricate_state() -> None:
    adapter = GenericCLIAdapter("task-1", AgentConfig(type="generic_cli"))
    assert adapter.classify("maybe this is waiting for something") is None
    assert adapter.classify("x" * 5000) is None


def test_registry_provides_specialized_agent_presets() -> None:
    config = default_config()
    codex = AdapterRegistry.create(config.tasks[0])
    claude = AdapterRegistry.create(config.tasks[1])
    kimi = AdapterRegistry.create(config.tasks[2])
    zcode = AdapterRegistry.create(config.tasks[3])
    assert codex.display_name == "Codex CLI"
    assert claude.display_name == "Claude Code"
    assert kimi.display_name == "Kimi Code"
    assert zcode.display_name == "Z Code"


def test_specialized_patterns_are_conservative_but_useful() -> None:
    config = default_config()
    codex = AdapterRegistry.create(config.tasks[0])
    claude = AdapterRegistry.create(config.tasks[1])
    kimi = AdapterRegistry.create(config.tasks[2])
    assert codex.classify("Would you like to run npm test?") is TaskStatus.WAITING_APPROVAL
    assert claude.classify("Task completed successfully") is TaskStatus.COMPLETED
    assert kimi.classify("分析完成") is TaskStatus.COMPLETED
    assert codex.classify("allowance calculation") is None


def test_codex_app_uses_process_capability_without_invented_log_patterns() -> None:
    task = default_config().tasks[0]
    task.agent = AgentConfig(type="codex_app", display_name="Codex App")
    adapter = AdapterRegistry.create(task)
    assert adapter.capabilities["can_detect_process"] is True
    assert adapter.classify("Approve") is None
