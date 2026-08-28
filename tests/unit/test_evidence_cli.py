import json
import sys
from types import SimpleNamespace

from invoiceops.domain.policy import recommend_from_probability
from invoiceops.legacy.db import insert_model_evaluation
from invoiceops.legacy.seed import seed_invoices


def _run() -> SimpleNamespace:
    return SimpleNamespace(
        data=SimpleNamespace(
            params={
                "dataset_version": "invoice-risk-v1",
                "feature_schema_version": "invoice-features-v1",
            },
            tags={"git_commit": "a" * 40},
        )
    )


def test_evidence_cli_lists_unusable_evaluations_without_mutating_demo_state(
    tmp_path, monkeypatch, capsys
) -> None:
    from invoiceops import evidence

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-cli",
        recommendation=recommend_from_probability(0.2),
    )
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["evidence", "list", "--db", str(db_path)])

    evidence.main()

    assert json.loads(capsys.readouterr().out) == [
        {"cause": "run_id is missing", "evaluation_id": 1, "usable": False}
    ]


def test_evidence_cli_builds_the_same_contract_as_the_python_api(
    tmp_path, monkeypatch, capsys
) -> None:
    from invoiceops import evidence

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-cli-build",
        model_name="invoice-review",
        model_version="7",
        run_id="run-123",
        manual_review_probability=0.2,
        recommendation=recommend_from_probability(0.2),
        created_at="2026-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(
        evidence, "MlflowClient", lambda: SimpleNamespace(get_run=lambda run_id: _run())
    )
    expected = evidence.build_evidence_record(db_path, 1).to_dict()
    capsys.readouterr()
    monkeypatch.setattr(
        sys, "argv", ["evidence", "build", "--db", str(db_path), "--evaluation-id", "1"]
    )

    evidence.main()

    assert json.loads(capsys.readouterr().out) == [expected]
