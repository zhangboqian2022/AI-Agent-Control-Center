import threading
from pathlib import Path

from fastapi.testclient import TestClient

from aacc.api import create_api
from aacc.automation import AutomationError
from aacc.automation_executor import AutomationExecutor
from aacc.config import default_config
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


def api_client(tmp_path: Path) -> tuple[TestClient, str, TaskManager]:
    config = default_config()
    store = StateStore(tmp_path / "api.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    return TestClient(create_api(config, manager)), config.app.api.token, manager


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_available_without_token(tmp_path: Path) -> None:
    client, _, manager = api_client(tmp_path)
    assert client.get("/api/v1/health").json() == {"status": "ok", "version": "1.3.0rc4"}
    manager.close()


def test_tasks_require_valid_bearer_token(tmp_path: Path) -> None:
    client, token, manager = api_client(tmp_path)
    assert client.get("/api/v1/tasks").status_code == 401
    assert client.get("/api/v1/tasks", headers=auth("wrong")).status_code == 401
    response = client.get("/api/v1/tasks", headers=auth(token))
    assert response.status_code == 200
    assert len(response.json()) == 4
    manager.close()


def test_status_update_round_trip(tmp_path: Path) -> None:
    client, token, manager = api_client(tmp_path)
    response = client.post(
        "/api/v1/tasks/task-1/status",
        headers=auth(token),
        json={"status": "waiting-approval", "message": "approve npm test"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "WAITING_APPROVAL"
    shown = client.get("/api/v1/tasks/task-1", headers=auth(token)).json()
    assert shown["message"] == "approve npm test"
    manager.close()


def test_api_validates_task_status_key_and_text(tmp_path: Path) -> None:
    client, token, manager = api_client(tmp_path)
    assert client.get("/api/v1/tasks/nope", headers=auth(token)).status_code == 404
    assert (
        client.post(
            "/api/v1/tasks/task-1/status", headers=auth(token), json={"status": "made-up"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/tasks/task-1/send-key", headers=auth(token), json={"key": "CMD_Q"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/tasks/task-1/send-text", headers=auth(token), json={"text": "x" * 2001}
        ).status_code
        == 422
    )
    manager.close()


def test_reset_and_events(tmp_path: Path) -> None:
    client, token, manager = api_client(tmp_path)
    client.post(
        "/api/v1/tasks/task-1/status",
        headers=auth(token),
        json={"status": "error", "message": "bad"},
    )
    reset = client.post("/api/v1/tasks/task-1/reset", headers=auth(token))
    assert reset.json()["status"] == "IDLE"
    events = client.get("/api/v1/tasks/task-1/events", headers=auth(token)).json()
    assert [item["status"] for item in events] == ["ERROR", "IDLE"]
    manager.close()


def test_automation_failure_returns_actionable_conflict_response(tmp_path: Path) -> None:
    class FailingController:
        def focus(self, _task: object, *, cancel_event: threading.Event | None = None) -> str:
            raise AutomationError("window missing")

        send_key = focus
        send_text = focus
        start_voice = focus

    config = default_config()
    store = StateStore(tmp_path / "api.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    client = TestClient(
        create_api(config, manager, FailingController()), raise_server_exceptions=False
    )
    response = client.post("/api/v1/tasks/task-1/focus", headers=auth(config.app.api.token))
    assert response.status_code == 409
    assert response.json() == {"detail": "window missing"}
    manager.close()


def test_executor_controller_is_accepted_by_api(tmp_path: Path) -> None:
    class BlockingController:
        def focus(self, _task: object, *, cancel_event: threading.Event | None = None) -> str:
            return "focused"

        send_key = focus
        send_text = focus
        start_voice = focus

    config = default_config()
    store = StateStore(tmp_path / "api.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    executor = AutomationExecutor(BlockingController(), total_timeout=0.01)
    client = TestClient(create_api(config, manager, executor), raise_server_exceptions=False)

    response = client.post("/api/v1/tasks/task-1/focus", headers=auth(config.app.api.token))

    assert response.status_code == 200
    executor.close()
    manager.close()
