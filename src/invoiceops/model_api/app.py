import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from math import isfinite

import mlflow
import mlflow.sklearn
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from mlflow.exceptions import MlflowException

from invoiceops.ml.features import MODEL_FEATURES
from invoiceops.model_api.schemas import (
    HealthResponse,
    ModelMetadata,
    PredictionRequest,
    PredictionResponse,
)

DEFAULT_MODEL_URI = "models:/invoice-review@champion"
UNAVAILABLE_DETAIL = "Model service is unavailable."
MODEL_OPERATION_ERRORS = (
    AttributeError,
    IndexError,
    KeyError,
    MlflowException,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def _model_name_from_uri(model_uri: str) -> str:
    if model_uri.startswith("models:/"):
        return (
            model_uri.removeprefix("models:/").split("@", maxsplit=1)[0].split("/", maxsplit=1)[0]
        )
    return model_uri


def _mark_model_unavailable(app: FastAPI, error: Exception) -> None:
    app.state.model_unavailable = True
    app.state.model_error_type = type(error).__name__


def _require_available_model(request: Request) -> None:
    if request.app.state.model_unavailable:
        raise HTTPException(status_code=503, detail=UNAVAILABLE_DETAIL)


def _model_metadata(model_uri: str, model_info: object) -> ModelMetadata:
    model_name = _model_name_from_uri(model_uri)
    model_version = model_info.registered_model_version
    run_id = model_info.run_id
    if (
        not model_name
        or model_version is None
        or isinstance(model_version, bool)
        or not str(model_version)
        or not isinstance(run_id, str)
        or not run_id
    ):
        raise ValueError("Model metadata is invalid.")
    return ModelMetadata(
        model_name=model_name,
        model_version=str(model_version),
        run_id=run_id,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.model_unavailable = True
    app.state.model = None
    app.state.model_metadata = None
    app.state.model_error_type = None

    try:
        tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)

        model_uri = os.getenv("INVOICEOPS_MODEL_URI", DEFAULT_MODEL_URI)
        model_info = mlflow.models.get_model_info(model_uri)
        app.state.model = mlflow.sklearn.load_model(model_uri)
        app.state.model_metadata = _model_metadata(model_uri, model_info)
    except MODEL_OPERATION_ERRORS as error:
        _mark_model_unavailable(app, error)
    else:
        app.state.model_unavailable = False

    yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=_lifespan)

    @app.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        _require_available_model(request)
        return HealthResponse(status="ok", **request.app.state.model_metadata.model_dump())

    @app.post("/predict", response_model=PredictionResponse)
    def predict(request: Request, payload: PredictionRequest) -> PredictionResponse:
        _require_available_model(request)
        features = pd.DataFrame([payload.model_dump()], columns=MODEL_FEATURES)
        try:
            probability = float(request.app.state.model.predict_proba(features)[0][1])
            if not isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("Model probability is outside the expected range.")
        except MODEL_OPERATION_ERRORS as error:
            _mark_model_unavailable(request.app, error)
            raise HTTPException(status_code=503, detail=UNAVAILABLE_DETAIL) from None
        return PredictionResponse(
            manual_review_probability=probability,
            **request.app.state.model_metadata.model_dump(),
        )

    return app


app = create_app()
