# syntax=docker/dockerfile:1.12@sha256:93bfd3b68c109427185cd78b4779fc82b484b0b7618e36d0f104d4d801e66d25
#
# Laabam API + PWA in one image (security plan S4). Hardening:
# - base images pinned by digest; Python packages installed only from the hash-pinned lock (wheels only)
# - the RDS CA bundle checked against its sha256 (TLS verify-full to Postgres)
# - runs as a non-root user with no shell login; nothing in the image is writable by that user
# - made for a read-only root filesystem: only /tmp needs to be writable (mount a tmpfs)
# - uvicorn without the Server and Date headers; LAABAM_ENV=prod by default, so a forgotten setting fails closed
#
#   docker build -t laabam .
#   docker run --rm -p 8000:8000 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
#     -e LAABAM_ENV=dev -e LAABAM_SANDBOX=1 -e LAABAM_DATABASE_URL=sqlite:////tmp/laabam.db \
#     -e LAABAM_PHOTO_DIR=/tmp/photos laabam

# ---- the PWA ----
FROM node:26-slim@sha256:e6bedef217677b276c591795e169840edd9096dbf13f29df8ef8d1856b8820bc AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Python dependencies, from the lock only ----
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m venv /venv \
 && /venv/bin/pip install --require-hashes --only-binary :all: --no-deps -r /tmp/requirements.txt

# ---- runtime ----
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/venv/bin:$PATH \
    LAABAM_ENV=prod \
    LAABAM_WEB_DIST=/app/web \
    LAABAM_DB_CA=/etc/ssl/rds/global-bundle.pem

# Amazon RDS CA bundle, checked by sha256 (a changed bundle fails the build until it is reviewed)
ADD --chmod=444 --checksum=sha256:90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b \
    https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem /etc/ssl/rds/global-bundle.pem

# Debian security fixes newer than the pinned base (Trivy fails the build on fixable HIGH/CRITICAL)
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*
RUN chmod 755 /etc/ssl/rds \
 && groupadd --system --gid 10001 laabam \
 && useradd --system --uid 10001 --gid laabam --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin laabam

COPY --from=deps /venv /venv
WORKDIR /app
COPY backend/laabam ./laabam
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
COPY --from=web /web/dist ./web
# byte-compile now: the root filesystem is read-only at run time
RUN python -m compileall -q /app/laabam /app/migrations \
 && chmod -R a-w /app /venv

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "laabam.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-date-header", \
     "--timeout-graceful-shutdown", "20"]
