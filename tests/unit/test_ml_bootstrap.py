from pathlib import Path

import pytest


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
