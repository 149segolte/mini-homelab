# Host

The host is a [bootc](https://bootc-dev.github.io/bootc/) image built from
`bootc/`, published to ghcr.io and quay.io, and applied with `bootc upgrade`
followed by a reboot. The base image is `quay.io/fedora/fedora-bootc:44`.

`bootc/files/` is copied to `/` during the build. Configuration lives under
`/usr` wherever the software allows it, so that it is versioned with the image.
`/etc` holds what the software insists on finding there, plus machine-local
files written at install time. Files shipped there are still versioned;
ostree's three-way merge keeps a local edit across an upgrade.

## Contents

The image adds the following to the base:

| Package                                 | Purpose                                    |
| --------------------------------------- | ------------------------------------------ |
| `NetworkManager-wifi`, `wpa_supplicant` | Both access points                         |
| `brcmfmac-firmware`, `mt7xxx-firmware`  | Onboard 2.4 GHz and USB 5 GHz radios       |
| `dnsmasq`                               | Required by NetworkManager `method=shared` |
| `chrony`                                | The Pi has no real-time clock              |
| `firewalld`                             | Zone-based firewall, default zone `drop`   |
| `tailscale`                             | Remote access, independent of the cluster  |
| `k3s`, `k3s-selinux`                    | The cluster itself                         |

Nothing in this repository configures dnsmasq; NetworkManager owns it.

CoreDNS and the Glance agent are copied from their upstream container images.
Neither is packaged by Fedora.

## Building

`build.py` is a [PEP 723](https://peps.python.org/pep-0723/) script, so `uv`
supplies the interpreter and there is no task runner to install. Subcommands
are the script's public functions, and `./build.py --help` lists them.

```bash
./build.py build                    # arm64, :latest, runs bootc container lint
./build.py push quay.io/149segolte  # tag and push an existing build
```

### Continuous integration

- `bootc-ci` builds, and nothing more, on a push or PR touching `bootc/`,
  `build.py` or the workflows.
- `bootc-deployment` builds and pushes to both registries after a successful
  `bootc-ci`, or on manual dispatch.
- `cleanup-ghcr` runs weekly, pruning images over 30 days old. It keeps five
  tagged images and never removes `latest`.

Builds from `main` publish `latest`. Any other branch publishes the short
commit SHA.

### Upgrades

`bootc-fetch-apply-updates.timer` runs 15 minutes after boot and every 15
minutes after that. A drop-in overrides `ExecStart` to run `bootc upgrade`
without `--apply`, so new images stage automatically while the reboot stays
manual. Two deployments are kept on disk, so `bootc rollback` works without
network access.

## Installation

Installation applies to a fresh disk only. Partition and mount the target
first:

| #   | Mount   | Size          | Filesystem | Contents                                     |
| --- | ------- | ------------- | ---------- | -------------------------------------------- |
| 1   | ESP     | 1 GiB         | FAT32      | Pi firmware and U-Boot                       |
| 2   | `/boot` | 2 GiB         | ext4       | Kernel and initramfs, one set per deployment |
| 3   | `/`     | 8 GiB or more | ext4       | Deployments; two are kept                    |
| 4   | `/var`  | Remainder     | ext4       | Container storage, k3s data, logs            |

Mount the root partition at `mounted_at`, `/boot` beneath it, and the ESP at
`boot/efi`. Leave `/var` unmounted. `bootc install to-filesystem` fails if
anything is mounted there
([bootc#997](https://github.com/bootc-dev/bootc/issues/997)).

```bash
sudo ./build.py install /mnt "$ROOT_UUID" "$BOOT_UUID" quay.io/149segolte \
  "systemd.mount-extra=UUID=$VAR_UUID:/var:ext4"
sudo ./build.py add-templates /mnt ADMIN_AP_PSK=... HOME_AP_PSK=... ADMIN_SSH_KEY=...
sudo bootc/install/fix-var-mount.py /mnt /dev/sda4
```

The order is fixed. `add-templates` writes under `/var`, and
`fix-var-mount.py` copies that content onto the real `/var` partition. The
registry argument becomes `--target-imgref`, which is where the installed host
pulls its upgrades from. Trailing arguments become kernel arguments.

### Raspberry Pi firmware

bootc installs the EFI bootloader but not the Broadcom firmware or U-Boot, and
does not revisit the ESP afterwards. Those files are overlaid once from
`bootc/install/templates/files/rpi/`, which is gitignored apart from
`config.txt` and has to be populated by hand. The
[Fedora CoreOS Raspberry Pi 4 guide](https://docs.fedoraproject.org/en-US/fedora-coreos/provisioning-raspberry-pi4/#_installing_fcos_and_booting_via_u_boot)
describes the process.

Four packages supply the files: `uboot-images-armv8`, `bcm2711-firmware`,
`bcm283x-firmware` and `bcm283x-overlays`. The guide's `dnf download` command
lists only three. It omits `bcm2711-firmware`, which contains `start4.elf` and
`fixup4.dat`, and without those the Pi never reaches a kernel.

### Install-time overlay

The overlay supplies files that cannot be baked into the image because they are
specific to this machine or secret: the access point PSKs, the admin SSH key,
the hostname and the timezone.

`bootc/install/templates/config.toml` lists the entries and documents its own
schema. `[vars]` provides substitution values, and `KEY=VALUE` arguments to
`add-templates` override them.

Destinations are written as the booted system sees them, then mapped onto disk:

| Booted path | On disk                                                      |
| ----------- | ------------------------------------------------------------ |
| `/boot/*`   | The target directly                                          |
| `/var/*`    | `ostree/deploy/<stateroot>/var/*`, shared across deployments |
| `/etc/*`    | `ostree/deploy/<stateroot>/deploy/<checksum>.<serial>/etc/*` |

These are the only legal destinations. `/usr` belongs to the image, and the
remaining top-level directories are symlinks into `/var`. Every entry is
validated before any file is written, because bootc marks the root and boot
filesystems read-only at the end of the install.

`systemd-sysusers.service` ships with `ConditionNeedsUpdate=|/etc`, which never
fires in image mode, and the admin user would otherwise never be created. A
drop-in resets the condition and `add-templates` touches the deployment's
`/usr`.

## Networking

All traffic transits the Pi: it is the upstream client, both access points, the
router and the resolver.

| Zone        | Interface           | Network            |
| ----------- | ------------------- | ------------------ |
| `ext`       | `eth-ext`, onboard  | Upstream DHCP      |
| `admin`     | `wlan-adm`, 2.4 GHz | 172.19.150.0/24    |
| `home`      | `wlan-usb`, 5 GHz   | 172.19.149.0/24    |
| `tailscale` | `tailscale0`        | Tailnet            |
| `k8s`       | `cni0`, `flannel.1` | 10.42/16, 10.43/16 |

Interfaces are named by driver in `systemd/network/*.link` so the names survive
probe order. NetworkManager runs with `no-auto-default=*`, which makes every
connection a declared keyfile, and it leaves `cni0` and `flannel*` alone.
Keyfiles must be mode `0600` or NetworkManager ignores them.

Both access points use `method=shared`, so each runs its own dnsmasq and NAT,
which the `to-ext` policy's masquerade overlaps. The `upstream` connection sets
`ignore-auto-dns`, which stops the upstream router's resolver from displacing
the local one.

firewalld governs traffic that terminates on the host. An nftables table
hooked ahead of it governs traffic that does not.

### Firewall

Every zone has target `DROP`, so a port is reachable only if listed.

- `home`: dhcp and dns.
- `admin`: dhcp, dns, ssh and 6443.
- `tailscale`: dns, ssh and 6443.
- `ext`: nothing.
- `k8s`: target `ACCEPT`, matched by both source address and the `cni0` and
  `flannel.1` interfaces, with `forward` set so pods reach each other.

One policy, `to-ext`, accepts and masquerades from `admin`, `home`, `tailscale`
and `k8s` towards `ext`. There is no policy towards `k8s`; nothing outside the
cluster may address it.

None of the ports the cluster serves appear above. Traefik's Service is a
ClusterIP carrying `externalIPs`, so kube-proxy DNATs 80, 443 and 3922 in
`prerouting` ([Services](services.md#traefik)). That traffic is forwarded to a
pod rather than delivered to the host, so no zone evaluates it and firewalld
accepts it.

### Pre-DNAT filter

`preroute-priority.service` loads `/usr/share/nftables/preroute-priority.nft`
into two tables of its own, hooked at `prerouting` priorities -140 and -130:
after `conntrack` at -200 and ahead of `dstnat` at -100, the only window where
the original destination is still intact.

- `block_external` drops every new connection arriving on `eth-ext`.
- `block_outside_cluster_access` drops traffic addressed to 10.42/16 or
  10.43/16 unless it came from there, so pod and service addresses are reachable
  from inside the cluster only. The DNAT above is unaffected, because at that
  point the destination is still the host's own address.

Neither chain sees host-originated traffic, which does not traverse
`prerouting`. Both accept `established` and `related` first, or replies to the
host's own connections would be dropped. `PartOf=firewalld.service` restarts
the unit with firewalld; a reload does not need it.

### DNS

```
client -> dnsmasq (per AP, no cache) -> CoreDNS :53 -> 127.0.0.1:5335, then 1.1.1.1, 8.8.8.8
```

dnsmasq forwards without caching, and CoreDNS is the only cache in the chain.
CoreDNS binds to loopback, so k3s is pointed at 172.19.150.1 instead; a pod's
loopback is its own.

The host's own name is answered by CoreDNS from `/etc/coredns/hosts`, which the
`hosts` plugin serves ahead of `forward`. It is not a Technitium zone, because
ssh has to work when the cluster does not. The Corefile ships in the image and
the hosts file is written at install time, so the record tracks the configured
hostname.

127.0.0.1:5335 is Technitium, a cluster workload with a `hostPort` bound to
loopback ([Technitium](technitium.md)). CoreDNS health-checks it, so while that
pod is down the host keeps resolving through the public forwarders. A cluster
outage costs ad blocking rather than DNS.

Technitium sits downstream of this chain, so its own forwarders must be IP
literals. A hostname there resolves back through the chain into Technitium
itself.

### Tailscale

Tailscale runs as a host service, so remote access survives a cluster outage.
Tailscale ACLs decide which devices may connect; the firewall zone decides only
what the host answers.

Two settings live in the Tailscale admin console rather than in this repo:

- Both advertised subnet routes and the exit node need approval.
- Split DNS maps `${DOMAIN}` to nameserver 172.19.149.1, which reaches the home
  AP's dnsmasq and from there the chain above.

## k3s

k3s runs as a single-node server. The binary and `k3s-selinux` are part of the
image; the cluster's contents belong to Flux ([Cluster](cluster.md)).

`/usr/lib/systemd/system/k3s.service` replaces the unit shipped by the
installer and adds two directives:

- `After=time-sync.target`. The Pi has no real-time clock and k3s issues
  certificates at first start, so without this the cluster comes up with
  epoch-dated certificates.
- `RequiresMountsFor=/var/lib/rancher`, because `/var` is mounted by a kernel
  argument.

`/etc/rancher/k3s/config.yaml` sets:

- `disable` drops ServiceLB; Traefik's Service carries `externalIPs` instead
  ([Services](services.md#traefik)).
- `node-ip` is 172.19.150.1, the admin AP address hosted by the Pi itself.
  Upstream DHCP disappears with upstream, and a node advertising an unroutable
  address is worse than one on a link that is always up.
- `resolv-conf` names a file holding 172.19.150.1. CoreDNS binds loopback,
  which a pod cannot reach.
- `kubelet-arg` sets `config=kubelet.config`: a 30 s shutdown grace and 10 s
  for critical pods, so an upgrade reboot drains.
- `kube-apiserver-arg` sets `admission-control-config-file=psa.yaml` for Pod
  Security Admission ([Cluster](cluster.md#admission-control)).

The unit and these files are the entire interface to k3s; service parameters
are otherwise left at their defaults. `k3s-killall.sh` is included for stops
that would leave containers and mounts behind.

Traefik ships with k3s and is configured in place rather than replaced
([Services](services.md#traefik)).
