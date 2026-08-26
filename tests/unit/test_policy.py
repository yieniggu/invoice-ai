import math

import pytest

from invoiceops.domain.models import Decision
from invoiceops.domain.policy import (
    INITIAL_REVIEW_THRESHOLD,
    POLICY_VERSION,
    Recommendation,
    fallback_recommendation,
    recommend_from_probability,
)
from invoiceops.domain.rules import RULE_VERSION


def test_policy_version_and_initial_review_threshold_are_explicit() -> None:
    assert POLICY_VERSION == "ml-policy-v1"
    assert INITIAL_REVIEW_THRESHOLD == 0.80


def test_probability_at_threshold_requires_manual_review() -> None:
    recommendation = recommend_from_probability(INITIAL_REVIEW_THRESHOLD)

    assert isinstance(recommendation, Recommendation)
    assert recommendation.decision is Decision.MANUAL_REVIEW
    assert recommendation.policy_version == "ml-policy-v1"
    assert recommendation.threshold == 0.80
    assert recommendation.source == "model"
    assert recommendation.reason


def test_probability_below_threshold_auto_processes() -> None:
    recommendation = recommend_from_probability(INITIAL_REVIEW_THRESHOLD - 0.01)

    assert isinstance(recommendation, Recommendation)
    assert recommendation.decision is Decision.AUTO_PROCESS
    assert recommendation.policy_version == "ml-policy-v1"
    assert recommendation.threshold == 0.80
    assert recommendation.source == "model"
    assert recommendation.reason


def test_zero_probability_auto_processes() -> None:
    recommendation = recommend_from_probability(0.0)

    assert isinstance(recommendation, Recommendation)
    assert recommendation.decision is Decision.AUTO_PROCESS
    assert recommendation.policy_version == "ml-policy-v1"
    assert recommendation.threshold == 0.80
    assert recommendation.source == "model"
    assert recommendation.reason


def test_one_probability_requires_manual_review() -> None:
    recommendation = recommend_from_probability(1.0)

    assert isinstance(recommendation, Recommendation)
    assert recommendation.decision is Decision.MANUAL_REVIEW
    assert recommendation.policy_version == "ml-policy-v1"
    assert recommendation.threshold == 0.80
    assert recommendation.source == "model"
    assert recommendation.reason


def test_fallback_returns_safe_manual_review_without_probability() -> None:
    recommendation = fallback_recommendation()

    assert isinstance(recommendation, Recommendation)
    assert recommendation.decision is Decision.MANUAL_REVIEW
    assert recommendation.policy_version == "ml-policy-v1"
    assert recommendation.threshold == 0.80
    assert recommendation.reason == "model_unavailable"
    assert recommendation.source == "fallback"
    assert not hasattr(recommendation, "probability")


@pytest.mark.parametrize(
    "probability",
    [-0.01, 1.01, math.nan, math.inf, -math.inf],
)
def test_recommend_from_probability_rejects_out_of_range_and_non_finite_values(
    probability: float,
) -> None:
    with pytest.raises(ValueError):
        recommend_from_probability(probability)


def test_policy_does_not_replace_rule_v1() -> None:
    assert RULE_VERSION == "invoice-rules-v1"
