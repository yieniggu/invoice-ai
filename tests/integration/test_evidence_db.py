import json
from pathlib import Path

from invoiceops.domain.policy import recommend_from_probability
from invoiceops.evidence import (
    EvidenceProvenance,
    EvidenceRecord,
    get_evidence_record,
    persist_evidence_records,
    verify_persisted_evidence_record,
)
from invoiceops.legacy.db import _connect, insert_model_evaluation, run_migrations
from invoiceops.legacy.seed import seed_invoices


def _record(evaluation_id: int) -> EvidenceRecord:
    return EvidenceRecord(
        evaluation_id=evaluation_id,
        invoice_id="INV-10023",
        correlation_id="corr-migration",
        model_name="invoice-review",
        model_version="7",
        run_id="run-123",
        manual_review_probability="0.8",
        policy_version="ml-policy-v1",
        policy_threshold="0.8",
        recommendation="MANUAL_REVIEW",
        source="model",
        reason="probability_at_or_above_threshold",
        evaluation_created_at="2026-01-01T00:00:00Z",
        provenance=EvidenceProvenance(
            dataset_version="invoice-risk-v1",
            feature_schema_version="invoice-features-v1",
            git_commit="a" * 40,
        ),
    )


def test_evidence_records_migration_links_one_v1_record_to_each_evaluation(tmp_path) -> None:
    db_path = tmp_path / "invoiceops.db"

    assert run_migrations(db_path) == 6

    with _connect(db_path) as connection:
        columns = connection.execute("PRAGMA table_info(evidence_records)").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_list(evidence_records)").fetchall()
        indexes = connection.execute("PRAGMA index_list(evidence_records)").fetchall()
    assert [column["name"] for column in columns] == [
        "id",
        "evaluation_id",
        "contract_version",
        "evidence_json",
        "created_at",
        "canonical_version",
        "canonical_payload",
        "digest_algorithm",
        "digest_hex",
    ]
    assert [(key["table"], key["from"], key["to"]) for key in foreign_keys] == [
        ("model_evaluations", "evaluation_id", "id")
    ]
    assert {index["name"] for index in indexes} == {"idx_evidence_records_evaluation_version"}


def test_evidence_hash_migration_is_additive_for_existing_evidence_records(tmp_path) -> None:
    db_path = tmp_path / "invoiceops.db"

    run_migrations(db_path)

    with _connect(db_path) as connection:
        columns = connection.execute("PRAGMA table_info(evidence_records)").fetchall()
    hash_columns = {column["name"]: column for column in columns}
    assert all(
        hash_columns[name]["notnull"] == 0
        for name in ("canonical_version", "canonical_payload", "digest_algorithm", "digest_hex")
    )


def test_evidence_hash_migration_preserves_existing_v1_records(tmp_path) -> None:
    db_path = tmp_path / "invoiceops.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    source_migrations_dir = Path(__file__).parents[2] / "migrations"
    for source in source_migrations_dir.glob("00[1-5]_*.sql"):
        (migrations_dir / source.name).write_text(source.read_text())

    assert run_migrations(db_path, migrations_dir=migrations_dir) == 5
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO invoices (
                invoice_id, vendor_name, invoice_amount_cents, has_purchase_order,
                three_way_match, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "INV-10023",
                "Acme Industrial",
                482_000,
                1,
                1,
                "PENDING",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-migration",
        model_name="invoice-review",
        model_version="7",
        run_id="run-123",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )
    record = _record(1)
    evidence_json = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"))
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO evidence_records (evaluation_id, contract_version, evidence_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (1, record.contract_version, evidence_json, "2026-01-01T00:00:00Z"),
        )
        before = connection.execute(
            """
            SELECT id, evaluation_id, contract_version, evidence_json, created_at
            FROM evidence_records
            """
        ).fetchone()

    migration = source_migrations_dir / "006_evidence_hashes.sql"
    (migrations_dir / migration.name).write_text(migration.read_text())

    assert run_migrations(db_path, migrations_dir=migrations_dir) == 1

    with _connect(db_path) as connection:
        after = connection.execute(
            """
            SELECT id, evaluation_id, contract_version, evidence_json, created_at,
                   canonical_version, canonical_payload, digest_algorithm, digest_hex
            FROM evidence_records
            """
        ).fetchone()
    assert tuple(after) == (*tuple(before), None, None, None, None)
    assert get_evidence_record(db_path, 1) == record


def test_verify_returns_false_when_persisted_digest_is_altered(tmp_path) -> None:
    db_path = tmp_path / "invoiceops.db"
    run_migrations(db_path)
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-migration",
        model_name="invoice-review",
        model_version="7",
        run_id="run-123",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )
    persist_evidence_records(db_path, [_record(1)])
    assert verify_persisted_evidence_record(db_path, 1) is True

    with _connect(db_path) as connection:
        connection.execute(
            "UPDATE evidence_records SET digest_hex = ? WHERE evaluation_id = ?", ("0", 1)
        )

    assert verify_persisted_evidence_record(db_path, 1) is False
