import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from invoiceops.ml.data import TARGET, generate_synthetic_dataset
from invoiceops.ml.features import MODEL_FEATURES
from invoiceops.ml.metrics import evaluate_binary_classifier
from invoiceops.ml.pipelines import (
    build_dummy_pipeline,
    build_logistic_pipeline,
    build_random_forest_pipeline,
)


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    return generate_synthetic_dataset(
        seed=20260826,
        rows=200,
        version="invoice-risk-v1",
        output_root=tmp_path / "data",
    )


@pytest.fixture
def train_validation_frames(dataset_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    return (
        pd.read_csv(dataset_dir / "train.csv"),
        pd.read_csv(dataset_dir / "validation.csv"),
    )


@pytest.mark.parametrize(
    "builder",
    [build_dummy_pipeline, build_logistic_pipeline, build_random_forest_pipeline],
)
def test_pipelines_train_on_features_and_predict_probabilities(
    builder: object, train_validation_frames: tuple[pd.DataFrame, pd.DataFrame]
) -> None:
    train, validation = train_validation_frames
    pipeline = builder()

    pipeline.fit(train[MODEL_FEATURES], train[TARGET])
    probabilities = pipeline.predict_proba(validation[MODEL_FEATURES])

    assert list(pipeline.named_steps) == ["preprocessor", "classifier"]
    assert probabilities.shape == (len(validation), 2)
    assert ((probabilities >= 0.0) & (probabilities <= 1.0)).all()


def test_logistic_pipeline_scales_numeric_features_and_ignores_unknown_categories(
    train_validation_frames: tuple[pd.DataFrame, pd.DataFrame]
) -> None:
    train, validation = train_validation_frames
    pipeline = build_logistic_pipeline().fit(train[MODEL_FEATURES], train[TARGET])
    validation.loc[validation.index[0], "country_risk"] = "unknown"

    probabilities = pipeline.predict_proba(validation[MODEL_FEATURES])
    preprocessor = pipeline.named_steps["preprocessor"]
    classifier = pipeline.named_steps["classifier"]

    assert probabilities.shape == (len(validation), 2)
    assert preprocessor.named_transformers_["numeric"].__class__.__name__ == "StandardScaler"
    assert hasattr(classifier, "coef_")
    assert preprocessor.get_feature_names_out().size == classifier.coef_.shape[1]


def test_random_forest_pipeline_exposes_estimators_and_feature_importances(
    train_validation_frames: tuple[pd.DataFrame, pd.DataFrame]
) -> None:
    train, validation = train_validation_frames
    pipeline = build_random_forest_pipeline().fit(train[MODEL_FEATURES], train[TARGET])
    classifier = pipeline.named_steps["classifier"]
    validation.loc[validation.index[0], "country_risk"] = "unknown"

    assert classifier.random_state == 20260826
    assert classifier.estimators_
    assert classifier.feature_importances_.size == pipeline.named_steps[
        "preprocessor"
    ].get_feature_names_out().size
    assert pipeline.predict_proba(validation[MODEL_FEATURES]).shape == (len(validation), 2)


def test_metrics_handle_zero_division() -> None:
    metrics = evaluate_binary_classifier([False, False], [False, False], [0.1, 0.2])

    assert metrics == {
        "accuracy": 1.0,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "roc_auc": 0.0,
    }


@pytest.mark.parametrize("model", ["dummy", "logistic", "random_forest"])
def test_train_cli_generates_dataset_and_prints_model_metrics(tmp_path: Path, model: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "invoiceops.ml.train", "--model", model],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    lines = result.stdout.strip().splitlines()
    assert lines[0] == f"model: {model}"
    assert {line.split(": ")[0] for line in lines[1:]} == {
        "accuracy",
        "precision",
        "recall",
        "f1",
        "roc_auc",
    }
    assert (tmp_path / "data" / "invoice-risk-v1" / "test.csv").exists()


def test_train_cli_creates_an_invoice_risk_tracking_run(tmp_path: Path) -> None:
    tracking_dir = tmp_path / "mlruns"
    environment = os.environ | {
        "MLFLOW_ALLOW_FILE_STORE": "true",
        "MLFLOW_TRACKING_URI": tracking_dir.as_uri(),
    }

    subprocess.run(
        [sys.executable, "-m", "invoiceops.ml.train", "--model", "dummy"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert tracking_dir.exists(), "training did not create the configured MLflow tracking store"


def test_train_cli_logs_tracking_metadata_metrics_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracking_dir = tmp_path / "mlruns"
    tracking_uri = tracking_dir.as_uri()
    environment = os.environ | {
        "MLFLOW_ALLOW_FILE_STORE": "true",
        "MLFLOW_TRACKING_URI": tracking_uri,
    }

    subprocess.run(
        [sys.executable, "-m", "invoiceops.ml.train", "--model", "random_forest"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert tracking_dir.exists(), "training did not create the configured MLflow tracking store"

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    experiment = client.get_experiment_by_name("invoice-risk")
    assert experiment is not None
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 1

    run = runs[0]
    assert (run.info.run_name, len(run.inputs.dataset_inputs)) == (
        "random_forest-invoice-risk-v1",
        2,
    )
    dataset_inputs_by_context = {
        {tag.key: tag.value for tag in dataset_input.tags}["mlflow.data.context"]: dataset_input
        for dataset_input in run.inputs.dataset_inputs
    }
    assert set(dataset_inputs_by_context) == {"training", "validation"}
    assert "test" not in dataset_inputs_by_context

    expected_inputs = {"training": ("training", "train.csv"), "validation": ("validation", "validation.csv")}
    for context, (name, source) in expected_inputs.items():
        dataset = dataset_inputs_by_context[context].dataset
        assert dataset.name == name
        assert json.loads(dataset.source)["uri"] == source
        assert {column["name"] for column in json.loads(dataset.schema)["mlflow_colspec"]} == set(
            MODEL_FEATURES + [TARGET]
        )
    assert run.data.params["model_type"] == "random_forest"
    assert run.data.params["dataset_version"] == "invoice-risk-v1"
    assert run.data.params["feature_schema_version"] == "invoice-features-v1"
    assert run.data.params["random_state"] == "20260826"
    assert run.data.params["n_estimators"] == "100"
    assert set(run.data.metrics) == {"accuracy", "precision", "recall", "f1", "roc_auc"}
    assert run.data.tags["dataset_sha256"]
    assert run.data.tags["git_commit"] == "unknown"
    assert run.data.tags["target"] == "manual_review_required"
    assert {artifact.path for artifact in client.list_artifacts(run.info.run_id)} == {
        "feature_schema.json",
        "metrics.json",
        "pipeline",
    }
    metrics_path = client.download_artifacts(run.info.run_id, "metrics.json")
    schema_path = client.download_artifacts(run.info.run_id, "feature_schema.json")
    assert json.loads(Path(metrics_path).read_text(encoding="utf-8")) == run.data.metrics
    assert json.loads(Path(schema_path).read_text(encoding="utf-8")) == {
        "feature_schema_version": "invoice-features-v1",
        "features": MODEL_FEATURES,
        "target": "manual_review_required",
    }
