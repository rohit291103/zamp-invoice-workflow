"""Domain types passed between pipeline stages."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# Severity ordering matters: the decision is driven by the worst finding.
SEVERITY_ORDER = {"info": 0, "warn": 1, "block": 2}


@dataclass
class Finding:
    """One rule's verdict on one invoice. This is the audit trail."""
    rule_id: str
    rule_name: str
    severity: str          # info | warn | block
    message: str           # written for an AP clerk, not a developer
    evidence: dict[str, Any] = field(default_factory=dict)
    # Blocking rules say *how* they block: a REJECT is final, a HOLD is
    # "we cannot decide this yet, here is what would unblock it".
    outcome: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LineItem:
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


@dataclass
class ExtractedInvoice:
    """What we believe the document says, before any validation."""
    vendor_name: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None       # ISO-8601
    po_reference: Optional[str] = None
    currency: Optional[str] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    invoice_total: Optional[float] = None
    line_items: list[dict[str, Any]] = field(default_factory=list)
    field_confidence: dict[str, float] = field(default_factory=dict)
    extraction_notes: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def confidence_for(self, name: str) -> float:
        return float(self.field_confidence.get(name, 1.0))


@dataclass
class Vendor:
    vendor_id: str
    legal_name: str
    aliases: list[str]
    status: str            # approved | on_hold | blocked
    status_reason: str
    tax_id: str
    country: str
    default_currency: str
    payment_terms: str


@dataclass
class PurchaseOrder:
    po_number: str
    vendor_id: str
    description: str
    currency: str
    po_amount: float
    issued_date: str
    status: str            # open | closed
    allow_partial: bool
    tolerance_pct: float
