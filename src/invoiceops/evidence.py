"""Build and persist versioned invoice evidence from canonical evaluations."""

import argparse
import json
import os
import sqlite3
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import mlflow
from eth_hash.auto import keccak
from mlflow.exceptions import MlflowException, RestException
from mlflow.tracking import MlflowClient

from invoiceops.legacy.db import (
    _connect,
    get_model_evaluation,
    insert_evidence_batch,
    insert_evidence_records,
    list_model_evaluation_records,
)
from invoiceops.legacy.db import get_evidence_batch as _get_evidence_batch_row
from invoiceops.legacy.db import get_evidence_hash as _get_evidence_hash_row
from invoiceops.legacy.db import get_evidence_record as _get_evidence_row
from invoiceops.legacy.db import list_evidence_batch_items as _list_evidence_batch_item_rows
from invoiceops.legacy.db import list_evidence_records as _list_evidence_rows

EVIDENCE_CONTRACT_VERSION = "invoice-evidence-v1"
CANONICAL_SERIALIZATION_VERSION = "invoice-evidence-canonical-v1"
KECCAK256_ALGORITHM = "keccak-256"
MERKLE_POLICY_VERSION = "invoice-merkle-v1"


class EvidenceError(ValueError):
    """Raised when an evaluation cannot produce a complete evidence record."""


class EvidencePersistenceError(EvidenceError):
    """Raised when evidence records cannot be atomically persisted or decoded."""


@dataclass(frozen=True)
class EvidenceBackfillResult:
    evaluation_ids: list[int]
    dry_run: bool


@dataclass(frozen=True)
class EvidenceBatchItem:
    evaluation_id: int
    leaf_index: int
    leaf_hash: str
    proof: list[tuple[str, str]]


@dataclass(frozen=True)
class EvidenceBatch:
    id: int
    policy_version: str
    root_hash: str
    leaf_count: int
    status: str
    items: list[EvidenceBatchItem]


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        raise EvidenceError("canonical serialization does not allow floats")
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise EvidenceError("canonical serialization requires string keys")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    raise EvidenceError(f"canonical serialization does not allow {type(value).__name__}")


@dataclass(frozen=True)
class EvidenceCandidate:
    evaluation_id: int
    usable: bool
    cause: str | None = None


@dataclass(frozen=True)
class EvidenceProvenance:
    dataset_version: str
    feature_schema_version: str
    git_commit: str


