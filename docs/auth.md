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

Three keys, all under `authelia/` in Infisical: `session-secret`,
`storage-encryption-key`, `users-database`. The first two are
`openssl rand -hex 32` and reach the pod as `*_FILE` env vars — the config path
uppercased, `_`-joined, prefixed `AUTHELIA_`, suffixed `_FILE`, which is how the
OIDC ones will be named.

There is no JWT secret because both password reset and password change are
disabled, so `identity_validation.reset_password` never runs.

`users-database` is a whole `users_database.yml`. Generate the hash locally so
no plaintext password reaches the cluster:

```bash
podman run --rm docker.io/authelia/authelia:4.39 \
  authelia crypto hash generate argon2 --random --random.length 24
```

```yaml
users:
  <user>:
    disabled: false
    displayname: '<name>'
    password: '$argon2id$v=19$...'
    email: '<address>'
    groups:
      - admins
```

`authentication_backend.file.watch: true` means an ESO refresh is picked up
without restarting the pod. Logins accept the email address or any casing.

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

## Identity

The forwardAuth response carries `Remote-User`, `Remote-Groups`, `Remote-Email`
and `Remote-Name`, but neither consumer reads them: the Flux UI accepts only
`Anonymous` or `OAuth2`/OIDC, and Technitium's `DNS_SERVER_SSO_*` is OIDC only.

So access control is entirely in front of the apps. The Flux UI is anonymous
behind Authelia, impersonating a group; Technitium keeps its own login as a
second layer. Neither knows who is signed in.

The headers remain available to any future workload that supports
trusted-header auth. That requires the app to be unreachable except through
Traefik — anything with cluster network access, port-forward included, can
otherwise forge `Remote-User` against the Service directly.

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

- Authelia's built-in OIDC provider, with the Flux UI and Technitium as
  clients. It needs no new component — a config block, an HMAC secret, a JWKS
  key and one client each.
- `flux-web-viewers` stays on `view` until then: port-forward bypasses Authelia
  entirely, so that binding is what an unauthenticated port-forward gets, and
  `edit` is only safe once the UI authenticates users itself.
- The tunnel reaches Traefik on the same entrypoint, so public traffic is gated
  by these same rules — anything that must stay public needs a bypass rule.
