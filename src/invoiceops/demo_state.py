"""Read-only inspection of the canonical InvoiceOps classroom state."""

import importlib.util
import json
import os
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from mlflow.exceptions import MlflowException, RestException
from mlflow.tracking import MlflowClient

from invoiceops.anchor import (
    LOCAL_RPC_URL,
    AnchorConfigurationError,
    AnchorError,
    chain,
    resolve_deployment,
)
from invoiceops.evidence import (
    EvidenceError,
    EvidencePersistenceError,
    get_evidence_batch,
    list_evaluation_candidates,
)
from invoiceops.legacy.db import _resolve_db_path, get_latest_evidence_batch_anchor
from invoiceops.ml.registry import MODEL_NAME

DISPLAY_ID_LIMIT = 20


@dataclass(frozen=True)
class DatabaseState:
    path: str
    status: str
    model_evaluation_count: int
    evaluation_ids: list[int]
    run_ids: list[str]


@dataclass(frozen=True)
class ChampionState:
    model_name: str
    model_version: str
    run_id: str | None


@dataclass(frozen=True)
class MlflowState:
    status: str
    tracking_uri: str | None
    registered_model_names: list[str]
    champion: ChampionState | None


@dataclass(frozen=True)
class EvmToolingState:
    forge: bool
    anvil: bool
    web3: bool
    contracts: bool


@dataclass(frozen=True)
class EvidenceState:
    status: str
    usable_evaluation_ids: list[int]


@dataclass(frozen=True)
class EvmRuntimeState:
    status: str
    rpc_url: str
    chain_id: int | None
    contract_address: str | None
    batch_id: int | None
    anchor_status: str | None


@dataclass(frozen=True)
class DemoState:
    database: DatabaseState
    mlflow: MlflowState
    evm_tooling: EvmToolingState
    evidence: EvidenceState
    evm_runtime: EvmRuntimeState

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _inspect_database(path: Path) -> DatabaseState:
    if not path.is_file():
        return DatabaseState(str(path), "missing", 0, [], [])

    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            total = connection.execute("SELECT COUNT(*) FROM model_evaluations").fetchone()[0]
            rows = connection.execute(
                "SELECT id, run_id FROM model_evaluations ORDER BY id LIMIT ?",
                (DISPLAY_ID_LIMIT,),
            ).fetchall()
    except sqlite3.Error:
        return DatabaseState(str(path), "unavailable", 0, [], [])

    evaluation_ids = [row[0] for row in rows]
    run_ids = list(dict.fromkeys(row[1] for row in rows if row[1]))
    return DatabaseState(str(path), "available", total, evaluation_ids, run_ids)


def _sanitize_tracking_uri(tracking_uri: str) -> str:
    parsed = urlsplit(tracking_uri)
    netloc = parsed.netloc.rsplit("@", maxsplit=1)[-1]
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _inspect_mlflow() -> tuple[MlflowState, MlflowClient | None]:
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        return MlflowState("not_configured", None, [], None), None

    safe_tracking_uri = _sanitize_tracking_uri(tracking_uri)
    try:
        client = MlflowClient(tracking_uri=tracking_uri)
        models = list(client.search_registered_models())
    except (MlflowException, RestException):
        return MlflowState("unavailable", safe_tracking_uri, [], None), None

    model_names = sorted(model.name for model in models)
    if not model_names:
        return MlflowState("empty", safe_tracking_uri, [], None), client

    champion = None
    if MODEL_NAME in model_names:
        try:
            version = client.get_model_version_by_alias(MODEL_NAME, "champion")
        except (MlflowException, RestException):
            pass
        else:
            champion = ChampionState(MODEL_NAME, str(version.version), version.run_id)
    return MlflowState(
        "available", safe_tracking_uri, model_names[:DISPLAY_ID_LIMIT], champion
    ), client


def _inspect_evm_tooling() -> EvmToolingState:
    project_root = Path(__file__).resolve().parents[2]
    return EvmToolingState(
        forge=shutil.which("forge") is not None,
        anvil=shutil.which("anvil") is not None,
        web3=importlib.util.find_spec("web3") is not None,
        contracts=(project_root / "contracts").is_dir(),
    )


def _inspect_evidence(path: Path, client: MlflowClient | None) -> EvidenceState:
    if not path.is_file():
        return EvidenceState("missing_database", [])
    if client is None:
        return EvidenceState("mlflow_not_available", [])
    try:
        candidates = list_evaluation_candidates(path, client=client)
    except (sqlite3.Error, EvidenceError, KeyError, IndexError):
        return EvidenceState("unavailable", [])
    return EvidenceState("available", [item.evaluation_id for item in candidates if item.usable])


def _batch_id_from_environment() -> int | None:
    value = os.getenv("INVOICEOPS_EVIDENCE_BATCH_ID")
    if value is None:
        return None
    try:
        batch_id = int(value)
    except ValueError:
        return None
    return batch_id if batch_id > 0 else None


def _inspect_evm_runtime(path: Path) -> EvmRuntimeState:
    rpc_url = os.getenv("INVOICEOPS_EVM_RPC_URL", LOCAL_RPC_URL)
    batch_id = _batch_id_from_environment()
    anchor_status = None
    if batch_id is not None and path.is_file():
        try:
            batch = get_evidence_batch(path, batch_id)
            anchor = get_latest_evidence_batch_anchor(path, batch.id)
            anchor_status = anchor["status"] if anchor is not None else "missing"
        except (sqlite3.Error, EvidenceError, EvidencePersistenceError):
            anchor_status = "unavailable"
    try:
        deployment = resolve_deployment()
    except AnchorConfigurationError:
        return EvmRuntimeState("not_deployed", rpc_url, None, None, batch_id, anchor_status)
    try:
        chain(rpc_url, expected_chain_id=deployment.chain_id)
    except AnchorError:
        return EvmRuntimeState(
            "rpc_unavailable",
            rpc_url,
            deployment.chain_id,
            deployment.address,
            batch_id,
            anchor_status,
        )
    return EvmRuntimeState(
        "available", rpc_url, deployment.chain_id, deployment.address, batch_id, anchor_status
    )


def inspect_demo_state() -> DemoState:
    """Return the current classroom state without initializing or changing it."""
    db_path = _resolve_db_path(None)
    mlflow, mlflow_client = _inspect_mlflow()
    return DemoState(
        database=_inspect_database(db_path),
        mlflow=mlflow,
        evm_tooling=_inspect_evm_tooling(),
        evidence=_inspect_evidence(db_path, mlflow_client),
        evm_runtime=_inspect_evm_runtime(db_path),
    )


def main() -> None:
    print(json.dumps(inspect_demo_state().to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
