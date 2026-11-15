"""Tenancy (security plan §2): one shop never sees another's data.

1. Generated from the route list: for every route that takes an id, shop B asking for shop A's resource gets 404 and
   no data. A route this test doesn't know how to fill makes it fail, so new endpoints are covered automatically.
2. Postgres row-level security, checked directly as the app role: no shop in context sees nothing; one shop sees only
   itself; writing another shop's row is refused; the audit log can't be changed; the lookup functions return ids only.
"""
import re

import pytest
from sqlalchemy import create_engine, text

from laabam.models import Timer
from laabam.tenancy import APP_ROLE, DEFINER_ROLE, TENANT_TABLES
from tests.conftest import login

AUDIO = b"\x1a\x45\xdf\xa3" + b"\x00" * 4000


def shop_a(client):
    """Shop A with one of everything a route can name."""
    h = login(client, history=True)
    me = client.get("/v1/me", headers=h).json()
    draft = client.post("/v1/bills/text", json={"text": "rice 200, sugar 100"}, headers=h).json()
    paid = client.post("/v1/bills/text", json={"text": "oil 165"}, headers=h).json()
    paid = client.post(f"/v1/bills/{paid['id']}/confirm", headers=h).json()
    pur = client.post("/v1/purchases/photo", content=b"", headers={**h, "Content-Type": "image/jpeg",
                                                                   "X-Laabam-Sample": "printed"}).json()
    plan = client.post("/v1/restock-plans", json={"multiplier_bp": 20000}, headers=h).json()
    offer = client.post(f"/v1/restock-plans/{plan['id']}/loan-offer", headers=h).json()
    alert = client.get("/v1/alerts", headers=h).json()[0]
    item = client.get("/v1/items", headers=h).json()[0]
    session = client.get("/v1/sessions", headers=h).json()[0]
    return {"h": h, "merchant_id": me["id"], "bill": draft, "paid": paid, "purchase": pur, "plan_id": plan["id"],
            "offer_id": offer["id"], "alert_id": alert["id"], "item_id": item["id"], "session_id": session["id"]}


def fill(path: str, a: dict, services) -> str | None:
    """The URL of shop A's resource for this route, or None if the route is unknown to this test."""
    purchase = path.startswith("/v1/purchases/")
    values = {
        "bill_id": a["purchase"]["id"] if purchase else a["bill"]["id"],
        "line_id": (a["purchase"] if purchase else a["bill"])["lines"][0]["id"],
        "item_id": a["item_id"], "alert_id": a["alert_id"], "plan_id": a["plan_id"], "offer_id": a["offer_id"],
        "session_id": a["session_id"], "merchant_id": a["merchant_id"], "order_id": a["paid"]["qr"]["order_id"],
        "seq": "1",
    }
    if "{timer_id}" in path:
        with services.admin_sessions() as s:
            values["timer_id"] = s.query(Timer.id).filter(Timer.merchant_id == a["merchant_id"],
                                                          Timer.status == "pending").first()[0]
    names = re.findall(r"{(\w+)}", path)
    if any(n not in values for n in names):
        return None
    return re.sub(r"{(\w+)}", lambda m: values[m.group(1)], path)


BODIES = {  # a valid body, so the request reaches the ownership check
    "/v1/bills/{bill_id}/lines": {"line_total_paise": 1000, "item_text": "salt"},
    "/v1/bills/{bill_id}/lines/{line_id}": {"line_total_paise": 1000},
    "/v1/bills/{bill_id}/say": {"text": "rice 300"},
    "/v1/purchases/{bill_id}": {"bill_no": "X1"},
    "/v1/purchases/{bill_id}/lines": {"raw_text": "SALT 1KG", "line_total_paise": 2000},
    "/v1/purchases/{bill_id}/lines/{line_id}": {"line_total_paise": 2000},
}


def all_routes(app):
    """Every route, including those of included routers (FastAPI keeps them nested)."""
    for r in app.routes:
        sub = getattr(r, "original_router", None)
        if sub is not None:
            yield from sub.routes
        else:
            yield r


def test_every_id_route_hides_other_shops(client, services):
    a = shop_a(client)
    b = login(client)  # shop B
    checked, unknown = 0, []
    # a presigned upload URL is its own permission (signed key, type, size, expiry): tested in test_uploads.py
    capability = {"/v1/uploads/{token}"}
    for route in all_routes(client.app):
        path = getattr(route, "path", "")
        if "{" not in path or path.startswith("/{path") or path in capability:
            continue
        url = fill(path, a, services)
        if url is None:
            unknown.append(path)
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            kw = {"headers": dict(b)}
            if path.endswith("/voice"):
                kw = {"content": AUDIO, "headers": {**b, "Content-Type": "audio/webm"}}
            elif method in ("POST", "PATCH") and path in BODIES:
                kw["json"] = BODIES[path]
            r = client.request(method, url, **kw)
            assert r.status_code == 404, f"{method} {path} as another shop: {r.status_code} {r.text[:200]}"
            assert a["merchant_id"] not in r.text
            checked += 1
    assert not unknown, f"routes this test can't fill (add them): {unknown}"
    assert checked >= 35
    # and shop A still sees its own
    assert client.get(f"/v1/bills/{a['bill']['id']}", headers=a["h"]).status_code == 200


