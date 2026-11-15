# OWASP ASVS 4.0.3, Level 2: Laabam checklist

Source: the official ASVS 4.0.3 requirements (github.com/OWASP/ASVS, tag v4.0.3), Level 2 rows only
(259 items). Status after phase S3. Each Gap or
Partial names the phase that closes it; the S6 report updates this table.

**Met** 141 · **Partial** 23 · **Gap** 30 · **N/A** 65

Statuses: Met (built, and tested where testable) · Partial (part built; the note says what's left) ·
Gap (not built; planned in the phase named) · N/A (doesn't apply, with the reason).
Requirement text is shortened for the table; the CSV has the full wording.

## V1 Architecture, Design and Threat Modeling

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V1.1.1 | Verify the use of a secure software development lifecycle that addresses security in all stages of development. | Partial | Phased plan with security gates (SECURITY_PLAN.md); CI security gates S4 |
| V1.1.2 | Verify the use of threat modeling for every design change or sprint planning to identify threats, plan for countermeasures, facilitate appropriate risk responses, and... | Met | THREAT_MODEL.md; review on every flow/adapter change |
| V1.1.3 | Verify that all user stories and features contain functional security constraints, such as "As a user, I should be able to view and edit my profile. I should not be... | Partial | BUILD_PLAN rules (no QR before confirm, no figure from a model) are security constraints; per-feature constraints added from S1 |
| V1.1.4 | Verify documentation and justification of all the application's trust boundaries, components, and significant data flows. | Met | Trust boundaries TB1-TB5 in THREAT_MODEL.md §3 |
| V1.1.5 | Verify definition and security analysis of the application's high-level architecture and all connected remote services. | Met | Architecture in SECURITY_PLAN.md and THREAT_MODEL.md |
| V1.1.6 | Verify implementation of centralized, simple (economy of design), vetted, secure, and reusable security controls to avoid duplicate, missing, ineffective, or insecure... | Partial | Central owner dependency, security.py, llm guard; RLS and rate limiter central in S1 |
| V1.1.7 | Verify availability of a secure coding checklist, security requirements, guideline, or policy to all developers and testers. | Partial | CLAUDE.md rules + SECURITY_PLAN.md; a secure-coding checklist in docs/security (S6) |
| V1.2.1 | Verify the use of unique or special low-privilege operating system accounts for all application components, services, and servers. | Partial | S1: the app connects as laabam_app (no table ownership, no BYPASSRLS, no CREATE); migrations as the owner. S4/S5: non-root container |
| V1.2.2 | Verify that communications between application components, including APIs, middleware and data layers, are authenticated. Components should have the least necessary... | Partial | S3: the app reaches Postgres only with TLS verify-full against the RDS CA (config refuses less in demo/prod). S5: ALB only from CloudFront |
| V1.2.3 | Verify that the application uses a single vetted authentication mechanism that is known to be secure, can be extended to include strong authentication, and has... | Met | One mechanism: partner assertion -> device session (ES256 access + rotating refresh); the demo uses the same path |
| V1.2.4 | Verify that all authentication pathways and identity management APIs implement consistent authentication security control strength, such that there are no weaker... | Met | Sandbox and partner sign-in share the verifier, AuthService and the owner dependency |
| V1.4.1 | Verify that trusted enforcement points, such as access control gateways, servers, and serverless functions, enforce access controls. Never enforce access controls on... | Met | Every check server-side in the owner dependency and service layer |
| V1.4.4 | Verify the application uses a single and well-vetted access control mechanism for accessing protected data and resources. All requests must pass through this single... | Met | merchant_id scoping in every query plus Postgres RLS (tenancy.py); generated cross-tenant tests |
| V1.4.5 | Verify that attribute or feature-based access control is used whereby the code checks the user's authorization for a feature/data item rather than just their role.... | N/A | One role (the owner of the shop); no roles or features to grant |
| V1.5.1 | Verify that input and output requirements clearly define how to handle and process data based on type, content, and applicable laws, regulations, and other policy... | Met | Strict Pydantic models (extra=forbid) with limits on every field; audio and photos checked by type and bytes |
| V1.5.2 | Verify that serialization is not used when communicating with untrusted clients. If this is not possible, ensure that adequate integrity controls (and possibly... | Met | JSON only; no pickle or object serialization with clients |
| V1.5.3 | Verify that input validation is enforced on a trusted service layer. | Met | Validation in the API (Pydantic) and the service layer |
| V1.5.4 | Verify that output encoding occurs close to or by the interpreter for which it is intended. | Met | React escapes output; SQL via ORM parameters |
| V1.6.1 | Verify that there is an explicit policy for management of cryptographic keys and that a cryptographic key lifecycle follows a key management standard such as NIST SP... | Partial | Token key in KMS (ES256, kid); per-shop audit keys wrapped by KMS (envelope, shop id as context). Key policy document in S5 |
| V1.6.2 | Verify that consumers of cryptographic services protect key material and other secrets by using key vaults or API based alternatives. | Partial | Token signing key in KMS (KmsSigner; config refuses local keys in demo/prod); other secrets S5 |
| V1.6.3 | Verify that all keys and passwords are replaceable and are part of a well-defined process to re-encrypt sensitive data. | Partial | Tokens carry kid, so the signing key can rotate; DB credential rotation S5 |
| V1.6.4 | Verify that the architecture treats client-side secrets--such as symmetric keys, passwords, or API tokens--as insecure and never uses them to protect or access... | Met | No secrets in the PWA; the token is per-owner and short-lived (shorter in S1) |
| V1.7.1 | Verify that a common logging format and approach is used across the system. | Met | One JSON-lines logger for laabam.*: request id on every line, redaction filter, tests (logs.py) |
| V1.7.2 | Verify that logs are securely transmitted to a preferably remote system for analysis, detection, alerting, and escalation. | Gap | S5: CloudWatch Logs over a VPC endpoint, KMS-encrypted |
| V1.8.1 | Verify that all sensitive data is identified and classified into protection levels. | Met | Assets A1-A10 classified in THREAT_MODEL.md §1 |
| V1.8.2 | Verify that all protection levels have an associated set of protection requirements, such as encryption requirements, integrity requirements, retention, privacy and... | Partial | Retention sweep (12 months), keyed audit digests, export/delete (S3); encryption at rest settings S5 |
| V1.9.1 | Verify the application encrypts communications between components, particularly when these components are in different containers, systems, sites, or cloud providers. | Partial | S3: app to RDS verify-full; S5: CloudFront-ALB-ECS TLS |
| V1.9.2 | Verify that application components verify the authenticity of each side in a communication link to prevent person-in-the-middle attacks. For example, application... | Met | RDS: sslmode=verify-full with the RDS CA bundle; partners: https only with verification (httpx default) |
| V1.10.1 | Verify that a source code control system is in use, with procedures to ensure that check-ins are accompanied by issues or change tickets. The source code control... | Met | Git with descriptive commits; branch protection S4 |
| V1.11.1 | Verify the definition and documentation of all application components in terms of the business or security functions they provide. | Met | Components and their roles in BUILD_PLAN.md and THREAT_MODEL.md |
| V1.11.2 | Verify that all high-value business logic flows, including authentication, session management and access control, do not share unsynchronized state. | Partial | Bills, payments, loans are transactional with row locks; auth flows in S1 |
| V1.12.2 | Verify that user-uploaded files - if required to be displayed or downloaded from the application - are served by either octet stream downloads, or from an unrelated... | Met | Photos re-encoded by the worker, served only to their shop as image/jpeg with nosniff |
| V1.14.1 | Verify the segregation of components of differing trust levels through well-defined security controls, firewall rules, API gateways, reverse proxies, cloud-based... | Gap | S5: VPC private subnets, SGs, ALB from CloudFront only |
| V1.14.2 | Verify that binary signatures, trusted connections, and verified endpoints are used to deploy binaries to remote devices. | Gap | S4: images by digest, OIDC deploy, ECR |
| V1.14.3 | Verify that the build pipeline warns of out-of-date or insecure components and takes appropriate actions. | Gap | S4: pip-audit, npm audit, Dependabot |
| V1.14.4 | Verify that the build pipeline contains a build step to automatically build and verify the secure deployment of the application, particularly if the application... | Gap | S4/S5: CDK synth + cdk-nag in CI |
| V1.14.5 | Verify that application deployments adequately sandbox, containerize and/or isolate at the network level to delay and deter attackers from attacking other... | Gap | S5: Fargate tasks in private subnets, no public IPs |
| V1.14.6 | Verify the application does not use unsupported, insecure, or deprecated client-side technologies such as NSAPI plugins, Flash, Shockwave, ActiveX, Silverlight, NACL,... | Met | React PWA; no Flash/ActiveX/NSAPI |

## V2 Authentication

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V2.1.1 | Verify that user set passwords are at least 12 characters in length (after multiple spaces are combined). | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.2 | Verify that passwords of at least 64 characters are permitted, and that passwords of more than 128 characters are denied. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.3 | Verify that password truncation is not performed. However, consecutive multiple spaces may be replaced by a single space. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.4 | Verify that any printable Unicode character, including language neutral characters such as spaces and Emojis are permitted in passwords. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.5 | Verify users can change their password. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.6 | Verify that password change functionality requires the user's current and new password. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.7 | Verify that passwords submitted during account registration, login, and password change are checked against a set of breached passwords either locally (such as the... | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.8 | Verify that a password strength meter is provided to help users set a stronger password. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.9 | Verify that there are no password composition rules limiting the type of characters permitted. There should be no requirement for upper or lower case or numbers or... | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.10 | Verify that there are no periodic credential rotation or password history requirements. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.11 | Verify that "paste" functionality, browser password helpers, and external password managers are permitted. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.1.12 | Verify that the user can choose to either temporarily view the entire masked password, or temporarily view the last typed character of the password on platforms that... | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.2.1 | Verify that anti-automation controls are effective at mitigating breached credential testing, brute force, and account lockout attacks. Such controls include blocking... | Met | Rate limits on partner sign-in and refresh per IP; assertions single-use (jti) and 5-minute |
| V2.2.2 | Verify that the use of weak authenticators (such as SMS and email) is limited to secondary verification and transaction approval and not as a replacement for more... | N/A | No SMS/email authenticators |
| V2.2.3 | Verify that secure notifications are sent to users after updates to authentication details, such as credential resets, email or address changes, logging in from... | Met | A new device's sign-in is announced on the shop's Soundbox in its language and listed in Settings |
| V2.3.1 | Verify system generated initial passwords or activation codes SHOULD be securely randomly generated, SHOULD be at least 6 characters long, and MAY contain letters and... | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.3.2 | Verify that enrollment and use of user-provided authentication devices are supported, such as a U2F or FIDO tokens. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.3.3 | Verify that renewal instructions are sent with sufficient time to renew time bound authenticators. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.4.1 | Verify that passwords are stored in a form that is resistant to offline attacks. Passwords SHALL be salted and hashed using an approved one-way key derivation or... | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.4.2 | Verify that the salt is at least 32 bits in length and be chosen arbitrarily to minimize salt value collisions among stored hashes. For each credential, a unique salt... | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.4.3 | Verify that if PBKDF2 is used, the iteration count SHOULD be as large as verification server performance will allow, typically at least 100,000 iterations. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.4.4 | Verify that if bcrypt is used, the work factor SHOULD be as large as verification server performance will allow, with a minimum of 10. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.4.5 | Verify that an additional iteration of a key derivation function is performed, using a salt value that is secret and known only to the verifier. Generate the salt... | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.5.1 | Verify that a system generated initial activation or recovery secret is not sent in clear text to the user. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.5.2 | Verify password hints or knowledge-based authentication (so-called "secret questions") are not present. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.5.3 | Verify password credential recovery does not reveal the current password in any way. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.5.4 | Verify shared or default accounts are not present (e.g. "root", "admin", or "sa"). | Met | No shared or default accounts; each shop signs in as itself |
| V2.5.5 | Verify that if an authentication factor is changed or replaced, that the user is notified of this event. | Met | A new device's sign-in is announced on the shop's Soundbox in its language and listed in Settings |
| V2.5.6 | Verify forgotten password, and other recovery paths use a secure recovery mechanism, such as time-based OTP (TOTP) or other soft token, mobile push, or another... | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.5.7 | Verify that if OTP or multi-factor authentication factors are lost, that evidence of identity proofing is performed at the same level as during enrollment. | N/A | No passwords: owners sign in through Paytm for Business (signed partner assertion, S1); the demo uses the sandbox login |
| V2.6.1 | Verify that lookup secrets can be used only once. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.6.2 | Verify that lookup secrets have sufficient randomness (112 bits of entropy), or if less than 112 bits of entropy, salted with a unique and random 32-bit salt and... | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.6.3 | Verify that lookup secrets are resistant to offline attacks, such as predictable values. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.7.1 | Verify that clear text out of band (NIST "restricted") authenticators, such as SMS or PSTN, are not offered by default, and stronger alternatives such as push... | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.7.2 | Verify that the out of band verifier expires out of band authentication requests, codes, or tokens after 10 minutes. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.7.3 | Verify that the out of band verifier authentication requests, codes, or tokens are only usable once, and only for the original authentication request. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.7.4 | Verify that the out of band authenticator and verifier communicates over a secure independent channel. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.7.5 | Verify that the out of band verifier retains only a hashed version of the authentication code. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.7.6 | Verify that the initial authentication code is generated by a secure random number generator, containing at least 20 bits of entropy (typically a six digital random... | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.8.1 | Verify that time-based OTPs have a defined lifetime before expiring. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.8.2 | Verify that symmetric keys used to verify submitted OTPs are highly protected, such as by using a hardware security module or secure operating system based key storage. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.8.3 | Verify that approved cryptographic algorithms are used in the generation, seeding, and verification of OTPs. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.8.4 | Verify that time-based OTP can be used only once within the validity period. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.8.5 | Verify that if a time-based multi-factor OTP token is re-used during the validity period, it is logged and rejected with secure notifications being sent to the holder... | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.8.6 | Verify physical single-factor OTP generator can be revoked in case of theft or other loss. Ensure that revocation is immediately effective across logged in sessions,... | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.8.7 | Verify that biometric authenticators are limited to use only as secondary factors in conjunction with either something you have and something you know. | N/A | No OTPs, lookup secrets or out-of-band codes in v1 (identity comes from Paytm) |
| V2.9.1 | Verify that cryptographic keys used in verification are stored securely and protected against disclosure, such as using a Trusted Platform Module (TPM) or Hardware... | Met | Partner keys from the configured https JWKS (P-256, ES256 only); jti (8-128 chars) stored to stop replay |
| V2.9.2 | Verify that the challenge nonce is at least 64 bits in length, and statistically unique or unique over the lifetime of the cryptographic device. | Met | Partner keys from the configured https JWKS (P-256, ES256 only); jti (8-128 chars) stored to stop replay |
| V2.9.3 | Verify that approved cryptographic algorithms are used in the generation, seeding, and verification. | Met | Partner keys from the configured https JWKS (P-256, ES256 only); jti (8-128 chars) stored to stop replay |
| V2.10.1 | Verify that intra-service secrets do not rely on unchanging credentials such as passwords, API keys or shared accounts with privileged access. | Gap | S5: task roles (no static keys), Secrets Manager, DB credential rotation |
| V2.10.2 | Verify that if passwords are required for service authentication, the service account used is not a default credential. (e.g. root/root or admin/admin are default in... | Gap | S5: task roles (no static keys), Secrets Manager, DB credential rotation |
| V2.10.3 | Verify that passwords are stored with sufficient protection to prevent offline recovery attacks, including local system access. | Gap | S5: task roles (no static keys), Secrets Manager, DB credential rotation |
| V2.10.4 | Verify passwords, integrations with databases and third-party systems, seeds and internal secrets, and API keys are managed securely and not included in the source... | Gap | S5: task roles (no static keys), Secrets Manager, DB credential rotation |

## V3 Session Management

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V3.1.1 | Verify the application never reveals session tokens in URL parameters. | Met | Bearer token in the Authorization header, never in URLs |
| V3.2.1 | Verify the application generates a new session token on user authentication. | Met | Every sign-in starts a new device session and tokens; refresh rotates the refresh token |
| V3.2.2 | Verify that session tokens possess at least 64 bits of entropy. | Met | Refresh tokens 256-bit random (token_urlsafe(32)); access tokens ES256-signed |
| V3.2.3 | Verify the application only stores session tokens in the browser using secure methods such as appropriately secured cookies (see section 3.4) or HTML 5 session storage. | Met | Access token in memory only; refresh token in a __Host- HttpOnly Secure SameSite=Strict cookie |
| V3.2.4 | Verify that session tokens are generated using approved cryptographic algorithms. | Met | PyJWT signing; secrets module for random |
| V3.3.1 | Verify that logout and expiration invalidate the session token, such that the back button or a downstream relying party does not resume an authenticated session,... | Met | Sign-out and revocation end the session in the DB; access tokens are checked against it on every request |
| V3.3.2 | If authenticators permit users to remain logged in, verify that re-authentication occurs periodically both when actively used or after an idle period. | Met | Access 15 min; refresh idle 30 days; session absolute 90 days, then the partner sign-in again |
| V3.3.3 | Verify that the application gives the option to terminate all other active sessions after a successful password change (including change via password reset/recovery),... | Met | Settings lists every signed-in device and signs any of them out |
| V3.3.4 | Verify that users are able to view and (having re-entered login credentials) log out of any or all currently active sessions and devices. | Met | GET /v1/sessions, DELETE /v1/sessions/{id} (re-authentication is the partner's) |
| V3.4.1 | Verify that cookie-based session tokens have the 'Secure' attribute set. | Met | Refresh cookie Secure (test_refresh_cookie_is_host_only_httponly_strict) |
| V3.4.2 | Verify that cookie-based session tokens have the 'HttpOnly' attribute set. | Met | Refresh cookie HttpOnly |
| V3.4.3 | Verify that cookie-based session tokens utilize the 'SameSite' attribute to limit exposure to cross-site request forgery attacks. | Met | Refresh cookie SameSite=Strict, plus the X-Laabam-Refresh header on refresh |
| V3.4.4 | Verify that cookie-based session tokens use the "__Host-" prefix so cookies are only sent to the host that initially set the cookie. | Met | __Host-laabam_refresh (Path=/, no Domain) |
| V3.4.5 | Verify that if the application is published under a domain name with other applications that set or use session cookies that might disclose the session cookies, set... | Met | __Host- cookie: this host only |
| V3.5.1 | Verify the application allows users to revoke OAuth tokens that form trust relationships with linked applications. | N/A | No OAuth grants to third-party apps |
| V3.5.2 | Verify the application uses session tokens rather than static API secrets and keys, except with legacy implementations. | Met | Session tokens, not static API keys, for the owner |
| V3.5.3 | Verify that stateless session tokens use digital signatures, encryption, and other countermeasures to protect against tampering, enveloping, replay, null cipher, and... | Met | ES256 (KMS), aud/iss/exp/iat/sid/jti required, lifetime capped, session checked live |
| V3.7.1 | Verify the application ensures a full, valid login session or requires re-authentication or secondary verification before allowing any sensitive transactions or... | Met | Deleting the shop needs the shop's name typed and a sign-in within the last 10 minutes (403 otherwise) |

## V4 Access Control

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V4.1.1 | Verify that the application enforces access control rules on a trusted service layer, especially if client-side access control is present and could be bypassed. | Met | Owner dependency on every /v1 route; checks in the service layer |
| V4.1.2 | Verify that all user and data attributes and policy information used by access controls cannot be manipulated by end users unless specifically authorized. | Met | merchant_id comes from the verified token, never from the request |
| V4.1.3 | Verify that the principle of least privilege exists - users should only be able to access functions, data files, URLs, controllers, services, and other resources, for... | Met | Owners see only their shop; the DB enforces it (RLS, laabam_app); IAM least privilege S5 |
| V4.1.5 | Verify that access controls fail securely including when an exception occurs. | Met | Unknown or foreign ids raise NotFound -> 404; token errors -> 401 |
| V4.2.1 | Verify that sensitive data and APIs are protected against Insecure Direct Object Reference (IDOR) attacks targeting creation, reading, updating and deletion of... | Met | test_every_id_route_hides_other_shops (generated from the route list) and RLS tests |
| V4.2.2 | Verify that the application or framework enforces a strong anti-CSRF mechanism to protect authenticated functionality, and effective anti-automation or anti-CSRF... | Met | Bearer tokens (not cookies) on API calls; the S1 refresh cookie is SameSite=Strict + a custom header check |
| V4.3.1 | Verify administrative interfaces use appropriate multi-factor authentication to prevent unauthorized use. | N/A | No administrative interface; AWS console access is outside the app (MFA in S5 runbook) |
| V4.3.2 | Verify that directory browsing is disabled unless deliberately desired. Additionally, applications should not allow discovery or disclosure of file or directory... | Met | No directory listing; the SPA route serves only built files; S5 S3 private with OAC |
| V4.3.3 | Verify the application has additional authorization (such as step up or adaptive authentication) for lower value systems, and / or segregation of duties for high... | Met | Step-up for deleting the shop: a sign-in in the last 10 minutes, plus typed confirmation |

## V5 Validation, Sanitization and Encoding

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V5.1.1 | Verify that the application has defenses against HTTP parameter pollution attacks, particularly if the application framework makes no distinction about the source of... | Met | A query key given twice is refused (RequestGuard, 400) |
| V5.1.2 | Verify that frameworks protect against mass parameter assignment attacks, or that the application has countermeasures to protect against unsafe parameter assignment,... | Met | Every request model forbids unknown fields (Strict, extra=forbid) |
| V5.1.3 | Verify that all input (HTML form fields, REST requests, URL parameters, HTTP headers, cookies, batch files, RSS feeds, etc) is validated using positive validation... | Met | Bodies, params and headers typed and length-limited; JSON 64 KB cap; audio and photo size caps |
| V5.1.4 | Verify that structured data is strongly typed and validated against a defined schema including allowed characters, length and pattern (e.g. credit card numbers,... | Met | Pydantic schemas with types, ranges and lengths; unknown fields refused |
| V5.1.5 | Verify that URL redirects and forwards only allow destinations which appear on an allow list, or show a warning when redirecting to potentially untrusted content. | Met | No redirects or forwards built from input |
| V5.2.1 | Verify that all untrusted HTML input from WYSIWYG editors or similar is properly sanitized with an HTML sanitizer library or framework feature. | N/A | No HTML input |
| V5.2.2 | Verify that unstructured data is sanitized to enforce safety measures such as allowed characters and length. | Met | Free text (orders, names, questions) length-limited and normalised |
| V5.2.3 | Verify that the application sanitizes user input before passing to mail systems to protect against SMTP or IMAP injection. | N/A | No email; SMS adapter is a sandbox (C2) |
| V5.2.4 | Verify that the application avoids the use of eval() or other dynamic code execution features. Where there is no alternative, any user input being included must be... | Met | No eval or dynamic code |
| V5.2.5 | Verify that the application protects against template injection attacks by ensuring that any user input being included is sanitized or sandboxed. | Met | Answer/summary templates are fixed strings from code; user text only fills values |
| V5.2.6 | Verify that the application protects against SSRF attacks, by validating or sanitizing untrusted data or HTTP file metadata, such as filenames and URL input fields,... | Met | No URLs fetched from input; outbound client allows only allowlisted https hosts (outbound.py) |
| V5.2.7 | Verify that the application sanitizes, disables, or sandboxes user-supplied Scalable Vector Graphics (SVG) scriptable content, especially as they relate to XSS... | Met | SVG uploads not accepted (JPEG/PNG/WebP/HEIC only); sample SVGs are ours |
| V5.2.8 | Verify that the application sanitizes, disables, or sandboxes user-supplied scriptable or expression template language content, such as Markdown, CSS or XSL... | N/A | No user-supplied templates or Markdown |
| V5.3.1 | Verify that output encoding is relevant for the interpreter and context required. For example, use encoders specifically for HTML values, HTML attributes, JavaScript,... | Met | React text nodes; JSON responses |
| V5.3.2 | Verify that output encoding preserves the user's chosen character set and locale, such that any Unicode character point is valid and safely handled. | Met | UTF-8 throughout; Tamil and Hindi round-trip in tests |
| V5.3.3 | Verify that context-aware, preferably automated - or at worst, manual - output escaping protects against reflected, stored, and DOM based XSS. | Met | React escaping; lint forbids dangerouslySetInnerHTML, eval, new Function, javascript: URLs |
| V5.3.4 | Verify that data selection or database queries (e.g. SQL, HQL, ORM, NoSQL) use parameterized queries, ORMs, entity frameworks, or are otherwise protected from... | Met | SQLAlchemy ORM / bound parameters |
| V5.3.5 | Verify that where parameterized or safer mechanisms are not present, context-specific output encoding is used to protect against injection attacks, such as the use of... | N/A | Parameterized everywhere |
| V5.3.6 | Verify that the application protects against JSON injection attacks, JSON eval attacks, and JavaScript expression evaluation. | Met | JSON via framework serializers; JSON.parse on the client |
| V5.3.7 | Verify that the application protects against LDAP injection vulnerabilities, or that specific security controls to prevent LDAP injection have been implemented. | N/A | No LDAP |
| V5.3.8 | Verify that the application protects against OS command injection and that operating system calls use parameterized OS queries or use contextual command line output... | Met | No OS commands from the app |
| V5.3.9 | Verify that the application protects against Local File Inclusion (LFI) or Remote File Inclusion (RFI) attacks. | Met | Photo keys built by the server and regex-checked; the SPA route resolves paths inside dist only |
| V5.3.10 | Verify that the application protects against XPath injection or XML injection attacks. | N/A | No XML, SOAP or GraphQL anywhere |
| V5.4.1 | Verify that the application uses memory-safe string, safer memory copy and pointer arithmetic to detect or prevent stack, buffer, or heap overflows. | Met | Python and TypeScript (memory-safe); Pillow is the native exposure (S2 re-encode, pixel cap) |
| V5.4.2 | Verify that format strings do not take potentially hostile input, and are constant. | Met | Log and template formats are constants |
| V5.4.3 | Verify that sign, range, and input validation techniques are used to prevent integer overflows. | Met | Money as Python ints (no overflow) with range limits on inputs; BigInteger columns |
| V5.5.1 | Verify that serialized objects use integrity checks or are encrypted to prevent hostile object creation or data tampering. | N/A | No serialized objects exchanged |
| V5.5.2 | Verify that the application correctly restricts XML parsers to only use the most restrictive configuration possible and to ensure that unsafe features such as... | N/A | No XML, SOAP or GraphQL anywhere |
| V5.5.3 | Verify that deserialization of untrusted data is avoided or is protected in both custom code and third-party libraries (such as JSON, XML and YAML parsers). | Met | Only JSON and YAML safe_load (our own files) |
| V5.5.4 | Verify that when parsing JSON in browsers or JavaScript-based backends, JSON.parse is used to parse the JSON document. Do not use eval() to parse JSON. | Met | fetch().json() / JSON.parse |

## V6 Stored Cryptography

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V6.1.1 | Verify that regulated private data is stored encrypted while at rest, such as Personally Identifiable Information (PII), sensitive personal information, or data... | Gap | S3/S5: RDS, S3, logs, secrets encrypted with customer-managed KMS keys |
| V6.1.2 | Verify that regulated health data is stored encrypted while at rest, such as medical records, medical device details, or de-anonymized research records. | N/A | No health data |
| V6.1.3 | Verify that regulated financial data is stored encrypted while at rest, such as financial accounts, defaults or credit history, tax records, pay history,... | Gap | S3/S5: payments and loan data in KMS-encrypted RDS |
| V6.2.1 | Verify that all cryptographic modules fail securely, and errors are handled in a way that does not enable Padding Oracle attacks. | Met | Signature failures reject (TokenError -> 401, bad HMAC -> 401) |
| V6.2.2 | Verify that industry proven or government approved cryptographic algorithms, modes, and libraries are used, instead of custom coded cryptography. | Met | HMAC-SHA256, JWT via PyJWT; ES256 via KMS in S1 |
| V6.2.3 | Verify that encryption initialization vector, cipher configuration, and block modes are configured securely using the latest advice. | N/A | No custom encryption; AWS KMS/TLS handle it |
| V6.2.4 | Verify that random number, encryption or hashing algorithms, key lengths, rounds, ciphers or modes, can be reconfigured, upgraded, or swapped at any time, to protect... | Met | Algorithms pinned (ES256 only for tokens and assertions); kid in every token for rotation |
| V6.2.5 | Verify that known insecure block modes (i.e. ECB, etc.), padding modes (i.e. PKCS#1 v1.5, etc.), ciphers with small block sizes (i.e. Triple-DES, Blowfish, etc.), and... | Met | No ECB/PKCS1v1.5/MD5/SHA1 used for security |
| V6.2.6 | Verify that nonces, initialization vectors, and other single use numbers must not be used more than once with a given encryption key. The method of generation must be... | Met | Nonces/ids from secrets module; jti for partner assertions in S1 |
| V6.3.1 | Verify that all random numbers, random file names, random GUIDs, and random strings are generated using the cryptographic module's approved cryptographically secure... | Met | secrets.token_hex / token_urlsafe for ids and secrets |
| V6.3.2 | Verify that random GUIDs are created using the GUID v4 algorithm, and a Cryptographically-secure Pseudo-random Number Generator (CSPRNG). GUIDs created using other... | N/A | Ids are 64-bit random hex, not GUIDs |
| V6.4.1 | Verify that a secrets management solution such as a key vault is used to securely create, store, control access to and destroy secrets. | Gap | S5: Secrets Manager with rotation |
| V6.4.2 | Verify that key material is not exposed to the application but instead uses an isolated security module like a vault for cryptographic operations. | Met | Token signing in KMS: the app sends a digest and gets a signature (KmsSigner) |

## V7 Error Handling and Logging

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V7.1.1 | Verify that the application does not log credentials or payment details. Session tokens should only be stored in logs in an irreversible, hashed form. ([C9,... | Met | Redaction filter on every laabam handler: tokens, JWTs, cookies, API keys, phones, emails, UPI ids (tests) |
| V7.1.2 | Verify that the application does not log other sensitive data as defined under local privacy laws or relevant security policy. | Met | No customer phones stored (C2); the audit log holds HMAC digests under a per-shop key, never words (C3); record() refuses raw text |
| V7.1.3 | Verify that the application logs security relevant events including successful and failed authentication events, access control failures, deserialization failures and... | Met | Audit: sign-ins, revocations, exports, deletion steps, retention; logs: refusals, access denials, last-resort errors |
| V7.1.4 | Verify that each log event includes necessary information that would allow for a detailed investigation of the timeline when an event happens. | Met | Audit has who/what/when/subject; every log line carries the request id (X-Request-Id, from the ALB or ours) |
| V7.2.1 | Verify that all authentication decisions are logged, without storing sensitive session tokens or passwords. This should include requests with relevant metadata needed... | Met | Sign-in, refusal, refresh and revocation decisions logged without tokens, with the request id |
| V7.2.2 | Verify that all access control decisions can be logged and all failed decisions are logged. This should include requests with relevant metadata needed for security... | Met | Every 401, 403, 429 and API 404 (incl. another shop's ids) logged as access_denied with route, IP, request id (tests) |
| V7.3.1 | Verify that all logging components appropriately encode data to prevent log injection. | Met | Logs are JSON objects (json.dumps escapes every value), so input can't forge lines |
| V7.3.3 | Verify that security logs are protected from unauthorized access and modification. | Partial | Audit append-only trigger; S5 CloudWatch + CloudTrail with validation, restricted IAM |
| V7.3.4 | Verify that time sources are synchronized to the correct time and time zone. Strongly consider logging only in UTC if systems are global to assist with post-incident... | Met | Stored in UTC (timezone-aware); IST only for display |
| V7.4.1 | Verify that a generic message is shown when an unexpected or security sensitive error occurs, potentially with a unique ID which support personnel can use to investigate. | Met | Short, generic messages; partner errors become 'That didn't work. Please try again.' |
| V7.4.2 | Verify that exception handling (or a functional equivalent) is used across the codebase to account for expected and unexpected error conditions. | Met | Typed exceptions mapped to responses; worker retries with backoff |
| V7.4.3 | Verify that a "last resort" error handler is defined which will catch all unhandled exceptions. | Met | Last-resort handler: logs the redacted error with the request id, returns only the id (test) |

## V8 Data Protection

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V8.1.1 | Verify the application protects sensitive data from being cached in server components such as load balancers and application caches. | Partial | API responses Cache-Control: no-store; CloudFront won't cache /v1 (S5) |
| V8.1.2 | Verify that all cached or temporary copies of sensitive data stored on the server are protected from unauthorized access or purged/invalidated after the authorized... | Met | Photos under the shop's key; originals deleted after the re-encode; served only to their shop |
| V8.1.3 | Verify the application minimizes the number of parameters in a request, such as hidden fields, Ajax variables, cookies and header values. | Met | Small explicit request models |
| V8.1.4 | Verify the application can detect and alert on abnormal numbers of requests, such as by IP, user, total per hour or day, or whatever makes sense for the application. | Partial | App rate limits: sign-in, refresh, demo shops, voice, ask, read-aloud, uploads, summaries; WAF and alarms S5 |
| V8.2.1 | Verify the application sets sufficient anti-caching headers so that sensitive data is not cached in modern browsers. | Met | Cache-Control: no-store on every API response |
| V8.2.2 | Verify that data stored in browser storage (such as localStorage, sessionStorage, IndexedDB, or cookies) does not contain sensitive data. | Met | No sensitive data in browser storage (sessionStorage holds only demo sample-bill ids) |
| V8.2.3 | Verify that authenticated data is cleared from client storage, such as the browser DOM, after the client or session is terminated. | Partial | Token in memory, gone on close; sign-out clears state in S1 |
| V8.3.1 | Verify that sensitive data is sent to the server in the HTTP message body or headers, and that query string parameters from any HTTP verb do not contain sensitive data. | Met | Sensitive data in bodies/headers; query strings carry only ids and periods |
| V8.3.2 | Verify that users have a method to remove or export their data on demand. | Met | GET /v1/me/export (zip of all the shop's data and photos, audited, rate-limited); DELETE /v1/me after a grace period, cancellable |
| V8.3.3 | Verify that users are provided clear language regarding collection and use of supplied personal information and that users have provided opt-in consent for the use of... | Met | Plain-language 'What Laabam keeps' in Settings, in the owner's language |
| V8.3.4 | Verify that all sensitive data created and processed by the application has been identified, and ensure that a policy is in place on how to deal with sensitive data. | Met | Sensitive data identified in THREAT_MODEL.md §1 |
| V8.3.5 | Verify accessing sensitive data is audited (without logging the sensitive data itself), if the data is collected under relevant data protection directives or where... | Met | Audit of confirms, payments, approvals, exports, deletion requests and cancels |
| V8.3.6 | Verify that sensitive information contained in memory is overwritten as soon as it is no longer required to mitigate memory dumping attacks, using zeroes or random data. | N/A | Python/JS memory can't be reliably zeroed; audio and photo bytes live only for the request or job |
| V8.3.7 | Verify that sensitive or private information that is required to be encrypted, is encrypted using approved algorithms that provide both confidentiality and integrity. | Gap | S3/S5: KMS encryption at rest |
| V8.3.8 | Verify that sensitive personal information is subject to data retention classification, such that old or out of date data is deleted automatically, on a schedule, or... | Met | Daily retention sweep: transcripts, heard text and photos older than 365 days (LAABAM_RETENTION_DAYS); audit kept |

## V9 Communication

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V9.1.1 | Verify that TLS is used for all client connectivity, and does not fall back to insecure or unencrypted communications. | Gap | S5: HTTPS only via CloudFront (HTTP->HTTPS redirect, HSTS) |
| V9.1.2 | Verify using up to date TLS testing tools that only strong cipher suites are enabled, with the strongest cipher suites set as preferred. | Gap | S5: CloudFront TLSv1.2_2021 security policy; ALB ELBSecurityPolicy-TLS13-1-2 |
| V9.1.3 | Verify that only the latest recommended versions of the TLS protocol are enabled, such as TLS 1.2 and TLS 1.3. The latest version of the TLS protocol should be the... | Gap | S5: TLS 1.2+ only |
| V9.2.1 | Verify that connections to and from the server use trusted TLS certificates. Where internally generated or self-signed certificates are used, the server must be... | Gap | S5: ACM certificates; partner certs verified by default |
| V9.2.2 | Verify that encrypted communications such as TLS is used for all inbound and outbound connections, including for management ports, monitoring, authentication, API, or... | Gap | S3/S5: TLS to RDS and partners; VPC endpoints for AWS |
| V9.2.3 | Verify that all encrypted connections to external systems that involve sensitive information or functions are authenticated. | Met | Partner calls https with verification; RDS verify-full with the RDS CA |
| V9.2.4 | Verify that proper certification revocation, such as Online Certificate Status Protocol (OCSP) Stapling, is enabled and configured. | N/A | CloudFront/ACM handle revocation and stapling |

## V10 Malicious Code

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V10.2.1 | Verify that the application source code and third party libraries do not contain unauthorized phone home or data collection capabilities. Where such functionality... | Met | No phone-home code; outbound calls only to configured partners (allowlist S2) |
| V10.2.2 | Verify that the application does not ask for unnecessary or excessive permissions to privacy related features or sensors, such as contacts, cameras, microphones, or... | Met | Microphone only while holding the button; camera only through the file input |
| V10.3.1 | Verify that if the application has a client or server auto-update feature, updates should be obtained over secure channels and digitally signed. The update code must... | Met | PWA updates come from our own origin over HTTPS (S5) |
| V10.3.2 | Verify that the application employs integrity protections, such as code signing or subresource integrity. The application must not load or execute code from untrusted... | Partial | Bundled assets, no third-party CDNs; S4 image digests and signed commits |
| V10.3.3 | Verify that the application has protection from subdomain takeovers if the application relies upon DNS entries or DNS subdomains, such as expired domain names, out of... | Gap | S5: Route 53 records only for live resources; no dangling CloudFront aliases |

## V11 Business Logic

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V11.1.1 | Verify that the application will only process business logic flows for the same user in sequential step order and without skipping steps. | Met | Bill flow is a state machine (draft -> confirmed -> paid); out-of-order calls refused |
| V11.1.2 | Verify that the application will only process business logic flows with all steps being processed in realistic human time, i.e. transactions are not submitted too... | Met | QR only after confirm; payment matched to an open QR in time; QR expires in 5 minutes |
| V11.1.3 | Verify the application has appropriate limits for specific business actions or transactions which are correctly enforced on a per user basis. | Met | Limits on lines and amounts; loan only for a shortfall; per-shop rate limits on the costly actions |
| V11.1.4 | Verify that the application has anti-automation controls to protect against excessive calls such as mass data exfiltration, business logic requests, file uploads or... | Met | Rate limits on auth, voice, ask, read-aloud, uploads and summaries per shop and per IP (Postgres buckets) |
| V11.1.5 | Verify the application has business logic limits or validation to protect against likely business risks or threats, identified using threat modeling or similar... | Met | Arithmetic checks, mismatch flags, owner confirmation, digit guard |
| V11.1.6 | Verify that the application does not suffer from "Time Of Check to Time Of Use" (TOCTOU) issues or other race conditions for sensitive operations. | Met | Row locks (SELECT ... FOR UPDATE) on bills, purchases, alerts, loan offers; atomic timer claim |
| V11.1.7 | Verify that the application monitors for unusual events or activity from a business logic perspective. For example, attempts to perform actions out of order or... | Partial | Payment mismatches and duplicates flagged; S5 alarms on unusual events |
| V11.1.8 | Verify that the application has configurable alerting when automated attacks or unusual activity is detected. | Gap | S5: CloudWatch alarms (auth failures, WAF blocks, 5xx) to the owner's email |

## V12 Files and Resources

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V12.1.1 | Verify that the application will not accept large files that could fill up storage or cause a denial of service. | Met | Presigned uploads limited to the exact size (10 MB max); JSON 64 KB; audio 2 MB |
| V12.1.2 | Verify that the application checks compressed files (e.g. zip, gz, docx, odt) against maximum allowed uncompressed size and against maximum number of files before... | N/A | No archives accepted |
| V12.1.3 | Verify that a file size quota and maximum number of files per user is enforced to ensure that a single user cannot fill up the storage with too many files, or... | Met | Photo uploads 30 an hour per shop and per IP |
| V12.2.1 | Verify that files obtained from untrusted sources are validated to be of expected type based on the file's content. | Met | The worker decodes every photo (JPEG/PNG/WebP/HEIC only), checks pixels, re-encodes; audio by magic bytes |
| V12.3.1 | Verify that user-submitted filename metadata is not used directly by system or framework filesystems and that a URL API is used to protect against path traversal. | Met | Uploaded filenames never used; keys are <merchant>/<bill>.<ext> |
| V12.3.2 | Verify that user-submitted filename metadata is validated or ignored to prevent the disclosure, creation, updating or removal of local files (LFI). | Met | Filenames ignored |
| V12.3.3 | Verify that user-submitted filename metadata is validated or ignored to prevent the disclosure or execution of remote files via Remote File Inclusion (RFI) or... | Met | Filenames ignored |
| V12.3.4 | Verify that the application protects against Reflective File Download (RFD) by validating or ignoring user-submitted filenames in a JSON, JSONP, or URL parameter, the... | Met | Filenames never used; photo responses typed image/jpeg with nosniff |
| V12.3.5 | Verify that untrusted file metadata is not used directly with system API or libraries, to protect against OS command injection. | Met | No file metadata passed to system APIs |
| V12.3.6 | Verify that the application does not include and execute functionality from untrusted sources, such as unverified content distribution networks, JavaScript libraries,... | Met | No code loaded from untrusted sources |
| V12.4.1 | Verify that files obtained from untrusted sources are stored outside the web root, with limited permissions. | Met | Photos in private storage (S3 in AWS) outside any web root, under the shop's prefix |
| V12.4.2 | Verify that files obtained from untrusted sources are scanned by antivirus scanners to prevent upload and serving of known malicious content. | N/A | Images are decoded and re-encoded to plain pixels (S2), which removes any payload; no files are passed on as uploaded |
| V12.5.1 | Verify that the web tier is configured to serve only files with specific file extensions to prevent unintentional information and source code leakage. For example,... | Met | The SPA route serves files only from the build directory |
| V12.5.2 | Verify that direct requests to uploaded files will never be executed as HTML/JavaScript content. | Met | Photos are re-encoded JPEGs served with image/jpeg and nosniff: never run as HTML/JS |
| V12.6.1 | Verify that the web or application server is configured with an allow list of resources or systems to which the server can send requests or load data/files from. | Met | Outbound allowlist (Sarvam, Paytm, Gemini in demo, the partner's JWKS); https only |

## V13 API and Web Service

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V13.1.1 | Verify that all application components use the same encodings and parsers to avoid parsing attacks that exploit different URI or file parsing behavior that could be... | Met | One parser stack (FastAPI/Pydantic) for all JSON |
| V13.1.3 | Verify API URLs do not expose sensitive information, such as the API key, session tokens etc. | Met | No keys or tokens in URLs |
| V13.1.4 | Verify that authorization decisions are made at both the URI, enforced by programmatic or declarative security at the controller or router, and at the resource level,... | Met | Per-route owner dependency plus per-object merchant checks |
| V13.1.5 | Verify that requests containing unexpected or missing content types are rejected with appropriate headers (HTTP response status 406 Unacceptable or 415 Unsupported... | Met | JSON routes refuse other content types (415); audio and photo routes check theirs |
| V13.2.1 | Verify that enabled RESTful HTTP methods are a valid choice for the user or action, such as preventing normal users using DELETE or PUT on protected API or resources. | Met | Each route declares its method; others 405 |
| V13.2.2 | Verify that JSON schema validation is in place and verified before accepting input. | Met | Strict request schemas, checked before the handler runs |
| V13.2.3 | Verify that RESTful web services that utilize cookies are protected from Cross-Site Request Forgery via the use of at least one or more of the following: double... | Met | No cookie auth on API calls (S1 refresh cookie: SameSite=Strict + header) |
| V13.2.5 | Verify that REST services explicitly check the incoming Content-Type to be the expected one, such as application/xml or application/json. | Met | Content-Type checked on every body (RequestGuard) |
| V13.2.6 | Verify that the message headers and payload are trustworthy and not modified in transit. Requiring strong encryption for transport (TLS only) may be sufficient in... | Partial | Partner webhooks HMAC-signed; TLS everywhere in S5 |
| V13.3.1 | Verify that XSD schema validation takes place to ensure a properly formed XML document, followed by validation of each input field before any processing of that data... | N/A | No XML, SOAP or GraphQL anywhere |
| V13.3.2 | Verify that the message payload is signed using WS-Security to ensure reliable transport between client and service. | N/A | No XML, SOAP or GraphQL anywhere |
| V13.4.1 | Verify that a query allow list or a combination of depth limiting and amount limiting is used to prevent GraphQL or data layer expression Denial of Service (DoS) as a... | N/A | No XML, SOAP or GraphQL anywhere |
| V13.4.2 | Verify that GraphQL or other data layer authorization logic should be implemented at the business logic layer instead of the GraphQL layer. | N/A | No XML, SOAP or GraphQL anywhere |

## V14 Configuration

| ID | Requirement | Status | Notes |
|---|---|---|---|
| V14.1.1 | Verify that the application build and deployment processes are performed in a secure and repeatable way, such as CI / CD automation, automated configuration... | Partial | CI builds and tests; S4 repeatable hardened builds |
| V14.1.2 | Verify that compiler flags are configured to enable all available buffer overflow protections and warnings, including stack randomization, data execution prevention,... | N/A | No compiled code of ours |
| V14.1.3 | Verify that server configuration is hardened as per the recommendations of the application server and frameworks in use. | Gap | S4/S5: hardened container and server config |
| V14.1.4 | Verify that the application, configuration, and all dependencies can be re-deployed using automated deployment scripts, built from a documented and tested runbook in... | Gap | S5: CDK deploys everything |
| V14.2.1 | Verify that all components are up to date, preferably using a dependency checker during build or compile time. | Gap | S4: dependency audit in CI |
| V14.2.2 | Verify that all unneeded features, documentation, sample applications and configurations are removed. | Met | API docs and the OpenAPI map are off outside development; sandbox routes absent in prod |
| V14.2.3 | Verify that if application assets, such as JavaScript libraries, CSS or web fonts, are hosted externally on a Content Delivery Network (CDN) or external provider,... | Met | Fonts and scripts self-hosted; no external CDNs |
| V14.2.4 | Verify that third party components come from pre-defined, trusted and continually maintained repositories. | Met | PyPI and npm only |
| V14.2.5 | Verify that a Software Bill of Materials (SBOM) is maintained of all third party libraries in use. | Gap | S4: SBOM generated in CI |
| V14.2.6 | Verify that the attack surface is reduced by sandboxing or encapsulating third party libraries to expose only the required behaviour into the application. | Partial | Adapters wrap partners; image decoding isolated in the worker (S2) |
| V14.3.2 | Verify that web or application server and application framework debug modes are disabled in production to eliminate debug features, developer consoles, and unintended... | Met | No debug mode; docs off outside dev; generic 500s |
| V14.3.3 | Verify that the HTTP headers or any part of the HTTP response do not expose detailed version information of system components. | Partial | Server header dropped on API responses; /api/health shows no versions outside dev; CloudFront S5 |
| V14.4.1 | Verify that every HTTP response contains a Content-Type header. Also specify a safe character set (e.g., UTF-8, ISO-8859-1) if the content types are text/*, /+xml and... | Met | JSON responses typed; files with their types |
| V14.4.2 | Verify that all API responses contain a Content-Disposition: attachment; filename="api.json" header (or other appropriate filename for the content type). | Met | The export sets Content-Disposition: attachment; JSON responses are never rendered |
| V14.4.3 | Verify that a Content Security Policy (CSP) response header is in place that helps mitigate impact for XSS attacks like HTML, DOM, JSON, and JavaScript injection... | Gap | S5: strict CSP from CloudFront |
| V14.4.4 | Verify that all responses contain a X-Content-Type-Options: nosniff header. | Partial | API responses send nosniff; CloudFront for the PWA in S5 |
| V14.4.5 | Verify that a Strict-Transport-Security header is included on all responses and for all subdomains, such as Strict-Transport-Security: max-age=15724800;... | Gap | S5: HSTS |
| V14.4.6 | Verify that a suitable Referrer-Policy header is included to avoid exposing sensitive information in the URL through the Referer header to untrusted parties. | Gap | S5: Referrer-Policy |
| V14.4.7 | Verify that the content of a web application cannot be embedded in a third-party site by default and that embedding of the exact resources is only allowed where... | Gap | S5: frame-ancestors 'none' |
| V14.5.1 | Verify that the application server only accepts the HTTP methods in use by the application/API, including pre-flight OPTIONS, and logs/alerts on any requests that are... | Met | Only declared methods; OPTIONS for CORS |
| V14.5.2 | Verify that the supplied Origin header is not used for authentication or access control decisions, as the Origin header can easily be changed by an attacker. | Met | Origin never used for auth |
| V14.5.3 | Verify that the Cross-Origin Resource Sharing (CORS) Access-Control-Allow-Origin header uses a strict allow list of trusted domains and subdomains to match against... | Met | No cross-origin access by default (LAABAM_CORS_ORIGINS empty; '*' refused outside dev) |
| V14.5.4 | Verify that HTTP headers added by a trusted proxy or SSO devices, such as a bearer token, are authenticated by the application. | Gap | S5: ALB accepts only CloudFront's secret header + prefix list |
