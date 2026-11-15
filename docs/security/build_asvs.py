"""Builds docs/security/ASVS.md from the official OWASP ASVS 4.0.3 CSV (Level 2 rows) and our status per item.

    python docs/security/build_asvs.py docs/security/asvs-4.0.3-en.csv docs/security/ASVS.md

The CSV is OWASP's (github.com/OWASP/ASVS, tag v4.0.3; CC BY-SA 4.0). Update S below as items close.
"""
import csv
import re
import sys
from collections import Counter

src, out = sys.argv[1], sys.argv[2]
NO_PW = ("N/A", "No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); "
                "the demo uses the sandbox login")
NO_OTP = ("N/A", "No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm)")
NO_XML = ("N/A", "No XML, SOAP or GraphQL anywhere")

# status: Met | Partial | Gap | N/A ; note says what exists or which phase closes it
S = {
    # V1 architecture
    "V1.1.1": ("Met", "Phased plan with security gates (SECURITY_PLAN.md); CI gates: tests, gitleaks, pip-audit, npm audit, CodeQL, Trivy, cdk-nag"),
    "V1.1.2": ("Met", "THREAT_MODEL.md; review on every flow/adapter change"),
    "V1.1.3": ("Partial", "BUILD_PLAN rules (no QR before confirm, no figure from a model) are security constraints; "
                          "per-feature constraints added from S1"),
    "V1.1.4": ("Met", "Trust boundaries TB1-TB5 in THREAT_MODEL.md §3"),
    "V1.1.5": ("Met", "Architecture in SECURITY_PLAN.md and THREAT_MODEL.md"),
    "V1.1.6": ("Partial", "Central owner dependency, security.py, llm guard; RLS and rate limiter central in S1"),
    "V1.1.7": ("Partial", "CLAUDE.md rules + SECURITY_PLAN.md; a secure-coding checklist in docs/security (S6)"),
    "V1.2.1": ("Partial", "App connects as laabam_app (no ownership, no BYPASSRLS); container runs as uid 10001, read-only root, no capabilities. S5: task roles"),
    "V1.2.2": ("Partial", "S3: the app reaches Postgres only with TLS verify-full against the RDS CA (config refuses less in demo/prod). S5: ALB only from CloudFront"),
    "V1.2.3": ("Met", "One mechanism: partner assertion -> device session (ES256 access + rotating refresh); the demo uses the same path"),
    "V1.2.4": ("Met", "Sandbox and partner sign-in share the verifier, AuthService and the owner dependency"),
    "V1.4.1": ("Met", "Every check server-side in the owner dependency and service layer"),
    "V1.4.4": ("Met", "merchant_id scoping in every query plus Postgres RLS (tenancy.py); generated cross-tenant tests"),
    "V1.4.5": ("N/A", "One role (the owner of the shop); no roles or features to grant"),
    "V1.5.1": ("Met", "Strict Pydantic models (extra=forbid) with limits on every field; audio and photos checked by type and bytes"),
    "V1.5.2": ("Met", "JSON only; no pickle or object serialization with clients"),
    "V1.5.3": ("Met", "Validation in the API (Pydantic) and the service layer"),
    "V1.5.4": ("Met", "React escapes output; SQL via ORM parameters"),
    "V1.6.1": ("Partial", "Token key in KMS (ES256, kid); per-shop audit keys wrapped by KMS (envelope, shop id as context). Key policy document in S5"),
    "V1.6.2": ("Partial", "Token signing key in KMS (KmsSigner; config refuses local keys in demo/prod); other secrets S5"),
    "V1.6.3": ("Partial", "Tokens carry kid, so the signing key can rotate; DB credential rotation S5"),
    "V1.6.4": ("Met", "No secrets in the PWA; the token is per-owner and short-lived (shorter in S1)"),
    "V1.7.1": ("Met", "One JSON-lines logger for laabam.*: request id on every line, redaction filter, tests (logs.py)"),
    "V1.7.2": ("Gap", "S5: CloudWatch Logs over a VPC endpoint, KMS-encrypted"),
    "V1.8.1": ("Met", "Assets A1-A10 classified in THREAT_MODEL.md §1"),
    "V1.8.2": ("Partial", "Retention sweep (12 months), keyed audit digests, export/delete (S3); encryption at rest settings S5"),
    "V1.9.1": ("Partial", "S3: app to RDS verify-full; S5: CloudFront-ALB-ECS TLS"),
    "V1.9.2": ("Met", "RDS: sslmode=verify-full with the RDS CA bundle; partners: https only with verification (httpx default)"),
    "V1.10.1": ("Partial", "Git with descriptive commits; branch ruleset (PRs, required checks, no force push) listed in docs/github-settings.md for the owner to apply"),
    "V1.11.1": ("Met", "Components and their roles in BUILD_PLAN.md and THREAT_MODEL.md"),
    "V1.11.2": ("Partial", "Bills, payments, loans are transactional with row locks; auth flows in S1"),
    "V1.12.2": ("Met", "Photos re-encoded by the worker, served only to their shop as image/jpeg with nosniff"),
    "V1.14.1": ("Gap", "S5: VPC private subnets, SGs, ALB from CloudFront only"),
    "V1.14.2": ("Partial", "Base images and CI images by digest, actions by SHA, packages by hash; OIDC deploy role in CDK (tested, not deployed). S5: ECR, deploy"),
    "V1.14.3": ("Met", "pip-audit and npm audit in CI on every push; Dependabot for pip, npm, actions and Docker"),
    "V1.14.4": ("Partial", "CDK synth + cdk-nag (AwsSolutions) in CI for the deploy role; S5 adds the application stacks"),
    "V1.14.5": ("Gap", "S5: Fargate tasks in private subnets, no public IPs"),
    "V1.14.6": ("Met", "React PWA; no Flash/ActiveX/NSAPI"),
    # V3 sessions
    "V3.1.1": ("Met", "Bearer token in the Authorization header, never in URLs"),
    "V3.2.1": ("Met", "Every sign-in starts a new device session and tokens; refresh rotates the refresh token"),
    "V3.2.2": ("Met", "Refresh tokens 256-bit random (token_urlsafe(32)); access tokens ES256-signed"),
    "V3.2.3": ("Met", "Access token in memory only; refresh token in a __Host- HttpOnly Secure SameSite=Strict cookie"),
    "V3.2.4": ("Met", "PyJWT signing; secrets module for random"),
    "V3.3.1": ("Met", "Sign-out and revocation end the session in the DB; access tokens are checked against it on every request"),
    "V3.3.2": ("Met", "Access 15 min; refresh idle 30 days; session absolute 90 days, then the partner sign-in again"),
    "V3.3.3": ("Met", "Settings lists every signed-in device and signs any of them out"),
    "V3.3.4": ("Met", "GET /v1/sessions, DELETE /v1/sessions/{id} (re-authentication is the partner's)"),
    "V3.4.1": ("Met", "Refresh cookie Secure (test_refresh_cookie_is_host_only_httponly_strict)"),
    "V3.4.2": ("Met", "Refresh cookie HttpOnly"),
    "V3.4.3": ("Met", "Refresh cookie SameSite=Strict, plus the X-Laabam-Refresh header on refresh"),
    "V3.4.4": ("Met", "__Host-laabam_refresh (Path=/, no Domain)"),
    "V3.4.5": ("Met", "__Host- cookie: this host only"),
    "V3.5.1": ("N/A", "No OAuth grants to third-party apps"),
    "V3.5.2": ("Met", "Session tokens, not static API keys, for the owner"),
    "V3.5.3": ("Met", "ES256 (KMS), aud/iss/exp/iat/sid/jti required, lifetime capped, session checked live"),
    "V3.7.1": ("Met", "Deleting the shop needs the shop's name typed and a sign-in within the last 10 minutes (403 otherwise)"),
    # V4 access control
    "V4.1.1": ("Met", "Owner dependency on every /v1 route; checks in the service layer"),
    "V4.1.2": ("Met", "merchant_id comes from the verified token, never from the request"),
    "V4.1.3": ("Met", "Owners see only their shop; the DB enforces it (RLS, laabam_app); IAM least privilege S5"),
    "V4.1.5": ("Met", "Unknown or foreign ids raise NotFound -> 404; token errors -> 401"),
    "V4.2.1": ("Met", "test_every_id_route_hides_other_shops (generated from the route list) and RLS tests"),
    "V4.2.2": ("Met", "Bearer tokens (not cookies) on API calls; the S1 refresh cookie is SameSite=Strict + a custom "
                      "header check"),
    "V4.3.1": ("N/A", "No administrative interface; AWS console access is outside the app (MFA in S5 runbook)"),
    "V4.3.2": ("Met", "No directory listing; the SPA route serves only built files; S5 S3 private with OAC"),
    "V4.3.3": ("Met", "Step-up for deleting the shop: a sign-in in the last 10 minutes, plus typed confirmation"),
    # V5 validation
    "V5.1.1": ("Met", "A query key given twice is refused (RequestGuard, 400)"),
    "V5.1.2": ("Met", "Every request model forbids unknown fields (Strict, extra=forbid)"),
    "V5.1.3": ("Met", "Bodies, params and headers typed and length-limited; JSON 64 KB cap; audio and photo size caps"),
    "V5.1.4": ("Met", "Pydantic schemas with types, ranges and lengths; unknown fields refused"),
    "V5.1.5": ("Met", "No redirects or forwards built from input"),
    "V5.2.1": ("N/A", "No HTML input"),
    "V5.2.2": ("Met", "Free text (orders, names, questions) length-limited and normalised"),
    "V5.2.3": ("N/A", "No email; SMS adapter is a sandbox (C2)"),
    "V5.2.4": ("Met", "No eval or dynamic code"),
    "V5.2.5": ("Met", "Answer/summary templates are fixed strings from code; user text only fills values"),
    "V5.2.6": ("Met", "No URLs fetched from input; outbound client allows only allowlisted https hosts (outbound.py)"),
    "V5.2.7": ("Met", "SVG uploads not accepted (JPEG/PNG/WebP/HEIC only); sample SVGs are ours"),
    "V5.2.8": ("N/A", "No user-supplied templates or Markdown"),
    "V5.3.1": ("Met", "React text nodes; JSON responses"),
    "V5.3.2": ("Met", "UTF-8 throughout; Tamil and Hindi round-trip in tests"),
    "V5.3.3": ("Met", "React escaping; lint forbids dangerouslySetInnerHTML, eval, new Function, javascript: URLs"),
    "V5.3.4": ("Met", "SQLAlchemy ORM / bound parameters"),
    "V5.3.5": ("N/A", "Parameterized everywhere"),
    "V5.3.6": ("Met", "JSON via framework serializers; JSON.parse on the client"),
    "V5.3.7": ("N/A", "No LDAP"),
    "V5.3.8": ("Met", "No OS commands from the app"),
    "V5.3.9": ("Met", "Photo keys built by the server and regex-checked; the SPA route resolves paths inside dist only"),
    "V5.3.10": NO_XML,
    "V5.4.1": ("Met", "Python and TypeScript (memory-safe); Pillow is the native exposure (S2 re-encode, pixel cap)"),
    "V5.4.2": ("Met", "Log and template formats are constants"),
    "V5.4.3": ("Met", "Money as Python ints (no overflow) with range limits on inputs; BigInteger columns"),
    "V5.5.1": ("N/A", "No serialized objects exchanged"),
    "V5.5.2": NO_XML,
    "V5.5.3": ("Met", "Only JSON and YAML safe_load (our own files)"),
    "V5.5.4": ("Met", "fetch().json() / JSON.parse"),
    # V6 crypto
    "V6.1.1": ("Gap", "S3/S5: RDS, S3, logs, secrets encrypted with customer-managed KMS keys"),
    "V6.1.2": ("N/A", "No health data"),
    "V6.1.3": ("Gap", "S3/S5: payments and loan data in KMS-encrypted RDS"),
    "V6.2.1": ("Met", "Signature failures reject (TokenError -> 401, bad HMAC -> 401)"),
    "V6.2.2": ("Met", "HMAC-SHA256, JWT via PyJWT; ES256 via KMS in S1"),
    "V6.2.3": ("N/A", "No custom encryption; AWS KMS/TLS handle it"),
    "V6.2.4": ("Met", "Algorithms pinned (ES256 only for tokens and assertions); kid in every token for rotation"),
    "V6.2.5": ("Met", "No ECB/PKCS1v1.5/MD5/SHA1 used for security"),
    "V6.2.6": ("Met", "Nonces/ids from secrets module; jti for partner assertions in S1"),
    "V6.3.1": ("Met", "secrets.token_hex / token_urlsafe for ids and secrets"),
    "V6.3.2": ("N/A", "Ids are 64-bit random hex, not GUIDs"),
    "V6.4.1": ("Gap", "S5: Secrets Manager with rotation"),
    "V6.4.2": ("Met", "Token signing in KMS: the app sends a digest and gets a signature (KmsSigner)"),
    # V7 logging
    "V7.1.1": ("Met", "Redaction filter on every laabam handler: tokens, JWTs, cookies, API keys, phones, emails, UPI ids (tests)"),
    "V7.1.2": ("Met", "No customer phones stored (C2); the audit log holds HMAC digests under a per-shop key, never words (C3); record() refuses raw text"),
    "V7.1.3": ("Met", "Audit: sign-ins, revocations, exports, deletion steps, retention; logs: refusals, access denials, last-resort errors"),
    "V7.1.4": ("Met", "Audit has who/what/when/subject; every log line carries the request id (X-Request-Id, from the ALB or ours)"),
    "V7.2.1": ("Met", "Sign-in, refusal, refresh and revocation decisions logged without tokens, with the request id"),
    "V7.2.2": ("Met", "Every 401, 403, 429 and API 404 (incl. another shop's ids) logged as access_denied with route, IP, request id (tests)"),
    "V7.3.1": ("Met", "Logs are JSON objects (json.dumps escapes every value), so input can't forge lines"),
    "V7.3.3": ("Partial", "Audit append-only trigger; S5 CloudWatch + CloudTrail with validation, restricted IAM"),
    "V7.3.4": ("Met", "Stored in UTC (timezone-aware); IST only for display"),
    "V7.4.1": ("Met", "Short, generic messages; partner errors become 'That didn't work. Please try again.'"),
    "V7.4.2": ("Met", "Typed exceptions mapped to responses; worker retries with backoff"),
    "V7.4.3": ("Met", "Last-resort handler: logs the redacted error with the request id, returns only the id (test)"),
    # V8 data protection
    "V8.1.1": ("Partial", "API responses Cache-Control: no-store; CloudFront won't cache /v1 (S5)"),
    "V8.1.2": ("Met", "Photos under the shop's key; originals deleted after the re-encode; served only to their shop"),
    "V8.1.3": ("Met", "Small explicit request models"),
    "V8.1.4": ("Partial", "App rate limits: sign-in, refresh, demo shops, voice, ask, read-aloud, uploads, summaries; WAF and alarms S5"),
    "V8.2.1": ("Met", "Cache-Control: no-store on every API response"),
    "V8.2.2": ("Met", "No sensitive data in browser storage (sessionStorage holds only demo sample-bill ids)"),
    "V8.2.3": ("Partial", "Token in memory, gone on close; sign-out clears state in S1"),
    "V8.3.1": ("Met", "Sensitive data in bodies/headers; query strings carry only ids and periods"),
    "V8.3.2": ("Met", "GET /v1/me/export (zip of all the shop's data and photos, audited, rate-limited); DELETE /v1/me after a grace period, cancellable"),
    "V8.3.3": ("Met", "Plain-language 'What Laabam keeps' in Settings, in the owner's language"),
    "V8.3.4": ("Met", "Sensitive data identified in THREAT_MODEL.md §1"),
    "V8.3.5": ("Met", "Audit of confirms, payments, approvals, exports, deletion requests and cancels"),
    "V8.3.6": ("N/A", "Python/JS memory can't be reliably zeroed; audio and photo bytes live only for the request or job"),
    "V8.3.7": ("Gap", "S3/S5: KMS encryption at rest"),
    "V8.3.8": ("Met", "Daily retention sweep: transcripts, heard text and photos older than 365 days (LAABAM_RETENTION_DAYS); audit kept"),
    # V9 communications
    "V9.1.1": ("Gap", "S5: HTTPS only via CloudFront (HTTP->HTTPS redirect, HSTS)"),
    "V9.1.2": ("Gap", "S5: CloudFront TLSv1.2_2021 security policy; ALB ELBSecurityPolicy-TLS13-1-2"),
    "V9.1.3": ("Gap", "S5: TLS 1.2+ only"),
    "V9.2.1": ("Gap", "S5: ACM certificates; partner certs verified by default"),
    "V9.2.2": ("Gap", "S3/S5: TLS to RDS and partners; VPC endpoints for AWS"),
    "V9.2.3": ("Met", "Partner calls https with verification; RDS verify-full with the RDS CA"),
    "V9.2.4": ("N/A", "CloudFront/ACM handle revocation and stapling"),
    # V10 malicious code
    "V10.2.1": ("Met", "No phone-home code; outbound calls only to configured partners (allowlist S2)"),
    "V10.2.2": ("Met", "Microphone only while holding the button; camera only through the file input"),
    "V10.3.1": ("Met", "PWA updates come from our own origin over HTTPS (S5)"),
    "V10.3.2": ("Partial", "Bundled assets, no CDNs; images by digest, actions by SHA, locks hashed, downloads checked by sha256; commit signing not yet required"),
    "V10.3.3": ("Gap", "S5: Route 53 records only for live resources; no dangling CloudFront aliases"),
    # V11 business logic
    "V11.1.1": ("Met", "Bill flow is a state machine (draft -> confirmed -> paid); out-of-order calls refused"),
    "V11.1.2": ("Met", "QR only after confirm; payment matched to an open QR in time; QR expires in 5 minutes"),
    "V11.1.3": ("Met", "Limits on lines and amounts; loan only for a shortfall; per-shop rate limits on the costly actions"),
    "V11.1.4": ("Met", "Rate limits on auth, voice, ask, read-aloud, uploads and summaries per shop and per IP (Postgres buckets)"),
    "V11.1.5": ("Met", "Arithmetic checks, mismatch flags, owner confirmation, digit guard"),
    "V11.1.6": ("Met", "Row locks (SELECT ... FOR UPDATE) on bills, purchases, alerts, loan offers; atomic timer claim"),
    "V11.1.7": ("Partial", "Payment mismatches and duplicates flagged; S5 alarms on unusual events"),
    "V11.1.8": ("Gap", "S5: CloudWatch alarms (auth failures, WAF blocks, 5xx) to the owner's email"),
    # V12 files
    "V12.1.1": ("Met", "Presigned uploads limited to the exact size (10 MB max); JSON 64 KB; audio 2 MB"),
    "V12.1.2": ("N/A", "No archives accepted"),
    "V12.1.3": ("Met", "Photo uploads 30 an hour per shop and per IP"),
    "V12.2.1": ("Met", "The worker decodes every photo (JPEG/PNG/WebP/HEIC only), checks pixels, re-encodes; audio by magic bytes"),
    "V12.3.1": ("Met", "Uploaded filenames never used; keys are <merchant>/<bill>.<ext>"),
    "V12.3.2": ("Met", "Filenames ignored"),
    "V12.3.3": ("Met", "Filenames ignored"),
    "V12.3.4": ("Met", "Filenames never used; photo responses typed image/jpeg with nosniff"),
    "V12.3.5": ("Met", "No file metadata passed to system APIs"),
    "V12.3.6": ("Met", "No code loaded from untrusted sources"),
    "V12.4.1": ("Met", "Photos in private storage (S3 in AWS) outside any web root, under the shop's prefix"),
    "V12.4.2": ("N/A", "Images are decoded and re-encoded to plain pixels (S2), which removes any payload; "
                       "no files are passed on as uploaded"),
    "V12.5.1": ("Met", "The SPA route serves files only from the build directory"),
    "V12.5.2": ("Met", "Photos are re-encoded JPEGs served with image/jpeg and nosniff: never run as HTML/JS"),
    "V12.6.1": ("Met", "Outbound allowlist (Sarvam, Paytm, Gemini in demo, the partner's JWKS); https only"),
    # V13 API
    "V13.1.1": ("Met", "One parser stack (FastAPI/Pydantic) for all JSON"),
    "V13.1.3": ("Met", "No keys or tokens in URLs"),
    "V13.1.4": ("Met", "Per-route owner dependency plus per-object merchant checks"),
    "V13.1.5": ("Met", "JSON routes refuse other content types (415); audio and photo routes check theirs"),
    "V13.2.1": ("Met", "Each route declares its method; others 405"),
    "V13.2.2": ("Met", "Strict request schemas, checked before the handler runs"),
    "V13.2.3": ("Met", "No cookie auth on API calls (S1 refresh cookie: SameSite=Strict + header)"),
    "V13.2.5": ("Met", "Content-Type checked on every body (RequestGuard)"),
    "V13.2.6": ("Partial", "Partner webhooks HMAC-signed; TLS everywhere in S5"),
    "V13.3.1": NO_XML,
    "V13.3.2": NO_XML,
    "V13.4.1": NO_XML,
    "V13.4.2": NO_XML,
    # V14 config
    "V14.1.1": ("Met", "Repeatable builds: hash-pinned locks, base images by digest, CI builds and smoke-tests the image on every push"),
    "V14.1.2": ("N/A", "No compiled code of ours"),
    "V14.1.3": ("Partial", "Hardened container: non-root, read-only root fs, no capabilities, no Server/Date headers, prod-safe defaults. S5: CloudFront/ALB config"),
    "V14.1.4": ("Gap", "S5: CDK deploys everything"),
    "V14.2.1": ("Met", "pip-audit (both locks) and npm audit (frontend) fail CI on known vulnerabilities; Trivy fails on fixable HIGH/CRITICAL in the image"),
    "V14.2.2": ("Met", "API docs and the OpenAPI map are off outside development; sandbox routes absent in prod"),
    "V14.2.3": ("Met", "Fonts and scripts self-hosted; no external CDNs"),
    "V14.2.4": ("Met", "PyPI and npm only"),
    "V14.2.5": ("Met", "CycloneDX SBOM of the image generated by Trivy in CI and kept as an artifact for 90 days"),
    "V14.2.6": ("Partial", "Adapters wrap partners; image decoding isolated in the worker (S2)"),
    "V14.3.2": ("Met", "No debug mode; docs off outside dev; generic 500s"),
    "V14.3.3": ("Partial", "Server header dropped on API responses; /api/health shows no versions outside dev; CloudFront S5"),
    "V14.4.1": ("Met", "JSON responses typed; files with their types"),
    "V14.4.2": ("Met", "The export sets Content-Disposition: attachment; JSON responses are never rendered"),
    "V14.4.3": ("Gap", "S5: strict CSP from CloudFront"),
    "V14.4.4": ("Partial", "API responses send nosniff; CloudFront for the PWA in S5"),
    "V14.4.5": ("Gap", "S5: HSTS"),
    "V14.4.6": ("Gap", "S5: Referrer-Policy"),
    "V14.4.7": ("Gap", "S5: frame-ancestors 'none'"),
    "V14.5.1": ("Met", "Only declared methods; OPTIONS for CORS"),
    "V14.5.2": ("Met", "Origin never used for auth"),
    "V14.5.3": ("Met", "No cross-origin access by default (LAABAM_CORS_ORIGINS empty; '*' refused outside dev)"),
    "V14.5.4": ("Gap", "S5: ALB accepts only CloudFront's secret header + prefix list"),
}

