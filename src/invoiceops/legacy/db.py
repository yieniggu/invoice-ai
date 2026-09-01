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
PROJECT_ROOT = Path(__file__).resolve().parents[3]
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


def resolve_db_path(db_path: str | Path | None) -> Path:
    """Resolve the configured SQLite path without opening or creating it."""
    if db_path is not None:
        return Path(db_path)
    path = Path(os.environ.get("INVOICEOPS_DB_PATH", DEFAULT_DB_PATH))
    return path if path.is_absolute() else PROJECT_ROOT / path


def _resolve_db_path(db_path: str | Path | None) -> Path:
    """Compatibility wrapper for internal callers pending their public migration."""
    return resolve_db_path(db_path)


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


def get_or_insert_model_evaluation(
    db_path: str | Path | None,
    invoice_id: str,
    *,
    correlation_id: str,
    recommendation: Recommendation,
    model_name: str,
    model_version: str,
    run_id: str,
    manual_review_probability: float,
) -> sqlite3.Row:
    created_at = datetime.now(UTC).isoformat()
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if (
            connection.execute("SELECT 1 FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
            is None
        ):
            raise LookupError(f"Invoice not found: {invoice_id}")
        existing = connection.execute(
            """
            SELECT * FROM model_evaluations
            WHERE invoice_id = ? AND correlation_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (invoice_id, correlation_id),
        ).fetchone()
        if existing is not None:
            return existing
        connection.execute(
            """
            INSERT INTO model_evaluations (
                invoice_id, correlation_id, model_name, model_version, run_id,
                manual_review_probability, policy_version, policy_threshold,
                recommendation, source, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        return connection.execute(
            "SELECT * FROM model_evaluations WHERE id = last_insert_rowid()"
        ).fetchone()


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


def get_evidence_context(db_path: str | Path | None, evaluation_id: int) -> sqlite3.Row | None:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT evidence_records.contract_version, evidence_records.digest_hex,
                   evidence_batches.id AS batch_id, evidence_batches.root_hash,
                   evidence_batches.status AS batch_status, evidence_batches.created_at AS batch_created_at,
                   evidence_batch_anchors.chain_id, evidence_batch_anchors.contract_address,
                   evidence_batch_anchors.transaction_hash, evidence_batch_anchors.block_number,
                   evidence_batch_anchors.gas_used, evidence_batch_anchors.submitted_at,
                   evidence_batch_anchors.anchored_at, evidence_batch_anchors.status AS anchor_status
            FROM evidence_records
            LEFT JOIN evidence_batch_items ON evidence_batch_items.evaluation_id = evidence_records.evaluation_id
                AND evidence_batch_items.evidence_contract_version = evidence_records.contract_version
            LEFT JOIN evidence_batches ON evidence_batches.id = evidence_batch_items.batch_id
            LEFT JOIN evidence_batch_anchors ON evidence_batch_anchors.batch_id = evidence_batches.id
            WHERE evidence_records.evaluation_id = ?
            ORDER BY evidence_batches.id DESC
            LIMIT 1
            """,
            (evaluation_id,),
        ).fetchone()


def get_persisted_canonical_payload(
    db_path: str | Path | None, evaluation_id: int
) -> str | None:
    """Return the canonical payload stored with an evidence record, without recomputing it."""
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT canonical_payload FROM evidence_records WHERE evaluation_id = ?", (evaluation_id,)
        ).fetchone()
    return None if row is None else row["canonical_payload"]


def get_model_evaluation(db_path: str | Path | None, evaluation_id: int) -> sqlite3.Row | None:
    with _connect(db_path) as connection:
        return connection.execute(
            "SELECT * FROM model_evaluations WHERE id = ?", (evaluation_id,)
        ).fetchone()


def list_model_evaluation_records(db_path: str | Path | None) -> list[sqlite3.Row]:
    with _connect(db_path) as connection:
        return connection.execute("SELECT * FROM model_evaluations ORDER BY id").fetchall()


def insert_evidence_records(
    db_path: str | Path | None,
    records: list[tuple[int, str, str, str, str, str, str, str]],
) -> None:
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            """
            INSERT INTO evidence_records (
                evaluation_id, contract_version, evidence_json, created_at,
                canonical_version, canonical_payload, digest_algorithm, digest_hex
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )


def get_evidence_record(
    db_path: str | Path | None, evaluation_id: int, contract_version: str
) -> sqlite3.Row | None:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT * FROM evidence_records
            WHERE evaluation_id = ? AND contract_version = ?
            """,
            (evaluation_id, contract_version),
        ).fetchone()


def list_evidence_records(db_path: str | Path | None, contract_version: str) -> list[sqlite3.Row]:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT * FROM evidence_records
            WHERE contract_version = ?
            ORDER BY evaluation_id
            """,
            (contract_version,),
        ).fetchall()


def get_evidence_hash(
    db_path: str | Path | None, evaluation_id: int, contract_version: str
) -> sqlite3.Row | None:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT evaluation_id, digest_hex
            FROM evidence_records
            WHERE evaluation_id = ? AND contract_version = ?
            """,
            (evaluation_id, contract_version),
        ).fetchone()


def insert_evidence_batch(
    db_path: str | Path | None,
    *,
    policy_version: str,
    root_hash: str,
    leaf_count: int,
    created_at: str,
    items: list[tuple[int, str, int, str, str]],
    source_batch_id: int | None = None,
    new_evaluation_ids: list[int] | None = None,
) -> int:
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        new_ids = new_evaluation_ids if new_evaluation_ids is not None else [item[0] for item in items]
        if source_batch_id is not None:
            source = connection.execute(
                "SELECT id FROM evidence_batches WHERE id = ?", (source_batch_id,)
            ).fetchone()
            if source is None:
                raise sqlite3.IntegrityError("source evidence batch does not exist")
            source_ids = {
                row[0]
                for row in connection.execute(
                    "SELECT evaluation_id FROM evidence_batch_items WHERE batch_id = ?",
                    (source_batch_id,),
                )
            }
            if source_ids.intersection(new_ids):
                raise sqlite3.IntegrityError("evidence record already belongs to the source batch")
        if new_ids:
            placeholders = ", ".join("?" for _ in new_ids)
            reused = connection.execute(
                f"SELECT evaluation_id FROM evidence_batch_items WHERE evaluation_id IN ({placeholders}) LIMIT 1",
                new_ids,
            ).fetchone()
            if reused is not None:
                raise sqlite3.IntegrityError("evidence record already belongs to an evidence batch")
        cursor = connection.execute(
            """
            INSERT INTO evidence_batches (
                policy_version, root_hash, leaf_count, status, created_at
            ) VALUES (?, ?, ?, 'verified', ?)
            """,
            (policy_version, root_hash, leaf_count, created_at),
        )
        batch_id = cursor.lastrowid
        connection.executemany(
            """
            INSERT INTO evidence_batch_items (
                batch_id, evaluation_id, evidence_contract_version, leaf_index, leaf_hash, proof_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [(batch_id, *item) for item in items],
        )
        if source_batch_id is not None:
            connection.execute(
                """
                INSERT INTO evidence_batch_successors (origin_batch_id, successor_batch_id, created_at)
                VALUES (?, ?, ?)
                """,
                (source_batch_id, batch_id, created_at),
            )
    return batch_id


def get_evidence_batch(db_path: str | Path | None, batch_id: int) -> sqlite3.Row | None:
    with _connect(db_path) as connection:
        return connection.execute(
            "SELECT * FROM evidence_batches WHERE id = ?", (batch_id,)
        ).fetchone()


def list_evidence_batches(db_path: str | Path | None) -> list[sqlite3.Row]:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT evidence_batches.*, evidence_batch_anchors.status AS anchor_status
            FROM evidence_batches
            LEFT JOIN evidence_batch_anchors ON evidence_batch_anchors.batch_id = evidence_batches.id
            ORDER BY evidence_batches.id DESC
            """
        ).fetchall()


def get_evidence_batch_predecessor(db_path: str | Path | None, batch_id: int) -> sqlite3.Row | None:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT origin_batch_id FROM evidence_batch_successors
            WHERE successor_batch_id = ?
            """,
            (batch_id,),
        ).fetchone()


def list_evidence_batch_successors(db_path: str | Path | None, batch_id: int) -> list[sqlite3.Row]:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT successor_batch_id FROM evidence_batch_successors
            WHERE origin_batch_id = ? ORDER BY successor_batch_id
            """,
            (batch_id,),
        ).fetchall()


def list_evidence_record_batch_memberships(db_path: str | Path | None) -> list[sqlite3.Row]:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT evaluation_id, batch_id FROM evidence_batch_items
            ORDER BY evaluation_id, batch_id DESC
            """
        ).fetchall()


