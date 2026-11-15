# laabam-beta on AWS: real sign-up by phone, durable data

Made 2026-11-15. A separate service from the judges' demo (deploy/ec2-demo), which is unchanged. Region ap-south-1
(Mumbai). Everything tagged `project=laabam-beta`. **Not S5**: the hardened, CDK-built stacks still come in S5.

**Link:** https://d2qhotuathfbcs.cloudfront.net

## What runs

```
phone --HTTPS--> CloudFront (no caching) --HTTP :8000, CloudFront's addresses only--> EC2 t4g.small (Graviton)
                                                                                         docker: the hardened image
                                                       --Postgres 5432, TLS verify-full, inside the VPC--> RDS
phone --OTP--> Firebase Phone Auth (project laabam-beta-34fb9) --ID token--> the API --our own KMS-signed session
```

- **Mode**: `LAABAM_ENV=prod` with `LAABAM_BETA=1`. Every prod check applies; the beta switch allows only simulated
  QR payments (the owner can simulate a payment on their own QR), the sandbox lender, and Firebase sign-in in place of
  Paytm's. No /sandbox routes, no demo sign-in, no SQLite, no local keys, no Gemini. Every screen says "Beta ·
  payments are simulated".
- **Sign-up**: Firebase proves the phone number (invisible reCAPTCHA, OTP by SMS; test numbers send none). The API
  checks the ID token (RS256 from Google's keys, audience and issuer = laabam-beta-34fb9, expiry, issued-at, a code
  entered in the last 10 minutes, phone provider, +91 only, single use) and issues its own session. A new number
  needs consent to the privacy notice (/privacy, version recorded). The number is stored only as an HMAC under a KMS
  key (`alias/laabam-beta-phone`, HMAC_256; the key never leaves KMS). Rate limits: OTP requests 5/hour per number and
  10/hour per IP (asked before the phone calls Firebase), token exchanges 10 per 10 minutes per IP and per number.
- **Database**: RDS PostgreSQL 16.15, db.t4g.micro, 20 GB gp3, encrypted, deletion protection on, not publicly
  accessible, 5432 open only to the beta app's security group. The app connects as `laabam_app` (no table
  ownership, no BYPASSRLS; row-level security on all 17 tenant tables) with TLS verify-full against the RDS CA.
  Migrations run separately as the owner (`laabam_owner`, password managed by RDS in Secrets Manager).
- **Keys and secrets**: tokens `alias/laabam-beta-tokens` (ECC P-256), audit keys `alias/laabam-beta-audit`, phone
  HMAC `alias/laabam-beta-phone`; secrets `laabam-beta/sarvam-api-key`, `laabam-beta/db-app`. The instance role
  (`instance-policy.json`) can use exactly those, and pull the `laabam-beta` image. No IAM users or access keys.
- **Data rights**: export (Settings → Download my data), delete (typed shop name, a sign-in in the last 10 minutes,
  7-day grace), retention sweep (transcripts and photos after 365 days). No audio is stored.

## Cost (ap-south-1, on-demand, approximate)

| Item | Per month |
|---|---|
| RDS db.t4g.micro ($0.021/h) | $15.3 |
| RDS storage 20 GB gp3 + backups within the free allowance | $2.6 |
| EC2 t4g.small ($0.0112/h) | $8.2 |
| Public IPv4 for the instance | $3.6 |
| EBS 10 GB gp3 | $0.9 |
| KMS, 3 keys | $3.0 |
| Secrets Manager, 2 secrets (+ the RDS-managed one) | $1.2 |
| CloudFront, ECR, KMS/Secrets requests | ~$0 at beta traffic |
| **Total** | **about $35/month (~$1.15/day)** |

Firebase bills real SMS separately (test numbers are free).

## Known gaps (to close in S5)

- **Backups**: 1 day, not 7: the AWS account is on the Free plan, which refuses more. After upgrading the plan:
  `aws rds modify-db-instance --region ap-south-1 --db-instance-identifier laabam-beta --backup-retention-period 7 --apply-immediately`
- **Network**: default VPC, public subnets; no private subnets, VPC endpoints or NAT. The instance has a public IP
  (only CloudFront can reach port 8000). The database is private, but in a public subnet group.
- **One instance**, SQLite-free but single: no load balancer, no auto scaling, no multi-AZ database.
- **No WAF, CSP or HSTS at CloudFront yet**; no CloudWatch alarms; container logs stay on the instance.
- **OTP abuse**: our limits apply when the app asks before sending a code, but anyone can call Firebase directly with
  the public API key. Firebase's own controls hold that: SMS region policy (India only), Firebase quotas; App Check
  (reCAPTCHA Enterprise) and an API-key restriction to this domain are recommended next.
- **Deploys are by hand** (this folder), not the CDK/OIDC pipeline.

## Updating

Build and push a new image tag (linux/arm64), launch a new instance from `user-data.sh` (ids filled in), point the
CloudFront origin at it, terminate the old one, and update `IID` in `teardown.sh`. Migrations: as the owner, with
the database briefly reachable from one IP, then closed again.

## Teardown

```bash
cd ~/projects/laabam-paytm && bash deploy/ec2-beta/teardown.sh
```

Keeps a final database snapshot (`laabam-beta-final-<date>`); everything else this deploy created is removed, and
the three KMS keys are scheduled for deletion after 7 days.