rows = [r for r in csv.DictReader(open(src, encoding="utf-8")) if r["level2"].strip()]
missing, lines = [], []
counts: Counter = Counter()
chapter = None
for r in rows:
    rid = r["req_id"]
    if rid.startswith("V2."):
        st = NO_OTP if rid[:5] in ("V2.6.", "V2.7.", "V2.8.") else NO_PW
        if rid in ("V2.2.1",):
            st = ("Met", "Rate limits on partner sign-in and refresh per IP; assertions single-use (jti) and 5-minute")
        if rid in ("V2.2.2",):
            st = ("N/A", "No SMS/email authenticators")
        if rid in ("V2.2.3", "V2.5.5"):
            st = ("Met", "A new device's sign-in is announced on the shop's Soundbox in its language and listed in Settings")
        if rid == "V2.5.4":
            st = ("Met", "No shared or default accounts; each shop signs in as itself")
        if rid in ("V2.9.1", "V2.9.2", "V2.9.3"):
            st = ("Met", "Partner keys from the configured https JWKS (P-256, ES256 only); jti (8-128 chars) stored to stop replay")
        if rid in ("V2.10.1", "V2.10.2", "V2.10.3", "V2.10.4"):
            st = ("Gap", "S5: task roles (no static keys), Secrets Manager, DB credential rotation")
    else:
        st = S.get(rid)
    if st is None:
        missing.append(rid)
        st = ("?", "")
    counts[st[0]] += 1
    if r["chapter_id"] != chapter:
        chapter = r["chapter_id"]
        lines.append(f"\n## {chapter} {r['chapter_name']}\n\n| ID | Requirement | Status | Notes |\n|---|---|---|---|")
    d = re.sub(r"\s*\(\[C\d+\]\([^)]*\)\)", "", r["req_description"]).replace("|", "/").strip()
    if len(d) > 170:
        d = d[:167].rsplit(" ", 1)[0] + "..."
    lines.append(f"| {rid} | {d} | {st[0]} | {st[1]} |")

if missing:
    sys.exit("no status for: " + ", ".join(missing))
PHASE = "S4"
head = f"""# OWASP ASVS 4.0.3, Level 2: Laabam checklist

Source: the official ASVS 4.0.3 requirements (github.com/OWASP/ASVS, tag v4.0.3), Level 2 rows only
({len(rows)} items). Status after phase {PHASE}. Each Gap or
Partial names the phase that closes it; the S6 report updates this table.

**Met** {counts['Met']} · **Partial** {counts['Partial']} · **Gap** {counts['Gap']} · **N/A** {counts['N/A']}

Statuses: Met (built, and tested where testable) · Partial (part built; the note says what's left) ·
Gap (not built; planned in the phase named) · N/A (doesn't apply, with the reason).
Requirement text is shortened for the table; the CSV has the full wording.
"""
open(out, "w", encoding="utf-8").write(head + "\n".join(lines) + "\n")
print(dict(counts))
