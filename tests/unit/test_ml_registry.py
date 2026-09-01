import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import INVALID_PARAMETER_VALUE, RESOURCE_DOES_NOT_EXIST


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


def test_ensure_registered_version_reuses_the_version_for_the_same_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invoiceops.ml import registry

    class StubMlflowClient:
        def search_model_versions(self, filter_string: str) -> list[SimpleNamespace]:
            assert filter_string == "name='invoice-review'"
            return [SimpleNamespace(version="7", run_id="run-123")]

    monkeypatch.setattr(registry, "MlflowClient", StubMlflowClient)

    assert registry.ensure_registered_version("run-123") == ("7", "reused")


def test_ensure_champion_does_not_mutate_an_alias_already_on_the_approved_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invoiceops.ml import registry

    class StubMlflowClient:
        def get_model_version(self, name: str, version: str) -> SimpleNamespace:
            return SimpleNamespace(version=version, run_id="run-123", status="READY")

        def get_model_version_by_alias(self, name: str, alias: str) -> SimpleNamespace:
            return SimpleNamespace(version="7", run_id="run-123")

        def set_registered_model_alias(self, name: str, alias: str, version: str) -> None:
            pytest.fail("Champion alias must not be written when it already matches")

    monkeypatch.setattr(registry, "MlflowClient", StubMlflowClient)

    assert registry.ensure_champion("7", "run-123") == "unchanged"


@pytest.mark.parametrize(
    ("error_code", "message"),
    [
        (RESOURCE_DOES_NOT_EXIST, "Registered model alias champion not found."),
        (
            INVALID_PARAMETER_VALUE,
            "INVALID_PARAMETER_VALUE: Registered model alias champion not found.",
        ),
    ],
)
def test_ensure_champion_promotes_when_mlflow_reports_a_missing_champion_alias(
    monkeypatch: pytest.MonkeyPatch, error_code: int, message: str
) -> None:
    from invoiceops.ml import registry

    calls: list[tuple[str, str, str]] = []

    class StubMlflowClient:
        def get_model_version(self, name: str, version: str) -> SimpleNamespace:
            return SimpleNamespace(version=version, run_id="run-123", status="READY")

        def get_model_version_by_alias(self, name: str, alias: str) -> SimpleNamespace:
            raise MlflowException(message, error_code)

        def set_registered_model_alias(self, name: str, alias: str, version: str) -> None:
            calls.append((name, alias, version))

    monkeypatch.setattr(registry, "MlflowClient", StubMlflowClient)

    assert registry.ensure_champion("7", "run-123") == "promoted:none->7"
    assert calls == [("invoice-review", "champion", "7")]


@pytest.mark.parametrize(
    ("error_code", "message"),
    [
        (INVALID_PARAMETER_VALUE, "Registered model alias champion not found."),
        (
            INVALID_PARAMETER_VALUE,
            "INVALID_PARAMETER_VALUE: Registered model alias challenger not found.",
        ),
        (INVALID_PARAMETER_VALUE, "Registered model alias challenger not found."),
        (INVALID_PARAMETER_VALUE, "Registry request is invalid."),
        (
            RESOURCE_DOES_NOT_EXIST,
            "INVALID_PARAMETER_VALUE: Registered model alias champion not found.",
        ),
        (RESOURCE_DOES_NOT_EXIST, "Registered model invoice-review not found."),
    ],
)
def test_ensure_champion_propagates_unrelated_registry_errors(
    monkeypatch: pytest.MonkeyPatch, error_code: int, message: str
) -> None:
    from invoiceops.ml import registry

    class StubMlflowClient:
        def get_model_version(self, name: str, version: str) -> SimpleNamespace:
            return SimpleNamespace(version=version, run_id="run-123", status="READY")

        def get_model_version_by_alias(self, name: str, alias: str) -> SimpleNamespace:
            raise MlflowException(message, error_code)

    monkeypatch.setattr(registry, "MlflowClient", StubMlflowClient)

    with pytest.raises(MlflowException, match=message):
        registry.ensure_champion("7", "run-123")
