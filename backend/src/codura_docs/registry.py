"""Composition root: the ONLY module that knows which Adapter backs which Port (ADR-0004).

Also enforces the model licence allowlist (ADR-0018).
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

from codura_docs.config import ConfigError, ModelConfig, ModelProfileSeed, Settings, env_secret
from codura_docs.ports.blob import BlobStore
from codura_docs.ports.chat import ChatModel, ModelProfile
from codura_docs.ports.email import EmailSender
from codura_docs.ports.embedding import EmbeddingModel
from codura_docs.ports.errors import UnsupportedDocument
from codura_docs.ports.language import LanguageDetector
from codura_docs.ports.ocr import OcrEngine
from codura_docs.ports.parser import Parser
from codura_docs.ports.rerank import Reranker

SELF_HOSTED_LICENSES = frozenset({"apache-2.0", "mit"})
_MICROS = 1_000_000


def is_local_url(url: str | None) -> bool:
    """Localhost, private/loopback IPs and single-label docker service names count as local."""
    if not url:
        return False
    host = urlparse(url).hostname or ""
    if host in {"localhost", "host.docker.internal"} or "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def validate_profile_license(profile: ModelProfile) -> None:
    """Self-hosted models must be Apache-2.0/MIT; ``commercial-api`` only for remote APIs."""
    if profile.provider == "fake":
        return
    if profile.license == "commercial-api":
        if profile.provider == "ollama" or profile.is_local:
            raise ConfigError(
                f"profile {profile.id!r}: 'commercial-api' licence is only valid for remote API providers"
            )
        return
    if profile.license not in SELF_HOSTED_LICENSES:
        raise ConfigError(
            f"profile {profile.id!r}: licence {profile.license!r} is not on the allowlist"
        )


def profile_from_seed(profile_id: str, seed: ModelProfileSeed, settings: Settings) -> ModelProfile:
    base_url = seed.base_url or (settings.ollama_base_url if seed.provider == "ollama" else None)
    is_local = seed.provider in {"ollama", "fake"} or is_local_url(base_url)
    profile = ModelProfile(
        id=profile_id,
        provider=seed.provider,
        model=seed.model,
        license=seed.license,
        is_local=is_local,
        base_url=base_url,
        api_key=env_secret(seed.api_key_env),
        temperature=seed.temperature,
        max_output_tokens=seed.max_output_tokens,
        disable_thinking=seed.disable_thinking,
        price_in_per_mtok_micros=round(seed.price_in_per_mtok_usd * _MICROS),
        price_out_per_mtok_micros=round(seed.price_out_per_mtok_usd * _MICROS),
        extra=dict(seed.extra),
    )
    validate_profile_license(profile)
    return profile


class Registry:
    def __init__(
        self, settings: Settings, models: ModelConfig, *, overrides: dict[str, Any] | None = None
    ) -> None:
        self.settings = settings
        self.models = models
        self._overrides = overrides or {}
        self._chat_cache: dict[tuple[str, int], ChatModel] = {}
        self._embedding: EmbeddingModel | None = None
        self._blob: BlobStore | None = None
        self._email: EmailSender | None = None
        self._ocr: OcrEngine | None = None
        self._language: LanguageDetector | None = None
        self.seed_profiles: dict[str, ModelProfile] = {
            pid: profile_from_seed(pid, seed, settings)
            for pid, seed in models.chat_profiles.items()
        }

    @classmethod
    def from_settings(cls, settings: Settings) -> Registry:
        return cls(settings, settings.load_models())

    # --- Chat (platform-managed Model Profiles, ADR-0019) -------------------------------------
    def default_profile(self) -> ModelProfile:
        return self.seed_profiles[self.models.default_profile]

    def chat(self, profile: ModelProfile) -> ChatModel:
        if "chat" in self._overrides:
            return self._overrides["chat"]  # type: ignore[no-any-return]
        validate_profile_license(profile)
        key = (profile.id, hash(repr(sorted(vars(profile).items()))))
        cached = self._chat_cache.get(key)
        if cached is not None:
            return cached
        model: ChatModel
        if profile.provider == "ollama":
            from codura_docs.adapters.ollama.chat import OllamaChatModel

            model = OllamaChatModel(profile)
        elif profile.provider == "openai_compat":
            from codura_docs.adapters.openai_compat.chat import OpenAICompatChatModel

            model = OpenAICompatChatModel(profile)
        elif profile.provider == "fake":
            from codura_docs.adapters.fake.chat import FakeChatModel

            model = FakeChatModel(profile.id)
        else:
            raise ConfigError(f"chat provider {profile.provider!r} is not available yet")
        self._chat_cache[key] = model
        return model

    # --- Embeddings (fixed, ADR-0015) ----------------------------------------------------------
    def embedding(self) -> EmbeddingModel:
        if "embedding" in self._overrides:
            return self._overrides["embedding"]  # type: ignore[no-any-return]
        if self._embedding is None:
            cfg = self.models.embedding
            if cfg.provider == "ollama":
                from codura_docs.adapters.ollama.embedding import OllamaEmbeddingModel

                self._embedding = OllamaEmbeddingModel(
                    cfg.base_url or self.settings.ollama_base_url, cfg.model, cfg.dimension
                )
            elif cfg.provider == "fake":
                from codura_docs.adapters.fake.embedding import FakeEmbeddingModel

                self._embedding = FakeEmbeddingModel(cfg.dimension, cfg.model)
            else:
                raise ConfigError(f"embedding provider {cfg.provider!r} is not available yet")
        return self._embedding

    # --- OCR / parsers ------------------------------------------------------------------------
    def ocr(self) -> OcrEngine:
        if "ocr" in self._overrides:
            return self._overrides["ocr"]  # type: ignore[no-any-return]
        if self._ocr is None:
            if self.models.ocr.engine == "fake":
                from codura_docs.adapters.fake.ocr import FakeOcrEngine

                self._ocr = FakeOcrEngine()
            else:
                try:
                    from codura_docs.adapters.ocr.tesseract import TesseractOcrEngine
                except ImportError as exc:  # pragma: no cover - depends on image build
                    raise ConfigError("tesseract OCR adapter is not installed") from exc
                self._ocr = TesseractOcrEngine()
        return self._ocr

    def parser_for(self, mime_type: str) -> Parser:
        parsers: dict[str, Parser] = self._overrides.get("parsers", {}) or self._load_parsers()
        parser = parsers.get(mime_type)
        if parser is None:
            raise UnsupportedDocument(f"unsupported document type: {mime_type}")
        return parser

    def _load_parsers(self) -> dict[str, Parser]:
        try:
            from codura_docs.adapters.parsers import all_parsers
        except ImportError:
            return {}
        return {mime: p for p in all_parsers() for mime in p.supported_mime_types}

    # --- Infrastructure Ports ------------------------------------------------------------------
    def blob_store(self) -> BlobStore:
        if "blob" in self._overrides:
            return self._overrides["blob"]  # type: ignore[no-any-return]
        if self._blob is None:
            s = self.settings
            if s.blob_store == "local":
                from codura_docs.adapters.blob.local import LocalBlobStore

                self._blob = LocalBlobStore(s.blob_local_path)
            elif s.blob_store == "s3":
                from codura_docs.adapters.blob.s3 import S3BlobStore

                if not s.s3_bucket:
                    raise ConfigError("BLOB_STORE=s3 requires S3_BUCKET")
                self._blob = S3BlobStore(
                    s.s3_bucket,
                    endpoint_url=s.s3_endpoint,
                    region=s.s3_region,
                    access_key=s.s3_access_key.get_secret_value() if s.s3_access_key else None,
                    secret_key=s.s3_secret_key.get_secret_value() if s.s3_secret_key else None,
                )
            else:
                from codura_docs.adapters.fake.blob import InMemoryBlobStore

                self._blob = InMemoryBlobStore()
        return self._blob

    def email(self) -> EmailSender:
        if "email" in self._overrides:
            return self._overrides["email"]  # type: ignore[no-any-return]
        if self._email is None:
            s = self.settings
            if s.email_backend == "fake":
                from codura_docs.adapters.fake.email import FakeEmailSender

                self._email = FakeEmailSender()
            else:
                from codura_docs.adapters.email.smtp import SmtpEmailSender

                self._email = SmtpEmailSender(
                    s.smtp_host,
                    s.smtp_port,
                    s.mail_from,
                    # Compose passes empty strings for unset values: treat them as "no login".
                    username=s.smtp_user or None,
                    password=(s.smtp_password.get_secret_value() or None)
                    if s.smtp_password
                    else None,
                    starttls=s.smtp_starttls,
                )
        return self._email

    def language(self) -> LanguageDetector:
        if "language" in self._overrides:
            return self._overrides["language"]  # type: ignore[no-any-return]
        if self._language is None:
            if self.settings.language_detector == "fake":
                from codura_docs.adapters.fake.language import FakeLanguageDetector

                self._language = FakeLanguageDetector()
            else:
                from codura_docs.adapters.langdetect.lingua_detector import LinguaDetector

                self._language = LinguaDetector()
        return self._language

    def reranker(self, name: str | None = None) -> Reranker:
        from codura_docs.adapters.rerank.noop import NoopReranker

        if name not in (None, "none", "noop"):
            raise ConfigError(f"reranker {name!r} is not available yet")
        return NoopReranker()
