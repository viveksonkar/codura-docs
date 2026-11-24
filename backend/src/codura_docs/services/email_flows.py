"""Transactional emails, queued as Jobs so SMTP latency/failures never block requests (ADR-0020)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.jobs.queue import enqueue


async def queue_email(session: AsyncSession, to: str, subject: str, body: str) -> None:
    await enqueue(
        session, "send_email", {"to": to, "subject": subject, "text": body}, max_attempts=5
    )


async def verification_email(session: AsyncSession, base_url: str, to: str, token: str) -> None:
    link = f"{base_url}/verify-email?token={token}"
    await queue_email(
        session,
        to,
        "Verify your email for Codura Docs",
        f"Welcome to Codura Docs!\n\nConfirm your email address to activate your organisation:\n{link}\n\n"
        "This link expires in 24 hours. If you did not sign up, ignore this email.",
    )


async def reset_email(session: AsyncSession, base_url: str, to: str, token: str) -> None:
    link = f"{base_url}/reset-password?token={token}"
    await queue_email(
        session,
        to,
        "Reset your Codura Docs password",
        f"Use this link to choose a new password:\n{link}\n\nThe link expires in 1 hour. "
        "If you did not ask for a reset, ignore this email.",
    )


async def invite_email(
    session: AsyncSession, base_url: str, to: str, org: str, role: str, token: str
) -> None:
    link = f"{base_url}/accept-invite?token={token}"
    label = "Tenant Admin" if role == "admin" else "Tenant User (read-only)"
    await queue_email(
        session,
        to,
        f"You're invited to {org} on Codura Docs",
        f"You have been invited to join {org} as a {label}.\n\nAccept the invitation:\n{link}\n\n"
        "The invitation expires in 7 days.",
    )
