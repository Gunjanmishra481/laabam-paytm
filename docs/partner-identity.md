# Partner sign-in: the assertion Laabam expects

**Status: the real Paytm integration is pending Paytm's specification.** This page defines the format Laabam
verifies today. The demo's sandbox partner signs assertions in exactly this format, so the same code path runs in
the demo and in production. When Paytm shares their format, we adapt the verifier (or ask Paytm to issue this one)
and record the differences here.

## Flow

1. The owner opens Laabam from inside Paytm for Business.
2. Paytm signs a short-lived assertion that this owner is merchant `mid`, and opens
   `https://<laabam>/#assertion=<JWS>`. The fragment is never sent to any server; the app reads it once, removes
   it from the address bar, and posts it.
3. `POST /v1/auth/partner` with `{"assertion": "<JWS>"}`. Laabam verifies it (below), finds the shop for `mid` (or
   starts onboarding for a new one), and starts a device session: a 15-minute access token in the response, and a
   refresh token in a `__Host-laabam_refresh` cookie (`Secure; HttpOnly; SameSite=Strict; Path=/`).
4. The owner's Soundbox announces the new sign-in in the shop's language.

## The assertion

A JWS in compact serialisation (a JWT).

Header:

| Field | Value |
|---|---|
| `alg` | `ES256` (ECDSA P-256, SHA-256). Nothing else is accepted: not `none`, not HMAC, not RSA |
| `kid` | The key id in the partner's published key set |
| `typ` | `JWT` (optional) |

Claims:

| Claim | Required | Meaning and checks |
|---|---|---|
| `iss` | yes | The partner's issuer id. Must equal `LAABAM_PARTNER_ISSUER` (default `paytm-for-business`) |
| `aud` | yes | `laabam` |
| `iat` | yes | Issued at (seconds). Not more than 30 s in the future |
| `exp` | yes | Expiry. `exp - iat` at most 300 s (5 minutes); checked with 30 s leeway |
| `jti` | yes | A unique id, 8 to 128 characters. Each `jti` is accepted once: it is stored until the assertion would have expired, so a replay is refused |
| `mid` | yes | The partner's merchant id, 1 to 64 characters |
| `name` | no | The shop's name, for a first sign-in (max 120 characters) |
| `language` | no | `en`, `ta` or `hi`: the owner's language, for a first sign-in |

Any other claim is ignored. A refused assertion gets `401` with a plain message; the reason goes to the log, never
to the client.

## Keys

The partner publishes its public keys as a JWKS (`{"keys": [...]}`, EC P-256 keys with `kid`) at an HTTPS URL set in
`LAABAM_PARTNER_JWKS_URL`. Laabam fetches it through the outbound allowlist, caches it for an hour, and refetches
when it sees an unknown `kid` (so the partner can rotate by publishing the new key before using it). Production
refuses to start without an `https://` JWKS URL.

## Sandbox partner (demo only)

`laabam-demo` (`LAABAM_SANDBOX=1`) uses an in-process sandbox partner with its own P-256 key and issuer
`sandbox-partner`. `POST /sandbox/session` creates a new demo shop with merchant id `SBX…`, has the sandbox partner
sign an assertion for it in the format above, and exchanges it through the same verifier. The sandbox partner and
its routes do not exist in `laabam-prod`.

## Open questions for Paytm

- Can Paytm for Business issue this assertion (ES256, claims above), or what does it issue today?
- The JWKS URL, issuer id and key rotation schedule.
- Whether `mid` is stable for the life of a merchant, and how a merchant closing their Paytm account is signalled
  (we would revoke the shop's sessions on that event).
