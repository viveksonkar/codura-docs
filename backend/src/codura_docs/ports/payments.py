"""PaymentProvider Port (specs/ports.md §9, specs/billing.md §8, ADR-0026).

Providers collect money; they never compute amounts. Our ledger (invoices + lines) is the source
of truth and is handed to the provider as-is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

Currency = Literal["USD", "INR"]
EventKind = Literal["checkout_completed", "invoice_paid", "invoice_payment_failed", "ignored"]


class PaymentProviderError(Exception):
    """The provider rejected a call or was unreachable."""


class WebhookVerificationError(Exception):
    """Bad or missing signature, or a stale timestamp (SEC-20)."""


@dataclass(frozen=True)
class BillingAccount:
    tenant_id: str
    name: str
    email: str | None
    country: str
    currency: Currency
    customer_id: str | None = None


@dataclass(frozen=True)
class CheckoutRequest:
    tenant_id: str
    customer_id: str
    currency: Currency
    amount_minor: int  # prorated first charge; may be 0 (card saved for later)
    description: str
    plan_version_id: str
    invoice_id: str
    success_url: str
    cancel_url: str


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    url: str | None  # None: the provider applies the change immediately (manual)


@dataclass(frozen=True)
class InvoiceLineDraft:
    description: str
    amount_minor: int


@dataclass(frozen=True)
class InvoiceDraft:
    invoice_id: str
    number: str
    tenant_id: str
    customer_id: str
    currency: Currency
    lines: Sequence[InvoiceLineDraft]
    total_minor: int
    due_days: int = 7


@dataclass(frozen=True)
class ExternalInvoiceRef:
    provider_invoice_id: str | None
    hosted_url: str | None
    status: Literal["open", "paid"]


@dataclass(frozen=True)
class ProviderEvent:
    id: str
    kind: EventKind
    raw_type: str
    tenant_id: str | None = None
    customer_id: str | None = None
    invoice_id: str | None = None  # OUR invoice id (from metadata)
    provider_invoice_id: str | None = None
    provider_payment_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    name: str
    immediate_upgrades: bool

    async def ensure_customer(self, account: BillingAccount) -> str: ...

    async def create_checkout(self, req: CheckoutRequest) -> CheckoutSession: ...

    async def report_invoice(self, invoice: InvoiceDraft) -> ExternalInvoiceRef: ...

    async def customer_portal_url(self, customer_id: str, return_url: str) -> str | None: ...

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent: ...
