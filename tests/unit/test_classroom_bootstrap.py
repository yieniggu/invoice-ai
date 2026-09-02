from pathlib import Path

from invoiceops.legacy.classroom_bootstrap import bootstrap_classroom_database
from invoiceops.legacy.db import _connect
from invoiceops.legacy.seed import SEED_INVOICES


def test_classroom_bootstrap_migrates_and_seeds_idempotently(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "invoiceops.db"
    monkeypatch.setenv("INVOICEOPS_DB_PATH", str(db_path))

    bootstrap_classroom_database()
    bootstrap_classroom_database()

    with _connect(db_path) as connection:
        invoice_count = connection.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        migration_count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

    assert invoice_count == len(SEED_INVOICES)
    assert migration_count == 9
