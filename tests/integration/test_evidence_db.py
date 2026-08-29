import json
from pathlib import Path

import pytest

from invoiceops.domain.policy import recommend_from_probability
from invoiceops.evidence import (
    EvidencePersistenceError,
    EvidenceProvenance,
    EvidenceRecord,
    backfill_canonical_evidence_records,
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

    assert run_migrations(db_path) == 7

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


def test_canonical_backfill_migrates_pre_006_records_without_touching_protected_fields(tmp_path) -> None:
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
        correlation_id="corr-backfill",
        model_name="invoice-review",
        model_version="7",
        run_id="run-123",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )
    record = _record(1)
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO evidence_records (evaluation_id, contract_version, evidence_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                1,
                record.contract_version,
                json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":")),
                "2026-01-01T00:00:00Z",
            ),
        )
    assert run_migrations(db_path) == 2

    with _connect(db_path) as connection:
        before = connection.execute(
            """
            SELECT id, evaluation_id, contract_version, evidence_json, created_at,
                   canonical_version, canonical_payload, digest_algorithm, digest_hex
            FROM evidence_records
            """
        ).fetchone()
        batch_counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("evidence_batches", "evidence_batch_items")
        )

    assert backfill_canonical_evidence_records(db_path, dry_run=True).evaluation_ids == [1]
    with _connect(db_path) as connection:
        assert tuple(connection.execute("SELECT * FROM evidence_records").fetchone()) == tuple(before)

    assert backfill_canonical_evidence_records(db_path).evaluation_ids == [1]
    assert verify_persisted_evidence_record(db_path, 1) is True
    assert backfill_canonical_evidence_records(db_path).evaluation_ids == []
    with _connect(db_path) as connection:
        after = connection.execute(
            """
            SELECT id, evaluation_id, contract_version, evidence_json, created_at,
                   canonical_version, canonical_payload, digest_algorithm, digest_hex
            FROM evidence_records
            """
        ).fetchone()
        assert tuple(after)[:5] == tuple(before)[:5]
        assert all(value is not None for value in tuple(after)[5:])
        assert batch_counts == tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("evidence_batches", "evidence_batch_items")
        )


def test_canonical_backfill_rejects_invalid_legacy_record_atomically(tmp_path) -> None:
    db_path = tmp_path / "invoiceops.db"
    run_migrations(db_path)
    seed_invoices(db_path)
    for index, invoice_id in enumerate(("INV-10023", "INV-10024"), start=1):
        insert_model_evaluation(
            db_path,
            invoice_id,
            correlation_id=f"corr-backfill-{index}",
            model_name="invoice-review",
            model_version="7",
            run_id=f"run-{index}",
            manual_review_probability=0.8,
            recommendation=recommend_from_probability(0.8),
        )
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO evidence_records (evaluation_id, contract_version, evidence_json, created_at)
            VALUES (?, ?, ?, ?), (?, ?, ?, ?)
            """,
            (
                1,
                _record(1).contract_version,
                json.dumps(_record(1).to_dict(), sort_keys=True, separators=(",", ":")),
                "2026-01-01T00:00:00Z",
                2,
                _record(2).contract_version,
                "{invalid-json",
                "2026-01-01T00:00:00Z",
            ),
        )

    with pytest.raises(EvidencePersistenceError, match="stored evidence record is invalid"):
        backfill_canonical_evidence_records(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT canonical_version, canonical_payload, digest_algorithm, digest_hex
            FROM evidence_records ORDER BY evaluation_id
            """
        ).fetchall()
    assert [tuple(row) for row in rows] == [(None, None, None, None), (None, None, None, None)]


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


