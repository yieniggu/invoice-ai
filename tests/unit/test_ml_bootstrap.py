import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from invoiceops.ml.data import TARGET
from invoiceops.ml.features import FEATURE_SCHEMA_VERSION


def _matching_dataset(tmp_path: Path) -> Path:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "metadata.json").write_text('{"dataset_version": "invoice-risk-v1"}\n')
    return dataset_dir


class _MlflowClient:
    def __init__(self, runs: list[SimpleNamespace]) -> None:
        self.runs = runs

    def get_experiment_by_name(self, name: str) -> SimpleNamespace:
        assert name == "invoice-risk"
        return SimpleNamespace(experiment_id="1")

    def search_runs(self, experiment_ids: list[str], **kwargs: object) -> list[SimpleNamespace]:
        assert experiment_ids == ["1"]
        assert kwargs["filter_string"] == "params.model_type = 'random_forest'"
        return self.runs

    def list_artifacts(self, run_id: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(path="pipeline", is_dir=True)]


def _matching_random_forest_run(
    dataset_dir: Path, params: dict[str, str]
) -> SimpleNamespace:
    dataset_sha256 = hashlib.sha256((dataset_dir / "metadata.json").read_bytes()).hexdigest()
    return SimpleNamespace(
        info=SimpleNamespace(run_id="run-compatible", status="FINISHED"),
        data=SimpleNamespace(
            params={
                "model_type": "random_forest",
                "dataset_version": "invoice-risk-v1",
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                **params,
            },
            tags={"dataset_sha256": dataset_sha256, "target": TARGET},
        ),
    )


def test_compatible_random_forest_run_rejects_matching_dataset_with_stale_model_params(
    tmp_path: Path,
) -> None:
    from invoiceops.ml import bootstrap

    dataset_dir = _matching_dataset(tmp_path)
    stale_run = _matching_random_forest_run(
        dataset_dir,
        {
            "n_estimators": "100",
            "max_features": "sqrt",
            "n_jobs": "-1",
            "random_state": "20260826",
        },
    )

    run_id = bootstrap.find_compatible_random_forest_run(_MlflowClient([stale_run]), dataset_dir)

    assert run_id is None


def test_compatible_random_forest_run_reuses_matching_dataset_and_current_model_params(
    tmp_path: Path,
) -> None:
    from invoiceops.ml import bootstrap

    dataset_dir = _matching_dataset(tmp_path)
    current_run = _matching_random_forest_run(
        dataset_dir,
        {
            "n_estimators": "500",
            "max_features": "None",
            "n_jobs": "1",
            "random_state": "20260826",
        },
    )

    run_id = bootstrap.find_compatible_random_forest_run(_MlflowClient([current_run]), dataset_dir)

    assert run_id == "run-compatible"


@pytest.mark.parametrize(
    ("champion_action", "restart_required"),
    [("unchanged", False), ("promoted:none->7", True)],
)
def test_bootstrap_reuses_a_compatible_candidate_and_preserves_or_repairs_its_champion(
    monkeypatch, tmp_path: Path, champion_action: str, restart_required: bool
) -> None:
    from invoiceops import demo_paths
    from invoiceops.ml import bootstrap

    calls: list[str] = []
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.example")
    monkeypatch.setattr(bootstrap, "ensure_tracking_available", lambda: object())
    monkeypatch.setattr(
        bootstrap,
        "ensure_canonical_dataset",
        lambda **_: (tmp_path / "data", "reused"),
    )
    monkeypatch.setattr(bootstrap, "run_migrations", lambda db_path: calls.append("migrate") or 0)
    monkeypatch.setattr(bootstrap, "seed_invoices", lambda db_path: calls.append("seed"))
    monkeypatch.setattr(
        bootstrap,
        "find_compatible_random_forest_run",
        lambda client, dataset_dir: "run-approved",
    )
    monkeypatch.setattr(
        bootstrap,
        "train_model",
        lambda *args, **kwargs: pytest.fail("A compatible run must not be retrained"),
    )
    monkeypatch.setattr(bootstrap, "evaluate_run", lambda run_id: (0.20, 0.50))
    monkeypatch.setattr(
        bootstrap,
        "ensure_registered_version",
        lambda run_id: ("7", "reused"),
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_champion",
        lambda version, run_id: champion_action,
    )
    monkeypatch.setattr(bootstrap, "verify_champion", lambda client: ("7", "run-approved"))

    result = bootstrap.bootstrap_local(tmp_path / "var" / "local-demo" / "invoiceops.db")

    assert calls == ["migrate", "seed"]
    assert result.dataset == "reused"
    assert result.candidate == "reused:run-approved"
    assert result.registry == "reused:7"
    assert result.champion == champion_action
    assert result.model_api_restart_required is restart_required


