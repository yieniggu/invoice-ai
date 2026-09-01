import argparse
import os

import mlflow
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

MODEL_NAME = "invoice-review"
PIPELINE_ARTIFACT_PATH = "pipeline"
MISSING_CHAMPION_ALIAS_MESSAGE = "Registered model alias champion not found."
MISSING_CHAMPION_ALIAS_ERRORS = {
    ("RESOURCE_DOES_NOT_EXIST", MISSING_CHAMPION_ALIAS_MESSAGE),
    ("INVALID_PARAMETER_VALUE", f"INVALID_PARAMETER_VALUE: {MISSING_CHAMPION_ALIAS_MESSAGE}"),
}


def _configure_tracking_uri() -> None:
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)


def register_model(run_id: str) -> str:
    _configure_tracking_uri()
    registered_model = mlflow.register_model(
        model_uri=f"runs:/{run_id}/{PIPELINE_ARTIFACT_PATH}",
        name=MODEL_NAME,
    )
    version = registered_model.version
    MlflowClient().set_registered_model_alias(MODEL_NAME, "challenger", version)
    return version


def ensure_registered_version(run_id: str) -> tuple[str, str]:
    """Return the Model Version for a run without registering it twice."""
    _configure_tracking_uri()
    client = MlflowClient()
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    for model_version in versions:
        if model_version.run_id == run_id:
            return str(model_version.version), "reused"

    registered_model = mlflow.register_model(
        model_uri=f"runs:/{run_id}/{PIPELINE_ARTIFACT_PATH}", name=MODEL_NAME
    )
    model_version = client.get_model_version(MODEL_NAME, registered_model.version)
    if model_version.run_id != run_id or model_version.status != "READY":
        raise RuntimeError("Registered model version is not ready for the approved run")
    return str(registered_model.version), "created"


def _is_missing_champion_alias(error: MlflowException) -> bool:
    return (error.error_code, error.message) in MISSING_CHAMPION_ALIAS_ERRORS


def ensure_champion(version: str, run_id: str) -> str:
    """Promote only a ready version that is traceable to the approved run."""
    _configure_tracking_uri()
    client = MlflowClient()
    model_version = client.get_model_version(MODEL_NAME, version)
    if model_version.run_id != run_id or model_version.status != "READY":
        raise RuntimeError("Cannot promote a version that is not ready for the approved run")
    try:
        champion = client.get_model_version_by_alias(MODEL_NAME, "champion")
    except MlflowException as error:
        if not _is_missing_champion_alias(error):
            raise
        champion = None

    if champion is not None and str(champion.version) == str(version) and champion.run_id == run_id:
        return "unchanged"
    previous_version = str(champion.version) if champion is not None else "none"
    client.set_registered_model_alias(MODEL_NAME, "champion", version)
    return f"promoted:{previous_version}->{version}"


def promote_model(version: str) -> None:
    _configure_tracking_uri()
    MlflowClient().set_registered_model_alias(MODEL_NAME, "champion", version)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage InvoiceOps MLflow model registry aliases.")
    commands = parser.add_subparsers(dest="command", required=True)
    register_parser = commands.add_parser("register")
    register_parser.add_argument("--run-id", required=True)
    promote_parser = commands.add_parser("promote")
    promote_parser.add_argument("--version", required=True)
    args = parser.parse_args()

    if args.command == "register":
        version = register_model(args.run_id)
        print(f"registered {MODEL_NAME} version: {version}")
    else:
        promote_model(args.version)


if __name__ == "__main__":
    main()
