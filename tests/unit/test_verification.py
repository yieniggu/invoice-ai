import json
import sqlite3
import sys
from dataclasses import replace
from types import SimpleNamespace

from invoiceops.domain.policy import recommend_from_probability
from invoiceops.legacy.db import insert_evidence_batch_anchor, insert_model_evaluation
from invoiceops.legacy.seed import seed_invoices

ROOT_ADDRESS = "0x1234567890123456789012345678901234567890"


def _persisted_batch(tmp_path):
    from invoiceops.evidence import (
        EvidenceProvenance,
        EvidenceRecord,
        create_evidence_batch,
        persist_evidence_records,
    )

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-verification",
        model_name="invoice-review",
        model_version="7",
        run_id="run-verification",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )
    persist_evidence_records(
        db_path,
        [
            EvidenceRecord(
                evaluation_id=1,
                invoice_id="INV-10023",
                correlation_id="corr-verification",
                model_name="invoice-review",
                model_version="7",
                run_id="run-verification",
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
        ],
    )
    return db_path, create_evidence_batch(db_path, [1])


def _anchor(db_path, batch) -> None:
    insert_evidence_batch_anchor(
        db_path,
        batch_id=batch.id,
        root_hash=batch.root_hash,
        chain_id=31337,
        contract_address=ROOT_ADDRESS,
        transaction_hash="a" * 64,
        submitted_at="2026-01-01T00:00:00Z",
        status="verified",
    )


def test_verify_evidence_batch_rechecks_persisted_sources_and_on_chain_root(
    monkeypatch, tmp_path
) -> None:
    from invoiceops import verification

    db_path, batch = _persisted_batch(tmp_path)
    _anchor(db_path, batch)
    calls: list[tuple[object, ...]] = []
    web3 = SimpleNamespace()
    monkeypatch.setattr(
        verification,
        "chain",
        lambda rpc_url, expected_chain_id: calls.append((rpc_url, expected_chain_id)) or web3,
    )
    monkeypatch.setattr(
        verification,
        "is_root_registered",
        lambda received_web3, address, root_hash: (
            calls.append((received_web3, address, root_hash)) or True
        ),
    )

    result = verification.verify_evidence_batch(db_path, batch.id, 1)

    assert result.canonical_hash_valid is True
    assert result.evidence_leaf_valid is True
    assert result.proof_valid is True
    assert result.batch_valid is True
    assert result.anchor_persisted is True
    assert result.root_on_chain is True
    assert result.valid is True
    assert calls == [
        (verification.LOCAL_RPC_URL, 31337),
        (web3, ROOT_ADDRESS, batch.root_hash),
    ]


def test_verify_evidence_batch_detects_tampering_without_writing(monkeypatch, tmp_path) -> None:
    from invoiceops import verification

    db_path, batch = _persisted_batch(tmp_path)
    _anchor(db_path, batch)
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE evidence_records SET digest_hex = ?", ("b" * 64,))
    before = db_path.read_bytes()
    monkeypatch.setattr(verification, "chain", lambda rpc_url, expected_chain_id: SimpleNamespace())
    monkeypatch.setattr(verification, "is_root_registered", lambda web3, address, root_hash: True)

    result = verification.verify_evidence_batch(db_path, batch.id, 1)

    assert result.canonical_hash_valid is False
    assert result.proof_valid is True
    assert result.valid is False
    assert db_path.read_bytes() == before


def test_verify_evidence_batch_rejects_a_coherently_rewritten_evidence_record_without_writing(
    monkeypatch, tmp_path
) -> None:
    from invoiceops import verification
    from invoiceops.evidence import (
        canonicalize_evidence_record,
        evidence_digest,
        get_evidence_record,
    )

    db_path, batch = _persisted_batch(tmp_path)
    _anchor(db_path, batch)
    record = get_evidence_record(db_path, 1)
    assert record is not None
    rewritten_record = replace(record, reason="coherently-rewritten")
    rewritten_digest = evidence_digest(rewritten_record)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE evidence_records
            SET evidence_json = ?, canonical_payload = ?, digest_hex = ?
            WHERE evaluation_id = ?
            """,
            (
                json.dumps(rewritten_record.to_dict()),
                canonicalize_evidence_record(rewritten_record).decode("utf-8"),
                rewritten_digest,
                1,
            ),
        )
    before = db_path.read_bytes()
    monkeypatch.setattr(verification, "chain", lambda rpc_url, expected_chain_id: SimpleNamespace())
    monkeypatch.setattr(verification, "is_root_registered", lambda web3, address, root_hash: True)

    result = verification.verify_evidence_batch(db_path, batch.id, 1)

    assert rewritten_digest != batch.items[0].leaf_hash
    assert result.canonical_hash_valid is True
    assert result.evidence_leaf_valid is False
    assert result.proof_valid is True
    assert result.batch_valid is True
    assert result.anchor_persisted is True
    assert result.root_on_chain is True
    assert result.valid is False
    assert db_path.read_bytes() == before


def test_verify_evidence_batch_rejects_an_invalid_persisted_proof(tmp_path) -> None:
    from invoiceops import verification

    db_path, batch = _persisted_batch(tmp_path)
    _anchor(db_path, batch)
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE evidence_batch_items SET proof_json = '[[]]'")

    result = verification.verify_evidence_batch(db_path, batch.id, 1)

    assert result.batch_valid is False
    assert result.proof_valid is False
    assert result.anchor_persisted is False
    assert result.root_on_chain is False
    assert result.valid is False


def test_verify_evidence_batch_requires_a_verified_persisted_anchor(tmp_path) -> None:
    from invoiceops import verification

    db_path, batch = _persisted_batch(tmp_path)

    result = verification.verify_evidence_batch(db_path, batch.id, 1)

    assert result.canonical_hash_valid is True
    assert result.proof_valid is True
    assert result.batch_valid is True
    assert result.anchor_persisted is False
    assert result.root_on_chain is False
    assert result.valid is False


def test_verify_evidence_batch_returns_invalid_for_missing_persisted_sources(tmp_path) -> None:
    from invoiceops import verification

    db_path = tmp_path / "missing.db"
    result = verification.verify_evidence_batch(db_path, 1, 1)

    assert result.canonical_hash_valid is False
    assert result.proof_valid is False
    assert result.batch_valid is False
    assert result.anchor_persisted is False
    assert result.root_on_chain is False
    assert result.valid is False
    assert not db_path.exists()


def test_verify_evidence_batch_rejects_a_root_missing_on_chain(monkeypatch, tmp_path) -> None:
    from invoiceops import verification

    db_path, batch = _persisted_batch(tmp_path)
    _anchor(db_path, batch)
    monkeypatch.setattr(verification, "chain", lambda rpc_url, expected_chain_id: SimpleNamespace())
    monkeypatch.setattr(verification, "is_root_registered", lambda web3, address, root_hash: False)

    result = verification.verify_evidence_batch(db_path, batch.id, 1)

    assert result.anchor_persisted is True
    assert result.root_on_chain is False
    assert result.valid is False


def test_verify_evidence_batch_treats_rpc_failure_as_invalid_without_writing(
    monkeypatch, tmp_path
) -> None:
    from invoiceops import verification
    from invoiceops.anchor import AnchorRpcError

    db_path, batch = _persisted_batch(tmp_path)
    _anchor(db_path, batch)
    before = db_path.read_bytes()
    monkeypatch.setattr(
        verification,
        "chain",
        lambda rpc_url, expected_chain_id: (_ for _ in ()).throw(AnchorRpcError("offline")),
    )

    result = verification.verify_evidence_batch(db_path, batch.id, 1)

    assert result.anchor_persisted is True
    assert result.root_on_chain is False
    assert result.valid is False
    assert db_path.read_bytes() == before


def test_verification_cli_reuses_the_python_api(monkeypatch, capsys, tmp_path) -> None:
    from invoiceops import verification

    expected = verification.EvidenceBatchVerification(1, 2, True, True, True, True, True, True)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        verification,
        "verify_evidence_batch",
        lambda db_path, batch_id, evaluation_id, rpc_url: (
            calls.append((db_path, batch_id, evaluation_id, rpc_url)) or expected
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verification",
            "--db",
            str(tmp_path / "invoiceops.db"),
            "--batch-id",
            "2",
            "--evaluation-id",
            "1",
            "--rpc-url",
            "http://rpc.test",
        ],
    )

    verification.main()

    assert calls == [(tmp_path / "invoiceops.db", 2, 1, "http://rpc.test")]
    assert json.loads(capsys.readouterr().out) == expected.to_dict()
