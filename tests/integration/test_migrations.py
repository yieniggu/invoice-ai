import sqlite3
from pathlib import Path

import pytest

from invoiceops import demo_paths
from invoiceops.demo_reset import reset_local_demo
from invoiceops.domain.policy import fallback_recommendation
from invoiceops.legacy.app import create_app
from invoiceops.legacy.db import _connect, init_db, insert_model_evaluation, run_migrations
from invoiceops.legacy.seed import seed_invoices


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

    assert run_migrations(db_path) == 10
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
        (4, "notebook_audit_idempotency"),
        (5, "evidence_records"),
        (6, "evidence_hashes"),
        (7, "evidence_batches"),
        (8, "evidence_batch_anchors"),
        (9, "evidence_batch_successors"),
        (10, "evidence_batch_anchor_targets"),
    ]


def test_anchor_target_migration_preserves_legacy_local_anchor_and_allows_remote(tmp_path: Path) -> None:
    db_path = tmp_path / "invoiceops.db"
    with _connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, '2026-01-01T00:00:00Z')",
            [
                (version, name)
                for version, name in (
                    (1, "initial"),
                    (2, "ml_risk_context"),
                    (3, "model_evaluations"),
                    (4, "notebook_audit_idempotency"),
                    (5, "evidence_records"),
                    (6, "evidence_hashes"),
                    (7, "evidence_batches"),
                    (8, "evidence_batch_anchors"),
                    (9, "evidence_batch_successors"),
                )
            ],
        )
        connection.execute("CREATE TABLE evidence_batches (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO evidence_batches (id) VALUES (1)")
        connection.execute(
            """
            CREATE TABLE evidence_batch_anchors (
                id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL, root_hash TEXT NOT NULL,
                chain_id INTEGER NOT NULL, contract_address TEXT NOT NULL, transaction_hash TEXT,
                block_number INTEGER, gas_used INTEGER, submitted_at TEXT NOT NULL, anchored_at TEXT,
                status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX idx_evidence_batch_anchors_batch_id ON evidence_batch_anchors (batch_id)"
        )
        connection.execute(
            """
            INSERT INTO evidence_batch_anchors (
                batch_id, root_hash, chain_id, contract_address, submitted_at, status
            ) VALUES (1, 'a', 31337, '0xlocal', '2026-01-01T00:00:00Z', 'verified')
            """
        )

    assert run_migrations(db_path) == 1

    with _connect(db_path) as connection:
        legacy_target = connection.execute(
            "SELECT target FROM evidence_batch_anchors WHERE batch_id = 1"
        ).fetchone()["target"]
        connection.execute(
            """
            INSERT INTO evidence_batch_anchors (
                batch_id, target, root_hash, chain_id, contract_address, submitted_at, status
            ) VALUES (1, 'remote', 'a', 10200, '0xremote', '2026-01-01T00:01:00Z', 'submitted')
            """
        )
        anchors = connection.execute(
            "SELECT target FROM evidence_batch_anchors WHERE batch_id = 1 ORDER BY id"
        ).fetchall()

    assert legacy_target == "local"
    assert [anchor["target"] for anchor in anchors] == ["local", "remote"]


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
    assert {index["name"] for index in indexes} == {
        "idx_model_evaluations_invoice_id",
        "idx_model_evaluations_notebook_operation",
    }
    notebook_index = next(
        index for index in indexes if index["name"] == "idx_model_evaluations_notebook_operation"
    )
    assert notebook_index["unique"] == 1


def test_model_evaluations_migration_rejects_unknown_sources(tmp_path: Path) -> None:
    db_path = tmp_path / "invoiceops.db"

    run_migrations(db_path)

    with (
        _connect(db_path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"),
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

    assert run_migrations(db_path) == 9

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
    assert [row["version"] for row in versions] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
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
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0


def test_reset_demo_migrates_then_seeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    demo_root = Path("var/local-demo")
    resolved_root = demo_paths.initialize_demo_root(demo_root)
    db_path = resolved_root / "invoiceops.db"
    state_path = resolved_root / "notebook-state" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"completed_actions": {}}\n')
    artifact_path = resolved_root / "mlflow-artifacts" / "run" / "model"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("demo artifact")

    reset_local_demo(demo_root, confirmed=True)

    with _connect(db_path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 8
    assert not state_path.exists()
    assert not artifact_path.exists()


def test_reset_demo_removes_successor_lineage_before_batches_and_preserves_foreign_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    demo_root = Path("var/local-demo")
    db_path = demo_paths.initialize_demo_root(demo_root) / "invoiceops.db"
    init_db(db_path)
    with _connect(db_path) as connection:
        origin_id = connection.execute(
            """
            INSERT INTO evidence_batches (policy_version, root_hash, leaf_count, status, created_at)
            VALUES ('invoice-merkle-v1', 'a' || printf('%063d', 0), 1, 'verified', '2026-01-01T00:00:00Z')
            """
        ).lastrowid
        successor_id = connection.execute(
            """
            INSERT INTO evidence_batches (policy_version, root_hash, leaf_count, status, created_at)
            VALUES ('invoice-merkle-v1', 'b' || printf('%063d', 0), 1, 'verified', '2026-01-01T00:00:00Z')
            """
        ).lastrowid
        connection.execute(
            """
            INSERT INTO evidence_batch_successors (origin_batch_id, successor_batch_id, created_at)
            VALUES (?, ?, '2026-01-01T00:00:00Z')
            """,
            (origin_id, successor_id),
        )
        connection.execute(
            """
            INSERT INTO evidence_batch_anchors (
                batch_id, root_hash, chain_id, contract_address, transaction_hash, submitted_at, status
            ) VALUES (?, 'b' || printf('%063d', 0), 31337, '0xabc', NULL, '2026-01-01T00:00:00Z', 'submitted')
            """,
            (successor_id,),
        )

    reset_local_demo(demo_root, confirmed=True)

    with _connect(db_path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM evidence_batches").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM evidence_batch_successors").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM evidence_batch_anchors").fetchone()[0] == 0


def test_reset_demo_without_confirmation_does_not_mutate_existing_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    demo_root = Path("var/local-demo")
    db_path = demo_paths.initialize_demo_root(demo_root) / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10030",
        correlation_id="audit-before-unconfirmed-reset",
        recommendation=fallback_recommendation(),
    )

    resources = reset_local_demo(demo_root, confirmed=False)

    with _connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 8
        assert connection.execute("SELECT COUNT(*) FROM model_evaluations").fetchone()[0] == 1
    assert db_path in resources


def test_reset_demo_refuses_a_symlinked_resource(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    demo_root = demo_paths.initialize_demo_root(Path("var/local-demo"))
    protected_database = tmp_path / "protected.db"
    protected_database.write_text("do not reset")
    (demo_root / "invoiceops.db").symlink_to(protected_database)

    with pytest.raises(ValueError, match="Refusing to follow symlinked demo resource"):
        reset_local_demo(demo_root, confirmed=True)
    assert protected_database.read_text() == "do not reset"
