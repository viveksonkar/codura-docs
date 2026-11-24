"""Stripe PaymentProvider over plain HTTPS (no SDK; ADR-0026 §6, SEC-20).

- Upgrades: Checkout in ``payment`` mode for the prorated charge (``setup`` mode when it is 0),
  saving the card for later invoices.
- Period invoices: an Invoice + invoice items carrying OUR amounts, auto-charged.
- Customer Portal for payment methods; webhooks verified with the ``Stripe-Signature`` HMAC scheme.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

import httpx

from codura_docs.ports.payments import (
    BillingAccount,
    CheckoutRequest,
    CheckoutSession,
    EventKind,
    ExternalInvoiceRef,
    InvoiceDraft,
    PaymentProviderError,
    ProviderEvent,
    WebhookVerificationError,
)

SIGNATURE_TOLERANCE_S = 300
_KINDS: dict[str, EventKind] = {
    "checkout.session.completed": "checkout_completed",
    "invoice.paid": "invoice_paid",
    "invoice.payment_failed": "invoice_payment_failed",
}


def sign(secret: str, body: bytes, timestamp: int) -> str:
    """The ``Stripe-Signature`` header value for *body* (used by tests and local tooling)."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


class StripePaymentProvider:
    name = "stripe"
    immediate_upgrades = False

    def __init__(
        self,
        secret_key: str,
        webhook_secret: str,
        *,
        api_base: str = "https://api.stripe.com",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 20.0,
        now: Any = time.time,
    ) -> None:
        if not secret_key or not webhook_secret:
            raise ValueError("Stripe needs STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET")
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret
        self._base = api_base.rstrip("/")
        self._transport = transport
        self._timeout = timeout_s
        self._now = now

    # --- HTTP -------------------------------------------------------------------------------------

    async def _post(
        self, path: str, data: list[tuple[str, str]], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._secret_key}", "Stripe-Version": "2024-06-20"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            async with httpx.AsyncClient(
                base_url=self._base, transport=self._transport, timeout=self._timeout
            ) as client:
                resp = await client.post(
                    path,
                    content=urlencode(data).encode(),
                    headers=headers | {"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"stripe unreachable: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            try:
                message = resp.json().get("error", {}).get("message", "")
            except ValueError:
                message = ""
            raise PaymentProviderError(f"stripe {resp.status_code}: {message}"[:300])
        body: dict[str, Any] = resp.json()
        return body

    # --- Port ---------------------------------------------------------------------------------------

    async def ensure_customer(self, account: BillingAccount) -> str:
        if account.customer_id:
            return account.customer_id
        data = [
            ("name", account.name),
            ("address[country]", account.country),
            ("preferred_locales[0]", "en"),
            ("metadata[tenant_id]", account.tenant_id),
        ]
        if account.email:
            data.append(("email", account.email))
        customer = await self._post(
            "/v1/customers", data, idempotency_key=f"customer:{account.tenant_id}"
        )
        return str(customer["id"])

    async def create_checkout(self, req: CheckoutRequest) -> CheckoutSession:
        meta = [
            ("metadata[tenant_id]", req.tenant_id),
            ("metadata[invoice_id]", req.invoice_id),
            ("metadata[plan_version_id]", req.plan_version_id),
        ]
        common = [
            ("customer", req.customer_id),
            ("client_reference_id", req.tenant_id),
            ("success_url", req.success_url),
            ("cancel_url", req.cancel_url),
            ("billing_address_collection", "required"),
            *meta,
        ]
        if req.amount_minor > 0:
            data = [
                *common,
                ("mode", "payment"),
                ("line_items[0][quantity]", "1"),
                ("line_items[0][price_data][currency]", req.currency.lower()),
                ("line_items[0][price_data][unit_amount]", str(req.amount_minor)),
                ("line_items[0][price_data][product_data][name]", req.description[:250]),
                ("payment_intent_data[setup_future_usage]", "off_session"),
                *[(k.replace("metadata", "payment_intent_data[metadata]", 1), v) for k, v in meta],
            ]
        else:
            data = [*common, ("mode", "setup"), ("currency", req.currency.lower())]
        session = await self._post(
            "/v1/checkout/sessions", data, idempotency_key=f"checkout:{req.invoice_id}"
        )
        return CheckoutSession(id=str(session["id"]), url=str(session["url"]))

    async def report_invoice(self, invoice: InvoiceDraft) -> ExternalInvoiceRef:
        if invoice.total_minor == 0:
            return ExternalInvoiceRef(None, None, "paid")
        created = await self._post(
            "/v1/invoices",
            [
                ("customer", invoice.customer_id),
                ("currency", invoice.currency.lower()),
                ("collection_method", "charge_automatically"),
                ("auto_advance", "true"),
                ("pending_invoice_items_behavior", "exclude"),
                ("description", f"Codura Docs invoice {invoice.number}"),
                ("metadata[invoice_id]", invoice.invoice_id),
                ("metadata[tenant_id]", invoice.tenant_id),
                ("metadata[number]", invoice.number),
            ],
            idempotency_key=f"invoice:{invoice.invoice_id}",
        )
        sid = str(created["id"])
        for i, line in enumerate(invoice.lines):
            if line.amount_minor == 0:
                continue
            await self._post(
                "/v1/invoiceitems",
                [
                    ("customer", invoice.customer_id),
                    ("invoice", sid),
                    ("currency", invoice.currency.lower()),
                    ("amount", str(line.amount_minor)),
                    ("description", line.description[:500]),
                ],
                idempotency_key=f"invoice:{invoice.invoice_id}:line:{i}",
            )
        final = await self._post(
            f"/v1/invoices/{sid}/finalize",
            [("auto_advance", "true")],
            idempotency_key=f"invoice:{invoice.invoice_id}:finalize",
        )
        return ExternalInvoiceRef(
            provider_invoice_id=sid,
            hosted_url=final.get("hosted_invoice_url"),
            status="paid" if final.get("status") == "paid" else "open",
        )

    async def customer_portal_url(self, customer_id: str, return_url: str) -> str | None:
        session = await self._post(
            "/v1/billing_portal/sessions", [("customer", customer_id), ("return_url", return_url)]
        )
        return str(session["url"])

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent:
        header = next((v for k, v in headers.items() if k.lower() == "stripe-signature"), "")
        parts: dict[str, list[str]] = {}
        for item in header.split(","):
            key, _, value = item.strip().partition("=")
            parts.setdefault(key, []).append(value)
        try:
            timestamp = int(parts["t"][0])
        except (KeyError, ValueError, IndexError) as exc:
            raise WebhookVerificationError("missing signature timestamp") from exc
        if abs(self._now() - timestamp) > SIGNATURE_TOLERANCE_S:
            raise WebhookVerificationError("signature timestamp outside tolerance")
        expected = sign(self._webhook_secret, body, timestamp).split("v1=", 1)[1]
        if not any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", [])):
            raise WebhookVerificationError("signature mismatch")
        try:
            event = json.loads(body)
            obj: dict[str, Any] = event["data"]["object"]
            raw_type = str(event["type"])
            event_id = str(event["id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise WebhookVerificationError("malformed event") from exc
        meta = {str(k): str(v) for k, v in (obj.get("metadata") or {}).items()}
        kind = _KINDS.get(raw_type, "ignored")
        if kind == "checkout_completed":
            return ProviderEvent(
                id=event_id,
                kind=kind,
                raw_type=raw_type,
                tenant_id=meta.get("tenant_id") or obj.get("client_reference_id"),
                customer_id=obj.get("customer"),
                invoice_id=meta.get("invoice_id"),
                provider_payment_id=obj.get("payment_intent") or obj.get("setup_intent"),
                amount_minor=obj.get("amount_total"),
                currency=(obj.get("currency") or "").upper() or None,
                metadata=meta,
            )
        if kind in ("invoice_paid", "invoice_payment_failed"):
            return ProviderEvent(
                id=event_id,
                kind=kind,
                raw_type=raw_type,
                tenant_id=meta.get("tenant_id"),
                customer_id=obj.get("customer"),
                invoice_id=meta.get("invoice_id"),
                provider_invoice_id=obj.get("id"),
                provider_payment_id=obj.get("payment_intent") or obj.get("charge"),
                amount_minor=obj.get("amount_paid")
                if kind == "invoice_paid"
                else obj.get("amount_due"),
                currency=(obj.get("currency") or "").upper() or None,
                metadata=meta,
            )
        return ProviderEvent(id=event_id, kind="ignored", raw_type=raw_type, metadata=meta)
