import argparse
import os

import mlflow
from mlflow.tracking import MlflowClient

RECALL_THRESHOLD = 0.18
PRECISION_THRESHOLD = 0.48


def _configure_tracking_uri() -> None:
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)


def evaluate_run(run_id: str) -> tuple[float, float]:
    _configure_tracking_uri()
    metrics = MlflowClient().get_run(run_id).data.metrics
    missing_metrics = sorted({"recall", "precision"} - metrics.keys())
    if missing_metrics:
        raise ValueError(f"Missing required MLflow metrics: {', '.join(missing_metrics)}")
    return metrics["recall"], metrics["precision"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate an InvoiceOps MLflow run against quality thresholds."
    )
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    recall, precision = evaluate_run(args.run_id)
    recall_passes = recall >= RECALL_THRESHOLD
    precision_passes = precision >= PRECISION_THRESHOLD
    gate_passes = recall_passes and precision_passes

    print(f"Recall: {recall:.6f} {'PASS' if recall_passes else 'FAIL'}")
    print(f"Precision: {precision:.6f} {'PASS' if precision_passes else 'FAIL'}")
    print(f"MODEL GATE: {'PASS' if gate_passes else 'FAIL'}")
    if not gate_passes:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
