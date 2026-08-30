"""Verify persisted evidence, its Merkle batch, and its local EVM anchor without writes."""

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from invoiceops.anchor import LOCAL_RPC_URL, AnchorError, chain, is_root_registered
from invoiceops.evidence import (
    EvidenceError,
    EvidencePersistenceError,
    evidence_digest,
    get_evidence_batch,
    get_evidence_record,
    verify_merkle_proof,
    verify_persisted_evidence_record,
)
from invoiceops.legacy.db import get_latest_evidence_batch_anchor


@dataclass(frozen=True)
class EvidenceBatchVerification:
    evaluation_id: int
    batch_id: int
    canonical_hash_valid: bool
    proof_valid: bool
    batch_valid: bool
    anchor_persisted: bool
    root_on_chain: bool
    valid: bool
    evidence_leaf_valid: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def verify_evidence_batch(
    db_path: str | Path,
    batch_id: int,
    evaluation_id: int,
    *,
    rpc_url: str = LOCAL_RPC_URL,
) -> EvidenceBatchVerification:
    """Recheck only persisted evidence and query its recorded EVM anchor read-only."""
    canonical_hash_valid = False
    evidence_leaf_valid = False
    proof_valid = False
    batch_valid = False
    anchor_persisted = False
    root_on_chain = False
    batch = None

    if not Path(db_path).is_file():
        return EvidenceBatchVerification(
            evaluation_id,
            batch_id,
            canonical_hash_valid,
            proof_valid,
            batch_valid,
            anchor_persisted,
            root_on_chain,
            False,
        )

    try:
        canonical_hash_valid = verify_persisted_evidence_record(db_path, evaluation_id)
        batch = get_evidence_batch(db_path, batch_id)
    except (sqlite3.Error, EvidenceError, EvidencePersistenceError):
        pass
    else:
        batch_valid = True
        item = next((item for item in batch.items if item.evaluation_id == evaluation_id), None)
        if item is not None:
            record = get_evidence_record(db_path, evaluation_id)
            evidence_leaf_valid = record is not None and evidence_digest(record) == item.leaf_hash
            proof_valid = verify_merkle_proof(item.leaf_hash, item.proof, batch.root_hash)

    if batch is not None:
        try:
            anchor = get_latest_evidence_batch_anchor(db_path, batch.id)
        except sqlite3.Error:
            anchor = None
        if (
            anchor is not None
            and anchor["status"] == "verified"
            and anchor["root_hash"] == batch.root_hash
        ):
            anchor_persisted = True
            try:
                web3 = chain(rpc_url, expected_chain_id=anchor["chain_id"])
                root_on_chain = is_root_registered(
                    web3, anchor["contract_address"], batch.root_hash
                )
            except (AnchorError, ValueError):
                pass

    valid = all(
        (
            canonical_hash_valid,
            evidence_leaf_valid,
            proof_valid,
            batch_valid,
            anchor_persisted,
            root_on_chain,
        )
    )
    return EvidenceBatchVerification(
        evaluation_id=evaluation_id,
        batch_id=batch_id,
        canonical_hash_valid=canonical_hash_valid,
        proof_valid=proof_valid,
        batch_valid=batch_valid,
        anchor_persisted=anchor_persisted,
        root_on_chain=root_on_chain,
        valid=valid,
        evidence_leaf_valid=evidence_leaf_valid,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify persisted InvoiceOps evidence end to end.")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--batch-id", required=True, type=int)
    parser.add_argument("--evaluation-id", required=True, type=int)
    parser.add_argument("--rpc-url", default=LOCAL_RPC_URL)
    args = parser.parse_args()
    result = verify_evidence_batch(args.db, args.batch_id, args.evaluation_id, rpc_url=args.rpc_url)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
