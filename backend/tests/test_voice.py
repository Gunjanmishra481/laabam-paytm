"""Voice billing: push-to-talk audio -> transcript -> the typed-order pipeline. Sarvam is mocked; no live calls."""
import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from laabam.adapters.base import AdapterError
from laabam.adapters.speech import OfflineSpeech, SarvamSpeech, SpeechNotSetUp
from laabam.app import build_services, create_app
from laabam.config import Settings
from laabam.models import AuditEvent, SalesBill
from tests.conftest import login

AUDIO = b"\x1aE\xdf\xa3" + b"\x00" * 4000  # stands in for a short webm recording
WEBM = {"Content-Type": "audio/webm;codecs=opus"}
MP3 = b"ID3\x04fake-mp3-bytes"


def voice(client, h, clip=None, audio=AUDIO, headers=WEBM, path="/v1/bills/voice"):
    extra = {"X-Laabam-Clip": clip} if clip else {}
    return client.post(path, content=audio, headers={**h, **headers, **extra})


# ---------------- offline samples ----------------


@pytest.mark.parametrize("lang,clip,items,total", [
    ("en", "en_order", ["Rice", "Sugar", "Oil"], 46500),
    ("ta", "ta_order", ["அரிசி", "சர்க்கரை", "எண்ணெய்"], 46500),
    ("hi", "hi_order", ["चावल", "चीनी", "तेल"], 46500),
])
def test_voice_order_from_sample_clip(client, lang, clip, items, total):
    h = login(client, language=lang)
    r = voice(client, h, clip)
    assert r.status_code == 200, r.text
    b = r.json()
    assert [ln["item_name"].lower() for ln in b["lines"]] == [i.lower() for i in items]
    assert b["total_paise"] == total and b["can_confirm"]
    assert b["speech"] == {"provider": "offline", "sample": clip, "heard": b["transcript"]}
    for k in ("transcribe_ms", "parse_ms", "resolve_verify_ms", "server_total_ms", "audio_bytes"):
        assert k in b["timings"]


def test_spoken_prices_still_checked_against_transcript(client, owner):
    b = voice(client, owner, "en_kg").json()
    sugar = next(ln for ln in b["lines"] if ln["item_name"].lower() == "sugar")
    assert sugar["qty_milli"] == 2000 and sugar["line_total_paise"] == 10000 and not sugar["flags"]


SAMPLE = {**WEBM, "X-Laabam-Sample-Order": "next"}


def test_demo_plays_samples_in_turn_without_a_speech_service(client, owner):
    first = voice(client, owner, audio=b"", headers=SAMPLE).json()
    second = voice(client, owner, audio=b"", headers=SAMPLE).json()
    assert first["speech"]["sample"] == "en_order" and second["speech"]["sample"] == "en_learned"
    # "Rice, oil." with nothing confirmed yet: no learned prices, so both lines ask for one
    assert all("price_needed" in ln["flags"] for ln in second["lines"])
    assert client.get("/api/health").json()["voice"] == "samples"


def test_learned_prices_fill_a_voice_order(client, owner):
    b = voice(client, owner, "en_order").json()
    assert client.post(f"/v1/bills/{b['id']}/confirm", headers=owner).status_code == 200
    again = voice(client, owner, "en_learned").json()
    assert again["total_paise"] == 36500 and all(ln["learned"] for ln in again["lines"])


def test_spoken_fix(client, owner):
    b = client.post("/v1/bills/text", json={"text": "sugar 100, rice 200"}, headers=owner).json()
    r = voice(client, owner, "en_fix", path=f"/v1/bills/{b['id']}/say/voice")
    assert r.status_code == 200, r.text
    sugar = next(ln for ln in r.json()["lines"] if ln["item_name"].lower() == "sugar")
    assert sugar["line_total_paise"] == 12000
    assert r.json()["speech"]["sample"] == "en_fix"


def test_spoken_fix_on_confirmed_bill_is_refused_before_transcribing(client, owner, services):
    b = voice(client, owner, "en_order").json()
    client.post(f"/v1/bills/{b['id']}/confirm", headers=owner)
    calls = []
    services.voice.speech = type("Spy", (), {"name": "spy", "transcribe": lambda *a: calls.append(a)})()
    r = voice(client, owner, "en_fix", path=f"/v1/bills/{b['id']}/say/voice")
    assert r.status_code == 409 and calls == []


def test_demo_does_not_invent_fixes(client, owner):
    b = client.post("/v1/bills/text", json={"text": "sugar 100"}, headers=owner).json()
    r = voice(client, owner, path=f"/v1/bills/{b['id']}/say/voice")
    assert r.status_code == 503 and "type" in r.json()["detail"]


