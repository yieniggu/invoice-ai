from pathlib import Path

import pytest

from invoiceops.domain.models import CountryRisk, Decision, InvoiceStatus
from invoiceops.legacy.db import (
    InvalidInvoiceTransition,
    _connect,
    get_invoice,
    list_decision_events,
    list_invoices,
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
