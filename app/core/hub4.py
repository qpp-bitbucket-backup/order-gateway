"""HUB4 standard transport helpers (Baozun OMS).

Implements the encryption and signature scheme defined in HUB4标准.docx:
- Business JSON is AES-CBC encrypted with key = base64(MD5(appSecret)) bytes
  (24 bytes -> AES-192), a zero IV and PKCS7 padding, then base64 encoded.
- sign = uppercase hex MD5 of: appSecret + sorted(key+value params) + body + appSecret.
- Signature, request time, version etc. are carried as URL query parameters.
"""
import base64
import hashlib
from datetime import datetime, timezone
from typing import Dict

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

# 16 zero bytes, per the HUB4 reference implementation (AES_IV)
_AES_IV = bytes(16)


def _derive_key(app_secret: str) -> bytes:
    """Derive the AES key: base64 encoding of the MD5 digest of the secret (24 bytes -> AES-192)."""
    md5_digest = hashlib.md5(app_secret.encode("utf-8")).digest()
    return base64.b64encode(md5_digest)


def aes_encrypt(plaintext: str, app_secret: str) -> str:
    """Encrypt plaintext to a base64 ciphertext string (AES-CBC, PKCS7)."""
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()

    cipher = Cipher(algorithms.AES(_derive_key(app_secret)), modes.CBC(_AES_IV))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("utf-8")


def aes_decrypt(ciphertext: str, app_secret: str) -> str:
    """Decrypt a base64 ciphertext string back to plaintext."""
    encrypted = base64.b64decode(ciphertext)

    cipher = Cipher(algorithms.AES(_derive_key(app_secret)), modes.CBC(_AES_IV))
    decryptor = cipher.decryptor()
    padded = decryptor.update(encrypted) + decryptor.finalize()

    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return plaintext.decode("utf-8")


def make_sign(params: Dict[str, str], body: str, app_secret: str) -> str:
    """
    Build the HUB4 signature.

    Steps (per HUB4标准 3.4.3):
    1. Sort parameter names alphabetically.
    2. Concatenate name+value pairs.
    3. Wrap with appSecret at head and tail, append the (encrypted) body before the tail secret.
    4. MD5 and convert to uppercase hex.
    """
    query = app_secret
    for key in sorted(params.keys()):
        value = params[key]
        if key and value:
            query += f"{key}{value}"
    if body:
        query += body
    query += app_secret

    return hashlib.md5(query.encode("utf-8")).hexdigest().upper()


def build_query_params(method_name: str, source_app: str, interface_type: str) -> Dict[str, str]:
    """Build the unsigned HUB4 URL parameters (sign is appended separately)."""
    return {
        "requestTime": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        "version": "1.0",
        "methodName": method_name,
        "sourceApp": source_app,
        "interfaceType": interface_type,
    }
