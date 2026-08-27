from datetime import UTC, datetime
from pathlib import Path

from invoiceops.domain.models import CountryRisk, InvoiceStatus
from invoiceops.legacy.db import _connect, init_db

SEED_INVOICES = (
    (
        "INV-10023",
        "Acme Industrial",
        482_000,
        True,
        True,
        InvoiceStatus.PENDING,
        1825,
        0,
        False,
        1.02,
        CountryRisk.LOW,
    ),
    (
        "INV-10024",
        "Northwind Parts",
        810_000,
        True,
        True,
        InvoiceStatus.PENDING,
        980,
        1,
        False,
        1.15,
        CountryRisk.MEDIUM,
    ),
    (
        "INV-10025",
        "Globex Services",
        210_000,
        False,
        True,
        InvoiceStatus.PENDING,
        95,
        2,
        True,
        2.4,
        CountryRisk.HIGH,
    ),
    (
        "INV-10026",
        "Contoso Supplies",
        395_000,
        True,
        False,
        InvoiceStatus.PENDING,
        730,
        0,
        False,
        0.98,
        CountryRisk.MEDIUM,
    ),
    (
        "INV-10027",
        "Umbrella Office",
        125_000,
        True,
        True,
        InvoiceStatus.PENDING,
        1460,
        0,
        False,
        1.01,
        CountryRisk.LOW,
    ),
    (
        "INV-10028",
        "Initech Ltd",
        270_000,
        True,
        True,
        InvoiceStatus.CANCELLED,
        420,
        1,
        True,
        1.5,
        CountryRisk.MEDIUM,
    ),
    (
        "INV-10029",
        "Stable Supplies",
        420_000,
        True,
        True,
        InvoiceStatus.PENDING,
        2200,
        0,
        False,
        1.05,
        CountryRisk.LOW,
    ),
    (
        "INV-10030",
        "Risky Ventures",
        420_000,
        True,
        True,
        InvoiceStatus.PENDING,
        12,
        4,
        True,
        3.4,
        CountryRisk.HIGH,
    ),
)


def seed_invoices(db_path: str | Path | None = None) -> None:
    init_db(db_path)
    timestamp = datetime.now(UTC).isoformat()
    rows = [
        (
            invoice_id,
            vendor_name,
            invoice_amount_cents,
            int(has_purchase_order),
            int(three_way_match),
            status.value,
            vendor_tenure_days,
            previous_incidents_12m,
            int(bank_account_recently_changed),
            amount_vs_vendor_median,
            country_risk.value,
            timestamp,
            timestamp,
        )
        for (
            invoice_id,
            vendor_name,
            invoice_amount_cents,
            has_purchase_order,
            three_way_match,
            status,
            vendor_tenure_days,
            previous_incidents_12m,
            bank_account_recently_changed,
            amount_vs_vendor_median,
            country_risk,
        ) in SEED_INVOICES
    ]
    with _connect(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO invoices (
                invoice_id, vendor_name, invoice_amount_cents, has_purchase_order,
                three_way_match, status, vendor_tenure_days, previous_incidents_12m,
                bank_account_recently_changed, amount_vs_vendor_median, country_risk,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_id) DO NOTHING
            """,
            rows,
        )
