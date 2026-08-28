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

from invoiceops.legacy.db import _resolve_db_path
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
class DemoState:
    database: DatabaseState
    mlflow: MlflowState
    evm_tooling: EvmToolingState

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


def _inspect_mlflow() -> MlflowState:
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        return MlflowState("not_configured", None, [], None)

    safe_tracking_uri = _sanitize_tracking_uri(tracking_uri)
    try:
        client = MlflowClient(tracking_uri=tracking_uri)
        models = list(client.search_registered_models())
    except (MlflowException, RestException):
        return MlflowState("unavailable", safe_tracking_uri, [], None)

    model_names = sorted(model.name for model in models)
    if not model_names:
        return MlflowState("empty", safe_tracking_uri, [], None)

    champion = None
    if MODEL_NAME in model_names:
        try:
            version = client.get_model_version_by_alias(MODEL_NAME, "champion")
        except (MlflowException, RestException):
            pass
        else:
            champion = ChampionState(MODEL_NAME, str(version.version), version.run_id)
    return MlflowState("available", safe_tracking_uri, model_names[:DISPLAY_ID_LIMIT], champion)


def _inspect_evm_tooling() -> EvmToolingState:
    project_root = Path(__file__).resolve().parents[2]
    return EvmToolingState(
        forge=shutil.which("forge") is not None,
        anvil=shutil.which("anvil") is not None,
        web3=importlib.util.find_spec("web3") is not None,
        contracts=(project_root / "contracts").is_dir(),
    )


def inspect_demo_state() -> DemoState:
    """Return the current classroom state without initializing or changing it."""
    return DemoState(
        database=_inspect_database(_resolve_db_path(None)),
        mlflow=_inspect_mlflow(),
        evm_tooling=_inspect_evm_tooling(),
    )


def main() -> None:
    print(json.dumps(inspect_demo_state().to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
