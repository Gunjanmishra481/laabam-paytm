"""Every test that uses `db_url` runs on SQLite, and on Postgres too when LAABAM_TEST_PG is reachable
(docker compose up -d db, or the CI service)."""
import os
import uuid

import pytest
from sqlalchemy import create_engine, make_url, text

os.environ["LAABAM_NO_APP"] = "1"
# Tests never call live services: a key in the developer's shell must not switch a reader or voice on.
for _k in ("LAABAM_SARVAM_API_KEY", "LAABAM_GEMINI_API_KEY", "LAABAM_VISION", "LAABAM_SPEECH", "LAABAM_LLM",
           "LAABAM_VISION_FALLBACK"):
    os.environ.pop(_k, None)
os.environ["LAABAM_WEB_DIST"] = os.path.join(os.path.dirname(__file__), "no-web-dist")  # tests never serve a build

from fastapi.testclient import TestClient  # noqa: E402

from laabam import app as app_module  # noqa: E402
from laabam.config import Settings  # noqa: E402

PG_URL = os.environ.get("LAABAM_TEST_PG", "postgresql+psycopg://laabam:laabam@localhost:55432/postgres")


def _pg_available() -> bool:
    try:
        engine = create_engine(PG_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as c:
            c.execute(text("select 1"))
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


PG = _pg_available()
if os.environ.get("LAABAM_REQUIRE_PG") == "1" and not PG:
    raise RuntimeError(f"LAABAM_REQUIRE_PG=1 but Postgres is not reachable at {PG_URL}")
BACKENDS = ["sqlite"] + (["postgres"] if PG else [])


# Test-only password for the app role on the local/CI Postgres (never used anywhere else).
APP_PASSWORD = "laabam-app-test-only"


@pytest.fixture(params=BACKENDS)
def db_url(request, tmp_path, monkeypatch):
    """SQLite, or a fresh Postgres database where the app connects as `laabam_app` (row-level security applies,
    as in production) and tests get the owner connection for their own set-up and inspection."""
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path}/t.db"
        return
    name = "t_" + uuid.uuid4().hex[:10]
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
    owner_url = PG_URL.replace("/postgres", f"/{name}", 1)
    owner = create_engine(owner_url)
    app_module.create_schema(owner)  # tables, append-only trigger, roles, policies, functions
    with owner.begin() as c:
        c.execute(text(f"ALTER ROLE laabam_app PASSWORD '{APP_PASSWORD}'"))
    owner.dispose()
    u = make_url(owner_url).set(username="laabam_app", password=APP_PASSWORD)
    monkeypatch.setenv("LAABAM_OWNER_DATABASE_URL", owner_url)
    yield u.render_as_string(hide_password=False)
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
    admin.dispose()


@pytest.fixture(params=BACKENDS)
def migration_db_url(request, tmp_path):
    """An empty database, as the owner role (migrations run as the owner, never as the app)."""
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path}/m.db"
        return
    name = "m_" + uuid.uuid4().hex[:10]
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
    yield PG_URL.replace("/postgres", f"/{name}", 1)
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
    admin.dispose()


@pytest.fixture
def settings(db_url, tmp_path):
    return Settings(database_url=db_url, sandbox=True, inline_worker=False,
                    photo_dir=str(tmp_path / "photos"))


@pytest.fixture
def services(settings):
    svc = app_module.build_services(settings)
    yield svc
    svc.sessions.kw["bind"].dispose()


@pytest.fixture
def client(services):
    # https: the refresh cookie is Secure (__Host-), so it only travels over https
    with TestClient(app_module.create_app(services), base_url="https://testserver") as c:
        yield c


def login(client, language: str | None = None, reset: bool = True, history: bool = False) -> dict:
    """Signs in as a new demo shop (each demo sign-in starts its own). history=True brings the seeded month."""
    body = {"reset": reset, "history": history} | ({"language": language} if language else {})
    r = client.post("/sandbox/session", json=body)
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.headers.update(h)  # the demo routes (/sandbox/qr/..., /sandbox/soundbox/...) need the shop's token too
    return h


def jpeg(size=(1200, 900), fmt="JPEG", **save) -> bytes:
    """A real photo (a plain white image)."""
    from io import BytesIO

    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", size, "white").save(buf, fmt, **save)
    return buf.getvalue()


def upload_photo(client, h, body: bytes, ctype: str = "image/jpeg"):
    """A bill photo the way the phone sends it: ask for an upload link, PUT the bytes, say it's up. Returns the
    last response (202, the bill "reading")."""
    r = client.post("/v1/purchases/uploads", json={"content_type": ctype, "size": len(body)}, headers=h)
    if r.status_code != 200:
        return r
    up = r.json()["upload"]
    put = client.put(up["url"], content=body, headers={**up["headers"], "Authorization": ""})
    assert put.status_code == 200, put.text
    return client.post(f"/v1/purchases/{r.json()['purchase']['id']}/uploaded", headers=h)


def owner_headers(services, merchant_id: str) -> dict:
    """An access token for an existing shop (as if it had signed in through the partner)."""
    with services.sessions.begin() as s:
        issued = services.auth.start(s, merchant_id, "test", "partner")
    return {"Authorization": f"Bearer {issued.access_token}"}


@pytest.fixture
def owner(client):
    return login(client)