@dataclass(frozen=True)
class EvidenceRecord:
    evaluation_id: int
    invoice_id: str
    correlation_id: str
    model_name: str
    model_version: str
    run_id: str
    manual_review_probability: str
    policy_version: str
    policy_threshold: str
    recommendation: str
    source: str
    reason: str
    evaluation_created_at: str
    provenance: EvidenceProvenance
    contract_version: str = EVIDENCE_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonicalize_evidence_record(record: EvidenceRecord | dict[str, object]) -> bytes:
    """Return the versioned UTF-8 JSON payload used by the evidence digest."""
    evidence = record.to_dict() if isinstance(record, EvidenceRecord) else record
    payload = {
        "canonical_version": CANONICAL_SERIALIZATION_VERSION,
        "evidence": _canonical_value(evidence),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def keccak256_hex(payload: bytes) -> str:
    """Return a lowercase Ethereum Keccak-256 hexadecimal digest without a 0x prefix."""
    return keccak(payload).hex()


def _merkle_leaf_bytes(leaf_hash: str) -> bytes:
    if not isinstance(leaf_hash, str) or len(leaf_hash) != 64:
        raise EvidenceError("Merkle leaves must be 32-byte hexadecimal digests")
    try:
        leaf = bytes.fromhex(leaf_hash)
    except ValueError as error:
        raise EvidenceError("Merkle leaves must be 32-byte hexadecimal digests") from error
    if leaf.hex() != leaf_hash:
        raise EvidenceError("Merkle leaves must be lowercase hexadecimal digests")
    return leaf


def _merkle_levels(leaves: list[str], *, sort_leaves: bool = True) -> list[list[str]]:
    if not leaves:
        raise EvidenceError("at least one Merkle leaf is required")
    levels = [sorted(leaves) if sort_leaves else leaves]
    for leaf in levels[0]:
        _merkle_leaf_bytes(leaf)
    while len(levels[-1]) > 1:
        current = levels[-1]
        if len(current) % 2:
            current = [*current, current[-1]]
        levels.append(
            [
                keccak256_hex(_merkle_leaf_bytes(current[index]) + _merkle_leaf_bytes(current[index + 1]))
                for index in range(0, len(current), 2)
            ]
        )
    return levels


def merkle_root(leaves: list[str]) -> str:
    """Return the invoice-merkle-v1 root for digest leaves, sorted deterministically."""
    return _merkle_levels(leaves)[-1][0]


def _merkle_proof(
    leaves: list[str], leaf_hash: str, *, sort_leaves: bool = True
) -> list[tuple[str, str]]:
    levels = _merkle_levels(leaves, sort_leaves=sort_leaves)
    try:
        index = levels[0].index(leaf_hash)
    except ValueError as error:
        raise EvidenceError("Merkle leaf is not part of the tree") from error
    proof: list[tuple[str, str]] = []
    for level in levels[:-1]:
        extended = [*level, level[-1]] if len(level) % 2 else level
        sibling_index = index - 1 if index % 2 else index + 1
        proof.append(("left" if index % 2 else "right", extended[sibling_index]))
        index //= 2
    return proof


def merkle_proof(leaves: list[str], leaf_hash: str) -> list[tuple[str, str]]:
    return _merkle_proof(leaves, leaf_hash)


def _ordered_merkle_root(leaves: list[str]) -> str:
    return _merkle_levels(leaves, sort_leaves=False)[-1][0]


def _ordered_merkle_proof(leaves: list[str], leaf_hash: str) -> list[tuple[str, str]]:
    return _merkle_proof(leaves, leaf_hash, sort_leaves=False)


def verify_merkle_proof(
    leaf_hash: str, proof: list[tuple[str, str]], root_hash: str
) -> bool:
    try:
        current = _merkle_leaf_bytes(leaf_hash)
        for orientation, sibling_hash in proof:
            sibling = _merkle_leaf_bytes(sibling_hash)
            if orientation == "left":
                current = bytes.fromhex(keccak256_hex(sibling + current))
            elif orientation == "right":
                current = bytes.fromhex(keccak256_hex(current + sibling))
            else:
                return False
        return current.hex() == root_hash
    except EvidenceError:
        return False


def evidence_digest(record: EvidenceRecord | dict[str, object]) -> str:
    return keccak256_hex(canonicalize_evidence_record(record))


def compare_evidence_records(expected: EvidenceRecord, candidate: EvidenceRecord) -> bool:
    """Return whether an in-memory candidate differs from the expected evidence digest."""
    return evidence_digest(expected) != evidence_digest(candidate)


def normalize_decimal(value: object) -> str:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("decimal value is invalid") from error
    if not decimal.is_finite():
        raise ValueError("decimal value must be finite")
    normalized = format(decimal.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def normalize_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise EvidenceError("evaluation timestamp is invalid")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise EvidenceError("evaluation timestamp is invalid") from error
    if timestamp.tzinfo is None:
        raise EvidenceError("evaluation timestamp must include a timezone")
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{field} is missing")
    return value


def _run_provenance(run: object) -> EvidenceProvenance:
    data = getattr(run, "data", None)
    params = getattr(data, "params", None)
    tags = getattr(data, "tags", None)
    if not isinstance(params, dict) or not isinstance(tags, dict):
        raise EvidenceError("MLflow run lineage is invalid")
    return EvidenceProvenance(
        dataset_version=_required_text(params.get("dataset_version"), "dataset_version"),
        feature_schema_version=_required_text(
            params.get("feature_schema_version"), "feature_schema_version"
        ),
        git_commit=_required_text(tags.get("git_commit"), "git_commit"),
    )


def _configure_tracking_uri() -> None:
    if tracking_uri := os.getenv("MLFLOW_TRACKING_URI"):
        mlflow.set_tracking_uri(tracking_uri)


def _build_from_row(row: Any, client: MlflowClient) -> EvidenceRecord:
    run_id = _required_text(row["run_id"], "run_id")
    try:
        provenance = _run_provenance(client.get_run(run_id))
    except (AttributeError, KeyError, MlflowException, RestException, TypeError) as error:
        raise EvidenceError(f"MLflow run is unavailable: {run_id}") from error
    return EvidenceRecord(
        evaluation_id=row["id"],
        invoice_id=_required_text(row["invoice_id"], "invoice_id"),
        correlation_id=_required_text(row["correlation_id"], "correlation_id"),
        model_name=_required_text(row["model_name"], "model_name"),
        model_version=_required_text(row["model_version"], "model_version"),
        run_id=run_id,
        manual_review_probability=_normalize_evaluation_decimal(
            row["manual_review_probability"], "manual_review_probability"
        ),
        policy_version=_required_text(row["policy_version"], "policy_version"),
        policy_threshold=_normalize_evaluation_decimal(row["policy_threshold"], "policy_threshold"),
        recommendation=_required_text(row["recommendation"], "recommendation"),
        source=_required_text(row["source"], "source"),
        reason=_required_text(row["reason"], "reason"),
        evaluation_created_at=normalize_timestamp(row["created_at"]),
        provenance=provenance,
    )


def _normalize_evaluation_decimal(value: object, field: str) -> str:
    try:
        return normalize_decimal(value)
    except ValueError as error:
        raise EvidenceError(f"{field} is invalid: {error}") from error


def build_evidence_record(
    db_path: str | Path | None, evaluation_id: int, *, client: MlflowClient | None = None
) -> EvidenceRecord:
    row = get_model_evaluation(db_path, evaluation_id)
    if row is None:
        raise EvidenceError(f"model evaluation not found: {evaluation_id}")
    _configure_tracking_uri()
    return _build_from_row(row, client or MlflowClient())


def build_evidence_records(
    db_path: str | Path | None, evaluation_ids: list[int], *, client: MlflowClient | None = None
) -> list[EvidenceRecord]:
    if not evaluation_ids:
        raise EvidenceError("at least one evaluation_id is required")
    _configure_tracking_uri()
    resolved_client = client or MlflowClient()
    return [
        build_evidence_record(db_path, evaluation_id, client=resolved_client)
        for evaluation_id in evaluation_ids
    ]


def list_evaluation_candidates(
    db_path: str | Path | None, *, client: MlflowClient | None = None
) -> list[EvidenceCandidate]:
    _configure_tracking_uri()
    resolved_client = client or MlflowClient()
    candidates: list[EvidenceCandidate] = []
    for row in list_model_evaluation_records(db_path):
        try:
            _build_from_row(row, resolved_client)
        except (EvidenceError, ValueError) as error:
            candidates.append(EvidenceCandidate(row["id"], False, str(error)))
        else:
            candidates.append(EvidenceCandidate(row["id"], True))
    return candidates


def persist_evidence_records(db_path: str | Path | None, records: list[EvidenceRecord]) -> None:
    if not records:
        raise EvidencePersistenceError("at least one evidence record is required")
    if any(record.contract_version != EVIDENCE_CONTRACT_VERSION for record in records):
        raise EvidencePersistenceError("unsupported evidence contract version")
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    rows = [
        (
            record.evaluation_id,
            record.contract_version,
            json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":")),
            created_at,
            CANONICAL_SERIALIZATION_VERSION,
            canonicalize_evidence_record(record).decode("utf-8"),
            KECCAK256_ALGORITHM,
            evidence_digest(record),
        )
        for record in records
    ]
    try:
        insert_evidence_records(db_path, rows)
    except sqlite3.IntegrityError as error:
        raise EvidencePersistenceError(
            "evidence record already exists or evaluation is invalid"
        ) from error