def test_list_routes_show_only_your_shop(client):
    a = shop_a(client)
    b = login(client)
    for path in ("/v1/bills", "/v1/purchases", "/v1/items", "/v1/alerts", "/v1/sessions"):
        mine = client.get(path, headers=b).json()
        assert all(a["merchant_id"] not in str(x) for x in mine), path
        ids = {x["id"] for x in client.get(path, headers=a["h"]).json()}
        assert not ids & {x["id"] for x in mine}, path


# ---------------- Postgres row-level security, directly ----------------


@pytest.fixture
def pg(db_url, services):
    if not db_url.startswith("postgresql"):
        pytest.skip("row-level security is Postgres only")
    app = create_engine(db_url)  # as laabam_app
    yield app
    app.dispose()


def test_app_role_is_not_owner_and_cannot_bypass(pg):
    with pg.connect() as c:
        assert c.execute(text("SELECT current_user")).scalar() == APP_ROLE
        su, bypass = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
        assert not su and not bypass
        owners = set(c.execute(text("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'")).scalars())
        assert APP_ROLE not in owners
        assert not c.execute(text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")).scalar()


def test_rls_hides_and_refuses_other_shops(client, pg):
    a = shop_a(client)
    b_h = login(client)
    b_id = client.get("/v1/me", headers=b_h).json()["id"]
    with pg.connect() as c:
        with c.begin():  # no shop in context: nothing
            for t in (*TENANT_TABLES, "merchants"):
                assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0, t
        with c.begin():  # shop A: only A
            c.execute(text("SELECT set_config('app.merchant_id', :m, true)"), {"m": a["merchant_id"]})
            assert set(c.execute(text("SELECT id FROM merchants")).scalars()) == {a["merchant_id"]}
            assert set(c.execute(text("SELECT DISTINCT merchant_id FROM sales_lines")).scalars()) == {a["merchant_id"]}
            assert c.execute(text("SELECT count(*) FROM sales_bills WHERE merchant_id = :b"), {"b": b_id}).scalar() == 0
            # even an UPDATE that names B's row changes nothing
            assert c.execute(text("UPDATE merchants SET name = 'x' WHERE id = :b"), {"b": b_id}).rowcount == 0
        with pytest.raises(Exception, match="row-level security"), c.begin():
            c.execute(text("SELECT set_config('app.merchant_id', :m, true)"), {"m": a["merchant_id"]})
            c.execute(text("INSERT INTO items (id, merchant_id, name, key, aliases, created_at) "
                           "VALUES ('item_x', :b, 'X', 'x', '[]', now())"), {"b": b_id})
        with pytest.raises(Exception, match="permission denied"), c.begin():  # append-only, by privilege too
            c.execute(text("SELECT set_config('app.merchant_id', :m, true)"), {"m": a["merchant_id"]})
            c.execute(text("UPDATE audit_events SET kind = 'x'"))


def test_lookup_functions_are_narrow(pg):
    fns = ("laabam_shop_ids", "laabam_shop_for_paytm_mid", "laabam_shop_for_refresh", "laabam_shop_for_order",
           "laabam_shop_for_bill")
    with pg.connect() as c:
        rows = c.execute(text(
            "SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner), p.proconfig, "
            "pg_get_function_result(p.oid), has_function_privilege('public', p.oid, 'EXECUTE') "
            "FROM pg_proc p WHERE p.proname = ANY(:f)"), {"f": list(fns)}).all()
        assert {r[0] for r in rows} == set(fns)
        for name, secdef, owner, config, result, public_exec in rows:
            assert secdef and owner == DEFINER_ROLE, name
            assert config and any(x.startswith("search_path=") for x in config), name  # fixed search_path
            assert result in ("text", "SETOF text"), name  # ids only
            assert not public_exec, name
        login_ok = c.execute(text("SELECT rolcanlogin FROM pg_roles WHERE rolname = :r"), {"r": DEFINER_ROLE}).scalar()
        assert login_ok is False
        # the app may call them, and gets ids only
        assert all(isinstance(x, str) for x in c.execute(text("SELECT laabam_shop_ids()")).scalars())
