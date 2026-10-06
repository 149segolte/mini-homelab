# Host

The host is a [bootc](https://bootc-dev.github.io/bootc/) image built from
`bootc/`, published to ghcr.io and quay.io, and applied with `bootc upgrade`
followed by a reboot. The base image is `quay.io/fedora/fedora-bootc:44`.

`bootc/files/` is copied to `/` during the build. Configuration lives under
`/usr` wherever the software allows it. `/etc` holds what the software insists
on finding there, plus machine-local files written at install time. Both are
versioned with the image, and ostree's three-way merge keeps a local edit to
`/etc` across an upgrade.

## Contents

`bootc/Containerfile` lists the packages. Three of them are not obvious from
the name: `brcmfmac-firmware` drives the onboard 2.4 GHz radio and
`mt7xxx-firmware` the USB 5 GHz one, `dnsmasq` is present because
NetworkManager's `method=shared` requires it, and `chrony` because the Pi has
no real-time clock.

NetworkManager owns dnsmasq, which one drop-in configures ([DNS](#dns)).
CoreDNS and the Glance agent are copied from upstream container images, because
Fedora packages neither.

## Building

`build.py` is a [PEP 723](https://peps.python.org/pep-0723/) script, so `uv`
supplies the interpreter and there is no task runner to install.

`bootc-ci` builds and nothing more, on a push or pull request touching
`bootc/`, `build.py` or the workflows. `bootc-deployment` builds and pushes to
both registries after a successful `bootc-ci`, or on manual dispatch.
`cleanup-ghcr` runs weekly and prunes images over 30 days old, keeping five
tagged images and never removing `latest`. Builds from `main` publish `latest`.
Any other branch publishes the short commit SHA.

A drop-in on `bootc-fetch-apply-updates.timer` runs `bootc upgrade` without
`--apply`, so new images stage automatically while the reboot stays manual. Two
deployments are kept on disk, so `bootc rollback` works without network access.

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
sudo ./build.py add-templates /mnt ADMIN_AP_PSK=... HOME_AP_PSK=... \
  UPLINK_PSK=... ADMIN_SSH_KEY=...
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
omits `bcm2711-firmware`, which holds `start4.elf` and `fixup4.dat`. Without
those the Pi never reaches a kernel.

### Install-time overlay

The overlay supplies files that cannot be baked into the image because they are
specific to this machine or secret: the wifi PSKs, the admin SSH key,
the hostname and the timezone. `bootc/install/templates/config.toml` lists the
entries and documents its own schema.

Destinations are written as the booted system sees them and mapped onto disk by
`overlay.py`. Only `/boot`, `/var` and `/etc` are legal. `/usr` belongs to the
image, and the remaining top-level directories are symlinks into `/var`. Every
entry is validated before any file is written, because bootc marks the root and
boot filesystems read-only at the end of the install.

`systemd-sysusers.service` ships with `ConditionNeedsUpdate=|/etc`, which never
fires in image mode, so the admin user would never be created. A drop-in resets
the condition, and `add-templates` touches the deployment's `/usr`.

## Networking

The host runs one of two uplink modes, named for how it reaches upstream.
`/etc/homelab/network-mode` selects it and `wired` is the shipped default.

| Connection        | Interface  | Mode     | Zone  | Address         |
| ----------------- | ---------- | -------- | ----- | --------------- |
| `ap-admin`        | `wlan-adm` | both     | admin | 172.19.149.1/24 |
| `ap-home`         | `wlan-usb` | wired    | home  | 172.19.150.1/24 |
| `uplink-wired`    | `eth-ext`  | wired    | ext   | Upstream DHCP   |
| `uplink-wireless` | `wlan-usb` | wireless | ext   | Upstream DHCP   |

Under `wired` the Pi routes for everything beneath it. Under `wireless` it sits
beside the devices on the upstream network and publishes nothing to them, so
services stay reachable over the tunnel, the admin AP and the tailnet.

`network-mode.service` stages the selected mode's profiles from
`/etc/homelab/network-modes/<mode>/` into
`/run/NetworkManager/system-connections`, a keyfile directory NetworkManager
reads alongside `/etc` and `/usr/lib`. The unselected mode's profiles are in
none of the three, so NetworkManager never sees them. Changing modes is an edit
and a reboot.

`ap-admin` is the only connection in `/etc/NetworkManager/system-connections`,
because it is identical in both modes. It holds 172.19.149.1, the address the
cluster publishes on and the resolver DHCP hands out. The admin AP holds it
rather than the home one so that it exists in both modes; `wlan-usb` is the
home AP or the uplink depending on the mode, so 172.19.150.0/24 exists under
`wired` only.

Interfaces are named by driver in `systemd/network/*.link`, so the names
survive probe order. NetworkManager runs with `no-auto-default=*`, which makes
every connection a declared keyfile, and leaves `cni0` and `flannel*` alone.
Keyfiles must be mode `0600` or NetworkManager ignores them.

Both access points use `method=shared`, so each runs its own dnsmasq and NAT.
The `to-ext` policy masquerades as well, so the two overlap. Both uplink
connections set `ignore-auto-dns`, which stops the upstream router's resolver
from displacing the local one.

firewalld governs traffic that terminates on the host. An nftables table hooked
ahead of it governs traffic that does not.

### Firewall

Every zone has target `DROP`, so a port is reachable only if listed.

- `home`: dhcp, dns.
- `admin`: dhcp, dns, ssh, 6443.
- `tailscale`: dns, ssh, 6443.
- `ext`: nothing, whichever interface is the uplink.
- `k8s`: target `ACCEPT`, matched by source address and by the `cni0` and
  `flannel.1` interfaces, with `forward` set so pods reach each other.

One policy, `to-ext`, accepts and masquerades from `admin`, `home`, `tailscale`
and `k8s` towards `ext`. No policy points at `k8s`, so nothing outside the
cluster may address it.

None of the ports the cluster serves appear above. Traefik's Service is a
ClusterIP carrying an `externalIP`, so kube-proxy rewrites 80, 443 and 3922 in
`prerouting` ([Services](services.md#traefik)). That traffic is forwarded to a
pod rather than delivered to the host, so no zone evaluates it and firewalld
accepts it.

### Pre-DNAT filter

`preroute-priority.service` loads two nftables tables of its own, hooked at
`prerouting` priorities -140 and -130. That is after `conntrack` at -200 and
ahead of `dstnat` at -100, the only window where the original destination is
still intact.

- `block_external` drops every new connection arriving on the uplink, and is
  the one rule that differs between modes. `load-preroute` picks
  `preroute-wired.nft` or `preroute-wireless.nft`, which drop `eth-ext` and,
  under `wireless`, `wlan-usb` with it. This is all that stands between the
  uplink and the ports the host and the cluster answer on, since both bind
  every interface.
- `block_outside_cluster_access` drops traffic addressed to 10.42/16 or
  10.43/16 unless it came from there. Pod and service addresses are therefore
  reachable from inside the cluster only. The rewrite above is unaffected,
  because at that point the destination is still the host's own address.

Neither chain sees host-originated traffic, which does not traverse
`prerouting`. Both accept
`established` and `related` first, or replies to the host's own connections
would be dropped. `PartOf=firewalld.service` restarts the unit with firewalld.
A reload does not need it.

### DNS

```
client -> CoreDNS :53 -> 10.43.0.53, then 1.1.1.1, 8.8.8.8
```

CoreDNS binds every interface, so one resolver answers the access points, the
tailnet, the pods and the host itself. dnsmasq runs with `port=0` and serves
DHCP alone, handing out 172.19.149.1 as the resolver on both access points.

The host's own name is answered from `/etc/coredns/hosts`, which the `hosts`
plugin serves ahead of `forward`. It is not a blocky record, because ssh has
to work when the cluster does not. The Corefile ships in the image and the
hosts file is written at install time, so the record tracks the configured
hostname.

10.43.0.53 is blocky's Service ([Services](services.md#dns)). The address is
fixed because the Corefile references it literally, and unreachable from
outside the cluster ([pre-DNAT filter](#pre-dnat-filter)). CoreDNS
health-checks it, so while the pods are down the host keeps resolving through
the public forwarders. A cluster outage costs ad blocking and the internal
records rather than DNS.

### Tailscale

Tailscale runs as a host service, so remote access survives a cluster outage.
Tailscale ACLs decide which devices may connect. The firewall zone decides only
what the host answers.

Two settings live in the Tailscale admin console:

- Advertised subnet routes and the exit node both need approval.
- Split DNS maps `${DOMAIN}` to nameserver 172.19.149.1, where CoreDNS answers
  in either mode.

## k3s

k3s runs as a single-node server. The binary and `k3s-selinux` are part of the
image. The cluster's contents belong to Flux ([Cluster](cluster.md)).

`/usr/lib/systemd/system/k3s.service` replaces the unit shipped by the
installer and adds two directives:

- `After=time-sync.target`. The Pi has no real-time clock and k3s issues
  certificates at first start, so without this the cluster comes up with
  epoch-dated certificates.
- `RequiresMountsFor=/var/lib/rancher`, because `/var` is mounted by a kernel
  argument.

`/etc/rancher/k3s/config.yaml` sets:

- `disable` drops ServiceLB. Traefik's Service carries `externalIPs` instead
  ([Services](services.md#traefik)).
- `node-ip` is 172.19.149.1, the admin AP address hosted by the Pi itself.
  Upstream DHCP disappears with upstream, and a node advertising an unroutable
  address is worse than one on a link that is always up.
- `resolv-conf` names a file holding that same address, which is where CoreDNS
  answers in either mode ([DNS](#dns)).
- `kubelet-arg` sets `config=kubelet.config`, a 30 s shutdown grace and 10 s
  for critical pods, so an upgrade reboot drains.
- `kube-apiserver-arg` sets `admission-control-config-file=admission.yaml`,
  which configures Pod Security Admission and the static mutating policies
  under `admission/mutating-policies/`
  ([Cluster](cluster.md#admission-control)).

Traefik ships with k3s and is configured in place rather than replaced
([Services](services.md#traefik)). `k3s-killall.sh` is included for stops that
would otherwise leave containers and mounts behind.
