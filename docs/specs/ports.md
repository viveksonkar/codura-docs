# Spec — Ports

**Status:** Draft v0.2 · **Last updated:** 2026-11-18 · **Frozen by:** M0-04 · **ADR:** 0004, 0008, 0009, 0015, 0016

Location: `backend/src/codura_docs/ports/`. Ports: `ChatModel`, `EmbeddingModel`, `Parser`,
`OcrEngine`, `BlobStore`, `Reranker`, `EmailSender`. (`PaymentProvider` is frozen in
`specs/billing.md`, M3.) Ports are `typing.Protocol`s plus frozen dataclasses /
Pydantic models. They import nothing outside the standard library and Pydantic.

## 1. Chat

```python
# ports/chat.py
Role = Literal["system", "user", "assistant"]

@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str

@dataclass(frozen=True)
class ChatParams:
    temperature: float = 0.1
    max_output_tokens: int = 800
    stop: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)   # provider-specific passthrough (e.g. num_ctx)

@dataclass(frozen=True)
class ChatDelta:
    text: str

@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None
    completion_tokens: int | None

@dataclass(frozen=True)
class ChatDone:
    finish_reason: Literal["stop", "length", "cancelled", "error"]
    usage: Usage
    model: str                       # provider-reported model id

ChatEvent = ChatDelta | ChatDone

class ChatModel(Protocol):
    @property
    def profile_name(self) -> str: ...
    @property
    def is_local(self) -> bool: ...                          # drives SEC-11 badge
    def stream(self, messages: Sequence[ChatMessage], params: ChatParams) -> AsyncIterator[ChatEvent]: ...
    async def complete(self, messages: Sequence[ChatMessage], params: ChatParams) -> tuple[str, ChatDone]: ...
    async def health(self) -> None: ...                      # raises ProviderUnavailable
```

Rules: `stream` yields zero or more `ChatDelta` then **exactly one** `ChatDone` last. Closing the
iterator (`aclose`) must cancel the upstream HTTP request. `complete` may be implemented on top of
`stream`.

## 2. Embeddings

```python
# ports/embedding.py
InputType = Literal["query", "document"]

class EmbeddingModel(Protocol):
    @property
    def provider(self) -> str: ...          # "ollama"
    @property
    def model_id(self) -> str: ...          # "bge-m3"
    @property
    def dimension(self) -> int: ...         # 1024
    @property
    def max_batch(self) -> int: ...
    @property
    def is_local(self) -> bool: ...
    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]: ...
    def count_tokens(self, text: str) -> int | None: ...     # None → caller estimates
    async def health(self) -> None: ...
```

Rules: returns `len(texts)` vectors, each `dimension` long, **L2-normalised**. The Adapter applies
any model-specific prefixes for `input_type` (none for bge-m3) and splits batches larger than
`max_batch`. There is exactly **one** embedding model per installation (ADR-0015). The registry
compares `(provider, model_id, dimension)` with `system_settings.embedding` at startup and aborts
on mismatch.

## 3. Parser

```python
# ports/parser.py
SectionKind = Literal["heading", "paragraph", "table", "list", "code"]

@dataclass(frozen=True)
class Section:
    text: str
    kind: SectionKind
    page: int | None                  # 1-based for paged formats
    heading_path: tuple[str, ...]
    ocr_confidence: float | None = None   # 0–100, set only for OCR'd text

@dataclass(frozen=True)
class ParsedDocument:
    title: str | None
    sections: tuple[Section, ...]
    page_count: int | None
    warnings: tuple[str, ...]          # e.g. "possibly_scanned:p3-7"

@dataclass(frozen=True)
class ParseOptions:
    ocr_languages: str                 # Tesseract spec, e.g. "eng+hin"
    ocr: "OcrEngine"                   # injected by the registry
    max_pages: int
    on_page_done: Callable[[int, int], None] | None = None   # (done, total) → progress

class Parser(Protocol):
    supported_mime_types: frozenset[str]
    def parse(self, data: BinaryIO, *, filename: str, mime_type: str, options: ParseOptions) -> ParsedDocument: ...
```

Parsers are synchronous (run in a worker thread/process with timeout). They raise
`UnsupportedDocument` (deterministic, no retry) or `ParseError`. The PDF and image parsers call
`options.ocr` for pages without a usable text layer.

## 3a. OCR (ADR-0016)

```python
# ports/ocr.py
@dataclass(frozen=True)
class OcrResult:
    text: str                          # lines kept, paragraphs separated by blank lines
    mean_confidence: float             # 0–100
    rotation_applied: int              # degrees

class OcrEngine(Protocol):
    @property
    def available_languages(self) -> frozenset[str]: ...      # installed packs, e.g. {"eng","hin","ara"}
    def recognize(self, image: "PIL.Image.Image", languages: str, *, timeout_s: float) -> OcrResult: ...
```

Rules: unknown language codes raise `UnsupportedDocument` before any work is done. Timeouts raise
`ParseError`. Default Adapter is `adapters/ocr/tesseract.py`. The fake returns fixture text.

