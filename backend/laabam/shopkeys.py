"""Per-shop keys for the audit log (security plan C3).

The audit log is append-only, so it must never hold raw content (transcripts, printed bill lines, spoken fixes):
it holds an HMAC-SHA256 of the text under the shop's own key instead. Equal texts give equal digests, so the log
still shows "the same words as the bill's transcript" without saying them. Deleting the shop deletes its key, and
from then on its digests can't be checked against anything (crypto-shredding).

Each shop's 256-bit key is created on first use and stored only wrapped (envelope encryption): by AWS KMS in the
deployed stacks (GenerateDataKey / Decrypt, encryption context = the shop id), by a local AES-GCM key in
development (config refuses that outside dev). Unwrapped keys are cached in memory, never written down.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from .models import ShopKey, utcnow

DEV_MASTER = hashlib.sha256(b"laabam development audit key - not a secret").digest()


class LocalKeyring:
    """Development: AES-256-GCM with a local wrapping key."""

    name = "local"

    def __init__(self, master: bytes = DEV_MASTER) -> None:
        self._aead = AESGCM(master)

    def new_key(self, merchant_id: str) -> tuple[bytes, bytes]:
        key = os.urandom(32)
        nonce = os.urandom(12)
        return key, nonce + self._aead.encrypt(nonce, key, merchant_id.encode())

    def unwrap(self, merchant_id: str, wrapped: bytes) -> bytes:
        return self._aead.decrypt(wrapped[:12], wrapped[12:], merchant_id.encode())


class KmsKeyring:
    """AWS KMS: a data key per shop, wrapped by the customer-managed key; the shop id is the encryption context,
    so a wrapped key can't be unwrapped as another shop's."""

    name = "kms"

    def __init__(self, key_id: str, client=None) -> None:
        if client is None:
            import boto3
            client = boto3.client("kms")
        self.client, self.key_id = client, key_id

    def new_key(self, merchant_id: str) -> tuple[bytes, bytes]:
        out = self.client.generate_data_key(KeyId=self.key_id, KeySpec="AES_256",
                                            EncryptionContext={"shop": merchant_id})
        return out["Plaintext"], out["CiphertextBlob"]

    def unwrap(self, merchant_id: str, wrapped: bytes) -> bytes:
        return self.client.decrypt(CiphertextBlob=wrapped, KeyId=self.key_id,
                                   EncryptionContext={"shop": merchant_id})["Plaintext"]


class ShopKeys:
    def __init__(self, keyring) -> None:
        self.keyring = keyring
        self._cache: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def key(self, s: Session, merchant_id: str) -> bytes:
        with self._lock:
            if merchant_id in self._cache:
                return self._cache[merchant_id]
        row = s.get(ShopKey, merchant_id)
        if row is None:
            key, wrapped = self.keyring.new_key(merchant_id)
            s.add(ShopKey(merchant_id=merchant_id, wrapped=wrapped, keyring=self.keyring.name, created_at=utcnow()))
            s.flush()
        else:
            key = self.keyring.unwrap(merchant_id, row.wrapped)
        with self._lock:
            if len(self._cache) > 5000:
                self._cache.clear()
            self._cache[merchant_id] = key
        return key

    def digest(self, s: Session, merchant_id: str, text: str) -> str:
        mac = hmac.new(self.key(s, merchant_id), text.encode(), hashlib.sha256).hexdigest()
        return f"hmac-sha256:{mac}"

    def forget(self, merchant_id: str) -> None:
        with self._lock:
            self._cache.pop(merchant_id, None)


def make_keyring(cfg):
    """KMS when a key is configured (config requires one outside development), the local keyring otherwise."""
    return KmsKeyring(cfg.audit_kms_key_id) if cfg.audit_kms_key_id else LocalKeyring()