def test_audio_is_not_stored(client, owner, services):
    b = voice(client, owner, "en_order").json()
    with services.admin_sessions() as s:
        bill = s.get(SalesBill, b["id"])
        assert bill.transcript == "Rice 200, sugar 100, oil 165."
        blobs = [json.dumps(e.detail, ensure_ascii=False) for e in s.execute(select(AuditEvent)).scalars()]
    assert not any("\\u001aE" in x or "audio/webm" in x for x in blobs)


def test_offline_speech_never_substitutes_a_sample_for_a_recording(client, owner, services):
    """Regression: with no speech service the sandbox used to answer a real recording with the next sample
    order, so the owner spoke one order and was shown another."""
    assert client.get("/api/health").json()["voice"] == "samples"
    r = voice(client, owner)  # a real recording, no sample asked for
    assert r.status_code == 503 and "can't hear recordings" in r.json()["detail"]
    empty = voice(client, owner, audio=b"")  # an empty upload is not a request for a sample either
    assert empty.status_code == 422
    with services.admin_sessions() as s:
        assert s.execute(select(SalesBill)).all() == []
    # asking for a sample by name still works, and says it is one
    b = voice(client, owner, audio=b"", headers=SAMPLE).json()
    assert b["speech"]["provider"] == "offline" and b["speech"]["sample"] == "en_order"


def test_sample_orders_refused_when_speech_is_live(client, owner, services):
    services.voice.speech = SarvamSpeech("key", client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(500))))
    assert services.voice.mode == "live"
    r = voice(client, owner, audio=b"", headers=SAMPLE)
    assert r.status_code == 409 and "Sample orders" in r.json()["detail"]


# ---------------- request checks ----------------


def test_voice_needs_audio(client, owner):
    assert voice(client, owner, headers={"Content-Type": "application/json"}).status_code == 415
    assert voice(client, owner, audio=b"x" * 2_000_001).status_code == 413
    assert client.post("/v1/bills/voice", content=AUDIO, headers={**WEBM, "Authorization": ""}).status_code == 401


def test_voice_off_outside_sandbox_without_a_service(db_url):
    cfg = Settings(database_url=db_url, sandbox=False, inline_worker=False)
    svc = build_services(cfg)
    assert svc.voice.mode == "off"
    with pytest.raises(SpeechNotSetUp):
        OfflineSpeech().transcribe(AUDIO, "audio/webm", "en")
    svc.admin_sessions.kw["bind"].dispose()


def test_tiny_recording_is_nothing_heard(client, owner, services):
    services.voice.demo_samples = False
    r = voice(client, owner, audio=AUDIO[:50])  # a real WebM header, but far too short to hold speech
    assert r.status_code == 422 and "Nothing was heard" in r.json()["detail"]


# ---------------- Sarvam (mocked) ----------------


class FakeSarvam:
    def __init__(self, transcript="Rice 200, sugar 100.", stt_status=200, tts_status=200):
        self.transcript, self.stt_status, self.tts_status = transcript, stt_status, tts_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/speech-to-text":
            if self.stt_status != 200:
                return httpx.Response(self.stt_status, json={"error": {"code": "rate_limit_exceeded_error",
                                                                      "message": "slow down"}})
            return httpx.Response(200, json={"request_id": "req_1", "transcript": self.transcript,
                                             "language_code": "en-IN"})
        if request.url.path == "/text-to-speech":
            if self.tts_status != 200:
                return httpx.Response(self.tts_status, json={"error": {"code": "internal_server_error"}})
            return httpx.Response(200, json={"request_id": "req_2", "audios": [base64.b64encode(MP3).decode()]})
        return httpx.Response(404)


def multipart_fields(request: httpx.Request) -> dict[str, str]:
    body = request.content.decode("latin-1")
    out = {}
    for part in body.split("Content-Disposition: form-data; ")[1:]:
        head, _, value = part.partition("\r\n\r\n")
        name = head.split('name="')[1].split('"')[0]
        out[name] = value.rsplit("\r\n--", 1)[0] if "filename" not in head else head
    return out


