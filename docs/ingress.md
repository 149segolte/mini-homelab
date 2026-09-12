# Ingress

```
internet ──▶ Cloudflare ──tunnel──▶ cloudflared ──▶ Traefik ──▶ Ingress
LAN / tailnet ─────────────────────────────────▶ Traefik ──▶ Ingress
```

The `ext` zone drops everything ([networking](networking.md)), so there is no
port forward upstream and nothing to expose by accident.

## Traefik

Bundled with k3s, configured in place with a `HelmChartConfig` in `kube-system`
rather than replaced: `web` redirects to `websecure`, JSON access logs, and the
Authelia middleware on the `websecure` entrypoint so every route is
authenticated by default ([auth](auth.md)).

The dashboard is served at `traefik.${DOMAIN}` by the chart's own IngressRoute.
`api@internal` is a Traefik service rather than a Kubernetes one, so no plain
Ingress can reach it; TLS and the Authelia middleware still come from the
entrypoint, so it carries neither.

Host ports were dropped deliberately — Traefik is reached through its Service,
which keeps it inside the `restricted` profile ([admission](admission.md)).

One wildcard is served as the default certificate, so no Ingress carries a
`tls:` block. Traefik falls back to a generated self-signed certificate only
while `wildcard-tls` is absent ([tls](tls.md)).

## cloudflared

Two replicas, both on the single node, so a rolling restart never drops the
tunnel. Credentials come from Infisical ([secrets](secrets.md)), hence
`dependsOn: external-secrets-crs`.

- Config goes through `configMapGenerator`, so the content hash rolls the pods.
  A hand-written ConfigMap does not — pods keep the old config until something
  restarts them, and the tunnel keeps reporting the old error.
- `metrics: 0.0.0.0:2000` because the probes hit `/ready` there; the default
  binds loopback on a random port.
- Exactly one ingress rule, a catch-all. cloudflared requires the catch-all to
  be last and rejects a hostname-less rule anywhere else with `Rule #N is
  matching the hostname ''`. Per-hostname routing is Traefik's job.
- `originServerName: ${DOMAIN}` rather than `noTLSVerify`: the in-cluster
  service name is not on the wildcard, so the apex is what gets verified.

## Hostnames

Built from `${DOMAIN}` in `cluster-vars` ([gitops](flux.md#tiers)); DNS records
are managed in Cloudflare, outside this repo.

| Host | |
| --- | --- |
| `whoami.` | smoke test — `one_factor`, so it stays usable |
| `auth.` | Authelia ([auth](auth.md)) |
| `flux.` | Flux UI — anonymous auth impersonating a group bound to `view`, plus `system:discovery` and `system:basic-user`, which impersonation does not inherit |
| `traefik.` | Traefik dashboard — chart IngressRoute onto `api@internal` |
| `dns.`, `doh.` | Technitium — **no public record** ([technitium](technitium.md)) |

`dns.` and `doh.` have no record deliberately: Technitium's recursion ACL is
`10.42.0.0/16` and everything through Traefik carries a pod source address, so
a reachable `doh.` host is an open resolver.