def test_bootstrap_aborts_before_registry_when_candidate_fails_gate(
    monkeypatch, tmp_path: Path
) -> None:
    from invoiceops import demo_paths
    from invoiceops.ml import bootstrap

    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.example")
    monkeypatch.setattr(bootstrap, "ensure_tracking_available", lambda: object())
    monkeypatch.setattr(
        bootstrap,
        "ensure_canonical_dataset",
        lambda **_: (tmp_path / "data", "reused"),
    )
    monkeypatch.setattr(bootstrap, "run_migrations", lambda db_path: 0)
    monkeypatch.setattr(bootstrap, "seed_invoices", lambda db_path: None)
    monkeypatch.setattr(
        bootstrap,
        "find_compatible_random_forest_run",
        lambda client, dataset_dir: "run-rejected",
    )
    monkeypatch.setattr(bootstrap, "evaluate_run", lambda run_id: (0.17, 0.50))
    monkeypatch.setattr(
        bootstrap,
        "ensure_registered_version",
        lambda run_id: pytest.fail("Registry must not be called after a Gate failure"),
    )

    with pytest.raises(bootstrap.BootstrapError, match="failed Gate.*not promoted"):
        bootstrap.bootstrap_local(tmp_path / "var" / "local-demo" / "invoiceops.db")


def test_bootstrap_reports_dataset_precondition_failure(monkeypatch, tmp_path: Path) -> None:
    from invoiceops import demo_paths
    from invoiceops.ml import bootstrap

    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.example")
    monkeypatch.setattr(bootstrap, "ensure_tracking_available", lambda: object())
    monkeypatch.setattr(
        bootstrap,
        "ensure_canonical_dataset",
        lambda **_: (_ for _ in ()).throw(ValueError("metadata is incompatible")),
    )

    with pytest.raises(bootstrap.BootstrapError, match="Dataset precondition failed: metadata"):
        bootstrap.bootstrap_local(tmp_path / "var" / "local-demo" / "invoiceops.db")


def test_model_only_bootstrap_promotes_and_verifies_the_approved_champion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from invoiceops.ml import bootstrap

    monkeypatch.setattr(bootstrap, "ensure_tracking_available", lambda: object())
    monkeypatch.setattr(
        bootstrap, "ensure_canonical_dataset", lambda **_: (tmp_path / "dataset", "created")
    )
    monkeypatch.setattr(
        bootstrap, "find_compatible_random_forest_run", lambda client, dataset: "run-approved"
    )
    monkeypatch.setattr(bootstrap, "evaluate_run", lambda run_id: (0.20, 0.50))
    monkeypatch.setattr(bootstrap, "ensure_registered_version", lambda run_id: ("7", "created"))
    monkeypatch.setattr(bootstrap, "ensure_champion", lambda version, run_id: "promoted:none->7")
    monkeypatch.setattr(bootstrap, "verify_champion", lambda client: ("7", "run-approved"))

    result = bootstrap.bootstrap_model(tmp_path / "model-bootstrap-data")

    assert result.model_version == "7"
    assert result.run_id == "run-approved"
    assert result.champion == "promoted:none->7"


def test_main_model_only_prints_completion_without_accessing_local_restart_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from invoiceops.ml import bootstrap

    dataset_root = tmp_path / "model-bootstrap-data"
    result = bootstrap.ModelBootstrapResult(
        dataset="reused",
        candidate="reused:run-approved",
        gate="passed:recall=0.200000,precision=0.500000",
        registry="reused:7",
        champion="unchanged",
        model_version="7",
        run_id="run-approved",
    )
    monkeypatch.setattr(bootstrap, "bootstrap_model", lambda root: result)
    monkeypatch.setattr(sys, "argv", ["bootstrap", "--model-only", "--dataset-root", str(dataset_root)])

    bootstrap.main()

    output = capsys.readouterr().out
    assert '"model_version": "7"' in output
    assert output.endswith("Model bootstrap complete; local SQLite state was not modified.\n")
