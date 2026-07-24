from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")


def make_card(qtbot, agent_type: str, metadata: dict):
    from aacc.gui import TaskCard
    from aacc.models import AgentConfig, TaskConfig, TaskState

    task = TaskConfig(id="kimi:s1", slot=1, name="demo", agent=AgentConfig(type=agent_type))
    state = TaskState.new("kimi:s1", "RUNNING", metadata=metadata)
    card = TaskCard(task, state)
    qtbot.addWidget(card)
    return card


USAGE = {
    "total_input_tokens": 12_300,
    "output_tokens": 1_200,
    "cache_read_pct": 68,
    "speed_tps": 42,
}


def test_kimi_card_shows_usage_row(qtbot):
    card = make_card(qtbot, "kimi_code", {"usage": USAGE})
    assert not card.usage_label.isHidden()
    assert card.usage_label.text() == "↑12.3k ↓1.2k 缓存68% · 42 tok/s"


def test_card_hides_usage_row_without_metadata(qtbot):
    card = make_card(qtbot, "kimi_code", {})
    assert card.usage_label.isHidden()


def test_non_kimi_card_hides_usage_row(qtbot):
    card = make_card(qtbot, "codex_cli", {"usage": USAGE})
    assert card.usage_label.isHidden()


def test_usage_row_updates_on_set_state(qtbot):
    from aacc.models import TaskState

    card = make_card(qtbot, "kimi_code", {})
    updated = TaskState.new("kimi:s1", "RUNNING", metadata={"usage": USAGE})
    card.set_state(updated)
    assert not card.usage_label.isHidden()
    assert "42 tok/s" in card.usage_label.text()
