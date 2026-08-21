# mini-homelab

Declarative container services host on a Raspberry Pi 4B (8GB).

Two layers, split along how often each changes:

- **Host** — a bootc image. Rebuilt in CI, applied deliberately with
  `bootc upgrade` + reboot. Changes rarely.
- **Workloads** — k3s driven by Flux from this same repo. Changes constantly,
  no reboot.

Removing something from either layer removes it from the running system. That is
the whole point of the design; nothing is managed imperatively.

## Topology

```
upstream wifi (MT7921AU, USB)      firewalld zone: ext, DROP except 80/443
        |
   reverse proxy  -->  k3s workloads (pods on 10.42/16, svc on 10.43/16)
        |
internal 2.4GHz AP (brcmfmac)      firewalld zone: admin, 172.19.149.0/24
        `-- SSH, kubectl (6443). k3s --node-ip pinned here.
```

## Key decisions

| Decision | Why |
|---|---|
| U-Boot / DeviceTree boot, not EDK2 | EDK2 puts onboard wifi out of scope |
| firewalld, not raw nftables | Zone model, and k3s documents a supported firewalld configuration |
| `--node-ip` on the admin address | Survives upstream wifi loss without reporting an unroutable address |
| `bootc upgrade` without `--apply` | Stages the image automatically; reboot stays deliberate |

Config goes in `/usr` wherever possible (versioned with the image, cannot drift).
`/etc` only for genuinely machine-local config. SSH keys are injected at install time
via `bootc-image-builder` config, never baked into the image.

## Layout

```
bootc/            host image
  Containerfile
  files/          content copied into the image, mirroring target paths
```

Flux directories (`clusters/`, `infrastructure/`, `apps/`) arrive with that layer.

## Build increments

- [x] bare scaffold
- [ ] CI build and push to ghcr.io/quay.io
- [ ] networking: admin AP + upstream client
- [ ] firewalld zones
- [ ] k3s
- [ ] install to disk (bootc-image-builder + ESP population)
- [ ] Flux bootstrap
- [ ] workloads: reverse proxy, TLS
