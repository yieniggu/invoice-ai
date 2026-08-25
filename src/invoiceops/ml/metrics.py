from collections.abc import Sequence

from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score


def evaluate_binary_classifier(
    y_true: Sequence[bool], y_pred: Sequence[bool], positive_probabilities: Sequence[float]
) -> dict[str, float]:
    roc_auc = 0.0
    if len(set(y_true)) == 2:
        roc_auc = float(roc_auc_score(y_true, positive_probabilities))

    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": roc_auc,
    }
