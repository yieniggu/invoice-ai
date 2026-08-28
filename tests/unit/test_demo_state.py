import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from mlflow.exceptions import MlflowException


def _create_evaluation_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE model_evaluations (
                id INTEGER PRIMARY KEY,
                run_id TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO model_evaluations (id, run_id) VALUES (?, ?)",
            [(1, "run-001"), (2, "run-002"), (3, "run-001")],
        )


def test_inspect_demo_state_reads_configured_database_without_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from invoiceops import demo_state

    db_path = tmp_path / "state" / "invoiceops.db"
    db_path.parent.mkdir()
    _create_evaluation_db(db_path)
    monkeypatch.setenv("INVOICEOPS_DB_PATH", str(db_path))
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

    state = demo_state.inspect_demo_state()

    assert state.database.path == str(db_path)
    assert state.database.status == "available"
    assert state.database.model_evaluation_count == 3
    assert state.database.evaluation_ids == [1, 2, 3]
    assert state.database.run_ids == ["run-001", "run-002"]
    assert state.mlflow.status == "not_configured"


def test_inspect_demo_state_does_not_create_a_missing_database_or_parent_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from invoiceops import demo_state

    db_path = tmp_path / "missing" / "invoiceops.db"
    monkeypatch.setenv("INVOICEOPS_DB_PATH", str(db_path))
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

    state = demo_state.inspect_demo_state()

    assert state.database.path == str(db_path)
    assert state.database.status == "missing"
    assert state.database.model_evaluation_count == 0
    assert state.database.evaluation_ids == []
    assert state.database.run_ids == []
    assert not db_path.parent.exists()


def test_inspect_demo_state_reports_mlflow_champion_and_sanitizes_tracking_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from invoiceops import demo_state

    db_path = tmp_path / "invoiceops.db"
    _create_evaluation_db(db_path)
    monkeypatch.setenv("INVOICEOPS_DB_PATH", str(db_path))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://reader:secret@mlflow.test:5000/path?token=x")

    class Client:
        def __init__(self, tracking_uri: str) -> None:
            assert tracking_uri == "http://reader:secret@mlflow.test:5000/path?token=x"

        def search_registered_models(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(name="invoice-review")]

        def get_model_version_by_alias(self, name: str, alias: str) -> SimpleNamespace:
            assert (name, alias) == ("invoice-review", "champion")
            return SimpleNamespace(version="4", run_id="run-champion")

    monkeypatch.setattr(demo_state, "MlflowClient", Client)

    state = demo_state.inspect_demo_state()

    assert state.mlflow.status == "available"
    assert state.mlflow.tracking_uri == "http://mlflow.test:5000/path"
    assert state.mlflow.registered_model_names == ["invoice-review"]
    assert state.mlflow.champion is not None
    assert state.mlflow.champion.model_name == "invoice-review"
    assert state.mlflow.champion.model_version == "4"
    assert state.mlflow.champion.run_id == "run-champion"


def test_inspect_demo_state_treats_missing_champion_as_valid_mlflow_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from invoiceops import demo_state

    db_path = tmp_path / "invoiceops.db"
    _create_evaluation_db(db_path)
    monkeypatch.setenv("INVOICEOPS_DB_PATH", str(db_path))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.test:5000")

    class Client:
        def __init__(self, tracking_uri: str) -> None:
            pass

        def search_registered_models(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(name="invoice-review")]

        def get_model_version_by_alias(self, name: str, alias: str) -> SimpleNamespace:
            raise MlflowException("alias does not exist")

    monkeypatch.setattr(demo_state, "MlflowClient", Client)

    state = demo_state.inspect_demo_state()

    assert state.mlflow.status == "available"
    assert state.mlflow.champion is None


def test_inspect_demo_state_reports_unavailable_or_empty_mlflow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from invoiceops import demo_state

    db_path = tmp_path / "invoiceops.db"
    _create_evaluation_db(db_path)
    monkeypatch.setenv("INVOICEOPS_DB_PATH", str(db_path))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.test:5000")

    class UnavailableClient:
        def __init__(self, tracking_uri: str) -> None:
            pass

        def search_registered_models(self) -> list[SimpleNamespace]:
            raise MlflowException("connection failed")

    monkeypatch.setattr(demo_state, "MlflowClient", UnavailableClient)
    assert demo_state.inspect_demo_state().mlflow.status == "unavailable"

    class EmptyClient:
        def __init__(self, tracking_uri: str) -> None:
            pass

        def search_registered_models(self) -> list[SimpleNamespace]:
            return []

    monkeypatch.setattr(demo_state, "MlflowClient", EmptyClient)
    assert demo_state.inspect_demo_state().mlflow.status == "empty"


def test_main_prints_stable_json(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from invoiceops import demo_state

    monkeypatch.setattr(
        demo_state,
        "inspect_demo_state",
        lambda: demo_state.DemoState(
            database=demo_state.DatabaseState("var/invoiceops.db", "available", 0, [], []),
            mlflow=demo_state.MlflowState("not_configured", None, [], None),
            evm_tooling=demo_state.EvmToolingState(False, False, False, False),
        ),
    )

    demo_state.main()

    assert json.loads(capsys.readouterr().out) == {
        "database": {
            "evaluation_ids": [],
            "model_evaluation_count": 0,
            "path": "var/invoiceops.db",
            "run_ids": [],
            "status": "available",
        },
        "evm_tooling": {"anvil": False, "contracts": False, "forge": False, "web3": False},
        "mlflow": {
            "champion": None,
            "registered_model_names": [],
            "status": "not_configured",
            "tracking_uri": None,
        },
    }
