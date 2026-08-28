from invoiceops.legacy.db import _connect, run_migrations


def test_evidence_records_migration_links_one_v1_record_to_each_evaluation(tmp_path) -> None:
    db_path = tmp_path / "invoiceops.db"

    assert run_migrations(db_path) == 5

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
    ]
    assert [(key["table"], key["from"], key["to"]) for key in foreign_keys] == [
        ("model_evaluations", "evaluation_id", "id")
    ]
    assert {index["name"] for index in indexes} == {"idx_evidence_records_evaluation_version"}
