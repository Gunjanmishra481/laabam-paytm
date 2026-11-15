"""Paytm payments: Dynamic QR (Create QR Code API) and the transaction status API.

Request and response shapes follow Paytm's published API docs; tests check them against recorded
shapes in tests/data/paytm/ and never call Paytm. Confirm field names against the docs for the
account Paytm enables (Dynamic QR is offered to selected enterprise merchants today).

Signature: Paytm's checksum scheme. sha256(body + "|" + salt) + salt, AES-CBC encrypted with the
merchant key and Paytm's fixed IV, base64. The signed body must be the exact bytes sent.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import string
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..money import to_paise, upi_amount
from .base import AdapterError, PaymentStatus, QRCode, Shop

IV = b"@@@@&&&&####$$$$"
STAGING_URL = "https://securegw-stage.paytm.in"
PRODUCTION_URL = "https://securegw.paytm.in"
_SALT_CHARS = string.ascii_letters + string.digits
_IST = timezone(timedelta(hours=5, minutes=30))


def _aes(key: str):
    return Cipher(algorithms.AES(key.encode()), modes.CBC(IV))


def _encrypt(text: str, key: str) -> str:
    padder = padding.PKCS7(128).padder()
    data = padder.update(text.encode()) + padder.finalize()
    enc = _aes(key).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def _decrypt(token: str, key: str) -> str:
    dec = _aes(key).decryptor()
    raw = dec.update(base64.b64decode(token)) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(raw) + unpadder.finalize()).decode()


def _hash(body: str, salt: str) -> str:
    return hashlib.sha256(f"{body}|{salt}".encode()).hexdigest() + salt


def generate_signature(body: str, key: str, salt: str | None = None) -> str:
    salt = salt or "".join(secrets.choice(_SALT_CHARS) for _ in range(4))
    return _encrypt(_hash(body, salt), key)


def verify_signature(body: str, key: str, signature: str) -> bool:
    try:
        plain = _decrypt(signature, key)
    except Exception:  # noqa: BLE001 - any decode failure is a bad signature
        return False
    return secrets.compare_digest(plain, _hash(body, plain[-4:]))


def _envelope(body: dict, head: dict, key: str) -> bytes:
    body_str = json.dumps(body, separators=(",", ":"))
    head = {**head, "signature": generate_signature(body_str, key)}
    return ('{"body":' + body_str + ',"head":' + json.dumps(head, separators=(",", ":")) + "}").encode()


class PaytmPayments:
    name = "paytm"

    def __init__(self, merchant_key: str, base_url: str = STAGING_URL, client_id: str = "C11",
                 http: httpx.Client | None = None) -> None:
        if not merchant_key:
            raise AdapterError("Set LAABAM_PAYTM_MERCHANT_KEY to use Paytm payments")
        self.key = merchant_key
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.http = http or httpx.Client(timeout=10)

    def _post(self, path: str, body: dict, head: dict) -> dict:
        try:
            r = self.http.post(self.base_url + path, content=_envelope(body, head, self.key),
                               headers={"Content-Type": "application/json"})
        except httpx.HTTPError as e:
            raise AdapterError(f"Paytm unreachable: {e}") from e
        if r.status_code != 200:
            raise AdapterError(f"Paytm HTTP {r.status_code}")
        return r.json().get("body") or {}

    def create_qr(self, shop: Shop, order_id: str, amount_paise: int, note: str, key: str) -> QRCode:
        if not shop.paytm_mid:
            raise AdapterError("The shop has no Paytm MID")
        body = {"mid": shop.paytm_mid, "orderId": order_id, "amount": upi_amount(amount_paise),
                "businessType": "UPI_QR_CODE", "posId": shop.soundbox_id or "LAABAM"}
        out = self._post("/paymentservices/qr/create", body, {"clientId": self.client_id, "version": "v1"})
        info = out.get("resultInfo") or {}
        if info.get("resultStatus") != "SUCCESS" or not out.get("qrData"):
            raise AdapterError(f"Paytm QR failed: {info.get('resultCode')} {info.get('resultMsg')}")
        return QRCode(order_id, out["qrData"], provider_ref=out.get("qrCodeId"))

    def payment_status(self, shop: Shop, order_id: str) -> PaymentStatus:
        out = self._post("/v3/order/status", {"mid": shop.paytm_mid, "orderId": order_id}, {})
        info = out.get("resultInfo") or {}
        status = {"TXN_SUCCESS": "succeeded", "TXN_FAILURE": "failed"}.get(info.get("resultStatus"), "pending")
        amount = to_paise(out["txnAmount"]) if out.get("txnAmount") and status == "succeeded" else None
        paid_at = None
        if out.get("txnDate") and status == "succeeded":
            paid_at = datetime.strptime(out["txnDate"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_IST)
        return PaymentStatus(order_id, status, amount, out.get("txnId"), paid_at)
