"""AES-256-GCM envelope encryption for secrets and PII at rest (SEC-17, SEC-19).

Ciphertext format: ``[1 byte key version][12 byte nonce][ciphertext+tag]``. The key version lets
old data be decrypted after rotating ``SECRETS_MASTER_KEY`` (keep the old key in
``SECRETS_PREVIOUS_KEYS``).
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from codura_docs.config import ConfigError, Settings

_NONCE_LEN = 12


class DecryptionError(Exception):
    """Ciphertext is corrupt, was tampered with, or its key is unknown."""


def _decode_key(b64: str) -> bytes:
    try:
        key = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigError("encryption key is not valid base64") from exc
    if len(key) != 32:
        raise ConfigError("encryption key must decode to exactly 32 bytes (AES-256)")
    return key


class Keyring:
    def __init__(self, current_version: int, keys: dict[int, bytes]) -> None:
        if current_version not in keys:
            raise ConfigError("current key version missing from keyring")
        self._current = current_version
        self._keys = {v: AESGCM(k) for v, k in keys.items()}

    @classmethod
    def from_settings(cls, settings: Settings) -> Keyring:
        keys = {
            settings.secrets_key_version: _decode_key(
                settings.secrets_master_key.get_secret_value()
            )
        }
        for item in filter(None, (p.strip() for p in settings.secrets_previous_keys.split(","))):
            version_s, _, b64 = item.partition(":")
            try:
                version = int(version_s)
            except ValueError as exc:
                raise ConfigError(
                    "SECRETS_PREVIOUS_KEYS must look like '1:<base64>,2:<base64>'"
                ) from exc
            if version in keys:
                raise ConfigError(f"duplicate key version {version}")
            keys[version] = _decode_key(b64)
        return cls(settings.secrets_key_version, keys)

    def encrypt(self, plaintext: bytes | str, aad: bytes = b"") -> bytes:
        data = plaintext.encode() if isinstance(plaintext, str) else plaintext
        nonce = os.urandom(_NONCE_LEN)
        ct = self._keys[self._current].encrypt(nonce, data, aad)
        return bytes([self._current]) + nonce + ct

    def decrypt(self, blob: bytes, aad: bytes = b"") -> bytes:
        if len(blob) < 1 + _NONCE_LEN + 16:
            raise DecryptionError("ciphertext too short")
        aead = self._keys.get(blob[0])
        if aead is None:
            raise DecryptionError(f"unknown key version {blob[0]}")
        try:
            return aead.decrypt(blob[1 : 1 + _NONCE_LEN], blob[1 + _NONCE_LEN :], aad)
        except InvalidTag as exc:
            raise DecryptionError("authentication failed") from exc

    def decrypt_str(self, blob: bytes, aad: bytes = b"") -> str:
        return self.decrypt(blob, aad).decode()
