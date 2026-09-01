import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

import mlflow
from mlflow.tracking import MlflowClient

from invoiceops.demo_paths import ensure_demo_root, initialize_demo_root
from invoiceops.legacy.db import run_migrations
from invoiceops.legacy.seed import seed_invoices
from invoiceops.ml.gate import evaluate_run, passes_gate
from invoiceops.ml.registry import ensure_champion, ensure_registered_version
from invoiceops.ml.train import (
    CANONICAL_DATASET_VERSION,
    FEATURE_SCHEMA_VERSION,
    TARGET,
    ensure_canonical_dataset,
    train_model,
)


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class BootstrapResult:
    dataset: str
    migrations_pending: int
    sqlite: str
    candidate: str
    gate: str
    registry: str
    champion: str
    model_api_restart_required: bool


def ensure_tracking_available() -> MlflowClient:
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri or urlparse(tracking_uri).scheme not in {"http", "https"}:
        raise BootstrapError("MLFLOW_TRACKING_URI must point to a running HTTP(S) MLflow server")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient()
    try:
        client.search_experiments(max_results=1)
    except Exception as error:
        raise BootstrapError(f"MLflow tracking is unavailable: {error}") from error
    return client


def find_compatible_random_forest_run(client: MlflowClient, dataset_dir: Path) -> str | None:
    experiment = client.get_experiment_by_name("invoice-risk")
    if experiment is None:
        return None
    metadata = json.loads((dataset_dir / "metadata.json").read_text(encoding="utf-8"))
    dataset_sha256 = hashlib.sha256((dataset_dir / "metadata.json").read_bytes()).hexdigest()
    runs = client.search_runs(
        [experiment.experiment_id],
        filter_string="params.model_type = 'random_forest'",
        order_by=["attributes.start_time DESC"],
    )
    for run in runs:
        if run.info.status != "FINISHED":
            continue
        if (
            run.data.params.get("dataset_version") == CANONICAL_DATASET_VERSION
            and run.data.params.get("feature_schema_version") == FEATURE_SCHEMA_VERSION
            and run.data.tags.get("dataset_sha256") == dataset_sha256
            and run.data.tags.get("target") == TARGET
            and metadata["dataset_version"] == CANONICAL_DATASET_VERSION
        ):
            artifacts = client.list_artifacts(run.info.run_id)
            if any(artifact.path == "pipeline" and artifact.is_dir for artifact in artifacts):
                return run.info.run_id
    return None


def _run_stage(stage: str, operation):
    try:
        return operation()
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(f"{stage}: {error}") from error


def bootstrap_local(db_path: Path | None = None) -> BootstrapResult:
    database = db_path or Path("var/local-demo/invoiceops.db")
    demo_root = ensure_demo_root(database.parent)
    database = demo_root / database.name
    client = ensure_tracking_available()
    dataset_dir, dataset_action = _run_stage(
        "Dataset precondition failed",
        lambda: ensure_canonical_dataset(output_root=demo_root / "data"),
    )
    migrations_pending = _run_stage("SQLite migration failed", lambda: run_migrations(database))
    _run_stage("SQLite seed failed", lambda: seed_invoices(database))

    run_id = _run_stage(
        "MLflow candidate lookup failed", lambda: find_compatible_random_forest_run(client, dataset_dir)
    )
    candidate_action = "reused" if run_id else "created"
    if run_id is None:
        run_id, _ = _run_stage(
            "Candidate training failed", lambda: train_model("random_forest", dataset_dir=dataset_dir)
        )

    recall, precision = _run_stage("Gate evaluation failed", lambda: evaluate_run(run_id))
    if not passes_gate(recall, precision):
        raise BootstrapError(
            f"Candidate {run_id} failed Gate: recall={recall:.6f}, precision={precision:.6f}; not promoted"
        )

    version, registry_action = _run_stage("Registry registration failed", lambda: ensure_registered_version(run_id))
    champion_action = _run_stage("Champion promotion failed", lambda: ensure_champion(version, run_id))
    return BootstrapResult(
        dataset=dataset_action,
        migrations_pending=migrations_pending,
        sqlite="migrated_and_seeded",
        candidate=f"{candidate_action}:{run_id}",
        gate=f"passed:recall={recall:.6f},precision={precision:.6f}",
        registry=f"{registry_action}:{version}",
        champion=champion_action,
        model_api_restart_required=champion_action != "unchanged",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap the local SQLite and MLflow demo state.")
    parser.add_argument("--db-path", type=Path)
    parser.add_argument(
        "--initialize-demo-root",
        action="store_true",
        help="create only the ownership marker required before a confirmed local reset",
    )
    args = parser.parse_args()
    if args.initialize_demo_root:
        if args.db_path is not None:
            root = args.db_path.parent
        else:
            root = Path("var/local-demo")
        print(f"Initialized local demo root: {initialize_demo_root(root)}")
        return
    try:
        result = bootstrap_local(args.db_path)
    except BootstrapError as error:
        raise SystemExit(f"BOOTSTRAP FAILED: {error}") from error
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    print(
        "Model API restart: required before serving the promoted champion."
        if result.model_api_restart_required
        else "Model API restart: not required; champion already targets the approved run."
    )


if __name__ == "__main__":
    main()
