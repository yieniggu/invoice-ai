import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_run_mutable_action_once_reuses_completed_result_without_running_again() -> None:
    from notebooks.demo_helpers import run_mutable_action_once

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
    from notebooks.demo_helpers import run_mutable_action_once

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


def test_evaluation_selection_uses_the_first_usable_candidate_or_an_explicit_override() -> None:
    from invoiceops.notebook_helpers import evaluation_selection_state

    class Candidate:
        def __init__(self, evaluation_id: int, usable: bool) -> None:
            self.evaluation_id = evaluation_id
            self.usable = usable

    candidates = [Candidate(4, True), Candidate(5, False), Candidate(6, True)]

    automatic = evaluation_selection_state(candidates, None)
    invalid = evaluation_selection_state(candidates, 5)
    selected = evaluation_selection_state(candidates, 6)

    assert automatic.ready is True
    assert automatic.evaluation_id == 4
    assert automatic.state == "Evaluación seleccionada automáticamente: ID 4."
    assert invalid.ready is False
    assert "4, 6" in invalid.next_action
    assert selected.ready is True
    assert selected.candidate_ids == [4, 6]
    assert selected.state == "Evaluación seleccionada por override: ID 6."


def test_batch_selection_requires_a_positive_explicit_integer() -> None:
    from invoiceops.notebook_helpers import batch_selection_state

    assert batch_selection_state(None).ready is False
    assert batch_selection_state("1").ready is False
    assert batch_selection_state(True).ready is False
    assert batch_selection_state(-1).ready is False
    assert batch_selection_state(7).ready is True
