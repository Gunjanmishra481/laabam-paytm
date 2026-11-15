#!/bin/bash
# laabam-demo instance boot script (EC2 user data). No secrets here: the Sarvam key is read from Secrets Manager
# with the instance role, written to a root-only file on tmpfs (/run), and passed to the container as an env var.
# The app runs in the hardened image: read-only root, tmpfs /tmp, no capabilities, no new privileges, non-root user.
set -euo pipefail
REGION=ap-south-1
REPO=806121397725.dkr.ecr.ap-south-1.amazonaws.com/laabam-demo
TAG=__TAG__
SIGN_KEY=__SIGN__
AUDIT_KEY=__AUDIT__
SECRET_ID=laabam-demo/sarvam-api-key

dnf install -y docker
systemctl enable --now docker

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}"
docker pull "$REPO:$TAG"

umask 077
ENV_FILE=/run/laabam-demo.env
{
  echo "LAABAM_SARVAM_API_KEY=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" --query SecretString --output text)"
} > "$ENV_FILE"

docker run -d --name laabam --restart unless-stopped \
  --read-only --tmpfs /tmp:rw,size=512m --cap-drop ALL --security-opt no-new-privileges \
  -p 8000:8000 --log-opt max-size=10m --log-opt max-file=3 \
  --env-file "$ENV_FILE" \
  -e LAABAM_ENV=demo -e LAABAM_SANDBOX=1 \
  -e LAABAM_DATABASE_URL=sqlite:////tmp/laabam.db -e LAABAM_PHOTO_DIR=/tmp/photos \
  -e LAABAM_TOKEN_SIGNER=kms -e LAABAM_TOKEN_KMS_KEY_ID="$SIGN_KEY" -e LAABAM_AUDIT_KMS_KEY_ID="$AUDIT_KEY" \
  -e LAABAM_SPEECH=sarvam -e LAABAM_VISION=sarvam -e LAABAM_VISION_FALLBACK=none \
  -e LAABAM_DEMO_SHOPS_PER_IP_HOUR=20 -e LAABAM_TRUSTED_PROXIES=1 \
  -e AWS_REGION="$REGION" -e AWS_DEFAULT_REGION="$REGION" \
  "$REPO:$TAG"
