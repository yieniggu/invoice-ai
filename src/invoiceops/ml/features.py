from invoiceops.domain.models import CountryRisk, Invoice

NUMERIC_FEATURES = [
    "invoice_amount_cents",
    "vendor_tenure_days",
    "previous_incidents_12m",
    "amount_vs_vendor_median",
]
BOOLEAN_FEATURES = [
    "has_purchase_order",
    "three_way_match",
    "bank_account_recently_changed",
]
CATEGORICAL_FEATURES = ["country_risk"]
MODEL_FEATURES = NUMERIC_FEATURES + BOOLEAN_FEATURES + CATEGORICAL_FEATURES
FEATURE_SCHEMA_VERSION = "invoice-features-v1"


def _is_numeric(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float)


def invoice_to_features(invoice: Invoice) -> dict[str, object]:
    return {
        "invoice_amount_cents": invoice.invoice_amount_cents,
        "vendor_tenure_days": invoice.vendor_tenure_days,
        "previous_incidents_12m": invoice.previous_incidents_12m,
        "amount_vs_vendor_median": invoice.amount_vs_vendor_median,
        "has_purchase_order": invoice.has_purchase_order,
        "three_way_match": invoice.three_way_match,
        "bank_account_recently_changed": invoice.bank_account_recently_changed,
        "country_risk": invoice.country_risk.value,
    }


def validate_feature_record(record: dict[str, object]) -> None:
    if set(record) != set(MODEL_FEATURES):
        raise ValueError("Feature record keys must match the model feature contract")

    for feature in (
        "invoice_amount_cents",
        "vendor_tenure_days",
        "previous_incidents_12m",
    ):
        if type(record[feature]) is not int:
            raise ValueError(f"{feature} must be an integer")

    ratio = record["amount_vs_vendor_median"]
    if not _is_numeric(ratio):
        raise ValueError("amount_vs_vendor_median must be numeric")

    for feature in BOOLEAN_FEATURES:
        if type(record[feature]) is not bool:
            raise ValueError(f"{feature} must be a boolean")

    country_risk = record["country_risk"]
    if not isinstance(country_risk, str) or country_risk not in {
        risk.value for risk in CountryRisk
    }:
        raise ValueError("country_risk must be a known country-risk value")
