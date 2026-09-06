# Host image

`bootc/` → a bootc image, published to ghcr.io and quay.io, applied with
`bootc upgrade` + reboot. Base: `quay.io/fedora/fedora-bootc:44`.

Non-obvious contents:

| | |
| --- | --- |
| `brcmfmac-firmware`, `mt7xxx-firmware` | onboard 2.4GHz and USB 5GHz radios |
| `dnsmasq` | required by NetworkManager `method=shared`; nothing configures it directly |
| `chrony` | no RTC on the Pi |
| `coredns` | copied from the upstream image; not packaged by Fedora |
| `tailscale` | host service, so remote access survives the cluster |

`files/` is copied to `/` verbatim. Config lives under `/usr` wherever the
software allows; `/etc` is machine-local only, written at install time
([overlay](overlay.md)).

`firewalld` defaults to zone `drop`. The build ends with `bootc container
lint`, so a passing build is a passing lint.

Both `dnf` layers remove `/run/dnf`, not `/run/*`: podman bind-mounts
`/run/systemd/resolve/stub-resolv.conf`, and unlinking a bind mount fails with
`Device or resource busy`.

## Building

`build.py` is a PEP 723 script — `uv` supplies the interpreter. Subcommands are
its public functions; `./build.py --help` is the current list.

```bash
./build.py build                    # :latest, arm64, lint included
./build.py push quay.io/149segolte  # tag and push an existing build
```

`--cache-registry` uses `<registry>/mini-homelab-cache` for layer cache.

## CI

| Workflow | Trigger | Does |
| --- | --- | --- |
| `bootc-ci` | push/PR touching `bootc/`, `build.py`, workflows | builds; publishes nothing |
| `bootc-deployment` | successful `bootc-ci`, or manual | builds, pushes to both registries |
| `cleanup-ghcr` | weekly | prunes >30d, keeps 5 tagged, never `latest` |

`main` publishes `latest`, anything else the short SHA.

## Upgrades

`bootc-fetch-apply-updates.timer` runs 15m after boot and every 15m after. Its
`ExecStart` is overridden to `bootc upgrade --quiet` — no `--apply` — so images
are staged automatically but the reboot stays deliberate. Two deployments are
kept, so `bootc rollback` works offline.
