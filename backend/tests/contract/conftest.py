"""Port contract fixtures: every Adapter of a Port runs the same suite (testing.md §2).

Live adapters are opt-in:
- ``CODURA_TEST_OLLAMA_URL`` (e.g. http://localhost:11434) with ``qwen3:4b`` + ``bge-m3`` pulled
- ``CODURA_TEST_SMTP_HOST`` / ``CODURA_TEST_SMTP_PORT`` for a Mailpit
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from codura_docs.adapters.blob.local import LocalBlobStore
from codura_docs.adapters.email.smtp import SmtpEmailSender
from codura_docs.adapters.fake.blob import InMemoryBlobStore
from codura_docs.adapters.fake.chat import FakeChatModel
from codura_docs.adapters.fake.email import FakeEmailSender
from codura_docs.adapters.fake.embedding import FakeEmbeddingModel
from codura_docs.adapters.fake.ocr import FakeOcrEngine
from codura_docs.ports.chat import ChatModel, ModelProfile

OLLAMA_URL = os.environ.get("CODURA_TEST_OLLAMA_URL")
SMTP_HOST = os.environ.get("CODURA_TEST_SMTP_HOST")


def _needs(value: str | None, reason: str) -> Any:
    return pytest.mark.skipif(not value, reason=reason)


@pytest.fixture(
    params=[
        "fake",
        pytest.param(
            "ollama", marks=[pytest.mark.ollama, _needs(OLLAMA_URL, "set CODURA_TEST_OLLAMA_URL")]
        ),
    ]
)
async def chat_model(request: pytest.FixtureRequest) -> AsyncIterator[ChatModel]:
    if request.param == "fake":
        yield FakeChatModel("fake")
        return
    from codura_docs.adapters.ollama.chat import OllamaChatModel

    model = OllamaChatModel(
        ModelProfile(
            id="local-default",
            provider="ollama",
            model=os.environ.get("CODURA_TEST_OLLAMA_CHAT_MODEL", "qwen3:4b"),
            license="apache-2.0",
            is_local=True,
            base_url=OLLAMA_URL,
            disable_thinking=True,
            extra={"num_ctx": 4096},
        )
    )
    yield model
    await model.aclose()


@pytest.fixture(
    params=[
        "fake",
        pytest.param(
            "ollama", marks=[pytest.mark.ollama, _needs(OLLAMA_URL, "set CODURA_TEST_OLLAMA_URL")]
        ),
    ]
)
async def embedding_model(request: pytest.FixtureRequest) -> AsyncIterator[Any]:
    if request.param == "fake":
        yield FakeEmbeddingModel()
        return
    from codura_docs.adapters.ollama.embedding import OllamaEmbeddingModel

    model = OllamaEmbeddingModel(str(OLLAMA_URL), "bge-m3", 1024)
    yield model
    await model.aclose()


@pytest.fixture(params=["memory", "local", "s3"])
def blob_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    if request.param == "memory":
        yield InMemoryBlobStore()
    elif request.param == "local":
        yield LocalBlobStore(tmp_path / "blobs")
    else:
        import boto3
        from moto import mock_aws

        from codura_docs.adapters.blob.s3 import S3BlobStore

        with mock_aws():
            client = boto3.client(
                "s3",
                region_name="us-east-1",
                aws_access_key_id="test",
                aws_secret_access_key="test",
            )
            client.create_bucket(Bucket="codura-test")
            yield S3BlobStore("codura-test", client=client)


@pytest.fixture(
    params=[
        "fake",
        pytest.param(
            "smtp", marks=[pytest.mark.mailpit, _needs(SMTP_HOST, "set CODURA_TEST_SMTP_HOST")]
        ),
    ]
)
def email_sender(request: pytest.FixtureRequest) -> Any:
    if request.param == "fake":
        return FakeEmailSender()
    return SmtpEmailSender(
        str(SMTP_HOST), int(os.environ.get("CODURA_TEST_SMTP_PORT", "1025")), "test@codura.local"
    )


@pytest.fixture(params=["fake"])
def ocr_engine(request: pytest.FixtureRequest) -> Any:
    return FakeOcrEngine()
