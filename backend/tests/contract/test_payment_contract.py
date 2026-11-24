"""PaymentProvider contract (specs/billing.md §8, SEC-20): `manual` and `stripe` (mocked HTTPS).

Replay of a processed event is covered by the dedupe table in tests/integration/test_billing.py.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from codura_docs.adapters.payments.manual import ManualPaymentProvider
from codura_docs.adapters.payments.stripe import StripePaymentProvider, sign
from codura_docs.ports.payments import (
    BillingAccount,
    CheckoutRequest,
    InvoiceDraft,
    InvoiceLineDraft,
    PaymentProvider,
    WebhookVerificationError,
)

SECRET = "whsec_test"


class FakeStripe:
    """Just enough of the Stripe REST API, keyed by Idempotency-Key like the real one."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, list[str]], str | None]] = []
        self.by_key: dict[str, dict[str, Any]] = {}
        self.seq = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        key = request.headers.get("idempotency-key")
        self.calls.append((request.url.path, form, key))
        if request.headers.get("authorization") != "Bearer sk_test_x":
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        if key and key in self.by_key:
            return httpx.Response(200, json=self.by_key[key])
        self.seq += 1
        path = request.url.path
        if path == "/v1/customers":
            body: dict[str, Any] = {"id": f"cus_{self.seq}"}
        elif path == "/v1/checkout/sessions":
            body = {"id": f"cs_{self.seq}", "url": f"https://checkout.stripe.test/{self.seq}"}
        elif path == "/v1/invoices":
            body = {"id": f"in_{self.seq}", "status": "draft"}
        elif path.endswith("/finalize"):
            body = {
                "id": path.split("/")[3],
                "status": "open",
                "hosted_invoice_url": "https://pay.stripe.test/i",
            }
        elif path == "/v1/billing_portal/sessions":
            body = {"url": "https://billing.stripe.test/p"}
        else:
            body = {"id": f"ii_{self.seq}"}
        if key:
            self.by_key[key] = body
        return httpx.Response(200, json=body)


@pytest.fixture(params=["manual", "stripe"])
def provider(request: pytest.FixtureRequest) -> Iterator[PaymentProvider]:
    if request.param == "manual":
        yield ManualPaymentProvider()
    else:
        yield StripePaymentProvider(
            "sk_test_x",
            SECRET,
            api_base="https://api.stripe.test",
            transport=httpx.MockTransport(FakeStripe()),
        )


ACCOUNT = BillingAccount("tnt_01JD8X0000000000000000000A", "Acme", "billing@acme.test", "IN", "INR")
DRAFT = InvoiceDraft(
    invoice_id="bill_1",
    number="CD-202612-001001",
    tenant_id=ACCOUNT.tenant_id,
    customer_id="cus_1",
    currency="INR",
    lines=[InvoiceLineDraft("Growth plan", 699_900), InvoiceLineDraft("Overage", 1_200)],
    total_minor=701_100,
)


async def test_ensure_customer_is_idempotent(provider: PaymentProvider) -> None:
    first = await provider.ensure_customer(ACCOUNT)
    second = await provider.ensure_customer(ACCOUNT)
    assert first == second
    assert (
        await provider.ensure_customer(
            BillingAccount(**{**ACCOUNT.__dict__, "customer_id": "cus_kept"})
        )
        == "cus_kept"
    )


async def test_checkout_returns_redirect_or_immediate(provider: PaymentProvider) -> None:
    session = await provider.create_checkout(
        CheckoutRequest(
            tenant_id=ACCOUNT.tenant_id,
            customer_id="cus_1",
            currency="INR",
            amount_minor=350_000,
            description="Growth plan (prorated)",
            plan_version_id="plv_growth_1",
            invoice_id="bill_1",
            success_url="http://x/billing?ok",
            cancel_url="http://x/billing?no",
        )
    )
    if provider.immediate_upgrades:
        assert session.url is None
    else:
        assert session.url and session.url.startswith("https://")


async def test_report_invoice_carries_our_amounts(provider: PaymentProvider) -> None:
    ref = await provider.report_invoice(DRAFT)
    assert ref.status in ("open", "paid")
    if isinstance(provider, StripePaymentProvider):
        fake: FakeStripe = provider._transport.handler  # type: ignore[union-attr, attr-defined]
        items = [form for path, form, _ in fake.calls if path == "/v1/invoiceitems"]
        assert [int(f["amount"][0]) for f in items] == [699_900, 1_200]
        assert all(f["currency"] == ["inr"] for f in items)
        assert ref.provider_invoice_id and ref.hosted_url
    zero = await provider.report_invoice(
        InvoiceDraft(**{**DRAFT.__dict__, "lines": [], "total_minor": 0})
    )
    assert zero.status == "paid"


async def test_forged_or_stale_webhooks_are_rejected(provider: PaymentProvider) -> None:
    body = json.dumps(
        {"id": "evt_1", "type": "invoice.paid", "data": {"object": {"metadata": {}}}}
    ).encode()
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook({"stripe-signature": "t=1,v1=deadbeef"}, body)
    if isinstance(provider, StripePaymentProvider):
        stale = sign(SECRET, body, int(time.time()) - 3600)
        with pytest.raises(WebhookVerificationError):
            provider.verify_webhook({"Stripe-Signature": stale}, body)
        wrong_secret = sign("whsec_other", body, int(time.time()))
        with pytest.raises(WebhookVerificationError):
            provider.verify_webhook({"Stripe-Signature": wrong_secret}, body)


def test_stripe_webhook_parses_signed_events() -> None:
    stripe = StripePaymentProvider("sk_test_x", SECRET)
    obj = {
        "id": "in_9",
        "customer": "cus_1",
        "amount_paid": 701_100,
        "currency": "inr",
        "payment_intent": "pi_1",
        "metadata": {"invoice_id": "bill_1", "tenant_id": ACCOUNT.tenant_id},
    }
    body = json.dumps({"id": "evt_2", "type": "invoice.paid", "data": {"object": obj}}).encode()
    event = stripe.verify_webhook({"Stripe-Signature": sign(SECRET, body, int(time.time()))}, body)
    assert (event.kind, event.invoice_id, event.amount_minor, event.currency) == (
        "invoice_paid",
        "bill_1",
        701_100,
        "INR",
    )
    other = json.dumps({"id": "evt_3", "type": "customer.updated", "data": {"object": {}}}).encode()
    assert (
        stripe.verify_webhook(
            {"Stripe-Signature": sign(SECRET, other, int(time.time()))}, other
        ).kind
        == "ignored"
    )


async def test_portal(provider: PaymentProvider) -> None:
    url = await provider.customer_portal_url("cus_1", "http://x/billing")
    assert (url is None) == (provider.name == "manual")
