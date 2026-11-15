"""The owner's phone number, as stored: an HMAC-SHA256, never the number itself (beta sign-up).

Sign-in looks a shop up by this value, so it must be the same every time for a number: a keyed hash, not a random
salt. The key never leaves AWS KMS in the deployed stacks (an HMAC_256 key, GenerateMac); development uses a local
key that config refuses outside dev. Deleting the shop deletes its row, so the number can sign up again afresh.
"""
from __future__ import annotations

import hashlib
import hmac
import re

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
DEV_KEY = hashlib.sha256(b"laabam development phone key - not a secret").digest()


class BadPhone(ValueError):
    pass


def normalise(phone: str, prefixes: tuple[str, ...]) -> str:
    """E.164 (+919876543210). Only numbers under the allowed country prefixes."""
    p = re.sub(r"[\s\-()]", "", phone or "")
    if not E164.match(p):
        raise BadPhone("not an E.164 number")
    if prefixes and not p.startswith(prefixes):
        raise BadPhone("country not allowed")
    return p


class LocalPhoneHasher:
    name = "local"

    def __init__(self, key: bytes = DEV_KEY) -> None:
        self.key = key

    def hash(self, phone: str) -> str:
        return hmac.new(self.key, phone.encode(), hashlib.sha256).hexdigest()


class KmsPhoneHasher:
    name = "kms"

    def __init__(self, key_id: str, client=None) -> None:
        if client is None:
            import boto3
            client = boto3.client("kms")
        self.client, self.key_id = client, key_id

    def hash(self, phone: str) -> str:
        out = self.client.generate_mac(KeyId=self.key_id, Message=phone.encode(), MacAlgorithm="HMAC_SHA_256")
        return out["Mac"].hex()


def make_hasher(cfg):
    return KmsPhoneHasher(cfg.phone_hash_kms_key_id) if cfg.phone_hash_kms_key_id else LocalPhoneHasher()
