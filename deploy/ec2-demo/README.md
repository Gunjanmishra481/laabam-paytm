# laabam-demo on AWS (judges' link)

A time-boxed demo deploy, made 2026-11-15. **Not S5** (that is the CDK stacks in `infra/`), and it doesn't replace it.
Region ap-south-1 (Mumbai). Everything is tagged `project=laabam-demo`.

**Link:** https://d1h4dpjxoavkk8.cloudfront.net (owner app) and https://d1h4dpjxoavkk8.cloudfront.net/demo (demo view)

## What runs

```
phone/browser --HTTPS--> CloudFront (*.cloudfront.net certificate, HTTPS only, no caching)
                             --HTTP :8000, CloudFront's addresses only--> EC2 t3.small (Amazon Linux 2023)
                                                                           docker: the hardened laabam image
```

- **App**: the hardened image from ECR (`laabam-demo` repository, immutable tags, scanned on push), run with a
  read-only root, tmpfs `/tmp`, no capabilities and no new privileges, as uid 10001.
- **Mode**: `LAABAM_ENV=demo`, sandbox on (each visitor gets their own seeded demo shop, deleted after 24 h),
  rate limits on, demo shops 20 an hour per IP (`LAABAM_TRUSTED_PROXIES=1`: the client IP is the one CloudFront
  appends), no vision fallback. Data is synthetic and lives in SQLite in the container's tmpfs: a restart starts clean.
- **Keys**: access tokens signed by KMS (`alias/laabam-demo-tokens`, ECC P-256); per-shop audit keys wrapped by KMS
  (`alias/laabam-demo-audit`, rotation on). The Sarvam key is in Secrets Manager (`laabam-demo/sarvam-api-key`);
  the instance reads it at boot into a root-only file on tmpfs. It is not in the image, the repo or the logs.
- **Access**: one instance role, `laabam-demo-ec2` (`instance-policy.json`): pull this one ECR repository, sign
  with and wrap under those two keys, read that one secret. No IAM users, no access keys, no SSH key, IMDSv2 only.
  The security group (`laabam-demo-origin`) admits port 8000 from CloudFront's origin-facing prefix list only.
- Files: `user-data.sh` (boot script; ids filled in at launch), `instance-policy.json`, `trust-ec2.json`,
  `cloudfront.json`, `teardown.sh`.

Why not App Runner: the account isn't subscribed to it ("needs a subscription for the service"). Why not Lightsail
containers: they have no IAM role, so KMS and Secrets Manager would need access keys.

## Cost (ap-south-1, on-demand, approximate)

| Item | Per day |
|---|---|
| EC2 t3.small | $0.54 |
| Public IPv4 address | $0.12 |
| EBS gp3 10 GB (encrypted) | $0.03 |
| KMS, 2 keys ($1/key/month) | $0.07 |
| Secrets Manager, 1 secret | $0.01 |
| CloudFront, ECR storage, KMS/Secrets requests | ~$0 at demo traffic (free tier) |
| **Total** | **about $0.77/day (~$23/month)** |

That is over the $10 monthly budget in about 13 days: tear it down after the judging.

## Teardown

```bash
cd ~/projects/laabam-paytm && bash deploy/ec2-demo/teardown.sh
```

Deletes exactly what this deploy created: the CloudFront distribution (disabled first; the script waits for it,
5-15 minutes), the instance and its volume, the security group, the instance role and profile, the secret (no
recovery window), the ECR repository and images, and the two KMS key aliases; the two keys are disabled and
scheduled for deletion after 7 days (the minimum KMS allows).
