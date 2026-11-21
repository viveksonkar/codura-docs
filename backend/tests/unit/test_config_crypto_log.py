from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import pytest

from codura_docs.config import ConfigError, Settings, load_model_config
from codura_docs.context import request_id_var
from codura_docs.crypto import DecryptionError, Keyring
from codura_docs.log import JsonFormatter, redact

KEY_A = base64.b64encode(b"a" * 32).decode()
KEY_B = base64.b64encode(b"b" * 32).decode()


def test_production_refuses_development_defaults() -> None:
    with pytest.raises(ConfigError) as exc:
        Settings(app_env="production").validate_for_production()
    message = str(exc.value)
    assert "SESSION_SECRET" in message
    assert "SECRETS_MASTER_KEY" in message
    assert "BOOTSTRAP_PLATFORM_ADMIN_PASSWORD" in message


def test_production_accepts_real_secrets() -> None:
    Settings(
        app_env="production",
        session_secret="s" * 40,
        secrets_master_key=KEY_A,
        bootstrap_platform_admin_password="a-strong-password",
        db_app_password="x1",
        db_worker_password="x2",
        db_platform_password="x3",
    ).validate_for_production()


def test_repo_model_config_is_valid_and_licensed() -> None:
    cfg = load_model_config(Path(__file__).parents[3] / "config" / "models.yaml")
    assert cfg.default_profile == "local-default"
    # Non-thinking instruct variant: the plain qwen3:4b tag always reasons first (ADR-0018).
    assert cfg.chat_profiles["local-default"].model == "qwen3:4b-instruct-2507-q4_K_M"
    assert cfg.chat_profiles["local-default"].disable_thinking is True
    assert cfg.embedding.model == "bge-m3"
    assert cfg.embedding.dimension == 1024


def test_model_config_rejects_unknown_default(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text(
        "default_profile: nope\nchat_profiles:\n  a: {provider: fake, model: x, license: mit}\n"
    )
    with pytest.raises(ConfigError):
        load_model_config(p)


def test_crypto_roundtrip_aad_and_rotation() -> None:
    old = Keyring.from_settings(Settings(secrets_master_key=KEY_A, secrets_key_version=1))
    blob = old.encrypt("sk-provider-key", aad=b"model_profiles:p1")
    assert blob[0] == 1
    assert old.decrypt_str(blob, aad=b"model_profiles:p1") == "sk-provider-key"
    with pytest.raises(DecryptionError):
        old.decrypt(blob, aad=b"other-row")
    rotated = Keyring.from_settings(
        Settings(
            secrets_master_key=KEY_B, secrets_key_version=2, secrets_previous_keys=f"1:{KEY_A}"
        )
    )
    assert rotated.decrypt_str(blob, aad=b"model_profiles:p1") == "sk-provider-key"
    assert rotated.encrypt("x")[0] == 2
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(DecryptionError):
        rotated.decrypt(tampered, aad=b"model_profiles:p1")


def test_crypto_rejects_short_keys() -> None:
    with pytest.raises(ConfigError):
        Keyring.from_settings(Settings(secrets_master_key=base64.b64encode(b"short").decode()))


def test_redaction_of_keys_tokens_and_passwords() -> None:
    line = "key=pk_live_ABCDEFGHIJKLMNOPQRSTUV auth Bearer abc.def.ghijkl password=hunter2"
    out = redact(line)
    assert "pk_live_ABCDEFGHIJKLMNOPQRSTUV" not in out
    assert "abc.def.ghijkl" not in out
    assert "hunter2" not in out


def test_json_formatter_includes_request_id_and_redacts_extras() -> None:
    token = request_id_var.set("req_test123")
    try:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        record.api_key = "sk_live_secret"
        record.attempt = 2
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert payload["msg"] == "hello world"
    assert payload["request_id"] == "req_test123"
    assert payload["api_key"] == "[REDACTED]"
    assert payload["attempt"] == 2
