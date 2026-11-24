"""``codura-docs`` command line: serve, worker, migrate, llm ping, mail test."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any

import typer

from codura_docs.config import get_settings
from codura_docs.log import setup_logging


def run_async(coro: Coroutine[Any, Any, None]) -> None:
    """asyncio.run with a psycopg-compatible loop on Windows hosts (containers are Linux)."""
    if sys.platform == "win32":
        asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(coro)


app = typer.Typer(no_args_is_help=True, add_completion=False)
llm_app = typer.Typer(no_args_is_help=True, help="LLM diagnostics")
mail_app = typer.Typer(no_args_is_help=True, help="Email diagnostics")
app.add_typer(llm_app, name="llm")
app.add_typer(mail_app, name="mail")


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:  # noqa: S104
    """Run the API (uvicorn)."""
    import uvicorn

    uvicorn.run(
        "codura_docs.api.app:app_factory",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=None,
    )


@app.command()
def worker() -> None:
    """Run the background Job worker."""
    from codura_docs.worker.runner import run_worker

    settings = get_settings()
    setup_logging(settings.log_level)
    run_async(run_worker(settings))


@app.command()
def migrate() -> None:
    """Upgrade the schema and run idempotent bootstrap (migrate service)."""
    from codura_docs.bootstrap import run_bootstrap

    settings = get_settings()
    setup_logging(settings.log_level)
    run_bootstrap(settings)
    typer.echo("migrate: ok")


@llm_app.command("ping")
def llm_ping(
    profile: str = typer.Option(None, help="Model Profile id (default: the default profile)"),
    prompt: str = "Reply with one short sentence confirming you are online.",
) -> None:
    """Stream a reply from a chat profile and embed a string with the fixed embedding model."""
    from codura_docs.ports.chat import ChatDelta, ChatMessage
    from codura_docs.registry import Registry

    async def run() -> None:
        registry = Registry.from_settings(get_settings())
        chosen = registry.seed_profiles[profile] if profile else registry.default_profile()
        chat = registry.chat(chosen)
        typer.echo(f"chat profile: {chosen.id} ({chosen.provider}:{chosen.model})")
        async for event in chat.stream([ChatMessage("user", prompt)], chosen.default_params()):
            if isinstance(event, ChatDelta):
                sys.stdout.write(event.text)
                sys.stdout.flush()
            else:
                typer.echo(
                    f"\n[done: {event.finish_reason}, tokens in/out: "
                    f"{event.usage.prompt_tokens}/{event.usage.completion_tokens}]"
                )
        embedding = registry.embedding()
        vectors = await embedding.embed(["hello world", "नमस्ते दुनिया"], "document")
        typer.echo(
            f"embedding: {embedding.model_id}, dimension {len(vectors[0])}, batch {len(vectors)}"
        )

    run_async(run())


@mail_app.command("test")
def mail_test(to: str) -> None:
    """Send a test email through the configured EmailSender (Mailpit locally)."""
    from codura_docs.ports.email import EmailMessage
    from codura_docs.registry import Registry

    async def run() -> None:
        registry = Registry.from_settings(get_settings())
        await registry.email().send(EmailMessage(to, "Codura Docs test email", "It works."))

    run_async(run())
    typer.echo(f"sent test email to {to}")


if __name__ == "__main__":
    app()
