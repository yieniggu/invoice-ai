from datetime import UTC, datetime

from invoiceops.domain.models import CountryRisk, Decision, Invoice, InvoiceStatus
from invoiceops.domain.rules import RULE_VERSION, decide_invoice


def make_invoice(**overrides: object) -> Invoice:
    values: dict[str, object] = {
        "invoice_id": "INV-TEST",
        "vendor_name": "Test Vendor",
        "invoice_amount_cents": 500_000,
        "has_purchase_order": True,
        "three_way_match": True,
        "vendor_tenure_days": 365,
        "previous_incidents_12m": 0,
        "bank_account_recently_changed": False,
        "amount_vs_vendor_median": 1.0,
        "country_risk": CountryRisk.MEDIUM,
        "status": InvoiceStatus.PENDING,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    values.update(overrides)
    return Invoice(**values)


def test_auto_process_when_all_conditions_pass() -> None:
    assert RULE_VERSION == "invoice-rules-v1"
    assert decide_invoice(make_invoice()) is Decision.AUTO_PROCESS


def test_country_risk_uses_persisted_values() -> None:
    assert [risk.value for risk in CountryRisk] == ["low", "medium", "high"]


def test_manual_review_when_amount_too_high() -> None:
    assert decide_invoice(make_invoice(invoice_amount_cents=500_001)) is Decision.MANUAL_REVIEW


def test_manual_review_without_purchase_order() -> None:
    assert decide_invoice(make_invoice(has_purchase_order=False)) is Decision.MANUAL_REVIEW


def test_manual_review_without_three_way_match() -> None:
    assert decide_invoice(make_invoice(three_way_match=False)) is Decision.MANUAL_REVIEW


def test_rule_v1_ignores_risk_context() -> None:
    assert (
        decide_invoice(
            make_invoice(
                vendor_tenure_days=12,
                previous_incidents_12m=4,
                bank_account_recently_changed=True,
                amount_vs_vendor_median=3.4,
                country_risk=CountryRisk.HIGH,
            )
        )
        is Decision.AUTO_PROCESS
    )
