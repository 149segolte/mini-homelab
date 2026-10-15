# Services

```
internet -----> Cloudflare --tunnel--> cloudflared --> Traefik --> Ingress
LAN / tailnet ---------------------------------------> Traefik --> Ingress
```

Public records live in Cloudflare. Internal records are the `customDNS.zone`
block in `infrastructure/blocky/config.yaml`, each a CNAME to `node.`.

| Host           | Service                                |
| -------------- | -------------------------------------- |
| `mini.`        | Glance                                 |
| `files.`       | HFS, with SFTP on 3922                 |
| `dav.`         | HFS's WebDAV, no public record         |
| `whoami.`      | Smoke test, `one_factor`               |
| `auth.`        | Authelia                               |
| `flux.`        | Flux UI, over OIDC                     |
| `grafana.`     | Grafana, over OIDC                     |
| `traefik.`     | Traefik dashboard                      |
| `dns.`, `doh.` | blocky's API and DoH, no public record |

A new hostname needs an Ingress and a zone line. Without the zone line it
resolves publicly and goes through the tunnel, which carries HTTP only.

## DNS

blocky serves every client, reached at the fixed Service address 10.43.0.53
([Host](host.md#dns)). Its upstreams are DNS stamps with literal addresses,
since resolving a hostname would loop back through CoreDNS to blocky. It reads
its configuration only at startup, so restart it after changing `EXTERNAL_IP`.

`dns.`, `doh.` and `dav.` have no public record. blocky has no client ACL, so a
public `doh.` would be an open resolver.

```bash
dig @127.0.0.1 doubleclick.net +short    # 0.0.0.0 once blocklists load
dig @127.0.0.1 whoami.${DOMAIN} +short   # CNAME to node., then its address
```

## Traefik

Traefik ships with k3s and is configured by a `HelmChartConfig`. Its Service is
a ClusterIP with `EXTERNAL_IP` as its `externalIP`, so nothing binds a host
port and two replicas run under `restricted`. cloudflared reaches the ClusterIP
directly.

## TLS

One cert-manager wildcard for `${DOMAIN}` and `*.${DOMAIN}`, issued over
DNS-01, is Traefik's default certificate, so no Ingress needs a `tls:` block.

- The DNS-01 self-check uses public resolvers only. Through blocky, negative
  caching of `_acme-challenge` makes it stall.
- A first issuance can outlast the tier's timeout; the next retry picks it up.
- The Cloudflare token in Infisical needs **Zone > DNS > Edit** and
  **Zone > Zone > Read** on the one zone.
- Switching to staging means deleting both `wildcard-tls` and
  `letsencrypt-account-key`.

cloudflared verifies Traefik against `originServerName: ${DOMAIN}`, since the
Service name is not on the certificate.

## Auth

Authelia is a ForwardAuth middleware on the `websecure` entrypoint, so every
route is protected unless a rule bypasses it, and no Ingress can remove it.
Public traffic arrives on the same entrypoint.

| Rule                 | Policy     | Reason                                   |
| -------------------- | ---------- | ---------------------------------------- |
| `auth.`              | bypass     | The portal itself                        |
| `doh.` `^/dns-query` | bypass     | DoH clients cannot follow a redirect     |
| `dav.`               | bypass     | WebDAV clients neither; HFS asks instead |
| `whoami.`            | one_factor | Usable smoke test                        |
| `*.${DOMAIN}`        | two_factor | Everything else                          |
| (default)            | deny       | The apex and deeper names                |

Everything on `websecure` fails closed when Authelia is down. `kubectl
port-forward` does not pass through Traefik and still works.

Services behind the middleware trust `Remote-User`. A pod that reaches a
Service directly can set any user.

### Users

Users are one JSON secret, `users`, in Infisical, mapping each username to
`displayname`, `email`, `groups`, `password` and optionally `hfs_srp`. Authelia
watches the rendered file, so adding a user needs no restart. Generate hashes
locally:

```bash
authelia crypto hash generate argon2            # user passwords
authelia crypto hash generate pbkdf2 --random   # OIDC client secrets
authelia crypto pair rsa generate               # the OIDC JWKS key
```

With `--random` it prints a random secret and its digest. The client gets the
secret and Authelia the digest.

Second-factor enrolment links are written to a file rather than mailed:

```bash
kubectl -n authelia exec deploy/authelia -- cat /data/notification.txt
```

### OIDC

The Flux UI and Grafana are OIDC clients, each with `id`, `secret` and
`secret-hash` under `authelia/oidc/clients/<name>/` in Infisical. Authelia
reads the client list only on start, so a new client needs a restart.

The Flux UI reads claims from the ID token alone, so its claims policy has to
put `groups` there, or impersonation silently grants nothing. The client must
also allow `offline_access`, which the Flux UI always requests. Grafana reads
the userinfo endpoint and needs no policy.

## Glance

The dashboard at `mini.` takes host metrics from `glance-agent` on the host,
bound to 10.42.0.1 so only pods reach it. In `glance.yml`, `${...}` is Flux's
and `$${...}` passes through to Glance. The `releases` widget lists every
deployed component's upstream repository.

## Files

One pod serves `/var/external` with HFS at `files.` and `dav.`, and sshd for
SFTP on 3922, which the tunnel does not carry.

HFS rewrites its config on start, so it runs from a copy, and runtime changes
such as comments are lost with the pod. HFS accounts are the `users` entries
with `hfs_srp`. To get a verifier, run HFS locally with a plain password and
copy the `srp` value it writes:

```bash
cd "$(mktemp -d)" && printf 'accounts:\n  <username>:\n    password: <password>\n' > config.yaml
npx hfs@latest
```

At `files.` the browser is logged in from `Remote-User`. `dav.` bypasses
Authelia, so WebDAV clients use the password. Uploads go in 50 MB chunks, under
Cloudflare's 100 MB request cap.

SFTP has one user, `files`, with keys in `apps/files/authorized_keys`. The host
key is `files/ssh-host-key` in Infisical:

```bash
ssh-keygen -t ed25519 -N '' -C '' -f ssh_host_ed25519_key
```

## Monitoring

VictoriaMetrics single-node scrapes and stores; Grafana draws at `grafana.`.
Neither uses an operator or CRDs.

The one target is the apiserver. k3s runs every control-plane component in one
process with one registry, so that endpoint also carries the controller-manager,
scheduler, kubelet and kine. The response is about 12 MB, mostly latency
histograms. `scrape.yaml` keeps an allowlist of metric names, and
`aggregation.yaml` sums the large families into one-minute series, leaving
about two thousand. A dropped metric is never stored, so one added to the
allowlist has history from then on.

Retention is 90 days. The pod has no memory limit, so `-memory.allowedBytes`
bounds the caches instead. `local-path` does not enforce the volume size, so
`-storage.minFreeDiskSpaceBytes` stops ingestion before `/var` runs out.
`vm_data_size_bytes` shows actual usage.

VictoriaMetrics has no Ingress. vmui is at `localhost:8428/vmui` over:

```bash
kubectl -n victoria-metrics port-forward svc/victoria-metrics 8428
```

Grafana keeps no state. Its datasource and dashboards are provisioned from
`apps/grafana/`, and anything made in the UI is lost with the pod. A dashboard
is added as JSON under `apps/grafana/dashboards/` and listed in the
`grafana-dashboards` generator. An exported dashboard that uses `${...}`
variables needs the substitution-disabled annotation
([Cluster](cluster.md#variables)). Users sign in over OIDC and become Editors,
or Admins in the `admins` group.
