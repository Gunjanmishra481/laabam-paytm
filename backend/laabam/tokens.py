"""Owner access tokens (security plan §1): ES256 JWTs, 15 minutes, signed by a key that never leaves AWS KMS in
the deployed stacks. Development and tests sign with a local P-256 key (generated per process unless one is
configured), which is refused in production by config.validate().

Header: alg ES256, typ JWT, kid. Claims: iss, aud, sub (shop), sid (device session), iat, exp, jti.
Verification takes ES256 only (no 'none', no HMAC), by kid, and the session is checked in the database on every
request, so a revoked device stops at its next call.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

ISSUER = "laabam"
AUDIENCE = "laabam"
ALG = "ES256"


class TokenError(Exception):
    pass


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def der_to_raw(der: bytes) -> bytes:
    """ECDSA DER signature (KMS) -> JWS raw r||s (64 bytes for P-256)."""
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


class LocalSigner:
    """A P-256 key in this process. For development and tests only."""

    name = "local"

    def __init__(self, pem: str | None = None) -> None:
        if pem:
            self._key = serialization.load_pem_private_key(pem.encode(), password=None)
            if not isinstance(self._key, ec.EllipticCurvePrivateKey) or self._key.curve.name != "secp256r1":
                raise ValueError("token key must be EC P-256")
        else:
            self._key = ec.generate_private_key(ec.SECP256R1())
        pub = self.public_key().public_bytes(serialization.Encoding.DER,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)
        self.kid = "local-" + hashlib.sha256(pub).hexdigest()[:16]

    def sign(self, message: bytes) -> bytes:
        return der_to_raw(self._key.sign(message, ec.ECDSA(hashes.SHA256())))

    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self._key.public_key()


class KmsSigner:
    """An asymmetric KMS key (ECC_NIST_P256, SIGN_VERIFY). The private key never leaves KMS: we send the SHA-256
    digest and get the signature back. The public key is fetched once and cached."""

    name = "kms"

    def __init__(self, key_id: str, client=None) -> None:
        if client is None:
            import boto3
            client = boto3.client("kms")
        self.client, self.key_id = client, key_id
        self._pub: ec.EllipticCurvePublicKey | None = None
        meta = self.client.describe_key(KeyId=key_id)["KeyMetadata"]
        if meta.get("KeySpec") != "ECC_NIST_P256" or meta.get("KeyUsage") != "SIGN_VERIFY":
            raise ValueError("the token key must be an ECC_NIST_P256 SIGN_VERIFY KMS key")
        self.kid = "kms-" + hashlib.sha256(meta["Arn"].encode()).hexdigest()[:16]

    def sign(self, message: bytes) -> bytes:
        digest = hashlib.sha256(message).digest()
        out = self.client.sign(KeyId=self.key_id, Message=digest, MessageType="DIGEST",
                               SigningAlgorithm="ECDSA_SHA_256")
        return der_to_raw(out["Signature"])

    def public_key(self) -> ec.EllipticCurvePublicKey:
        if self._pub is None:
            der = self.client.get_public_key(KeyId=self.key_id)["PublicKey"]
            key = serialization.load_der_public_key(der)
            if not isinstance(key, ec.EllipticCurvePublicKey):
                raise ValueError("KMS key is not EC")
            self._pub = key
        return self._pub


@dataclass(frozen=True)
class Access:
    merchant_id: str
    session_id: str
    jti: str
    exp: int


class Tokens:
    def __init__(self, signer, ttl: int = 900) -> None:
        self.signer, self.ttl = signer, ttl

    def issue(self, merchant_id: str, session_id: str, now: int | None = None) -> tuple[str, int]:
        now = int(now if now is not None else time.time())
        header = {"alg": ALG, "typ": "JWT", "kid": self.signer.kid}
        claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": merchant_id, "sid": session_id, "iat": now,
                  "exp": now + self.ttl, "jti": secrets.token_hex(12)}
        signing_input = (b64url(json.dumps(header, separators=(",", ":")).encode()) + "."
                         + b64url(json.dumps(claims, separators=(",", ":")).encode()))
        sig = self.signer.sign(signing_input.encode())
        return signing_input + "." + b64url(sig), claims["exp"]

    def verify(self, token: str) -> Access:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise TokenError("bad token") from e
        if header.get("alg") != ALG or header.get("kid") != self.signer.kid:
            raise TokenError("unexpected algorithm or key")
        try:
            claims = jwt.decode(token, self.signer.public_key(), algorithms=[ALG], audience=AUDIENCE, issuer=ISSUER,
                                options={"require": ["exp", "iat", "sub", "sid", "jti", "iss", "aud"]})
        except jwt.PyJWTError as e:
            raise TokenError(str(e)) from e
        if claims["exp"] - claims["iat"] > self.ttl:
            raise TokenError("token lifetime too long")
        return Access(str(claims["sub"]), str(claims["sid"]), str(claims["jti"]), int(claims["exp"]))


def make_signer(cfg):
    if cfg.token_signer == "kms":
        return KmsSigner(cfg.token_kms_key_id)
    return LocalSigner(cfg.token_key_pem or None)


__all__ = ["Access", "KmsSigner", "LocalSigner", "TokenError", "Tokens", "encode_dss_signature", "make_signer"]
