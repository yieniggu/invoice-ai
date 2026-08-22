from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class InvoiceStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    AUTO_PROCESSED = "AUTO_PROCESSED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Decision(str, Enum):
    AUTO_PROCESS = "AUTO_PROCESS"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass
class Invoice:
    invoice_id: str
    vendor_name: str
    invoice_amount_cents: int
    has_purchase_order: bool
    three_way_match: bool
    status: InvoiceStatus
    created_at: datetime
    updated_at: datetime
