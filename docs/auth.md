# Auth

Authelia as a ForwardAuth middleware on Traefik's `websecure` entrypoint. File
backed users, TOTP and WebAuthn passkeys. No OIDC provider yet — that is what
would turn Technitium (`DNS_SERVER_SSO_*`) and the Flux UI (`type: OAuth2`)
into real identities rather than anonymous-behind-a-proxy.

Its config is a plain YAML file through `configMapGenerator`, so editing it
rolls the pod and stays in git — unlike Technitium
([technitium](technitium.md)).

## Default deny

The middleware sits on the entrypoint, not on Ingresses, so every router is
authenticated and a new Ingress is protected by omission rather than exposed by
it. `access_control` is the whole public surface. First match wins:

| Rule | Policy | |
| --- | --- | --- |
| `auth.` | bypass | the portal would otherwise sit behind itself and loop |
| `doh.` `^/dns-query` | bypass | DoH clients cannot follow a browser redirect |
| `whoami.` | one_factor | password only, so it stays a usable smoke test |
| `*.${DOMAIN}` | two_factor | everything else, including the Flux and Technitium UIs |
| — | deny | the default, so an unmatched host is refused rather than waved through |

`*.${DOMAIN}` matches one label, so the apex and any deeper name fall to
`deny` and need a rule of their own.

```yaml
ports:
  websecure:
    http:
      middlewares:
        - authelia-authelia@kubernetescrd
```

`<namespace>-<name>@kubernetescrd`, which needs
`providers.kubernetesCRD.allowCrossNamespace` since the Middleware lives in
`authelia` and Traefik in `kube-system`. Per-Ingress annotations still work and
win over the entrypoint default.

## Non-browser clients

```yaml
server:
  endpoints:
    authz:
      forward-auth:
        authn_strategies:
          - name: HeaderProxyAuthorization
          - name: CookieSession
```

Declaring the strategies explicitly adds `Proxy-Authorization` alongside the
cookie, so API and CLI clients authenticate with a header instead of being
redirected to a login page. Ordering matters: the header is tried first, and a
browser falls through to the session cookie.

## Secrets

