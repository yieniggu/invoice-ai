import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from invoiceops.domain.models import CountryRisk, Decision, Invoice, InvoiceStatus
from invoiceops.domain.policy import Recommendation
from invoiceops.domain.rules import RULE_VERSION

DEFAULT_DB_PATH = Path("var/invoiceops.db")
INVOICE_LIST_LIMIT = 100
MIGRATION_FILENAME = re.compile(r"(?P<version>\d{3})_(?P<name>[a-z0-9]+(?:_[a-z0-9]+)*)\.sql$")
INITIAL_TABLE_COLUMNS = {
    "invoices": (
        ("invoice_id", "TEXT", 0, 1),
        ("vendor_name", "TEXT", 1, 0),
        ("invoice_amount_cents", "INTEGER", 1, 0),
        ("has_purchase_order", "INTEGER", 1, 0),
        ("three_way_match", "INTEGER", 1, 0),
        ("status", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "decision_events": (
        ("id", "INTEGER", 0, 1),
        ("invoice_id", "TEXT", 1, 0),
        ("decision", "TEXT", 1, 0),
        ("rule_version", "TEXT", 1, 0),
        ("actor", "TEXT", 1, 0),
        ("correlation_id", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ),
}


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


def _default_migrations_path() -> Path:
    return Path(__file__).resolve().parents[3] / "migrations"


def _migration_files(migrations_dir: Path) -> list[tuple[int, str, Path]]:
    migrations: list[tuple[int, str, Path]] = []
    for path in migrations_dir.glob("*.sql"):
        match = MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            raise ValueError(f"Invalid migration filename: {path.name}")
        migrations.append((int(match["version"]), match["name"], path))
    migrations.sort()
    if len({version for version, _, _ in migrations}) != len(migrations):
        raise ValueError("Migration versions must be unique")
    return migrations


def _migration_statements(path: Path) -> list[str]:
    statements: list[str] = []
    statement = ""
    for line in path.read_text().splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            statements.append(statement)
            statement = ""
    if statement.strip():
        raise ValueError(f"Migration has an incomplete statement: {path.name}")
    return statements


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
        ).fetchone()
        is not None
    )


def _matches_initial_schema(connection: sqlite3.Connection) -> bool:
    for table_name, expected_columns in INITIAL_TABLE_COLUMNS.items():
        columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        actual_columns = tuple(
            (column["name"], column["type"].upper(), column["notnull"], column["pk"])
            for column in columns
        )
        if actual_columns != expected_columns:
            return False

    foreign_keys = connection.execute("PRAGMA foreign_key_list(decision_events)").fetchall()
    if [
        (
            foreign_key["table"],
            foreign_key["from"],
            foreign_key["to"],
            foreign_key["on_update"],
            foreign_key["on_delete"],
            foreign_key["match"],
        )
        for foreign_key in foreign_keys
    ] != [("invoices", "invoice_id", "invoice_id", "NO ACTION", "NO ACTION", "NONE")]:
        return False

    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'decision_events'"
    ).fetchone()["sql"]
    return "AUTOINCREMENT" in table_sql.upper()


def run_migrations(db_path: str | Path | None = None, *, migrations_dir: Path | None = None) -> int:
    migrations = _migration_files(migrations_dir or _default_migrations_path())
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
        applied_versions = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations")
        }

        # Legacy databases already contain the exact initial schema but no migration ledger.
        if (
            1 not in applied_versions
            and _table_exists(connection, "invoices")
            and _table_exists(connection, "decision_events")
        ):
            if not _matches_initial_schema(connection):
                raise ValueError("Legacy schema does not match the expected initial schema")
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (1, "initial", datetime.now(UTC).isoformat()),
            )
            connection.commit()
            applied_versions.add(1)

        pending = [migration for migration in migrations if migration[0] not in applied_versions]
        for version, name, path in pending:
            try:
                connection.execute("BEGIN")
                for statement in _migration_statements(path):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, datetime.now(UTC).isoformat()),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    print(f"{len(pending)} migrations pending")
    return len(pending)


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
        vendor_tenure_days=row["vendor_tenure_days"],
        previous_incidents_12m=row["previous_incidents_12m"],
        bank_account_recently_changed=bool(row["bank_account_recently_changed"]),
        amount_vs_vendor_median=row["amount_vs_vendor_median"],
        country_risk=CountryRisk(row["country_risk"]),
    )


def init_db(db_path: str | Path | None = None) -> None:
    run_migrations(db_path)


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


def insert_model_evaluation(
    db_path: str | Path | None,
    invoice_id: str,
    *,
    correlation_id: str,
    recommendation: Recommendation,
    model_name: str | None = None,
    model_version: str | None = None,
    run_id: str | None = None,
    manual_review_probability: float | None = None,
    created_at: str | None = None,
) -> None:
    created_at = created_at or datetime.now(UTC).isoformat()

    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if (
            connection.execute(
                "SELECT 1 FROM invoices WHERE invoice_id = ?", (invoice_id,)
            ).fetchone()
            is None
        ):
            raise LookupError(f"Invoice not found: {invoice_id}")
        connection.execute(
            """
            INSERT INTO model_evaluations (
                invoice_id, correlation_id, model_name, model_version, run_id,
                manual_review_probability, policy_version, policy_threshold,
                recommendation, source, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                invoice_id,
                correlation_id,
                model_name,
                model_version,
                run_id,
                manual_review_probability,
                recommendation.policy_version,
                recommendation.threshold,
                recommendation.decision.value,
                recommendation.source,
                recommendation.reason,
                created_at,
            ),
        )


def list_model_evaluations(db_path: str | Path | None, invoice_id: str) -> list[sqlite3.Row]:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT * FROM model_evaluations
            WHERE invoice_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (invoice_id,),
        ).fetchall()


def reset_db(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as connection:
        connection.execute("DROP TABLE IF EXISTS model_evaluations")
        connection.execute("DROP TABLE IF EXISTS decision_events")
        connection.execute("DROP TABLE IF EXISTS invoices")
        connection.execute("DROP TABLE IF EXISTS schema_migrations")
    init_db(db_path)
