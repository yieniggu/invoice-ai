import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from invoiceops.domain.models import Decision, Invoice, InvoiceStatus
from invoiceops.domain.rules import RULE_VERSION

DEFAULT_DB_PATH = Path("var/invoiceops.db")
INVOICE_LIST_LIMIT = 100


@dataclass
class InvoiceListResult:
    invoices: list[Invoice]
    has_more: bool


class InvalidInvoiceTransition(Exception):
    """Raised when a decision is applied to an invoice outside PENDING."""


def _resolve_db_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return Path(os.environ.get("INVOICEOPS_DB_PATH", DEFAULT_DB_PATH))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    path = _resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _invoice_from_row(row: sqlite3.Row) -> Invoice:
    return Invoice(
        invoice_id=row["invoice_id"],
        vendor_name=row["vendor_name"],
        invoice_amount_cents=row["invoice_amount_cents"],
        has_purchase_order=bool(row["has_purchase_order"]),
        three_way_match=bool(row["three_way_match"]),
        status=InvoiceStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def init_db(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                invoice_id TEXT PRIMARY KEY,
                vendor_name TEXT NOT NULL,
                invoice_amount_cents INTEGER NOT NULL,
                has_purchase_order INTEGER NOT NULL,
                three_way_match INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                actor TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
            )
            """
        )


def get_invoice(db_path: str | Path | None, invoice_id: str) -> Invoice | None:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()
    return _invoice_from_row(row) if row is not None else None


def list_invoices(db_path: str | Path | None = None, *, query: str = "") -> InvoiceListResult:
    escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped_query}%"
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM invoices
            WHERE invoice_id LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR vendor_name LIKE ? ESCAPE '\\' COLLATE NOCASE
            ORDER BY invoice_id
            LIMIT ?
            """,
            (pattern, pattern, INVOICE_LIST_LIMIT + 1),
        ).fetchall()
    return InvoiceListResult(
        invoices=[_invoice_from_row(row) for row in rows[:INVOICE_LIST_LIMIT]],
        has_more=len(rows) > INVOICE_LIST_LIMIT,
    )


def update_invoice_decision(
    db_path: str | Path | None,
    invoice_id: str,
    decision: Decision,
    *,
    actor: str,
    correlation_id: str,
) -> Invoice:
    updated_at = datetime.now(UTC).isoformat()
    status = (
        InvoiceStatus.AUTO_PROCESSED
        if decision is Decision.AUTO_PROCESS
        else InvoiceStatus.MANUAL_REVIEW
    )

    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Invoice not found: {invoice_id}")
        if InvoiceStatus(row["status"]) is not InvoiceStatus.PENDING:
            raise InvalidInvoiceTransition(
                f"Invoice {invoice_id} cannot be decided from status {row['status']}"
            )

        connection.execute(
            "UPDATE invoices SET status = ?, updated_at = ? WHERE invoice_id = ?",
            (status.value, updated_at, invoice_id),
        )
        connection.execute(
            """
            INSERT INTO decision_events (
                invoice_id, decision, rule_version, actor, correlation_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (invoice_id, decision.value, RULE_VERSION, actor, correlation_id, updated_at),
        )
        updated_row = connection.execute(
            "SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()

    return _invoice_from_row(updated_row)


def list_decision_events(
    db_path: str | Path | None = None, invoice_id: str | None = None
) -> list[sqlite3.Row]:
    with _connect(db_path) as connection:
        if invoice_id is None:
            return connection.execute("SELECT * FROM decision_events ORDER BY id").fetchall()
        return connection.execute(
            "SELECT * FROM decision_events WHERE invoice_id = ? ORDER BY id", (invoice_id,)
        ).fetchall()


def reset_db(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as connection:
        connection.execute("DROP TABLE IF EXISTS decision_events")
        connection.execute("DROP TABLE IF EXISTS invoices")
    init_db(db_path)
