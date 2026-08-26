import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from invoiceops.legacy.app import create_app
from invoiceops.legacy.db import _connect, init_db, run_migrations


def test_migrations_apply_in_order(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "002_second.sql").write_text("CREATE TABLE second_table (id INTEGER);\n")
    (migrations_dir / "001_first.sql").write_text("CREATE TABLE first_table (id INTEGER);\n")
    db_path = tmp_path / "invoiceops.db"

    assert run_migrations(db_path, migrations_dir=migrations_dir) == 2

    with _connect(db_path) as connection:
        versions = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [(row["version"], row["name"]) for row in versions] == [
        (1, "first"),
        (2, "second"),
    ]


def test_migrations_are_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "invoiceops.db"

    assert run_migrations(db_path) == 3
    assert run_migrations(db_path) == 0

    assert "0 migrations pending" in capsys.readouterr().out
    with _connect(db_path) as connection:
        versions = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [(row["version"], row["name"]) for row in versions] == [
        (1, "initial"),
        (2, "ml_risk_context"),
        (3, "model_evaluations"),
    ]


def test_model_evaluations_migration_has_the_expected_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "invoiceops.db"

    run_migrations(db_path)

    with _connect(db_path) as connection:
        evaluations = connection.execute("PRAGMA table_info(model_evaluations)").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_list(model_evaluations)").fetchall()
        indexes = connection.execute("PRAGMA index_list(model_evaluations)").fetchall()
    assert [row["name"] for row in evaluations] == [
        "id",
        "invoice_id",
        "correlation_id",
        "model_name",
        "model_version",
        "run_id",
        "manual_review_probability",
        "policy_version",
        "policy_threshold",
        "recommendation",
        "source",
        "reason",
        "created_at",
    ]
    assert foreign_keys[0]["table"] == "invoices"
    assert [index["name"] for index in indexes] == ["idx_model_evaluations_invoice_id"]


def test_model_evaluations_migration_rejects_unknown_sources(tmp_path: Path) -> None:
    db_path = tmp_path / "invoiceops.db"

    run_migrations(db_path)

    with _connect(db_path) as connection, pytest.raises(
        sqlite3.IntegrityError, match="CHECK constraint failed"
    ):
        connection.execute(
            """
            INSERT INTO model_evaluations (
                invoice_id, correlation_id, policy_version, policy_threshold,
                recommendation, source, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "INV-INVALID",
                "corr-invalid",
                "ml-policy-v1",
                0.8,
                "MANUAL_REVIEW",
                "unknown",
                "invalid-source-test",
                "2026-01-01T00:00:00+00:00",
            ),
        )


def test_init_db_applies_pending_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_initial.sql").write_text(
        "CREATE TABLE initialized_by_migration (id INTEGER);\n"
    )
    db_path = tmp_path / "invoiceops.db"
    monkeypatch.setattr("invoiceops.legacy.db._default_migrations_path", lambda: migrations_dir)

    init_db(db_path)

    with _connect(db_path) as connection:
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'initialized_by_migration'"
        ).fetchone()
    assert [row["version"] for row in versions] == [1]
    assert table_exists is not None


def test_initial_migration_reproduces_legacy_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "invoiceops.db"

    run_migrations(db_path)

    with _connect(db_path) as connection:
        invoices = connection.execute("PRAGMA table_info(invoices)").fetchall()
        events = connection.execute("PRAGMA table_info(decision_events)").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_list(decision_events)").fetchall()
    assert [row["name"] for row in invoices] == [
        "invoice_id",
        "vendor_name",
        "invoice_amount_cents",
        "has_purchase_order",
        "three_way_match",
        "status",
        "created_at",
        "updated_at",
        "vendor_tenure_days",
        "previous_incidents_12m",
        "bank_account_recently_changed",
        "amount_vs_vendor_median",
        "country_risk",
    ]
    assert [row["name"] for row in events] == [
        "id",
        "invoice_id",
        "decision",
        "rule_version",
        "actor",
        "correlation_id",
        "created_at",
    ]
    assert foreign_keys[0]["table"] == "invoices"


def test_invalid_migration_filename_is_rejected(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "invalid.sql").write_text("CREATE TABLE ignored (id INTEGER);\n")

    with pytest.raises(ValueError, match="Invalid migration filename"):
        run_migrations(tmp_path / "invoiceops.db", migrations_dir=migrations_dir)


def test_failed_migration_does_not_record_version(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_bad.sql").write_text(
        "CREATE TABLE incomplete (id INTEGER);\nINVALID SQL;\n"
    )
    db_path = tmp_path / "invoiceops.db"

    with pytest.raises(sqlite3.OperationalError):
        run_migrations(db_path, migrations_dir=migrations_dir)

    with _connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'incomplete'"
            ).fetchone()[0]
            == 0
        )


def test_legacy_database_is_adopted_without_changing_data(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE invoices (
                invoice_id TEXT PRIMARY KEY, vendor_name TEXT NOT NULL,
                invoice_amount_cents INTEGER NOT NULL, has_purchase_order INTEGER NOT NULL,
                three_way_match INTEGER NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE decision_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT NOT NULL,
                decision TEXT NOT NULL, rule_version TEXT NOT NULL, actor TEXT NOT NULL,
                correlation_id TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO invoices VALUES (
                'INV-LEGACY', 'Legacy Vendor', 100, 1, 1, 'PENDING',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
            )
            """
        )
        before = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'invoices'"
        ).fetchone()[0]

    assert run_migrations(db_path) == 2

    with _connect(db_path) as connection:
        after = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'invoices'"
        ).fetchone()[0]
        count = connection.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        risk_context = connection.execute(
            """
            SELECT vendor_tenure_days, previous_incidents_12m, bank_account_recently_changed,
                   amount_vs_vendor_median, country_risk
            FROM invoices WHERE invoice_id = 'INV-LEGACY'
            """
        ).fetchone()
    assert after != before
    assert count == 1
    assert [row["version"] for row in versions] == [1, 2, 3]
    assert tuple(risk_context) == (0, 0, 0, 1.0, "medium")


def test_legacy_database_with_divergent_schema_is_not_adopted(tmp_path: Path) -> None:
    db_path = tmp_path / "divergent-legacy.db"
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE invoices (
                invoice_id TEXT PRIMARY KEY, vendor_name TEXT NOT NULL,
                invoice_amount_cents INTEGER NOT NULL, has_purchase_order INTEGER NOT NULL,
                three_way_match INTEGER NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE decision_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT NOT NULL,
                decision TEXT NOT NULL, rule_version TEXT NOT NULL, actor TEXT NOT NULL,
                correlation_id TEXT NOT NULL, created_at TEXT NOT NULL
            )
            """
        )

    with pytest.raises(ValueError, match="does not match the expected initial schema"):
        run_migrations(db_path)

    with _connect(db_path) as connection:
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
    assert [row["version"] for row in versions] == []


def test_create_app_initializes_an_empty_database(tmp_path: Path) -> None:
    db_path = tmp_path / "invoiceops.db"

    create_app(db_path)

    with _connect(db_path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0


def test_reset_demo_migrates_then_seeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "invoiceops.db"
    monkeypatch.setenv("INVOICEOPS_DB_PATH", str(db_path))

    result = subprocess.run(
        [sys.executable, "scripts/reset_demo.py"],
        cwd=Path(__file__).parents[2],
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )

    with _connect(db_path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 8
    assert "Invoices: 8" in result.stdout
