"""Bill photos (security plan §3): presigned uploads (5 minutes, exact type and size, the shop's own key), and the
worker's re-encode (EXIF and trailing data gone, decompression bombs refused) before anything reaches the reader."""
import time
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

from laabam.photos import MAX_PIXELS, LocalPhotoStore, PhotoNotReadable, S3PhotoStore, sanitize
from tests.conftest import jpeg, login

# ---------------- presigned uploads ----------------


@pytest.fixture
def live_client(client, services):
    # a reader that is "live" (the upload flow is for real photos)
    from laabam.purchases.reading import BillReading, ReadLine
    services.purchases.vision = type("Stub", (), {"name": "stub", "read_bill": lambda *a: BillReading(
        lines=[ReadLine("SUGAR LOOSE", "5", "kg", "42", "210")], provider="stub")})()
    return client


def start(client, h, ctype="image/jpeg", size=5000):
    r = client.post("/v1/purchases/uploads", json={"content_type": ctype, "size": size}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_link_is_for_this_shop_type_and_size(live_client, services):
    c = live_client
    h = login(c)
    mid = c.get("/v1/me", headers=h).json()["id"]
    body = jpeg()
    out = start(c, h, size=len(body))
    up, bill = out["upload"], out["purchase"]
    assert bill["status"] == "uploading" and up["method"] == "PUT" and up["expires_in"] == 300
    claims = services.purchases.photos.check_upload(up["url"].rsplit("/", 1)[1])
    assert claims["k"].startswith(f"{mid}/") and claims["t"] == "image/jpeg" and claims["n"] == len(body)
    url = up["url"]
    # a different type, a different size, a tampered link: refused
    assert c.put(url, content=body, headers={"Content-Type": "image/png"}).status_code == 403
    assert c.put(url, content=body + b"x", headers={"Content-Type": "image/jpeg"}).status_code == 403
    tampered = url[:-3] + ("AAA" if not url.endswith("AAA") else "BBB")
    assert c.put(tampered, content=body, headers={"Content-Type": "image/jpeg"}).status_code == 403
    # not uploaded yet: can't be marked sent
    assert c.post(f"/v1/purchases/{bill['id']}/uploaded", headers=h).status_code == 409
    assert c.put(url, content=body, headers={"Content-Type": "image/jpeg"}).status_code == 200
    r = c.post(f"/v1/purchases/{bill['id']}/uploaded", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "reading"
    assert c.post(f"/v1/purchases/{bill['id']}/uploaded", headers=h).status_code == 409  # once


def test_upload_link_expires(tmp_path, monkeypatch):
    store = LocalPhotoStore(tmp_path)
    up = store.presign_put("mer_1/pur_1_orig.jpg", "image/jpeg", 5000)
    token = up["url"].rsplit("/", 1)[1]
    assert store.check_upload(token) is not None
    monkeypatch.setattr(time, "time", lambda: 10**12)
    assert store.check_upload(token) is None
    # another process's links don't work here (per-process key)
    assert LocalPhotoStore(tmp_path).check_upload(token) is None
    with pytest.raises(ValueError):
        store.presign_put("../etc/passwd", "image/jpeg", 10)


def test_s3_link_signs_type_size_and_encryption():
    import boto3
    s3 = boto3.client("s3", region_name="ap-south-1", aws_access_key_id="AKIAEXAMPLE", aws_secret_access_key="x")
    store = S3PhotoStore("laabam-photos", "arn:aws:kms:ap-south-1:111122223333:key/abc", client=s3)
    up = store.presign_put("mer_1/pur_1_orig.jpg", "image/jpeg", 123_456)
    q = parse_qs(urlparse(up["url"]).query)
    signed = q["X-Amz-SignedHeaders"][0].split(";")
    assert {"content-type", "content-length", "x-amz-server-side-encryption"} <= set(signed)
    assert q["X-Amz-Expires"] == ["300"]
    assert urlparse(up["url"]).path == "/mer_1/pur_1_orig.jpg" or "pur_1_orig.jpg" in up["url"]
    assert up["headers"]["x-amz-server-side-encryption"] == "aws:kms"


def test_the_worker_reads_only_the_re_encoded_copy(live_client, services):
    c = live_client
    h = login(c)
    seen = []
    from laabam.purchases.reading import BillReading, ReadLine

    def read_bill(image, ctype, sample=None):
        seen.append((image, ctype))
        return BillReading(lines=[ReadLine("SUGAR LOOSE", "5", "kg", "42", "210")], provider="stub")

    services.purchases.vision = type("Stub", (), {"name": "stub", "read_bill": staticmethod(read_bill)})()
    original = with_exif_and_trailer(jpeg())
    from tests.conftest import upload_photo
    assert upload_photo(c, h, original).status_code == 202
    services.timers.tick()
    image, ctype = seen[0]
    assert ctype == "image/jpeg" and image != original
    assert b"GPS-SECRET" not in image and b"<script>" not in image
    assert not Image.open(BytesIO(image)).getexif()
    bill = c.get("/v1/purchases", headers=h).json()[0]
    assert bill["status"] == "read" and bill["has_photo"]
    store = services.purchases.photos
    assert not list(store.root.rglob("*_orig.*"))  # the original is gone


# ---------------- re-encoding ----------------


def with_exif_and_trailer(img: bytes) -> bytes:
    im = Image.open(BytesIO(img))
    exif = Image.Exif()
    exif[0x010E] = "GPS-SECRET description"  # ImageDescription
    buf = BytesIO()
    im.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue() + b"<script>alert(1)</script>"


def test_sanitize_drops_metadata_and_trailing_data():
    dirty = with_exif_and_trailer(jpeg())
    assert b"GPS-SECRET" in dirty
    clean = sanitize(dirty)
    assert b"GPS-SECRET" not in clean and b"<script>" not in clean
    assert Image.open(BytesIO(clean)).format == "JPEG" and not Image.open(BytesIO(clean)).getexif()


def test_sanitize_refuses_bombs_and_non_photos():
    bomb = BytesIO()
    Image.new("1", (9000, 9000)).save(bomb, "PNG")  # 81 MP that compresses to a few kB
    assert len(bomb.getvalue()) < 200_000 and 9000 * 9000 > MAX_PIXELS
    with pytest.raises(PhotoNotReadable):
        sanitize(bomb.getvalue())
    gif = BytesIO()
    Image.new("RGB", (10, 10)).save(gif, "GIF")
    for bad in (b"not a picture" * 100, gif.getvalue(), b"<svg xmlns='http://www.w3.org/2000/svg'/>"):
        with pytest.raises(PhotoNotReadable):
            sanitize(bad)
    with pytest.raises(PhotoNotReadable):  # a truncated JPEG
        sanitize(jpeg()[:300])