def test_evidence_batch_persists_verified_records_in_evaluation_id_order(tmp_path) -> None:
    from invoiceops.evidence import create_evidence_batch, get_evidence_batch, verify_merkle_proof

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    for index, invoice_id in enumerate(("INV-10023", "INV-10024", "INV-10025"), start=1):
        insert_model_evaluation(
            db_path,
            invoice_id,
            correlation_id=f"corr-batch-{index}",
            model_name="invoice-review",
            model_version="7",
            run_id=f"run-{index}",
            manual_review_probability=0.8,
            recommendation=recommend_from_probability(0.8),
        )
    persist_evidence_records(db_path, [_record(1), _record(2), _record(3)])

    batch = create_evidence_batch(db_path, [3, 1, 2])
    reordered_batch = create_evidence_batch(db_path, [2, 3, 1])
    reread = get_evidence_batch(db_path, batch.id)

    assert [item.evaluation_id for item in batch.items] == [1, 2, 3]
    assert [item.leaf_index for item in batch.items] == [0, 1, 2]
    assert reread == batch
    assert reordered_batch.root_hash == batch.root_hash
    assert all(verify_merkle_proof(item.leaf_hash, item.proof, batch.root_hash) for item in batch.items)


def test_evidence_batch_rejects_structurally_invalid_persisted_proof(tmp_path) -> None:
    import pytest

    from invoiceops.evidence import (
        EvidencePersistenceError,
        create_evidence_batch,
        get_evidence_batch,
    )

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-batch-invalid-proof",
        model_name="invoice-review",
        model_version="7",
        run_id="run-1",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )
    persist_evidence_records(db_path, [_record(1)])
    batch = create_evidence_batch(db_path, [1])

    with _connect(db_path) as connection:
        connection.execute(
            "UPDATE evidence_batch_items SET proof_json = ? WHERE batch_id = ?",
            (json.dumps([["left"]]), batch.id),
        )

    with pytest.raises(EvidencePersistenceError, match="stored evidence batch item is invalid"):
        get_evidence_batch(db_path, batch.id)


def test_evidence_batch_rejects_invalid_selection_without_persisting_partial_batch(tmp_path) -> None:
    import pytest

    from invoiceops.evidence import EvidenceError, EvidencePersistenceError, create_evidence_batch

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-batch-invalid",
        model_name="invoice-review",
        model_version="7",
        run_id="run-1",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )
    insert_model_evaluation(
        db_path,
        "INV-10024",
        correlation_id="corr-batch-unpersisted",
        model_name="invoice-review",
        model_version="7",
        run_id="run-2",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )
    persist_evidence_records(db_path, [_record(1)])

    with pytest.raises(EvidencePersistenceError, match="unique"):
        create_evidence_batch(db_path, [1, 1])
    with pytest.raises(EvidenceError, match="not found"):
        create_evidence_batch(db_path, [1, 99])
    with pytest.raises(EvidenceError, match="not found"):
        create_evidence_batch(db_path, [1, 2])
    with _connect(db_path) as connection:
        connection.execute("UPDATE evidence_records SET digest_hex = '0' WHERE evaluation_id = 1")
    with pytest.raises(EvidencePersistenceError, match="not verified"):
        create_evidence_batch(db_path, [1])
    with _connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence_batches").fetchone()[0] == 0


def test_evidence_batch_migration_adds_batch_tables_and_foreign_keys(tmp_path) -> None:
    db_path = tmp_path / "invoiceops.db"

    assert run_migrations(db_path) == 7

    with _connect(db_path) as connection:
        batches = connection.execute("PRAGMA table_info(evidence_batches)").fetchall()
        items = connection.execute("PRAGMA table_info(evidence_batch_items)").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_list(evidence_batch_items)").fetchall()
        indexes = connection.execute("PRAGMA index_list(evidence_batch_items)").fetchall()
    assert [column["name"] for column in batches] == [
        "id",
        "policy_version",
        "root_hash",
        "leaf_count",
        "status",
        "created_at",
    ]
    assert [column["name"] for column in items] == [
        "batch_id",
        "evaluation_id",
        "evidence_contract_version",
        "leaf_index",
        "leaf_hash",
        "proof_json",
    ]
    assert {key["table"] for key in foreign_keys} == {"evidence_batches", "evidence_records"}
    assert "idx_evidence_batch_items_evaluation" in {index["name"] for index in indexes}
