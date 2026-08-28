from decimal import Decimal
from types import SimpleNamespace

import pytest

from invoiceops.domain.policy import recommend_from_probability
from invoiceops.legacy.db import insert_model_evaluation
from invoiceops.legacy.seed import seed_invoices


def _run(*, dataset_version: str = "invoice-risk-v1") -> SimpleNamespace:
    return SimpleNamespace(
        data=SimpleNamespace(
            params={
                "dataset_version": dataset_version,
                "feature_schema_version": "invoice-features-v1",
            },
            tags={"git_commit": "a" * 40},
        )
    )


def test_build_evidence_record_normalizes_operational_values_and_mlflow_lineage(tmp_path) -> None:
    from invoiceops.evidence import build_evidence_record

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-evidence",
        model_name="invoice-review",
        model_version="7",
        run_id="run-123",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
        created_at="2026-01-01T01:00:00+01:00",
    )

    record = build_evidence_record(
        db_path, 1, client=SimpleNamespace(get_run=lambda run_id: _run())
    )

    assert record.to_dict() == {
        "contract_version": "invoice-evidence-v1",
        "evaluation_id": 1,
        "invoice_id": "INV-10023",
        "correlation_id": "corr-evidence",
        "model_name": "invoice-review",
        "model_version": "7",
        "run_id": "run-123",
        "manual_review_probability": "0.8",
        "policy_version": "ml-policy-v1",
        "policy_threshold": "0.8",
        "recommendation": "MANUAL_REVIEW",
        "source": "model",
        "reason": "probability_at_or_above_threshold",
        "evaluation_created_at": "2026-01-01T00:00:00Z",
        "provenance": {
            "dataset_version": "invoice-risk-v1",
            "feature_schema_version": "invoice-features-v1",
            "git_commit": "a" * 40,
        },
    }


@pytest.mark.parametrize(
    ("run_id", "run", "message"),
    [
        (None, None, "run_id is missing"),
        ("run-123", _run(dataset_version=""), "dataset_version is missing"),
    ],
)
def test_evaluation_candidate_reports_unusable_lineage_without_inventing_provenance(
    tmp_path, run_id, run, message
) -> None:
    from invoiceops.evidence import list_evaluation_candidates

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-unusable",
        run_id=run_id,
        manual_review_probability=0.2,
        recommendation=recommend_from_probability(0.2),
    )

    candidates = list_evaluation_candidates(
        db_path,
        client=SimpleNamespace(get_run=lambda requested_run_id: run),
    )

    assert [
        (candidate.evaluation_id, candidate.usable, candidate.cause) for candidate in candidates
    ] == [(1, False, message)]


def test_persisting_a_batch_is_atomic_and_allows_one_v1_record_per_evaluation(tmp_path) -> None:
    from invoiceops.evidence import (
        EvidencePersistenceError,
        build_evidence_records,
        get_evidence_record,
        list_evidence_records,
        persist_evidence_records,
    )

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    for invoice_id, correlation_id in (("INV-10023", "corr-1"), ("INV-10024", "corr-2")):
        insert_model_evaluation(
            db_path,
            invoice_id,
            correlation_id=correlation_id,
            model_name="invoice-review",
            model_version="7",
            run_id="run-123",
            manual_review_probability=0.2,
            recommendation=recommend_from_probability(0.2),
        )
    client = SimpleNamespace(get_run=lambda run_id: _run())
    records = build_evidence_records(db_path, [1, 2], client=client)

    persist_evidence_records(db_path, records)
    assert get_evidence_record(db_path, 1) == records[0]
    assert list_evidence_records(db_path) == records

    with pytest.raises(EvidencePersistenceError, match="already exists"):
        persist_evidence_records(db_path, records)
    assert get_evidence_record(db_path, 2) == records[1]


def test_decimal_normalization_rejects_non_finite_values() -> None:
    from invoiceops.evidence import normalize_decimal

    assert normalize_decimal(Decimal("1.2300")) == "1.23"
    with pytest.raises(ValueError, match="finite"):
        normalize_decimal(Decimal("NaN"))
