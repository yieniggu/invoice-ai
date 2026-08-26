import argparse
import os

import mlflow
from mlflow.tracking import MlflowClient

MODEL_NAME = "invoice-review"
PIPELINE_ARTIFACT_PATH = "pipeline"


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
