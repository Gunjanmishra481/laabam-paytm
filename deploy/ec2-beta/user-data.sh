#!/bin/bash
# laabam-beta instance boot script (EC2 user data). No secrets here: the Sarvam key and the app's database password
# are read from Secrets Manager with the instance role into a root-only file on tmpfs (/run) and passed to the
# container. The app connects to Postgres as laabam_app (row-level security applies), with TLS verify-full against
# the RDS CA bundle baked into the image. Migrations are not run from here: they run as the owner, separately.
set -euo pipefail
REGION=ap-south-1
REPO=806121397725.dkr.ecr.ap-south-1.amazonaws.com/laabam-beta
TAG=__TAG__
DB_HOST=__DB_HOST__
TOKEN_KEY=__TOK__
AUDIT_KEY=__AUD__
PHONE_KEY=__PH__
FB_PROJECT=__FB_PROJECT__
FB_API_KEY=__FB_API_KEY__
FB_AUTH_DOMAIN=__FB_AUTH_DOMAIN__
FB_APP_ID=__FB_APP_ID__

dnf install -y docker
systemctl enable --now docker

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}"
docker pull "$REPO:$TAG"

umask 077
ENV_FILE=/run/laabam-beta.env
DB_JSON=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id laabam-beta/db-app --query SecretString --output text)
{
  echo "LAABAM_SARVAM_API_KEY=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id laabam-beta/sarvam-api-key --query SecretString --output text)"
  echo "LAABAM_DB_USER=$(echo "$DB_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["username"])')"
  echo "LAABAM_DB_PASSWORD=$(echo "$DB_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')"
} > "$ENV_FILE"
unset DB_JSON

docker run -d --name laabam --restart unless-stopped \
  --read-only --tmpfs /tmp:rw,size=512m --cap-drop ALL --security-opt no-new-privileges \
  -p 8000:8000 --log-opt max-size=10m --log-opt max-file=3 \
  --env-file "$ENV_FILE" \
  -e LAABAM_ENV=prod -e LAABAM_BETA=1 -e LAABAM_LENDING=sandbox \
  -e LAABAM_DB_HOST="$DB_HOST" -e LAABAM_DB_PORT=5432 -e LAABAM_DB_NAME=laabam \
  -e LAABAM_PHOTO_DIR=/tmp/photos \
  -e LAABAM_TOKEN_SIGNER=kms -e LAABAM_TOKEN_KMS_KEY_ID="$TOKEN_KEY" -e LAABAM_AUDIT_KMS_KEY_ID="$AUDIT_KEY" \
  -e LAABAM_PHONE_HASH_KMS_KEY_ID="$PHONE_KEY" \
  -e LAABAM_FIREBASE_PROJECT_ID="$FB_PROJECT" -e LAABAM_FIREBASE_API_KEY="$FB_API_KEY" \
  -e LAABAM_FIREBASE_AUTH_DOMAIN="$FB_AUTH_DOMAIN" -e LAABAM_FIREBASE_APP_ID="$FB_APP_ID" \
  -e LAABAM_SPEECH=sarvam -e LAABAM_VISION=sarvam -e LAABAM_VISION_FALLBACK=none \
  -e LAABAM_TRUSTED_PROXIES=1 \
  -e AWS_REGION="$REGION" -e AWS_DEFAULT_REGION="$REGION" \
  "$REPO:$TAG"
