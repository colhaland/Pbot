import base64
import hashlib
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.settings import get_settings


class EncryptionError(ValueError):
    pass


def _key_bytes() -> bytes:
    encoded = get_settings().encryption_master_key
    if encoded is None:
        raise EncryptionError("ENCRYPTION_KEY_UNAVAILABLE")
    value = encoded.get_secret_value()
    try:
        key = bytes.fromhex(value) if len(value) == 64 else base64.urlsafe_b64decode(value)
    except (ValueError, TypeError) as error:
        raise EncryptionError("ENCRYPTION_KEY_INVALID") from error
    if len(key) != 32:
        raise EncryptionError("ENCRYPTION_KEY_INVALID")
    return key


def encrypt_secret(plaintext: str, *, table: str, record_id: str) -> bytes:
    settings = get_settings()
    nonce = secrets.token_bytes(12)
    aad = f"{table}:{record_id}:{settings.encryption_key_version}".encode()
    ciphertext = AESGCM(_key_bytes()).encrypt(nonce, plaintext.encode(), aad)
    return settings.encryption_key_version.to_bytes(4, "big") + nonce + ciphertext


def decrypt_secret(value: bytes, *, table: str, record_id: str) -> str:
    if len(value) < 33:
        raise EncryptionError("CIPHERTEXT_INVALID")
    version = int.from_bytes(value[:4], "big")
    settings = get_settings()
    if version != settings.encryption_key_version:
        raise EncryptionError("KEY_VERSION_UNKNOWN")
    aad = f"{table}:{record_id}:{version}".encode()
    try:
        return AESGCM(_key_bytes()).decrypt(value[4:16], value[16:], aad).decode()
    except Exception as error:
        raise EncryptionError("DECRYPTION_FAILED") from error


def sha256_hex(value: bytes | str) -> str:
    content = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(content).hexdigest()