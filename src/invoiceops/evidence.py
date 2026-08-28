"""Build and persist versioned invoice evidence from canonical evaluations."""

import argparse
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import mlflow
from mlflow.exceptions import MlflowException, RestException
from mlflow.tracking import MlflowClient

from invoiceops.legacy.db import get_evidence_record as _get_evidence_row
from invoiceops.legacy.db import (
    get_model_evaluation,
    insert_evidence_records,
    list_model_evaluation_records,
)
from invoiceops.legacy.db import list_evidence_records as _list_evidence_rows

EVIDENCE_CONTRACT_VERSION = "invoice-evidence-v1"


class EvidenceError(ValueError):
    """Raised when an evaluation cannot produce a complete evidence record."""


class EvidencePersistenceError(EvidenceError):
    """Raised when evidence records cannot be atomically persisted or decoded."""


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and persist InvoiceOps evidence records.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "records"):
        subparsers.add_parser(command).add_argument("--db", required=True, type=Path)
    for command in ("build", "persist"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--db", required=True, type=Path)
        command_parser.add_argument("--evaluation-id", required=True, type=int, action="append")
    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("--db", required=True, type=Path)
    get_parser.add_argument("--evaluation-id", required=True, type=int)
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
