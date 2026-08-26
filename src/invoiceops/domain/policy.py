import math
from dataclasses import dataclass

from invoiceops.domain.models import Decision

POLICY_VERSION = "ml-policy-v1"
INITIAL_REVIEW_THRESHOLD = 0.80


@dataclass
class Recommendation:
    decision: Decision
    policy_version: str
    threshold: float
    reason: str
    source: str


def recommend_from_probability(probability: float) -> Recommendation:
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be finite and within [0, 1]")

    if probability >= INITIAL_REVIEW_THRESHOLD:
        return Recommendation(
            decision=Decision.MANUAL_REVIEW,
            policy_version=POLICY_VERSION,
            threshold=INITIAL_REVIEW_THRESHOLD,
            reason="probability_at_or_above_threshold",
            source="model",
        )

    return Recommendation(
        decision=Decision.AUTO_PROCESS,
        policy_version=POLICY_VERSION,
        threshold=INITIAL_REVIEW_THRESHOLD,
        reason="probability_below_threshold",
        source="model",
    )


def fallback_recommendation() -> Recommendation:
    return Recommendation(
        decision=Decision.MANUAL_REVIEW,
        policy_version=POLICY_VERSION,
        threshold=INITIAL_REVIEW_THRESHOLD,
        reason="model_unavailable",
        source="fallback",
    )