def _record_from_row(row: Any) -> EvidenceRecord:
    try:
        payload = json.loads(row["evidence_json"])
        provenance = EvidenceProvenance(**payload.pop("provenance"))
        return EvidenceRecord(provenance=provenance, **payload)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise EvidencePersistenceError("stored evidence record is invalid") from error


def get_evidence_record(db_path: str | Path | None, evaluation_id: int) -> EvidenceRecord | None:
    row = _get_evidence_row(db_path, evaluation_id, EVIDENCE_CONTRACT_VERSION)
    return _record_from_row(row) if row is not None else None


def list_evidence_records(db_path: str | Path | None) -> list[EvidenceRecord]:
    return [
        _record_from_row(row) for row in _list_evidence_rows(db_path, EVIDENCE_CONTRACT_VERSION)
    ]


def verify_persisted_evidence_record(db_path: str | Path | None, evaluation_id: int) -> bool:
    row = _get_evidence_row(db_path, evaluation_id, EVIDENCE_CONTRACT_VERSION)
    if row is None:
        raise EvidenceError(f"evidence record not found: {evaluation_id}")
    if row["canonical_version"] != CANONICAL_SERIALIZATION_VERSION:
        raise EvidencePersistenceError("stored canonical version is unsupported")
    if row["digest_algorithm"] != KECCAK256_ALGORITHM:
        raise EvidencePersistenceError("stored digest algorithm is unsupported")
    record = _record_from_row(row)
    canonical_payload = canonicalize_evidence_record(record).decode("utf-8")
    return row["canonical_payload"] == canonical_payload and row["digest_hex"] == evidence_digest(
        record
    )


