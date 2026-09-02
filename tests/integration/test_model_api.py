from collections.abc import Generator
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from mlflow.exceptions import MlflowException

from invoiceops.model_api import app as model_api

VALID_FEATURES = {
    "invoice_amount_cents": 500_000,
    "vendor_tenure_days": 365,
    "previous_incidents_12m": 2,
    "amount_vs_vendor_median": 1.25,
    "has_purchase_order": True,
    "three_way_match": False,
    "bank_account_recently_changed": True,
    "country_risk": "high",
}
DEFAULT_RESULT = object()


class StubModel:
    def __init__(self, result: object = DEFAULT_RESULT) -> None:
        self.inputs: list[pd.DataFrame] = []
        self.result = [[0.2, 0.8]] if result is DEFAULT_RESULT else result

    def predict_proba(self, features: pd.DataFrame) -> object:
        self.inputs.append(features)
        return self.result


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, StubModel, list[str]], None, None]:
    model = StubModel()
    load_calls: list[str] = []

    def load_model(model_uri: str) -> StubModel:
        load_calls.append(model_uri)
        return model

    monkeypatch.setattr(model_api.mlflow.sklearn, "load_model", load_model)
    monkeypatch.setattr(
        model_api.mlflow.models,
        "get_model_info",
        lambda model_uri: SimpleNamespace(run_id="run-123", registered_model_version=7),
    )
    with TestClient(model_api.create_app()) as test_client:
        yield test_client, model, load_calls


def test_loads_default_model_once_and_exposes_metadata(
    client: tuple[TestClient, StubModel, list[str]],
) -> None:
    test_client, _, load_calls = client

    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model_name": "invoice-review",
        "model_version": "7",
        "run_id": "run-123",
    }
    assert load_calls == ["models:/invoice-review@champion"]


def test_predict_returns_probability_metadata_and_does_not_reload_model(
    client: tuple[TestClient, StubModel, list[str]],
) -> None:
    test_client, model, load_calls = client

    response = test_client.post("/predict", json=VALID_FEATURES)

    assert response.status_code == 200
    assert response.json() == {
        "manual_review_probability": 0.8,
        "model_name": "invoice-review",
        "model_version": "7",
        "run_id": "run-123",
    }
    assert load_calls == ["models:/invoice-review@champion"]
    assert model.inputs[0].to_dict(orient="records") == [VALID_FEATURES]


def test_honors_model_and_tracking_uri_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = StubModel()
    load_calls: list[str] = []
    tracking_uris: list[str] = []
    monkeypatch.setenv("INVOICEOPS_MODEL_URI", "models:/custom-review@candidate")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.test")
    monkeypatch.setattr(
        model_api.mlflow.sklearn,
        "load_model",
        lambda model_uri: load_calls.append(model_uri) or model,
    )
    monkeypatch.setattr(
        model_api.mlflow,
        "set_tracking_uri",
        tracking_uris.append,
    )
    monkeypatch.setattr(
        model_api.mlflow.models,
        "get_model_info",
        lambda model_uri: SimpleNamespace(run_id="run-456", registered_model_version=9),
    )

    with TestClient(model_api.create_app()) as test_client:
        response = test_client.get("/health")

    assert response.json()["model_name"] == "custom-review"
    assert load_calls == ["models:/custom-review@candidate"]
    assert tracking_uris == ["http://mlflow.test"]


def test_resolves_alias_metadata_when_model_info_omits_version_and_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = StubModel()
    alias_calls: list[tuple[str, str]] = []

    class StubMlflowClient:
        def get_model_version_by_alias(self, name: str, alias: str) -> SimpleNamespace:
            alias_calls.append((name, alias))
            return SimpleNamespace(version="12", run_id="run-789")

    monkeypatch.setattr(model_api.mlflow.sklearn, "load_model", lambda _: model)
    monkeypatch.setattr(
        model_api.mlflow.models,
        "get_model_info",
        lambda _: SimpleNamespace(run_id=None, registered_model_version=None),
    )
    monkeypatch.setattr(model_api, "MlflowClient", StubMlflowClient)

    with TestClient(model_api.create_app()) as test_client:
        health_response = test_client.get("/health")
        predict_response = test_client.post("/predict", json=VALID_FEATURES)

    expected_metadata = {"model_name": "invoice-review", "model_version": "12", "run_id": "run-789"}
    assert health_response.json() == {"status": "ok", **expected_metadata}
    assert predict_response.json() == {"manual_review_probability": 0.8, **expected_metadata}
    assert alias_calls == [("invoice-review", "champion")]


@pytest.mark.parametrize("field", VALID_FEATURES)
def test_predict_rejects_each_missing_feature(
    client: tuple[TestClient, StubModel, list[str]], field: str
) -> None:
    test_client, _, _ = client

    response = test_client.post(
        "/predict", json={name: value for name, value in VALID_FEATURES.items() if name != field}
    )

    assert response.status_code == 422


def test_predict_rejects_extra_fields(client: tuple[TestClient, StubModel, list[str]]) -> None:
    test_client, _, _ = client

    response = test_client.post("/predict", json={**VALID_FEATURES, "unexpected": "value"})

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("invoice_amount_cents", True),
        ("vendor_tenure_days", 1.0),
        ("previous_incidents_12m", "2"),
        ("amount_vs_vendor_median", True),
        ("has_purchase_order", 1),
        ("three_way_match", "false"),
        ("bank_account_recently_changed", 0),
        ("country_risk", 1),
    ],
)
def test_predict_rejects_invalid_feature_types(
    client: tuple[TestClient, StubModel, list[str]], field: str, invalid_value: object
) -> None:
    test_client, _, _ = client

    response = test_client.post("/predict", json={**VALID_FEATURES, field: invalid_value})

    assert response.status_code == 422


