# mini-homelab

Declarative container services host on a Raspberry Pi 4B (8 GB).

Two layers, split by how often they change:

- **Host** — a bootc image, built in CI. Applied with `bootc upgrade` + reboot.
- **Workloads** — k3s driven by Flux from this repo. No reboot.

Removing something from either layer removes it from the running system.
Nothing is managed imperatively.

## Docs

| | |
| --- | --- |
| [Host image](docs/host-image.md) | what is in the image, how it is built and published, how upgrades land |
| [Installing to disk](docs/install.md) | partition layout, the install sequence, the Pi's ESP |
| [Install-time overlay](docs/overlay.md) | machine-local config and secrets: `config.toml`, `overlay.py` |
| [Networking](docs/networking.md) | firewalld zones, both APs, DNS, tailscale |
| [k3s](docs/k3s.md) | the host service and its configuration |
| [GitOps](docs/flux.md) | Flux shape, tiers and ordering |
| [Bootstrapping](docs/bootstrap.md) | the one-time commands that hand the cluster to Flux |
| [Secrets](docs/secrets.md) | External Secrets Operator and Infisical |
| [Ingress](docs/ingress.md) | Traefik and the Cloudflare tunnel |
| [Admission control](docs/admission.md) | PSA and Kyverno |

## Layout

```
build.py                project tasks; public functions are the subcommands
.github/workflows/      build, publish, registry cleanup
bootc/
  Containerfile
  files/                copied into the image, mirroring target paths
  install/              run against a mounted target, not the image
    fix-var-mount.py
    templates/          overlay.py, config.toml, files/
clusters/rpi4/          FluxInstance and the Kustomization tiers
infrastructure/         cluster services
apps/                   workloads
docs/
```

## Tasks

`build.py` is a PEP 723 script, so `uv` supplies the interpreter and there is
no task runner to install. `./build.py --help` is always the current list.

```bash
./build.py build                    # build :latest, bootc lint included
./build.py push quay.io/149segolte  # tag and push an existing build
```

Installing to a fresh disk is three commands — see [install](docs/install.md).

## Decisions

| Decision | Why |
| -------- | --- |
| U-Boot / DeviceTree boot, not EDK2 | EDK2 puts onboard wifi out of scope |
| firewalld, not raw nftables | zone model, and k3s documents a supported firewalld configuration |
| `--node-ip` on the admin address | survives upstream loss without reporting an unroutable address |
| `bootc upgrade` without `--apply` | stages the image automatically; reboot stays deliberate |
| ghcr.io **and** quay.io | build once, push twice; either can serve an upgrade |
| PSA at the apiserver, Kyverno above it | a floor that cannot fail open, under policy that can |

Config lives in `/usr` wherever possible — versioned with the image, unable to
drift. `/etc` only for genuinely machine-local state. Secrets, SSH keys
included, are overlaid at install time and never baked into the image.

## License

MPL-2.0, see [LICENSE](LICENSE). SPDX headers are on the scripts and the
Containerfile only; everything else here is covered by that file.
