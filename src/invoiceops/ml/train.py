import argparse
import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

import mlflow
import mlflow.sklearn
import pandas as pd
from mlflow.data.sources import LocalArtifactDatasetSource
from sklearn.pipeline import Pipeline

from invoiceops.ml.data import SPLIT_FILENAMES, TARGET, generate_synthetic_dataset
from invoiceops.ml.features import FEATURE_SCHEMA_VERSION, MODEL_FEATURES
from invoiceops.ml.metrics import evaluate_binary_classifier
from invoiceops.ml.pipelines import (
    build_dummy_pipeline,
    build_logistic_pipeline,
    build_random_forest_pipeline,
)

CANONICAL_DATASET_VERSION = "invoice-risk-v1"
CANONICAL_DATASET_SEED = 20260826
CANONICAL_DATASET_ROWS = 12_000
MODEL_BUILDERS: dict[str, Callable[[], Pipeline]] = {
    "dummy": build_dummy_pipeline,
    "logistic": build_logistic_pipeline,
    "random_forest": build_random_forest_pipeline,
}


def ensure_canonical_dataset(*, output_root: Path = Path("data")) -> tuple[Path, str]:
    """Create the canonical dataset once, or verify the existing lineage."""
    path = output_root / CANONICAL_DATASET_VERSION
    if not path.exists():
        return (
            generate_synthetic_dataset(
                seed=CANONICAL_DATASET_SEED,
                rows=CANONICAL_DATASET_ROWS,
                version=CANONICAL_DATASET_VERSION,
                output_root=output_root,
            ),
            "created",
        )

    metadata_path = path / "metadata.json"
    if not metadata_path.is_file() or not all((path / filename).is_file() for filename in SPLIT_FILENAMES):
        raise ValueError("Canonical dataset is incomplete; restore it or use a clean data directory")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    expected = {
        "dataset_version": CANONICAL_DATASET_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "rows": CANONICAL_DATASET_ROWS,
        "seed": CANONICAL_DATASET_SEED,
        "target": TARGET,
    }
    if any(metadata.get(name) != value for name, value in expected.items()):
        raise ValueError("Canonical dataset metadata is incompatible")
    hashes = metadata.get("split_sha256", {})
    if any(
        hashes.get(filename) != hashlib.sha256((path / filename).read_bytes()).hexdigest()
        for filename in SPLIT_FILENAMES
    ):
        raise ValueError("Canonical dataset contents do not match its metadata")
    return path, "reused"


def _dataset_dir() -> Path:
    return ensure_canonical_dataset()[0]


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unknown"


def _model_params(pipeline: Pipeline) -> dict[str, str]:
    params = pipeline.named_steps["classifier"].get_params(deep=True)
    return {
        name: str(value)
        for name, value in params.items()
        if value is None or isinstance(value, str | int | float | bool)
    }


def _track_run(
    model_type: str,
    pipeline: Pipeline,
    dataset_dir: Path,
    metrics: dict[str, float],
    train: pd.DataFrame,
    validation: pd.DataFrame,
) -> str:
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("invoice-risk")

    dataset_metadata = json.loads((dataset_dir / "metadata.json").read_text(encoding="utf-8"))
    params = _model_params(pipeline) | {
        "model_type": model_type,
        "dataset_version": dataset_metadata["dataset_version"],
        "feature_schema_version": dataset_metadata["feature_schema_version"],
    }
    tags = {
        "dataset_sha256": hashlib.sha256((dataset_dir / "metadata.json").read_bytes()).hexdigest(),
        "git_commit": _git_commit(),
        "target": TARGET,
    }

    with TemporaryDirectory() as temporary_directory:
        artifacts_dir = Path(temporary_directory)
        (artifacts_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (artifacts_dir / "feature_schema.json").write_text(
            json.dumps(
                {
                    "feature_schema_version": FEATURE_SCHEMA_VERSION,
                    "features": MODEL_FEATURES,
                    "target": TARGET,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        mlflow.sklearn.save_model(pipeline, artifacts_dir / "pipeline")

        with mlflow.start_run(run_name=f"{model_type}-{dataset_metadata['dataset_version']}") as run:
            mlflow.log_input(
                mlflow.data.from_pandas(
                    train[MODEL_FEATURES + [TARGET]],
                    source=LocalArtifactDatasetSource(
                        (dataset_dir / "train.csv").resolve().as_uri()
                    ),
                    targets=TARGET,
                    name="training",
                ),
                context="training",
            )
            mlflow.log_input(
                mlflow.data.from_pandas(
                    validation[MODEL_FEATURES + [TARGET]],
                    source=LocalArtifactDatasetSource(
                        (dataset_dir / "validation.csv").resolve().as_uri()
                    ),
                    targets=TARGET,
                    name="validation",
                ),
                context="validation",
            )
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            mlflow.set_tags(tags)
            mlflow.log_artifact(artifacts_dir / "metrics.json")
            mlflow.log_artifact(artifacts_dir / "feature_schema.json")
            mlflow.log_artifacts(artifacts_dir / "pipeline", artifact_path="pipeline")
            return run.info.run_id


def train_model(model_type: str, *, dataset_dir: Path | None = None) -> tuple[str, dict[str, float]]:
    if model_type not in MODEL_BUILDERS:
        raise ValueError(f"Unsupported model type: {model_type}")
    dataset_dir = dataset_dir or _dataset_dir()
    train = pd.read_csv(dataset_dir / "train.csv")
    validation = pd.read_csv(dataset_dir / "validation.csv")
    pipeline = MODEL_BUILDERS[model_type]()
    pipeline.fit(train[MODEL_FEATURES], train[TARGET])

    predictions = pipeline.predict(validation[MODEL_FEATURES])
    probabilities = pipeline.predict_proba(validation[MODEL_FEATURES])[:, 1]
    metrics = evaluate_binary_classifier(validation[TARGET], predictions, probabilities)
    return _track_run(model_type, pipeline, dataset_dir, metrics, train, validation), metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and validate an invoice-risk model.")
    parser.add_argument("--model", choices=MODEL_BUILDERS, required=True)
    args = parser.parse_args()

    _, metrics = train_model(args.model)

    print(f"model: {args.model}")
    for name, value in metrics.items():
        print(f"{name}: {value}")


if __name__ == "__main__":
    main()
