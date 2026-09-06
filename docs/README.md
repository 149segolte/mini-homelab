# Docs

## Host

| | |
| --- | --- |
| [Host image](host-image.md) | what is in the image, how it is built and published, how upgrades land |
| [Installing to disk](install.md) | partition layout, the install sequence, the Pi's ESP |
| [Install-time overlay](overlay.md) | machine-local config and secrets: `config.toml`, `overlay.py` |
| [Networking](networking.md) | firewalld zones, both APs, DNS, tailscale |
| [k3s](k3s.md) | the host service and its configuration |

## Cluster

| | |
| --- | --- |
| [Bootstrapping](bootstrap.md) | the one-time commands that hand the cluster to Flux |
| [GitOps](flux.md) | Flux shape, tiers and ordering |
| [Secrets](secrets.md) | External Secrets Operator and Infisical |
| [Ingress](ingress.md) | Traefik and the Cloudflare tunnel |
| [TLS](tls.md) | cert-manager, the wildcard, and Traefik's default certificate |
| [Admission control](admission.md) | PSA and Kyverno |

## Workloads

| | |
| --- | --- |
| [Technitium settings](technitium.md) | the DNS settings that live in its store, not in git |