def backfill_canonical_evidence_records(
    db_path: str | Path | None, *, dry_run: bool = False
) -> EvidenceBackfillResult:
    """Backfill canonical metadata for complete legacy v1 evidence records only."""
    plans: list[tuple[int, int, str, str, str, str]] = []
    with _connect(db_path) as connection:
        if not dry_run:
            connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT id, evaluation_id, contract_version, evidence_json, created_at,
                   canonical_version, canonical_payload, digest_algorithm, digest_hex
            FROM evidence_records
            WHERE contract_version = ?
            ORDER BY evaluation_id
            """,
            (EVIDENCE_CONTRACT_VERSION,),
        ).fetchall()
        for row in rows:
            stored_metadata = (
                row["canonical_version"],
                row["canonical_payload"],
                row["digest_algorithm"],
                row["digest_hex"],
            )
            record = _record_from_row(row)
            if record.contract_version != EVIDENCE_CONTRACT_VERSION:
                raise EvidencePersistenceError("stored evidence contract version is unsupported")
            derived_metadata = (
                CANONICAL_SERIALIZATION_VERSION,
                canonicalize_evidence_record(record).decode("utf-8"),
                KECCAK256_ALGORITHM,
                evidence_digest(record),
            )
            if all(value is None for value in stored_metadata):
                plans.append((row["id"], row["evaluation_id"], *derived_metadata))
            elif any(value is None for value in stored_metadata):
                raise EvidencePersistenceError("stored canonical metadata is incomplete")
            elif stored_metadata[0] != CANONICAL_SERIALIZATION_VERSION:
                raise EvidencePersistenceError("stored canonical version is unsupported")
            elif stored_metadata[2] != KECCAK256_ALGORITHM:
                raise EvidencePersistenceError("stored digest algorithm is unsupported")
            elif stored_metadata != derived_metadata:
                raise EvidencePersistenceError("stored canonical metadata does not verify")

        if not dry_run:
            for record_id, _, canonical_version, canonical_payload, digest_algorithm, digest_hex in plans:
                cursor = connection.execute(
                    """
                    UPDATE evidence_records
                    SET canonical_version = ?, canonical_payload = ?,
                        digest_algorithm = ?, digest_hex = ?
                    WHERE id = ?
                      AND canonical_version IS NULL
                      AND canonical_payload IS NULL
                      AND digest_algorithm IS NULL
                      AND digest_hex IS NULL
                    """,
                    (
                        canonical_version,
                        canonical_payload,
                        digest_algorithm,
                        digest_hex,
                        record_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise EvidencePersistenceError("evidence record changed during backfill")

    result = EvidenceBackfillResult(
        evaluation_ids=[evaluation_id for _, evaluation_id, *_ in plans], dry_run=dry_run
    )
    if not dry_run:
        for evaluation_id in result.evaluation_ids:
            if not verify_persisted_evidence_record(db_path, evaluation_id):
                raise EvidencePersistenceError("backfilled evidence record does not verify")
    return result


def create_evidence_batch(
    db_path: str | Path | None, evaluation_ids: list[int]
) -> EvidenceBatch:
    if not evaluation_ids:
        raise EvidencePersistenceError("at least one evaluation_id is required")
    if any(isinstance(evaluation_id, bool) or not isinstance(evaluation_id, int) for evaluation_id in evaluation_ids):
        raise EvidencePersistenceError("evaluation_ids must be integers")
    if len(set(evaluation_ids)) != len(evaluation_ids):
        raise EvidencePersistenceError("evaluation_ids must be unique")

    items: list[EvidenceBatchItem] = []
    for leaf_index, evaluation_id in enumerate(sorted(evaluation_ids)):
        if not verify_persisted_evidence_record(db_path, evaluation_id):
            raise EvidencePersistenceError(f"evidence record digest is not verified: {evaluation_id}")
        row = _get_evidence_hash_row(db_path, evaluation_id, EVIDENCE_CONTRACT_VERSION)
        if row is None or row["digest_hex"] is None:
            raise EvidencePersistenceError(f"evidence record is not persisted: {evaluation_id}")
        items.append(EvidenceBatchItem(evaluation_id, leaf_index, row["digest_hex"], []))

    leaves = [item.leaf_hash for item in items]
    root_hash = _ordered_merkle_root(leaves)
    items = [
        EvidenceBatchItem(
            item.evaluation_id,
            item.leaf_index,
            item.leaf_hash,
            _ordered_merkle_proof(leaves, item.leaf_hash),
        )
        for item in items
    ]
    try:
        batch_id = insert_evidence_batch(
            db_path,
            policy_version=MERKLE_POLICY_VERSION,
            root_hash=root_hash,
            leaf_count=len(items),
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            items=[
                (
                    item.evaluation_id,
                    EVIDENCE_CONTRACT_VERSION,
                    item.leaf_index,
                    item.leaf_hash,
                    json.dumps(item.proof, separators=(",", ":")),
                )
                for item in items
            ],
        )
    except sqlite3.IntegrityError as error:
        raise EvidencePersistenceError("evidence batch could not be persisted") from error
    return get_evidence_batch(db_path, batch_id)


def get_evidence_batch(db_path: str | Path | None, batch_id: int) -> EvidenceBatch:
    batch_row = _get_evidence_batch_row(db_path, batch_id)
    if batch_row is None:
        raise EvidenceError(f"evidence batch not found: {batch_id}")
    try:
        items = []
        for row in _list_evidence_batch_item_rows(db_path, batch_id):
            proof = json.loads(row["proof_json"])
            if not isinstance(proof, list) or any(
                not isinstance(step, list)
                or len(step) != 2
                or not all(isinstance(value, str) for value in step)
                for step in proof
            ):
                raise ValueError("stored Merkle proof is invalid")
            items.append(
                EvidenceBatchItem(
                    row["evaluation_id"],
                    row["leaf_index"],
                    row["leaf_hash"],
                    [tuple(step) for step in proof],
                )
            )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise EvidencePersistenceError("stored evidence batch item is invalid") from error
    batch = EvidenceBatch(
        batch_row["id"],
        batch_row["policy_version"],
        batch_row["root_hash"],
        batch_row["leaf_count"],
        batch_row["status"],
        items,
    )
    if (
        batch.policy_version != MERKLE_POLICY_VERSION
        or batch.status != "verified"
        or batch.leaf_count != len(items)
        or _ordered_merkle_root([item.leaf_hash for item in items]) != batch.root_hash
        or any(not verify_merkle_proof(item.leaf_hash, item.proof, batch.root_hash) for item in items)
    ):
        raise EvidencePersistenceError("stored evidence batch is not verified")
    return batch


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and persist InvoiceOps evidence records.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "records"):
        subparsers.add_parser(command).add_argument("--db", required=True, type=Path)
    for command in ("build", "persist"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--db", required=True, type=Path)
        command_parser.add_argument("--evaluation-id", required=True, type=int, action="append")
    batch_parser = subparsers.add_parser("batch")
    batch_parser.add_argument("--db", required=True, type=Path)
    batch_parser.add_argument("--evaluation-id", required=True, type=int, action="append")
    batch_get_parser = subparsers.add_parser("batch-get")
    batch_get_parser.add_argument("--db", required=True, type=Path)
    batch_get_parser.add_argument("--batch-id", required=True, type=int)
    proof_parser = subparsers.add_parser("proof")
    proof_parser.add_argument("--db", required=True, type=Path)
    proof_parser.add_argument("--batch-id", required=True, type=int)
    proof_parser.add_argument("--evaluation-id", required=True, type=int)
    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("--db", required=True, type=Path)
    get_parser.add_argument("--evaluation-id", required=True, type=int)
    for command in ("hash", "verify"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--db", required=True, type=Path)
        command_parser.add_argument("--evaluation-id", required=True, type=int)
    backfill_parser = subparsers.add_parser("backfill")
    backfill_parser.add_argument("--db", required=True, type=Path)
    backfill_parser.add_argument("--dry-run", action="store_true")
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--db", required=True, type=Path)
    compare_parser.add_argument("--evaluation-id", required=True, type=int)
    compare_parser.add_argument(
        "--field",
        required=True,
        choices=[field.name for field in fields(EvidenceRecord) if field.name != "provenance"],
    )
    compare_parser.add_argument("--value", required=True)
    args = parser.parse_args()

    try:
        if args.command == "list":
            result: object = [
                asdict(candidate) for candidate in list_evaluation_candidates(args.db)
            ]
        elif args.command == "records":
            result = [record.to_dict() for record in list_evidence_records(args.db)]
        elif args.command == "get":
            record = get_evidence_record(args.db, args.evaluation_id)
            if record is None:
                raise EvidenceError(f"evidence record not found: {args.evaluation_id}")
            result = record.to_dict()
        elif args.command == "hash":
            record = get_evidence_record(args.db, args.evaluation_id)
            if record is None:
                raise EvidenceError(f"evidence record not found: {args.evaluation_id}")
            result = {
                "algorithm": KECCAK256_ALGORITHM,
                "canonical_version": CANONICAL_SERIALIZATION_VERSION,
                "digest": evidence_digest(record),
                "evaluation_id": record.evaluation_id,
            }
        elif args.command == "verify":
            result = {
                "evaluation_id": args.evaluation_id,
                "verified": verify_persisted_evidence_record(args.db, args.evaluation_id),
            }
        elif args.command == "backfill":
            result = asdict(backfill_canonical_evidence_records(args.db, dry_run=args.dry_run))
        elif args.command == "compare":
            record = get_evidence_record(args.db, args.evaluation_id)
            if record is None:
                raise EvidenceError(f"evidence record not found: {args.evaluation_id}")
            result = {
                "evaluation_id": record.evaluation_id,
                "tampered": compare_evidence_records(
                    record, replace(record, **{args.field: args.value})
                ),
            }
        elif args.command == "batch":
            result = asdict(create_evidence_batch(args.db, args.evaluation_id))
        elif args.command == "batch-get":
            result = asdict(get_evidence_batch(args.db, args.batch_id))
        elif args.command == "proof":
            batch = get_evidence_batch(args.db, args.batch_id)
            item = next(
                (item for item in batch.items if item.evaluation_id == args.evaluation_id), None
            )
            if item is None:
                raise EvidenceError(
                    f"evaluation_id is not part of evidence batch: {args.evaluation_id}"
                )
            result = {
                "batch_id": batch.id,
                "evaluation_id": item.evaluation_id,
                "leaf_hash": item.leaf_hash,
                "proof": item.proof,
                "root_hash": batch.root_hash,
                "verified": verify_merkle_proof(item.leaf_hash, item.proof, batch.root_hash),
            }
        else:
            records = build_evidence_records(args.db, args.evaluation_id)
            if args.command == "persist":
                persist_evidence_records(args.db, records)
            result = [record.to_dict() for record in records]
    except EvidenceError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
