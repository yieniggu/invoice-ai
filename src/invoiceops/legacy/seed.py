from datetime import UTC, datetime
from pathlib import Path

from invoiceops.domain.models import InvoiceStatus
from invoiceops.legacy.db import _connect, init_db

SEED_INVOICES = (
    ("INV-10023", "Acme Industrial", 482_000, True, True, InvoiceStatus.PENDING),
    ("INV-10024", "Northwind Parts", 810_000, True, True, InvoiceStatus.PENDING),
    ("INV-10025", "Globex Services", 210_000, False, True, InvoiceStatus.PENDING),
    ("INV-10026", "Contoso Supplies", 395_000, True, False, InvoiceStatus.PENDING),
    ("INV-10027", "Umbrella Office", 125_000, True, True, InvoiceStatus.PENDING),
    ("INV-10028", "Initech Ltd", 270_000, True, True, InvoiceStatus.CANCELLED),
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
        ) in SEED_INVOICES
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
