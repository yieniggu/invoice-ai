from pathlib import Path

import pytest

from invoiceops.domain.models import CountryRisk, Decision, InvoiceStatus
from invoiceops.domain.policy import fallback_recommendation, recommend_from_probability
from invoiceops.legacy.db import (
    InvalidInvoiceTransition,
    _connect,
    get_invoice,
    insert_model_evaluation,
    list_decision_events,
    list_invoices,
    list_model_evaluations,
    reset_db,
    update_invoice_decision,
)
from invoiceops.legacy.seed import seed_invoices


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "invoiceops.db"
    seed_invoices(path)
    return path


def test_decision_updates_invoice_and_creates_event(db_path: Path) -> None:
    invoice = update_invoice_decision(
        db_path,
        "INV-10023",
        Decision.AUTO_PROCESS,
        actor="demo-user",
        correlation_id="corr-10023",
    )

    assert invoice.status is InvoiceStatus.AUTO_PROCESSED
    assert get_invoice(db_path, "INV-10023") == invoice
    events = list_decision_events(db_path, "INV-10023")
    assert len(events) == 1
    assert events[0]["decision"] == Decision.AUTO_PROCESS.value
    assert events[0]["rule_version"] == "invoice-rules-v1"
    assert events[0]["actor"] == "demo-user"
    assert events[0]["correlation_id"] == "corr-10023"


def test_cancelled_invoice_cannot_be_processed(db_path: Path) -> None:
    with pytest.raises(InvalidInvoiceTransition):
        update_invoice_decision(
            db_path,
            "INV-10028",
            Decision.AUTO_PROCESS,
            actor="demo-user",
            correlation_id="corr-10028",
        )

    assert get_invoice(db_path, "INV-10028").status is InvoiceStatus.CANCELLED
    assert list_decision_events(db_path, "INV-10028") == []


def test_processed_invoice_cannot_be_processed_again(db_path: Path) -> None:
    update_invoice_decision(
        db_path,
        "INV-10023",
        Decision.AUTO_PROCESS,
        actor="demo-user",
        correlation_id="corr-first",
    )

    with pytest.raises(InvalidInvoiceTransition):
        update_invoice_decision(
            db_path,
            "INV-10023",
            Decision.AUTO_PROCESS,
            actor="demo-user",
            correlation_id="corr-second",
        )

    assert len(list_decision_events(db_path, "INV-10023")) == 1


def test_seed_creates_the_eight_specified_invoices_with_risk_context(db_path: Path) -> None:
    result = list_invoices(db_path)

    assert [invoice.invoice_id for invoice in result.invoices] == [
        "INV-10023",
        "INV-10024",
        "INV-10025",
        "INV-10026",
        "INV-10027",
        "INV-10028",
        "INV-10029",
        "INV-10030",
    ]
    assert result.has_more is False
    low_risk = get_invoice(db_path, "INV-10029")
    high_risk = get_invoice(db_path, "INV-10030")
    assert low_risk is not None
    assert high_risk is not None
    assert (
        low_risk.vendor_tenure_days,
        low_risk.previous_incidents_12m,
        low_risk.bank_account_recently_changed,
        low_risk.amount_vs_vendor_median,
        low_risk.country_risk,
    ) == (2200, 0, False, 1.05, CountryRisk.LOW)
    assert (
        high_risk.vendor_tenure_days,
        high_risk.previous_incidents_12m,
        high_risk.bank_account_recently_changed,
        high_risk.amount_vs_vendor_median,
        high_risk.country_risk,
    ) == (12, 4, True, 3.4, CountryRisk.HIGH)


def test_invoice_list_is_limited_and_reports_when_more_results_exist(db_path: Path) -> None:
    insert_invoices(db_path, count=101)

    result = list_invoices(db_path)

    assert len(result.invoices) == 100
    assert result.has_more is True


def test_invoice_search_treats_wildcards_as_literal_text(db_path: Path) -> None:
    result = list_invoices(db_path, query="%' OR 1=1 --")

    assert result.invoices == []
    assert result.has_more is False


def test_database_path_can_be_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "override.db"
    monkeypatch.setenv("INVOICEOPS_DB_PATH", str(db_path))

    seed_invoices()

    assert db_path.exists()
    assert len(list_invoices().invoices) == 8


def test_seed_is_idempotent_and_preserves_existing_decisions_and_audits(db_path: Path) -> None:
    update_invoice_decision(
        db_path,
        "INV-10023",
        Decision.AUTO_PROCESS,
        actor="demo-user",
        correlation_id="decision-before-reseed",
    )
    insert_model_evaluation(
        db_path,
        "INV-10024",
        correlation_id="audit-before-reseed",
        recommendation=fallback_recommendation(),
    )

    seed_invoices(db_path)

    assert [invoice.invoice_id for invoice in list_invoices(db_path).invoices] == [
        "INV-10023",
        "INV-10024",
        "INV-10025",
        "INV-10026",
        "INV-10027",
        "INV-10028",
        "INV-10029",
        "INV-10030",
    ]
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.AUTO_PROCESSED
    assert [event["correlation_id"] for event in list_decision_events(db_path, "INV-10023")] == [
        "decision-before-reseed"
    ]
    assert [
        evaluation["correlation_id"] for evaluation in list_model_evaluations(db_path, "INV-10024")
    ] == ["audit-before-reseed"]


