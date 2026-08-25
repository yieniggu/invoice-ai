import argparse
from collections.abc import Callable
from pathlib import Path

import pandas as pd
from sklearn.pipeline import Pipeline

from invoiceops.ml.data import SPLIT_FILENAMES, TARGET, generate_synthetic_dataset
from invoiceops.ml.features import MODEL_FEATURES
from invoiceops.ml.metrics import evaluate_binary_classifier
from invoiceops.ml.pipelines import (
    build_dummy_pipeline,
    build_logistic_pipeline,
    build_random_forest_pipeline,
)

CANONICAL_DATASET_VERSION = "invoice-risk-v1"
CANONICAL_DATASET_SEED = 20260826
CANONICAL_DATASET_ROWS = 12_000
MODEL_BUILDERS: dict[str, Callable[[], Pipeline]] = {
    "dummy": build_dummy_pipeline,
    "logistic": build_logistic_pipeline,
    "random_forest": build_random_forest_pipeline,
}


def _dataset_dir() -> Path:
    path = Path("data") / CANONICAL_DATASET_VERSION
    if not all((path / filename).is_file() for filename in SPLIT_FILENAMES):
        return generate_synthetic_dataset(
            seed=CANONICAL_DATASET_SEED,
            rows=CANONICAL_DATASET_ROWS,
            version=CANONICAL_DATASET_VERSION,
        )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and validate an invoice-risk model.")
    parser.add_argument("--model", choices=MODEL_BUILDERS, required=True)
    args = parser.parse_args()

    dataset_dir = _dataset_dir()
    train = pd.read_csv(dataset_dir / "train.csv")
    validation = pd.read_csv(dataset_dir / "validation.csv")
    pipeline = MODEL_BUILDERS[args.model]()
    pipeline.fit(train[MODEL_FEATURES], train[TARGET])

    predictions = pipeline.predict(validation[MODEL_FEATURES])
    probabilities = pipeline.predict_proba(validation[MODEL_FEATURES])[:, 1]
    metrics = evaluate_binary_classifier(validation[TARGET], predictions, probabilities)

    print(f"model: {args.model}")
    for name, value in metrics.items():
        print(f"{name}: {value}")


if __name__ == "__main__":
    main()
