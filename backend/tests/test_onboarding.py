"""Onboarding (Welcome -> Setup) and the shop's settings in /v1/me. The demo shop is labelled as sample data."""


def session(client, **body) -> tuple[dict, dict]:
    r = client.post("/sandbox/session", json=body).json()
    return {"Authorization": f"Bearer {r['access_token']}"}, r["merchant"]


def test_seeded_demo_shop_is_ready_and_labelled_sample_data(client):
    _, me = session(client, reset=True)
    assert me["name"] == "Demo Kirana" and me["sample_data"] is True
    assert me["needs"] == [] and me["language_set"] is True
    assert me["upi_vpa"].endswith("@sandbox")  # no real UPI app can pay it


def test_new_shop_goes_welcome_then_setup(client):
    h, me = session(client, fresh=True)
    assert me["needs"] == ["language", "shop"]
    assert me["name"] == "" and me["upi_vpa"] == "" and me["sample_data"] is False and me["language_set"] is False
    # a reload keeps the same shop: the refresh cookie, not a new demo sign-in
    assert client.post("/v1/auth/refresh", headers={"X-Laabam-Refresh": "1"}).json()["merchant"]["id"] == me["id"]
    # every demo sign-in is a shop of its own (visitors never share one)
    assert session(client, fresh=True)[1]["id"] != me["id"]

    after_welcome = client.patch("/v1/me", json={"language": "ta"}, headers=h).json()
    assert after_welcome["needs"] == ["shop"] and after_welcome["language"] == "ta"

    assert client.patch("/v1/me", json={"name": "  "}, headers=h).status_code == 422
    assert client.patch("/v1/me", json={"upi_vpa": "not a vpa"}, headers=h).status_code == 422
    done = client.patch("/v1/me", json={"name": "Anbu Stores", "upi_vpa": "anbu@okaxis", "closing_time": "21:30"},
                        headers=h).json()
    assert done["needs"] == [] and done["name"] == "Anbu Stores" and done["closing_time"] == "21:30"

    # the seeded shop is still one reset away
    assert session(client, reset=True)[1]["name"] == "Demo Kirana"


def test_a_new_shop_has_no_seeded_data(client):
    h, _ = session(client, fresh=True)
    assert client.get("/v1/bills", headers=h).json() == []
    assert client.get("/v1/purchases", headers=h).json() == []
    assert client.get("/v1/items", headers=h).json() == []


def test_cash_in_hand_and_privacy_settings(client, owner):
    me = client.patch("/v1/me", json={"cash_on_hand_paise": 1_500_000}, headers=owner).json()
    assert me["cash_on_hand_paise"] == 1_500_000 and me["keep_recordings"] is False
    assert client.patch("/v1/me", json={"cash_on_hand_paise": -1}, headers=owner).status_code == 422
    assert client.patch("/v1/me", json={"clear_cash_on_hand": True}, headers=owner).json()["cash_on_hand_paise"] is None
    # audio isn't stored at all, so the setting can't be turned on yet rather than pretending
    r = client.patch("/v1/me", json={"keep_recordings": True}, headers=owner)
    assert r.status_code == 422 and "isn't available yet" in r.json()["detail"]
    assert client.get("/v1/me", headers=owner).json()["keep_recordings"] is False