def list_evidence_batches_for_invoice(
    db_path: str | Path | None, invoice_id: str
) -> list[sqlite3.Row]:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT DISTINCT evidence_batches.*, evidence_batch_anchors.status AS anchor_status
            FROM evidence_batches
            JOIN evidence_batch_items ON evidence_batch_items.batch_id = evidence_batches.id
            JOIN model_evaluations ON model_evaluations.id = evidence_batch_items.evaluation_id
            LEFT JOIN evidence_batch_anchors ON evidence_batch_anchors.batch_id = evidence_batches.id
            WHERE model_evaluations.invoice_id = ?
            ORDER BY evidence_batches.id DESC
            """,
            (invoice_id,),
        ).fetchall()


def list_evidence_batch_items(db_path: str | Path | None, batch_id: int) -> list[sqlite3.Row]:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT * FROM evidence_batch_items
            WHERE batch_id = ?
            ORDER BY leaf_index
            """,
            (batch_id,),
        ).fetchall()


def get_evidence_batch_anchor(db_path: str | Path | None, anchor_id: int) -> sqlite3.Row | None:
    with _connect(db_path) as connection:
        return connection.execute(
            "SELECT * FROM evidence_batch_anchors WHERE id = ?", (anchor_id,)
        ).fetchone()


def get_latest_evidence_batch_anchor(
    db_path: str | Path | None, batch_id: int
) -> sqlite3.Row | None:
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT * FROM evidence_batch_anchors
            WHERE batch_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (batch_id,),
        ).fetchone()


