from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from invoiceops.ml.features import BOOLEAN_FEATURES, CATEGORICAL_FEATURES, NUMERIC_FEATURES

RANDOM_FOREST_SEED = 20260826
RANDOM_FOREST_ESTIMATORS = 500


def _categorical_encoder() -> OneHotEncoder:
    return OneHotEncoder(handle_unknown="ignore")


def build_dummy_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("preprocessor", ColumnTransformer([], remainder="passthrough")),
            ("classifier", DummyClassifier(strategy="prior")),
        ]
    )


def build_logistic_pipeline() -> Pipeline:
    preprocessor = ColumnTransformer(
        [
            ("numeric", StandardScaler(), NUMERIC_FEATURES),
            ("boolean", "passthrough", BOOLEAN_FEATURES),
            ("categorical", _categorical_encoder(), CATEGORICAL_FEATURES),
        ]
    )
    return Pipeline(
        [
            ("preprocessor", preprocessor),
            ("classifier", LogisticRegression(max_iter=1_000)),
        ]
    )


def build_random_forest_pipeline() -> Pipeline:
    preprocessor = ColumnTransformer(
        [
            ("numeric", "passthrough", NUMERIC_FEATURES),
            ("boolean", "passthrough", BOOLEAN_FEATURES),
            ("categorical", _categorical_encoder(), CATEGORICAL_FEATURES),
        ]
    )
    return Pipeline(
        [
            ("preprocessor", preprocessor),
            # Stabilize the canonical candidate while retaining deterministic teaching runs.
            (
                "classifier",
                RandomForestClassifier(
                    max_features=None,
                    n_estimators=RANDOM_FOREST_ESTIMATORS,
                    n_jobs=1,
                    random_state=RANDOM_FOREST_SEED,
                ),
            ),
        ]
    )
