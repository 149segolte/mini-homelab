# Ingress

```
internet ──▶ Cloudflare ──tunnel──▶ cloudflared ──▶ Traefik ──▶ Ingress
LAN / tailnet ─────────────────────────────────▶ Traefik ──▶ Ingress
```

The `ext` zone drops everything ([networking](networking.md)), so there is no
port forward upstream and nothing to expose by accident.

## Traefik

Bundled with k3s, configured in place with a `HelmChartConfig` in `kube-system`
rather than replaced: `web` redirects to `websecure`, JSON access logs.

Host ports were dropped deliberately — Traefik is reached through its Service,
which keeps it inside the `restricted` profile ([admission](admission.md)).

Traefik serves its generated self-signed certificate until the wildcard issues
([tls](tls.md)).

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
- `noTLSVerify` stands until cert-manager issues a real certificate, then
  becomes `originServerName`.

## Hostnames

Built from `${DOMAIN}` in `cluster-vars` ([gitops](flux.md#tiers)); DNS records
are managed in Cloudflare, outside this repo.

| Host | |
| --- | --- |
| `whoami.` | smoke test |
| `flux.` | Flux UI — anonymous auth mapped to a group bound to the built-in `view` ClusterRole, read-only |
| `dns.`, `doh.` | Technitium — **no public record** ([technitium](technitium.md)) |

TLS is one wildcard served as Traefik's default certificate, so no Ingress here
carries a `tls:` block ([tls](tls.md)).

`dns.` and `doh.` have no record deliberately: Technitium's recursion ACL is
`10.42.0.0/16` and everything through Traefik carries a pod source address, so
a reachable `doh.` host is an open resolver.
