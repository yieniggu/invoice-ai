import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_run_mutable_action_once_reuses_completed_result_without_running_again() -> None:
    from notebooks._demo_helpers import run_mutable_action_once

    completed = {"seed-demo": {"invoice_id": "INV-001"}}
    runs: list[str] = []

    result = run_mutable_action_once(
        "seed-demo",
        completed,
        lambda: runs.append("seed-demo") or {"invoice_id": "INV-002"},
    )

    assert result == {"invoice_id": "INV-001"}
    assert runs == []


def test_promotion_and_audit_actions_are_each_guarded_on_rerun() -> None:
    from notebooks._demo_helpers import run_mutable_action_once

    completed: dict[str, object] = {}
    promotions: list[str] = []
    audits: list[str] = []

    for _ in range(2):
        run_mutable_action_once(
            "promote-for-audit-A",
            completed,
            lambda: promotions.append("A") or "1",
        )
        run_mutable_action_once(
            "persist-INV-10030-A",
            completed,
            lambda: audits.append("A") or {"model_version": "1"},
        )

    assert promotions == ["A"]
    assert audits == ["A"]


def test_wait_for_health_polls_health_endpoint_until_ready() -> None:
    from notebooks._demo_helpers import wait_for_health

    responses = iter([False, True])
    requested_urls: list[str] = []
    pauses: list[float] = []

    def request(url: str) -> bool:
        requested_urls.append(url)
        return next(responses)

    wait_for_health(
        "http://127.0.0.1:8000/health",
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        request=request,
        sleep=pauses.append,
    )

    assert requested_urls == ["http://127.0.0.1:8000/health"] * 2
    assert pauses == [0.1]


def test_cleanup_created_process_leaves_reused_process_running() -> None:
    from notebooks._demo_helpers import cleanup_created_process

    class Process:
        def __init__(self) -> None:
            self.terminate_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1

    created_process = Process()
    reused_process = Process()

    cleanup_created_process(created_process, created_by_helper=True)
    cleanup_created_process(reused_process, created_by_helper=False)

    assert created_process.terminate_calls == 1
    assert reused_process.terminate_calls == 0