def insert_evidence_batch_anchor(
    db_path: str | Path | None,
    *,
    batch_id: int,
    root_hash: str,
    chain_id: int,
    contract_address: str,
    transaction_hash: str | None,
    submitted_at: str,
    status: str,
) -> int:
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        batch = connection.execute(
            "SELECT root_hash, status FROM evidence_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise LookupError(f"Evidence batch not found: {batch_id}")
        if batch["status"] != "verified" or batch["root_hash"] != root_hash:
            raise ValueError("Evidence batch is not the verified canonical root")
        cursor = connection.execute(
            """
            INSERT INTO evidence_batch_anchors (
                batch_id, root_hash, chain_id, contract_address, transaction_hash,
                submitted_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                root_hash,
                chain_id,
                contract_address,
                transaction_hash,
                submitted_at,
                status,
            ),
        )
    return cursor.lastrowid


def update_evidence_batch_anchor(
    db_path: str | Path | None,
    anchor_id: int,
    *,
    status: str,
    block_number: int | None = None,
    gas_used: int | None = None,
    anchored_at: str | None = None,
) -> None:
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if (
            connection.execute(
                "SELECT 1 FROM evidence_batch_anchors WHERE id = ?", (anchor_id,)
            ).fetchone()
            is None
        ):
            raise LookupError(f"Evidence batch anchor not found: {anchor_id}")
        connection.execute(
            """
            UPDATE evidence_batch_anchors
            SET status = ?, block_number = ?, gas_used = ?, anchored_at = ?
            WHERE id = ?
            """,
            (status, block_number, gas_used, anchored_at, anchor_id),
        )


def set_evidence_batch_anchor_transaction(
    db_path: str | Path | None, anchor_id: int, transaction_hash: str
) -> None:
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE evidence_batch_anchors
            SET transaction_hash = ?, status = 'submitted'
            WHERE id = ?
            """,
            (transaction_hash, anchor_id),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"Evidence batch anchor not found: {anchor_id}")


def reset_db(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as connection:
        connection.execute("DROP TABLE IF EXISTS evidence_batch_anchors")
        # The lineage table references both batches, so it must be removed first.
        connection.execute("DROP TABLE IF EXISTS evidence_batch_successors")
        connection.execute("DROP TABLE IF EXISTS evidence_batch_items")
        connection.execute("DROP TABLE IF EXISTS evidence_batches")
        connection.execute("DROP TABLE IF EXISTS evidence_records")
        connection.execute("DROP TABLE IF EXISTS model_evaluations")
        connection.execute("DROP TABLE IF EXISTS decision_events")
        connection.execute("DROP TABLE IF EXISTS invoices")
        connection.execute("DROP TABLE IF EXISTS schema_migrations")
    init_db(db_path)