def test_sarvam_transcribe_request_shape():
    fake = FakeSarvam(transcript=" அரிசி 200 ")
    sp = SarvamSpeech("key-123", client=httpx.Client(transport=httpx.MockTransport(fake)))
    t = sp.transcribe(AUDIO, "audio/webm;codecs=opus", "ta", keyterms=("அரிசி", "Sugar"))
    assert t.text == "அரிசி 200" and t.provider == "sarvam" and t.request_id == "req_1"
    req = fake.requests[0]
    assert str(req.url) == "https://api.sarvam.ai/speech-to-text"
    assert req.headers["api-subscription-key"] == "key-123"
    fields = multipart_fields(req)
    assert fields["model"] == "saaras:v4" and fields["language_code"] == "ta-IN"
    assert json.loads(fields["keyterms"].encode("latin-1").decode()) == ["அரிசி", "Sugar"]
    assert 'filename="order.webm"' in fields["file"] and "Content-Type: audio/webm" in fields["file"]


def test_sarvam_errors_do_not_leak_the_key():
    sp = SarvamSpeech("key-123", client=httpx.Client(transport=httpx.MockTransport(FakeSarvam(stt_status=429))))
    with pytest.raises(AdapterError) as e:
        sp.transcribe(AUDIO, "audio/webm", "en")
    assert "429" in str(e.value) and "rate_limit" in str(e.value) and "key-123" not in str(e.value)
    with pytest.raises(SpeechNotSetUp):
        SarvamSpeech("", client=httpx.Client(transport=httpx.MockTransport(FakeSarvam()))).transcribe(
            AUDIO, "audio/webm", "en")


def test_sarvam_speak():
    fake = FakeSarvam()
    sp = SarvamSpeech("key-123", speaker="priya", client=httpx.Client(transport=httpx.MockTransport(fake)))
    assert sp.speak("₹465 received.", "hi") == MP3
    body = json.loads(fake.requests[0].content)
    assert body == {"text": "₹465 received.", "language_code": "hi-IN", "model": "bulbul:v3",
                    "output_audio_codec": "mp3", "speaker": "priya"}


def sarvam_client(db_url, fake):
    cfg = Settings(database_url=db_url, sandbox=True, inline_worker=False,
                   speech="sarvam", sarvam_api_key="key-123")
    svc = build_services(cfg, http_client=httpx.Client(transport=httpx.MockTransport(fake)))
    return svc, TestClient(create_app(svc))


def test_voice_bill_through_sarvam_then_paid_announcement_is_spoken(db_url):
    fake = FakeSarvam()
    svc, client = sarvam_client(db_url, fake)
    with client:
        h = login(client)
        assert client.get("/api/health").json()["voice"] == "live"
        b = voice(client, h).json()
        assert b["speech"] == {"provider": "sarvam", "sample": None, "heard": "Rice 200, sugar 100."}
        assert b["total_paise"] == 30000
        b = client.post(f"/v1/bills/{b['id']}/confirm", headers=h).json()
        client.post(f"/sandbox/qr/{b['qr']['order_id']}/pay")
        mid = client.get("/v1/me", headers=h).json()["id"]
        ev = [e for e in client.get(f"/sandbox/soundbox/{mid}").json()["events"] if e["kind"] == "announce"][-1]
        assert ev["text"] == "₹300 received." and ev["audio"] is True
        audio = client.get(f"/sandbox/soundbox/audio/{ev['seq']}")
        assert audio.status_code == 200 and audio.content == MP3 and audio.headers["content-type"] == "audio/mpeg"
        kinds = [e["kind"] for e in client.get(f"/sandbox/trace/{mid}").json()]
        assert "speech.transcribed" in kinds and "speech.spoken" in kinds
    svc.admin_sessions.kw["bind"].dispose()


def test_sarvam_failures(db_url):
    fake = FakeSarvam(stt_status=500, tts_status=500)
    svc, client = sarvam_client(db_url, fake)
    with client:
        h = login(client)
        r = voice(client, h)
        assert r.status_code == 502 and r.json()["detail"].startswith("Couldn't hear that")
        with svc.admin_sessions() as s:
            assert s.execute(select(SalesBill)).first() is None  # nothing half-made
        # speaking fails -> the announcement still goes out as text
        b = client.post("/v1/bills/text", json={"text": "rice 200"}, headers=h).json()
        b = client.post(f"/v1/bills/{b['id']}/confirm", headers=h).json()
        client.post(f"/sandbox/qr/{b['qr']['order_id']}/pay")
        mid = client.get("/v1/me", headers=h).json()["id"]
        ev = [e for e in client.get(f"/sandbox/soundbox/{mid}").json()["events"] if e["kind"] == "announce"][-1]
        assert ev["text"] == "₹200 received." and "audio" not in ev
        fake.transcript = "   "
        fake.stt_status = 200
        assert voice(client, h).status_code == 422
    svc.admin_sessions.kw["bind"].dispose()
