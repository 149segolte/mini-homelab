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

TLS is still Traefik's default self-signed certificate; cert-manager and a
wildcard for `${DOMAIN}` are not deployed.

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
are managed in Cloudflare, outside this repo. The Flux web UI is anonymous
auth mapped to a group bound to the built-in `view` ClusterRole — read-only,
reachable only through the tunnel or the LAN.