def test_pedagogical_audit_identity_is_idempotent_without_notebook_state(db_path: Path) -> None:
    correlation_id = "notebook-05:INV-10030:champion-A"

    for _ in range(2):
        insert_model_evaluation(
            db_path,
            "INV-10030",
            correlation_id=correlation_id,
            recommendation=fallback_recommendation(),
        )

    assert [
        evaluation["correlation_id"] for evaluation in list_model_evaluations(db_path, "INV-10030")
    ] == [correlation_id]


def test_model_evaluations_are_persisted_per_invoice_without_deciding_it(db_path: Path) -> None:
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-model",
        model_name="manual-review-model",
        model_version="7",
        run_id="run-123",
        manual_review_probability=0.82,
        recommendation=recommend_from_probability(0.82),
        created_at="2026-01-01T00:00:00+00:00",
    )
    insert_model_evaluation(
        db_path,
        "INV-10024",
        correlation_id="corr-other",
        model_name="manual-review-model",
        model_version="7",
        run_id="run-124",
        manual_review_probability=0.10,
        recommendation=recommend_from_probability(0.10),
        created_at="2026-01-02T00:00:00+00:00",
    )

    evaluations = list_model_evaluations(db_path, "INV-10023")

    assert len(evaluations) == 1
    assert dict(evaluations[0]) == {
        "id": 1,
        "invoice_id": "INV-10023",
        "correlation_id": "corr-model",
        "model_name": "manual-review-model",
        "model_version": "7",
        "run_id": "run-123",
        "manual_review_probability": 0.82,
        "policy_version": "ml-policy-v1",
        "policy_threshold": 0.80,
        "recommendation": "MANUAL_REVIEW",
        "source": "model",
        "reason": "probability_at_or_above_threshold",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.PENDING
    assert list_decision_events(db_path, "INV-10023") == []


def test_model_evaluations_preserve_fallback_without_model_values(db_path: Path) -> None:
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-fallback",
        recommendation=fallback_recommendation(),
        created_at="2026-01-01T00:00:00+00:00",
    )

    evaluation = list_model_evaluations(db_path, "INV-10023")[0]

    assert evaluation["manual_review_probability"] is None
    assert evaluation["model_name"] is None
    assert evaluation["model_version"] is None
    assert evaluation["run_id"] is None
    assert evaluation["recommendation"] == "MANUAL_REVIEW"
    assert evaluation["source"] == "fallback"
    assert evaluation["reason"] == "model_unavailable"


def test_model_evaluation_requires_an_existing_invoice(db_path: Path) -> None:
    with pytest.raises(LookupError, match="Invoice not found: INV-MISSING"):
        insert_model_evaluation(
            db_path,
            "INV-MISSING",
            correlation_id="corr-missing",
            recommendation=fallback_recommendation(),
        )

    assert list_model_evaluations(db_path, "INV-MISSING") == []


def test_model_evaluations_are_listed_newest_first(db_path: Path) -> None:
    recommendation = recommend_from_probability(0.10)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-first",
        model_name="manual-review-model",
        model_version="7",
        run_id="run-1",
        manual_review_probability=0.10,
        recommendation=recommendation,
        created_at="2026-01-01T00:00:00+00:00",
    )
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-second",
        model_name="manual-review-model",
        model_version="7",
        run_id="run-2",
        manual_review_probability=0.10,
        recommendation=recommendation,
        created_at="2026-01-02T00:00:00+00:00",
    )

    assert [
        evaluation["correlation_id"] for evaluation in list_model_evaluations(db_path, "INV-10023")
    ] == [
        "corr-second",
        "corr-first",
    ]


def test_reset_and_seed_leave_no_model_evaluations(db_path: Path) -> None:
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-before-reset",
        recommendation=fallback_recommendation(),
    )

    reset_db(db_path)
    seed_invoices(db_path)

    with _connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_evaluations").fetchone()[0] == 0


def insert_invoices(db_path: Path, *, count: int) -> None:
    rows = [
        (
            f"INV-EXTRA-{number:03d}",
            "Bulk Vendor",
            100,
            1,
            1,
            InvoiceStatus.PENDING.value,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        )
        for number in range(count)
    ]
    with _connect(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO invoices (
                invoice_id, vendor_name, invoice_amount_cents, has_purchase_order,
                three_way_match, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
