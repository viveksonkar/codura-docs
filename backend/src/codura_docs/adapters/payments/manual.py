"""``manual`` PaymentProvider: no network, the Platform Admin records payments (NFR-01, ADR-0026).

Upgrades apply immediately and their invoice stays ``open`` until a Platform Admin marks it paid.
It has no webhooks: every event is rejected as unverifiable.
"""

from __future__ import annotations

from collections.abc import Mapping

from codura_docs.ports.payments import (
    BillingAccount,
    CheckoutRequest,
    CheckoutSession,
    ExternalInvoiceRef,
    InvoiceDraft,
    ProviderEvent,
    WebhookVerificationError,
)


class ManualPaymentProvider:
    name = "manual"
    immediate_upgrades = True

    async def ensure_customer(self, account: BillingAccount) -> str:
        return account.customer_id or f"manual_{account.tenant_id}"

    async def create_checkout(self, req: CheckoutRequest) -> CheckoutSession:
        return CheckoutSession(id=f"manual_{req.invoice_id}", url=None)

    async def report_invoice(self, invoice: InvoiceDraft) -> ExternalInvoiceRef:
        return ExternalInvoiceRef(
            provider_invoice_id=None,
            hosted_url=None,
            status="paid" if invoice.total_minor == 0 else "open",
        )

    async def customer_portal_url(self, customer_id: str, return_url: str) -> str | None:
        return None

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent:
        raise WebhookVerificationError("the manual provider has no webhooks")
