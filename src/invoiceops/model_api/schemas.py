from typing import Literal

from pydantic import BaseModel, ConfigDict


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    invoice_amount_cents: int
    vendor_tenure_days: int
    previous_incidents_12m: int
    amount_vs_vendor_median: float
    has_purchase_order: bool
    three_way_match: bool
    bank_account_recently_changed: bool
    country_risk: Literal["low", "medium", "high"]


class ModelMetadata(BaseModel):
    model_name: str
    model_version: str
    run_id: str


class HealthResponse(ModelMetadata):
    status: Literal["ok"]


class PredictionResponse(ModelMetadata):
    manual_review_probability: float