Everything lives under `authelia/` in Infisical, one single-line value each.
The structure that consumes them is in the ExternalSecret
([composition](#composition)).

| Key | |
| --- | --- |
| `session-secret`, `storage-encryption-key` | `openssl rand -hex 32`; reach the pod as `*_FILE` env vars |
| `user-password-hashes/<username>` | one argon2 hash per user in the template |
| `oidc/hmac-secret` | random, 64+ chars |
| `oidc/jwks-key/private.pem` | the RSA private key |
| `oidc/clients/<name>/id` | client id |
| `oidc/clients/<name>/secret` | plaintext, for the client |
| `oidc/clients/<name>/secret-hash` | pbkdf2, for Authelia |

The `*_FILE` env var name is the config path uppercased and `_`-joined,
prefixed `AUTHELIA_`. There is no JWT secret: password reset and change are
both disabled, so `identity_validation.reset_password` never runs.

Generate hashes locally so no plaintext reaches the cluster:

```bash
podman run --rm docker.io/authelia/authelia:4.39 \
  authelia crypto hash generate argon2 --random --random.length 24   # users
podman run --rm docker.io/authelia/authelia:4.39 \
  authelia crypto hash generate pbkdf2 --variant sha512 --random --random.length 72
podman run --rm docker.io/authelia/authelia:4.39 \
  authelia crypto pair rsa generate --directory /tmp
```

The pbkdf2 command prints both the plaintext and the hash — they go to
different places, per the table above.

Adding a user means an entry in the ExternalSecret's `users_database.yml`
template plus its hash in Infisical. `authentication_backend.file.watch: true`
means an ESO refresh is picked up without restarting the pod, and logins accept
the email address or any casing.

## Enrolling a second factor

`*.${DOMAIN}` is `two_factor`, and registering a device needs a one-time link
that Authelia normally emails. The notifier is the filesystem, so it lands in
the pod instead:

```bash
kubectl -n authelia exec deploy/authelia -- cat /data/notification.txt
```

That is the first thing to do after the first login. SMTP is deliberately not
set up — sending mail from the domain is not worth the setup for a homelab, and
reading the file is a fine substitute for something done once per device.

## WebAuthn

Passkey login is enabled, with attestation `direct` and MDS metadata validation
on, so authenticator models are checked against FIDO's blob.

- `metadata.cache_policy: relaxed` — the blob is large and its service rate
  limits. Relaxed logs the 429 and keeps the cached copy; strict refuses to
  start.
- Backup-eligible credentials stay permitted. Prohibiting them blocks synced
  passkeys, Bitwarden's included.

Passwords are checked against zxcvbn with a minimum score of 3.

## OIDC

Authelia's built-in provider, no extra component. Two clients: the Flux UI and
Technitium, both of which speak only OIDC — the `Remote-*` headers the
middleware emits are not readable by either.

Redirect URIs are fixed by each app and are not conventions:

| Client | Redirect URI |
| --- | --- |
| `flux-web` | `https://flux.${DOMAIN}/oauth2/callback` |
| `technitium` | `https://dns.${DOMAIN}/sso/callback` |

`offline_access` is in the Flux UI's default scope set, so its client must
permit it or authorization fails with `invalid_scope`.

### Per-client quirks

Neither of these is optional; both were found by the client failing.

**The Flux UI needs a claims policy.** Authelia calls `id_token` hydration an
escape hatch because a compliant client reads these claims from userinfo. The
Flux UI never calls userinfo — it reads the ID token and nothing else — so
without the `flux` policy `claims.groups` is empty, impersonation grants
nothing, and the UI fails in a way that looks like broken RBAC. Technitium does
read userinfo, so it needs no policy.

**Technitium needs `token_endpoint_auth_method: client_secret_post`.** Authelia
defaults confidential clients to `client_secret_basic`, as the spec requires,
but ASP.NET's OIDC handler puts the credentials in the request body. The
mismatch surfaces as `invalid_client` at the pushed-authorization endpoint —
which reads like a bad secret and is not one.

### Composition

Nothing credential-bearing is stored as a blob. `spec.target.template` with
`engineVersion: v2` builds both `oidc.yml` and the users database in git and
pulls in only the secrets, one single-line Infisical value each
([secrets](secrets.md)).

Authelia merges `--config a,b`, and a section must not appear in both, so
`configuration.yml` carries no `identity_providers` block.

User hashes come in through `dataFrom.find` on
`/authelia/user-password-hashes`, keyed by username, with a `transform` rewrite
turning each into `user_<name>_hash`. Adding a user is a hash in Infisical plus
an entry in the `users_database.yml` template.

Three things bite when editing those templates:

- A Go template cannot reference a field starting with a digit, so a username
  like `149segolte` must arrive as `user_149segolte_hash`; `.149segolte_...`
  will not parse. This is why the rewrite prefixes rather than using the bare
  name.
- A PEM must go into a block scalar with `nindent`. In a quoted scalar YAML
  folds its newlines into spaces, which parses cleanly and yields an unusable
  key.
- Prefer a `transform` rewrite over `regexp`. A regexp target needs `${1}`,
  which Flux's envsubst then tries to resolve as a variable named `1` and fails
  the whole build; `transform` has no `$` to escape.

The template renders all or nothing, so a mistake anywhere drops `oidc.yml`
from the Secret — and a missing config file is fatal to Authelia, which gates
every route. Test template edits before pushing them.

### The Flux UI config

`web.config` and `web.configSecretName` are mutually exclusive, and the Secret
must hold a whole `config.yaml`, so a client secret would drag the entire
config out of git. The ExternalSecret composes it instead. Impersonation maps
`claims.preferred_username` and `claims.groups`, so tokens have to carry both.

## Verify

The check that matters is whether the entrypoint default actually applied:

```bash
curl -sI https://flux.${DOMAIN} | head -1            # 302 to auth.
curl -sI https://whoami.${DOMAIN} | head -1          # 302 — nothing opts in
curl -sI https://auth.${DOMAIN} | head -1            # 200, or it loops
curl -sI https://doh.${DOMAIN}/dns-query | head -1   # not a 302
```

A `200` on either of the first two means the middleware is not attached and
nothing is protected.

A redirect loop is almost always `session.cookies[].domain` not being the
parent of the protected host.

## When Authelia is down

Everything on `websecure` fails closed, **including `auth.` itself** — its
bypass rule is evaluated by Authelia, so there is nothing to ask while Authelia
is the thing that is missing. That is inherent to a default on the entrypoint;
per-Ingress annotations left the portal genuinely untouched.

| | |
| --- | --- |
| pod down, Middleware present | the forwardAuth call fails, 5xx per request |
| Middleware missing | the entrypoint reference cannot resolve and every `websecure` router breaks |

The second cannot be worked around from an Ingress, because the reference lives
in Traefik's static config — it needs the `HelmChartConfig` fixed and
helm-controller to catch up.

Either way `kubectl` does not traverse Traefik, so port-forward is the way in:

```bash
kubectl -n flux-system port-forward svc/flux-operator 9080:9080
```

A normal restart is 15–30s of this: `Recreate` with one replica takes the old
pod down first, and startup includes an MDS fetch attempt. `Recreate` stays
anyway — two pods can both mount an RWO volume on one node, which would mean
two processes writing one SQLite file.

## Next

- `flux-web-viewers` is now dead weight: the UI authenticates users itself and
  impersonates their real groups, so RBAC should bind those instead.
- Technitium's SSO settings apply on a rebuild only; the running instance is
  configured in its UI ([technitium](technitium.md)).
- The tunnel reaches Traefik on the same entrypoint, so public traffic is gated
  by these same rules — anything that must stay public needs a bypass rule.
