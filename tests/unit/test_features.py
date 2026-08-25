from datetime import UTC, datetime

import pytest

from invoiceops.domain.models import CountryRisk, Invoice, InvoiceStatus
from invoiceops.ml.features import (
    BOOLEAN_FEATURES,
    CATEGORICAL_FEATURES,
    FEATURE_SCHEMA_VERSION,
    MODEL_FEATURES,
    NUMERIC_FEATURES,
    invoice_to_features,
    validate_feature_record,
)


def make_invoice(**overrides: object) -> Invoice:
    values: dict[str, object] = {
        "invoice_id": "INV-TEST",
        "vendor_name": "Test Vendor",
        "invoice_amount_cents": 500_000,
        "has_purchase_order": True,
        "three_way_match": False,
        "vendor_tenure_days": 365,
        "previous_incidents_12m": 2,
        "bank_account_recently_changed": True,
        "amount_vs_vendor_median": 1.25,
        "country_risk": CountryRisk.HIGH,
        "status": InvoiceStatus.PENDING,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    values.update(overrides)
    return Invoice(**values)


def test_invoice_to_features_exact_contract() -> None:
    assert invoice_to_features(make_invoice()) == {
        "invoice_amount_cents": 500_000,
        "vendor_tenure_days": 365,
        "previous_incidents_12m": 2,
        "amount_vs_vendor_median": 1.25,
        "has_purchase_order": True,
        "three_way_match": False,
        "bank_account_recently_changed": True,
        "country_risk": "high",
    }


def test_target_not_in_features() -> None:
    assert "status" not in invoice_to_features(make_invoice())


def test_metadata_not_in_features() -> None:
    record = invoice_to_features(make_invoice())

    for name in ("invoice_id", "submitted_at", "vendor_name"):
        assert name not in record


def test_feature_schema_version() -> None:
    assert NUMERIC_FEATURES == [
        "invoice_amount_cents",
        "vendor_tenure_days",
        "previous_incidents_12m",
        "amount_vs_vendor_median",
    ]
    assert BOOLEAN_FEATURES == [
        "has_purchase_order",
        "three_way_match",
        "bank_account_recently_changed",
    ]
    assert CATEGORICAL_FEATURES == ["country_risk"]
    assert MODEL_FEATURES == NUMERIC_FEATURES + BOOLEAN_FEATURES + CATEGORICAL_FEATURES
    assert FEATURE_SCHEMA_VERSION == "invoice-features-v1"


def test_validate_feature_record_accepts_valid_record() -> None:
    assert validate_feature_record(invoice_to_features(make_invoice())) is None


@pytest.mark.parametrize(
    "record",
    [
        {
            name: value
            for name, value in invoice_to_features(make_invoice()).items()
            if name != "country_risk"
        },
        {**invoice_to_features(make_invoice()), "unexpected": "value"},
        {**invoice_to_features(make_invoice()), "invoice_amount_cents": None},
        {**invoice_to_features(make_invoice()), "country_risk": "unknown"},
        {**invoice_to_features(make_invoice()), "invoice_amount_cents": True},
        {**invoice_to_features(make_invoice()), "vendor_tenure_days": 1.0},
        {**invoice_to_features(make_invoice()), "previous_incidents_12m": False},
        {**invoice_to_features(make_invoice()), "amount_vs_vendor_median": True},
        {**invoice_to_features(make_invoice()), "has_purchase_order": 1},
        {**invoice_to_features(make_invoice()), "three_way_match": "true"},
        {**invoice_to_features(make_invoice()), "bank_account_recently_changed": 0},
        {**invoice_to_features(make_invoice()), "country_risk": 1},
    ],
)
def test_validate_feature_record_rejects_invalid_contract(record: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_feature_record(record)