## 4. Blob Store

```python
# ports/blob.py
class BlobStore(Protocol):
    async def put(self, key: str, data: AsyncIterator[bytes] | BinaryIO, *, content_type: str) -> int: ...  # bytes written
    async def open(self, key: str) -> AsyncIterator[bytes]: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...            # idempotent
```

Keys are produced only by `domain.blobs.blob_key(tenant_id, sha256)` → `"<tenant_id>/<aa>/<sha256>"`.
`BlobStore` methods take a `TenantContext` and refuse keys outside that Tenant's prefix.

## 5. Reranker

```python
# ports/rerank.py
class Reranker(Protocol):
    async def rerank(self, query: str, candidates: Sequence[str], top_n: int) -> list[tuple[int, float]]: ...  # (index, score) desc
```

`NoopReranker` returns input order. Real Adapters arrive in M4.

## 5b. Language detection (M1-16)

```python
# ports/language.py
SUPPORTED_LANGUAGES = {"en": ("English", "eng"), "hi": ("Hindi", "hin"), ...}  # launch set

@dataclass(frozen=True)
class Detection:
    language: str | None  # code from SUPPORTED_LANGUAGES, None when unsure
    confidence: float

class LanguageDetector(Protocol):
    def detect(self, text: str) -> Detection: ...
```

Adapters: `lingua` (default, restricted to the launch set), `fake` (script-based, tests). Added so that
services never import a detection library (ADR-0004 invariant).

## 5a. Email (ADR-0020)

```python
# ports/email.py
@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None

class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...   # raises ProviderUnavailable
```

Adapters: `smtp` (aiosmtplib; Mailpit locally, any SMTP relay in production), `fake`. Sending
happens from a Job (`send_email`) so request latency and SMTP failures are decoupled.

## 6. Errors

```python
# ports/errors.py
class ProviderError(Exception): provider: str
class ProviderUnavailable(ProviderError): ...    # connection refused, 5xx, model not pulled
class ProviderRateLimited(ProviderError): retry_after_s: float | None
class ProviderBadRequest(ProviderError): ...     # 4xx, context too long
class ProviderAuthError(ProviderError): ...
class UnsupportedDocument(Exception): reason: str
class ParseError(Exception): ...
```

Adapters must not leak vendor exception types.

## 7. Configuration & registry

```python
# config.py (pydantic-settings)
class ModelProfileSeed(BaseModel):        # seeds the platform-managed model_profiles table (ADR-0019)
    provider: Literal["ollama", "openai_compat", "anthropic", "fake"]
    model: str
    license: Literal["apache-2.0", "mit", "commercial-api"]   # allowlist enforced (ADR-0018)
    base_url: str | None = None
    api_key_env: str | None = None          # seed only; stored encrypted in DB, never in plain config
    temperature: float = 0.1
    max_output_tokens: int = 800
    disable_thinking: bool = False          # Qwen3 → Ollama `think: false`
    price_in_per_mtok_usd: float = 0.0      # COGS inputs (self-hosted: amortised GPU cost)
    price_out_per_mtok_usd: float = 0.0
    extra: dict[str, Any] = {}

class EmbeddingConfig(BaseModel):          # fixed for the installation (ADR-0015)
    provider: Literal["ollama", "openai_compat", "fake"] = "ollama"
    model: str = "bge-m3"
    dimension: Literal[1024] = 1024
    base_url: str | None = None
    api_key_env: str | None = None

class OcrConfig(BaseModel):
    engine: Literal["tesseract", "fake"] = "tesseract"
    text_layer_min_chars: int = 30
    low_confidence_threshold: float = 60
    page_timeout_s: float = 20
    render_dpi: int = 300

class ModelConfig(BaseModel):
    chat_profiles: dict[str, ModelProfileSeed] # first-boot seed; the DB is authoritative afterwards
    default_profile: str = "local-default"
    embedding: EmbeddingConfig                 # fixed
    ocr: OcrConfig
```

```python
# registry.py — the composition root
class Registry:
    def chat(self, profile: ModelProfile) -> ChatModel: ...  # built from a DB row (decrypted creds); cached by (id, updated_at)
    def email(self) -> EmailSender: ...
    def embedding(self) -> EmbeddingModel: ...              # the one fixed model; guarded at startup
    def ocr(self) -> OcrEngine: ...
    def parser_for(self, mime_type: str) -> Parser: ...
    def blob_store(self) -> BlobStore: ...
    def reranker(self, name: str | None) -> Reranker: ...
```

Adapter modules register via a dict in `registry.py` (`{"ollama": OllamaChatModel, …}`) — no
entry-point plugin magic.

## 8. Adapter checklist

1. Lives in `adapters/<provider>/`; only file tree allowed to import the vendor SDK/HTTP API.
2. Passes `tests/contract/` suites for every Port it implements.
3. Maps errors to §6; sets `is_local` truthfully.
4. Documented in [architecture.md](../architecture.md) §4 table and `.env.example`.
