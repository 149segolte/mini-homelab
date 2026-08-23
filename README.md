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

### Disk partitioning

The image does not partition anything. Lay the target disk out first, mount it,
then pass the resulting UUIDs to the `install` task. Any layout bootc supports
will work — this is the one this host is built around:

| #   | Mount   | Size          | Filesystem | Why                                                             |
| --- | ------- | ------------- | ---------- | --------------------------------------------------------------- |
| 1   | ESP     | 1 GiB         | FAT32      | Pi firmware and U-Boot; FAT32 is mandatory                      |
| 2   | `/boot` | 2 GiB         | ext4       | Kernel and initramfs per deployment, managed directly by ostree |
| 3   | `/`     | 8 GiB or more | ext4       | Image deployments; two are kept, so allow twice one image       |
| 4   | `/var`  | Remainder     | ext4       | State bootc never manages: container storage, k3s data, logs    |

Mount the root partition at whatever path you pass as `mounted_at`, with
`/boot` beneath it and the ESP at `boot/efi`, which is where bootc looks for
it. `install` takes those two UUIDs, the registry the host should track,
and any extra kernel arguments.

`/var` needs a second step, because `bootc install to-filesystem` will not set
up a separate one ([bootc#997](https://github.com/bootc-dev/bootc/issues/997)).
Install with the partition unmounted, then seed it from the installed root:

```bash
sudo bootc/install/fix-var-mount.py /mnt /dev/sda4
```

That mounts the partition on a temporary directory, copies `/mnt/var/` across
with hard links, ACLs and SELinux labels intact, relabels the copy unless you
pass `--no-relabel`, and unmounts. The installed system picks the partition up
from a `systemd.mount-extra` karg.

## Key decisions

| Decision                           | Why                                                                 |
| ---------------------------------- | ------------------------------------------------------------------- |
| U-Boot / DeviceTree boot, not EDK2 | EDK2 puts onboard wifi out of scope                                 |
| firewalld, not raw nftables        | Zone model, and k3s documents a supported firewalld configuration   |
| `--node-ip` on the admin address   | Survives upstream wifi loss without reporting an unroutable address |
| `bootc upgrade` without `--apply`  | Stages the image automatically; reboot stays deliberate             |

Config goes in `/usr` wherever possible (versioned with the image, cannot drift).
`/etc` only for genuinely machine-local config. SSH keys are injected at install time
via `bootc-image-builder` config, never baked into the image.

## Layout

```
build.py             project tasks; public functions are the subcommands
.github/workflows/   CI/CD workflows
bootc/               host image
  Containerfile
  files/             content copied into the image, mirroring target paths
  install/           scripts run against a target at install time
```

Flux directories (`clusters/`, `infrastructure/`, `apps/`) arrive with that layer.

## Building

`build.py` is a PEP 723 script, so `uv` supplies the interpreter and there is no
task runner to install. Subcommands are derived from the script's public
functions and their signatures, which makes `./build.py --help` the current
list of subcommands.

```bash
./build.py build                    # build :latest, lint included
./build.py push quay.io/149segolte  # tag an existing build and push it

# On the installer host, as root, with the target filesystems mounted.
# Trailing arguments become kernel arguments.
sudo ./build.py install /mnt "$ROOT_UUID" "$BOOT_UUID" quay.io/149segolte \
  "systemd.mount-extra=UUID=$VAR_UUID:/var:ext4"
```

`install` wraps `bootc install to-filesystem` in a privileged container. The
registry argument is recorded as `--target-imgref`, which is what the installed
host pulls on later `bootc upgrade` runs, so it chooses which registry the Pi
actually tracks. Everything after it is passed through as `--karg` — including
the `systemd.mount-extra` that mounts `/var`.

The build pins `linux/arm64`, so the result targets the Pi whatever host built
it. CI tags `latest` on `main` and the short commit SHA elsewhere.

## Build increments

- [x] bare scaffold
- [x] CI build and push to ghcr.io/quay.io
- [ ] install to disk (partitioning + `install` task + ESP population)
- [ ] networking: admin AP + upstream client
- [ ] firewalld zones
- [ ] k3s
- [ ] Flux bootstrap
- [ ] workloads: reverse proxy, TLS
