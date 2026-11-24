"""SEC-05/06 building blocks: token buckets, concurrency caps, origin rules (M2-01, M2-02)."""

from __future__ import annotations

import pytest

from codura_docs.api.auth.public import origin_allowed
from codura_docs.config import Settings
from codura_docs.ratelimit import InMemoryConcurrencyLimiter, InMemoryRateLimiter
from codura_docs.services.channel_keys import (
    KeyServiceError,
    ResolvedKey,
    normalize_origin,
    normalize_origins,
)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_token_bucket_refills_and_reports_retry_after() -> None:
    clock = Clock()
    rl = InMemoryRateLimiter(clock=clock)
    assert all(rl.hit("k", per_minute=3).allowed for _ in range(3))
    denied = rl.hit("k", per_minute=3)
    assert not denied.allowed and denied.retry_after_s == 20  # 3/min → one token per 20 s
    clock.t += 20
    assert rl.hit("k", per_minute=3).allowed
    assert rl.hit("disabled", per_minute=0).allowed


def test_tenant_a_saturating_does_not_block_tenant_b() -> None:
    rl = InMemoryRateLimiter(clock=Clock())
    for _ in range(5):
        rl.hit("tenant:a", per_minute=5)
    assert not rl.hit("tenant:a", per_minute=5).allowed
    assert rl.hit("tenant:b", per_minute=5).allowed

    cc = InMemoryConcurrencyLimiter()
    a_slots = [("key:a1", 4), ("tenant:a", 2)]
    assert cc.acquire(a_slots) and cc.acquire(a_slots)
    assert not cc.acquire(a_slots)  # Tenant A at its cap
    assert cc.active("key:a1") == 2  # all-or-nothing: the refused attempt took nothing
    assert cc.acquire([("key:b1", 4), ("tenant:b", 2)])  # Tenant B unaffected
    cc.release(a_slots)
    assert cc.acquire(a_slots)


def test_bucket_table_is_bounded() -> None:
    rl = InMemoryRateLimiter(clock=Clock(), max_buckets=10)
    for i in range(50):
        rl.hit(f"ip:{i}", per_minute=5)
    assert len(rl._buckets) <= 10


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Shop.Example.com/", "https://shop.example.com"),
        ("https://shop.example.com:443", "https://shop.example.com"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("http://127.0.0.1:5174", "http://127.0.0.1:5174"),
    ],
)
def test_normalize_origin(raw: str, expected: str) -> None:
    assert normalize_origin(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "*",
        "shop.example.com",
        "https://shop.example.com/path",
        "javascript:alert(1)",
        "ftp://x.com",
        "https://",
    ],
)
def test_normalize_origin_rejects(bad: str) -> None:
    with pytest.raises(KeyServiceError):
        normalize_origin(bad)


def test_normalize_origins_dedupes_and_caps() -> None:
    assert normalize_origins(["https://a.com", "https://A.com/"]) == ["https://a.com"]
    with pytest.raises(KeyServiceError):
        normalize_origins([f"https://a{i}.com" for i in range(21)])


def _key(channel: str, origins: list[str] | None = None) -> ResolvedKey:
    return ResolvedKey(
        "key_1", "tnt_1", "channel", channel, "asst_1", origins or [], False, "active"
    )


def test_origin_rules_per_channel() -> None:
    dev = Settings(app_env="test", public_base_url="http://localhost:8080")
    prod = dev.model_copy(update={"app_env": "production"})
    shop = "https://shop.example.com"
    # No Origin header: not a browser; SEC-06 limits apply instead.
    for ch in ("hosted", "widget", "wordpress", "api", "android"):
        assert origin_allowed(_key(ch), None, prod)
    # Hosted keys: only the platform's own origin.
    assert origin_allowed(_key("hosted"), "http://localhost:8080", prod)
    assert not origin_allowed(_key("hosted"), shop, prod)
    # Widget/WordPress: Allowed Origins only, fail closed when empty.
    assert origin_allowed(_key("widget", [shop]), shop, prod)
    assert origin_allowed(_key("wordpress", [shop]), "https://SHOP.example.com", prod)
    assert not origin_allowed(_key("widget", [shop]), "https://evil.example", prod)
    assert not origin_allowed(_key("widget"), shop, prod)
    # The dev-only demo page on the platform origin, never in production.
    assert origin_allowed(_key("widget"), "http://localhost:8080", dev)
    assert not origin_allowed(_key("widget"), "http://localhost:8080", prod)
    # api / android keys are not for browsers.
    assert not origin_allowed(_key("api"), shop, dev)
    assert not origin_allowed(_key("android"), "http://localhost:8080", dev)
    assert not origin_allowed(_key("widget", [shop]), "null", prod)
