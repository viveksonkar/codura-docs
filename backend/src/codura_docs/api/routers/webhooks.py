"""Payment webhooks (FR-67, SEC-20): POST /api/webhooks/payments/{provider}.

No session or key: the provider's signature is the only credential. Only the configured provider
is accepted; a bad signature is 400; a duplicate event is 200 with no effect.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from codura_docs.api.errors import ApiError
from codura_docs.billing import webhooks
from codura_docs.ports.payments import WebhookVerificationError

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
MAX_BODY = 512 * 1024


@router.post("/payments/{provider}")
async def payment_webhook(provider: str, request: Request) -> dict[str, str]:
    payments = request.app.state.registry.payments()
    if provider != payments.name or payments.name == "manual":
        raise ApiError(404, "not_found", "Unknown payment provider")
    body = await request.body()
    if len(body) > MAX_BODY:
        raise ApiError(413, "payload_too_large", "Webhook body too large")
    try:
        outcome = await webhooks.handle(
            request.app.state.billing_engine, payments, dict(request.headers), body
        )
    except WebhookVerificationError as exc:
        raise ApiError(400, "invalid_signature", "Webhook signature verification failed") from exc
    return {"status": outcome}
