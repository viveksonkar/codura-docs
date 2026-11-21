from __future__ import annotations

import hashlib

import pytest

from codura_docs.config import ConfigError, ModelProfileSeed, Settings
from codura_docs.domain.blobs import blob_key
from codura_docs.ids import is_id, new_id
from codura_docs.ports.blob import BlobKeyError, check_blob_key
from codura_docs.ports.chat import ModelProfile
from codura_docs.registry import Registry, is_local_url, profile_from_seed, validate_profile_license
from codura_docs.tenancy import TenantContext


def test_ids_are_prefixed_and_validated() -> None:
    tid = new_id("tnt")
    assert is_id("tnt", tid)
    assert not is_id("usr", tid)
    assert not is_id("tnt", "tnt_../../etc")
    with pytest.raises(ValueError, match="unknown"):
        new_id("nope")


def test_tenant_context_requires_valid_id() -> None:
    with pytest.raises(ValueError, match="tenant id"):
        TenantContext("anything")


def test_blob_key_is_tenant_prefixed_and_checked() -> None:
    a, b = TenantContext(new_id("tnt")), TenantContext(new_id("tnt"))
    digest = hashlib.sha256(b"doc").hexdigest()
    key = blob_key(a.tenant_id, digest)
    assert key == f"{a.tenant_id}/{digest[:2]}/{digest}"
    check_blob_key(a, key)
    with pytest.raises(BlobKeyError, match="another tenant"):
        check_blob_key(b, key)
    for bad in ("../x", f"{a.tenant_id}/zz/{digest}", f"{a.tenant_id}/{digest[:2]}/{digest}/.."):
        with pytest.raises(BlobKeyError):
            check_blob_key(a, bad)
    with pytest.raises(ValueError, match="sha256"):
        blob_key(a.tenant_id, "ABC")


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://ollama:11434", True),
        ("http://localhost:1234/v1", True),
        ("http://192.168.1.20:8000/v1", True),
        ("https://api.openai.com/v1", False),
        ("https://8.8.8.8/v1", False),
        (None, False),
    ],
)
def test_is_local_url(url: str | None, local: bool) -> None:
    assert is_local_url(url) is local


def test_licence_allowlist_for_self_hosted_models() -> None:
    settings = Settings()
    ok = profile_from_seed(
        "q", ModelProfileSeed(provider="ollama", model="qwen3:4b", license="apache-2.0"), settings
    )
    assert ok.is_local
    assert ok.base_url == settings.ollama_base_url
    with pytest.raises(ConfigError, match="only valid for remote"):
        profile_from_seed(
            "bad",
            ModelProfileSeed(provider="ollama", model="x", license="commercial-api"),
            settings,
        )
    remote = ModelProfile(
        id="premium", provider="anthropic", model="m", license="commercial-api", is_local=False
    )
    validate_profile_license(remote)


def test_registry_builds_fakes_and_caches_chat(registry: Registry) -> None:
    profile = registry.default_profile()
    assert registry.chat(profile) is registry.chat(profile)
    assert registry.embedding().dimension == 1024
    assert registry.ocr().available_languages
    assert registry.reranker() is not None
    with pytest.raises(ConfigError):
        registry.reranker("cross-encoder")


def test_seed_prices_converted_to_micros() -> None:
    seed = ModelProfileSeed(
        provider="fake",
        model="m",
        license="mit",
        price_in_per_mtok_usd=0.15,
        price_out_per_mtok_usd=0.6,
    )
    profile = profile_from_seed("p", seed, Settings())
    assert profile.price_in_per_mtok_micros == 150_000
    assert profile.price_out_per_mtok_micros == 600_000