def test_predict_rejects_unknown_country_risk(
    client: tuple[TestClient, StubModel, list[str]],
) -> None:
    test_client, _, _ = client

    response = test_client.post("/predict", json={**VALID_FEATURES, "country_risk": "unknown"})

    assert response.status_code == 422


@pytest.mark.parametrize("failing_operation", ["get_model_info", "load_model"])
def test_model_loading_failure_keeps_app_running_and_returns_safe_unavailable_response(
    monkeypatch: pytest.MonkeyPatch, failing_operation: str
) -> None:
    def fail(*_: object) -> object:
        raise RuntimeError("mlflow://credentials@internal.example/secret")

    monkeypatch.setattr(
        model_api.mlflow.models,
        "get_model_info",
        fail
        if failing_operation == "get_model_info"
        else lambda _: SimpleNamespace(run_id="run-123", registered_model_version=7),
    )
    monkeypatch.setattr(
        model_api.mlflow.sklearn,
        "load_model",
        fail if failing_operation == "load_model" else lambda _: StubModel(),
    )

    with TestClient(model_api.create_app()) as test_client:
        health_response = test_client.get("/health")
        predict_response = test_client.post("/predict", json=VALID_FEATURES)

    assert health_response.status_code == 503
    assert predict_response.status_code == 503
    assert health_response.json() == {"detail": "Model service is unavailable."}
    assert predict_response.json() == {"detail": "Model service is unavailable."}


def test_missing_champion_alias_keeps_the_503_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_champion(*_: object) -> object:
        raise MlflowException("Registered model alias champion not found.", "RESOURCE_DOES_NOT_EXIST")

    monkeypatch.setattr(model_api.mlflow.models, "get_model_info", missing_champion)

    with TestClient(model_api.create_app()) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "Model service is unavailable."}


def test_bootstrap_champion_is_the_precondition_for_a_healthy_model_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path
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
    monkeypatch.setattr(model_api.mlflow.sklearn, "load_model", lambda _: StubModel())
    monkeypatch.setattr(
        model_api.mlflow.models,
        "get_model_info",
        lambda _: SimpleNamespace(
            run_id=result.run_id, registered_model_version=int(result.model_version)
        ),
    )

    with TestClient(model_api.create_app()) as test_client:
        response = test_client.get("/health")

    assert response.json() == {
        "status": "ok",
        "model_name": "invoice-review",
        "model_version": "7",
        "run_id": "run-approved",
    }


def test_prediction_failure_marks_model_unavailable_and_returns_safe_response(
    client: tuple[TestClient, StubModel, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, model, _ = client

    def fail(_: pd.DataFrame) -> object:
        raise RuntimeError("model inference internals")

    monkeypatch.setattr(model, "predict_proba", fail)

    predict_response = test_client.post("/predict", json=VALID_FEATURES)
    health_response = test_client.get("/health")

    assert predict_response.status_code == 503
    assert health_response.status_code == 503
    assert predict_response.json() == {"detail": "Model service is unavailable."}
    assert health_response.json() == {"detail": "Model service is unavailable."}


def test_invalid_model_metadata_returns_safe_unavailable_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INVOICEOPS_MODEL_URI", "invoice-review")
    monkeypatch.setattr(
        model_api,
        "MlflowClient",
        lambda: pytest.fail("invalid metadata must not resolve an MLflow model alias"),
    )
    monkeypatch.setattr(model_api.mlflow.sklearn, "load_model", lambda _: StubModel())
    monkeypatch.setattr(
        model_api.mlflow.models,
        "get_model_info",
        lambda _: SimpleNamespace(run_id=None, registered_model_version=7),
    )

    with TestClient(model_api.create_app()) as test_client:
        health_response = test_client.get("/health")
        predict_response = test_client.post("/predict", json=VALID_FEATURES)

    assert health_response.status_code == 503
    assert predict_response.status_code == 503
    assert health_response.json() == {"detail": "Model service is unavailable."}
    assert predict_response.json() == {"detail": "Model service is unavailable."}


@pytest.mark.parametrize(
    "model_result",
    [
        [[0.2, float("nan")]],
        [[0.2, float("inf")]],
        [[0.2, -0.1]],
        [[0.2, 1.1]],
        [],
        [[0.2]],
        None,
    ],
)
def test_predict_rejects_invalid_model_probabilities_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, model_result: object
) -> None:
    model = StubModel(model_result)
    monkeypatch.setattr(model_api.mlflow.sklearn, "load_model", lambda _: model)
    monkeypatch.setattr(
        model_api.mlflow.models,
        "get_model_info",
        lambda _: SimpleNamespace(run_id="run-123", registered_model_version=7),
    )

    with TestClient(model_api.create_app()) as test_client:
        predict_response = test_client.post("/predict", json=VALID_FEATURES)
        health_response = test_client.get("/health")

    assert predict_response.status_code == 503
    assert health_response.status_code == 503
    assert predict_response.json() == {"detail": "Model service is unavailable."}
    assert health_response.json() == {"detail": "Model service is unavailable."}


@pytest.mark.parametrize("probability", [0.0, 1.0])
def test_predict_preserves_probability_boundaries(
    monkeypatch: pytest.MonkeyPatch, probability: float
) -> None:
    model = StubModel([[1.0 - probability, probability]])
    monkeypatch.setattr(model_api.mlflow.sklearn, "load_model", lambda _: model)
    monkeypatch.setattr(
        model_api.mlflow.models,
        "get_model_info",
        lambda _: SimpleNamespace(run_id="run-123", registered_model_version=7),
    )

    with TestClient(model_api.create_app()) as test_client:
        response = test_client.post("/predict", json=VALID_FEATURES)

    assert response.status_code == 200
    assert response.json()["manual_review_probability"] == probability
