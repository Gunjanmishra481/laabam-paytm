"""Bill photos (security plan §3). S3 in AWS; local disk in development; one interface.

The phone uploads straight to storage with a presigned PUT (5 minutes, exact content type and size, a key under
the shop's prefix): the API never streams photo bytes. The worker then re-encodes the photo (sanitize): it must
open as an image, stay under a pixel limit (decompression bombs), and is rewritten as a plain JPEG, which drops
EXIF and anything appended. The original is deleted; only the clean copy is kept and sent to the reader.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

_KEY = re.compile(r"^[a-z0-9_]+/[a-z0-9_]+\.(jpg|png|webp|heic|heif)$")
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic", "image/heif": "heif"}


MAX_UPLOAD_BYTES = 10_000_000
MIN_UPLOAD_BYTES = 1_000
UPLOAD_SECONDS = 300
MAX_PIXELS = 40_000_000  # a 40 MP phone photo; anything bigger is refused before it is decoded


class LocalPhotoStore:
    """Development: files under `root`. Presigned uploads are URLs on the API (PUT /v1/uploads/{token}), signed
    with a per-process key, checked exactly like S3 checks its own."""

    name = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._key = secrets.token_bytes(32)

    def presign_put(self, key: str, content_type: str, size: int) -> dict:
        self._path(key)
        claims = {"k": key, "t": content_type, "n": size, "e": int(time.time()) + UPLOAD_SECONDS}
        body = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).decode().rstrip("=")
        sig = base64.urlsafe_b64encode(hmac.new(self._key, body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        return {"url": f"/v1/uploads/{body}.{sig}", "method": "PUT", "headers": {"Content-Type": content_type},
                "expires_in": UPLOAD_SECONDS}

    def check_upload(self, token: str) -> dict | None:
        """The upload's key, type and size if the token is genuine and unexpired."""
        try:
            body, sig = token.split(".", 1)
            want = base64.urlsafe_b64encode(hmac.new(self._key, body.encode(), hashlib.sha256).digest()).decode()
            if not hmac.compare_digest(want.rstrip("="), sig):
                return None
            claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, json.JSONDecodeError):
            return None
        if claims.get("e", 0) < time.time():
            return None
        return claims

    def size(self, key: str) -> int | None:
        p = self._path(key)
        return p.stat().st_size if p.is_file() else None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        if not _KEY.match(key):
            raise ValueError("bad photo key")
        return self.root / key

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.is_file() else None


class PhotoNotReadable(ValueError):
    """The bytes aren't an image we can open."""


MAX_SIDE = 3000  # plenty for a bill; bigger photos are scaled down when converted


def for_reader(image: bytes, content_type: str, accepts: tuple[str, ...]) -> tuple[bytes, str]:
    """The photo in a type the reader takes. A JPEG or PNG the reader accepts passes through untouched,
    unless it carries an EXIF rotation (readers don't all apply it); anything else (WebP, HEIC from a
    phone gallery) becomes an upright JPEG."""
    upright = content_type in accepts and content_type in ("image/jpeg", "image/png")
    if content_type in accepts and not upright:
        return image, content_type
    from io import BytesIO

    from PIL import Image, ImageOps, UnidentifiedImageError

    if content_type in ("image/heic", "image/heif"):
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError as e:
            raise PhotoNotReadable("HEIC support is not installed") from e
    try:
        with Image.open(BytesIO(image)) as im:
            if upright and im.getexif().get(0x0112, 1) == 1:  # 0x0112: orientation; 1 = already upright
                return image, content_type
            im = ImageOps.exif_transpose(im)
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            if content_type == "image/png" and upright:
                out_type, fmt = "image/png", "PNG"
            else:
                out_type, fmt = "image/jpeg", "JPEG"
                im = im.convert("RGB")
            buf = BytesIO()
            im.save(buf, fmt, quality=90)
            return buf.getvalue(), out_type
    except (UnidentifiedImageError, OSError, ValueError) as e:
        if upright:  # a type the reader takes: let the reader judge it
            return image, content_type
        raise PhotoNotReadable(str(e)) from e


def photo_key(merchant_id: str, bill_id: str, content_type: str) -> str:
    return f"{merchant_id}/{bill_id}.{EXT[content_type]}"


class S3PhotoStore:
    """AWS: a private bucket, KMS-encrypted (customer-managed key), keys under the shop's prefix."""

    name = "s3"

    def __init__(self, bucket: str, kms_key_id: str, client=None) -> None:
        if client is None:
            import boto3
            client = boto3.client("s3")
        self.client, self.bucket, self.kms_key_id = client, bucket, kms_key_id

    def presign_put(self, key: str, content_type: str, size: int) -> dict:
        if not _KEY.match(key):
            raise ValueError("bad photo key")
        params = {"Bucket": self.bucket, "Key": key, "ContentType": content_type, "ContentLength": size,
                  "ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key_id}
        url = self.client.generate_presigned_url("put_object", Params=params, ExpiresIn=UPLOAD_SECONDS)
        return {"url": url, "method": "PUT", "expires_in": UPLOAD_SECONDS,
                "headers": {"Content-Type": content_type, "x-amz-server-side-encryption": "aws:kms",
                            "x-amz-server-side-encryption-aws-kms-key-id": self.kms_key_id}}

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType="image/jpeg",
                               ServerSideEncryption="aws:kms", SSEKMSKeyId=self.kms_key_id)

    def get(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def size(self, key: str) -> int | None:
        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except Exception:  # noqa: BLE001 - not there (404) or not readable: not uploaded
            return None

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def upload_key(merchant_id: str, bill_id: str, content_type: str) -> str:
    """Where the phone uploads the original (deleted once the worker has re-encoded it)."""
    return f"{merchant_id}/{bill_id}_orig.{EXT[content_type]}"


def clean_key(merchant_id: str, bill_id: str) -> str:
    return f"{merchant_id}/{bill_id}.jpg"


def sanitize(image: bytes, max_pixels: int = MAX_PIXELS) -> bytes:
    """A plain JPEG re-encoded from the pixels: upright, no EXIF, nothing appended, at most MAX_SIDE a side.
    Raises PhotoNotReadable for anything that isn't a photo we can decode safely."""
    from io import BytesIO

    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    Image.MAX_IMAGE_PIXELS = max_pixels  # Pillow refuses (DecompressionBombError) at twice this
    try:
        with Image.open(BytesIO(image)) as probe:
            if probe.format not in ("JPEG", "PNG", "WEBP", "HEIF", "HEIC"):
                raise PhotoNotReadable(f"format {probe.format}")
            if probe.width * probe.height > max_pixels:
                raise PhotoNotReadable("too many pixels")
            probe.verify()  # structure check, before any pixel is decoded for real
        with Image.open(BytesIO(image)) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            out = BytesIO()
            im.convert("RGB").save(out, "JPEG", quality=88, optimize=True)  # no exif=, no icc: metadata dropped
            return out.getvalue()
    except PhotoNotReadable:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, SyntaxError) as e:
        raise PhotoNotReadable(type(e).__name__) from e
