import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


def test_register_command_registers_run_pipeline_and_assigns_challenger(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    calls: list[tuple[str, object]] = []
    mlflow = ModuleType("mlflow")
    tracking = ModuleType("mlflow.tracking")

    def register_model(model_uri: str, name: str) -> SimpleNamespace:
        calls.append(("register_model", (model_uri, name)))
        return SimpleNamespace(version="7")

    def set_tracking_uri(uri: str) -> None:
        calls.append(("set_tracking_uri", uri))

    class StubMlflowClient:
        def set_registered_model_alias(self, name: str, alias: str, version: str) -> None:
            calls.append(("set_registered_model_alias", (name, alias, version)))

    mlflow.register_model = register_model
    mlflow.set_tracking_uri = set_tracking_uri
    tracking.MlflowClient = StubMlflowClient
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking)
    monkeypatch.delitem(sys.modules, "invoiceops.ml.registry", raising=False)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.example")
    monkeypatch.setattr(sys, "argv", ["registry", "register", "--run-id", "run-123"])

    importlib.import_module("invoiceops.ml.registry").main()

    assert calls == [
        ("set_tracking_uri", "http://mlflow.example"),
        ("register_model", ("runs:/run-123/pipeline", "invoice-review")),
        ("set_registered_model_alias", ("invoice-review", "challenger", "7")),
    ]
    assert capsys.readouterr().out == "registered invoice-review version: 7\n"


def test_promote_command_assigns_champion_to_explicit_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []
    mlflow = ModuleType("mlflow")
    tracking = ModuleType("mlflow.tracking")

    class StubMlflowClient:
        def set_registered_model_alias(self, name: str, alias: str, version: str) -> None:
            calls.append((name, alias, version))

    tracking.MlflowClient = StubMlflowClient
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking)
    monkeypatch.delitem(sys.modules, "invoiceops.ml.registry", raising=False)
    monkeypatch.setattr(sys, "argv", ["registry", "promote", "--version", "11"])

    importlib.import_module("invoiceops.ml.registry").main()

    assert calls == [("invoice-review", "champion", "11")]


def test_register_model_propagates_mlflow_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    mlflow = ModuleType("mlflow")
    tracking = ModuleType("mlflow.tracking")

    def register_model(model_uri: str, name: str) -> None:
        raise RuntimeError("registry unavailable")

    class StubMlflowClient:
        pass

    mlflow.register_model = register_model
    tracking.MlflowClient = StubMlflowClient
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking)
    monkeypatch.delitem(sys.modules, "invoiceops.ml.registry", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

    registry = importlib.import_module("invoiceops.ml.registry")

    with pytest.raises(RuntimeError, match="registry unavailable"):
        registry.register_model("run-123")
