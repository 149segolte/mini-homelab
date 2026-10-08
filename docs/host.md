# Host

The host is a [bootc](https://bootc-dev.github.io/bootc/) image built from
`bootc/`, published to ghcr.io and quay.io, and applied with `bootc upgrade`
followed by a reboot. The base is `quay.io/fedora/fedora-bootc:44`.

`bootc/files/` is copied to `/` during the build. Configuration lives under
`/usr` wherever the software allows it; `/etc` holds what insists on being
there, plus machine-local files written at install time. ostree's three-way
merge keeps a local edit to `/etc` across an upgrade.

## Contents

`brcmfmac-firmware` drives the onboard 2.4 GHz radio and `mt7xxx-firmware` the
USB 5 GHz one. `dnsmasq` is there because NetworkManager's `method=shared`
requires it, `chrony` because the Pi has no real-time clock. CoreDNS and the
Glance agent are copied from upstream container images, which Fedora does not
package.

## Building

`SOURCE_DATE_EPOCH` is taken from the last commit, so the image's created
timestamp follows the source rather than the clock. `--timestamp` overrides it.
Layer mtimes are untouched, so this is not a bit-identical rebuild.

`bootc` builds on every push or pull request touching `bootc/`, `build.py` or
the workflow, and pushes to both registries on everything except a pull
request. `main` publishes `latest`, any other ref the short commit SHA.
`cleanup-ghcr` prunes weekly, keeping five tagged images and never `latest`.

Drop-ins on `bootc-fetch-apply-updates` run `bootc upgrade` without `--apply`,
so images stage automatically while the reboot stays manual. Two deployments are
kept, so `bootc rollback` works offline.

## Installation

For a fresh disk only. Partition and mount the target first:

| #   | Mount   | Size          | Filesystem | Contents                                     |
| --- | ------- | ------------- | ---------- | -------------------------------------------- |
| 1   | ESP     | 1 GiB         | FAT32      | Pi firmware and U-Boot                       |
| 2   | `/boot` | 2 GiB         | ext4       | Kernel and initramfs, one set per deployment |
| 3   | `/`     | 8 GiB or more | ext4       | Deployments; two are kept                    |
| 4   | `/var`  | Remainder     | ext4       | Container storage, k3s data, logs            |

Mount the root partition at `mounted_at`, `/boot` beneath it, and the ESP at
`boot/efi`. Leave `/var` unmounted; `bootc install to-filesystem` fails if
anything is mounted there
([bootc#997](https://github.com/bootc-dev/bootc/issues/997)).

```bash
sudo ./build.py install /mnt "$ROOT_UUID" "$BOOT_UUID" quay.io/149segolte \
  "systemd.mount-extra=UUID=$VAR_UUID:/var:ext4"
sudo ./build.py add-templates /mnt ADMIN_AP_PSK=... HOME_AP_PSK=... \
  UPLINK_PSK=... ADMIN_SSH_KEY=...
sudo bootc/install/fix-var-mount.py /mnt /dev/sda4
```

The order is fixed: `add-templates` writes under `/var`, and `fix-var-mount.py`
copies that onto the real `/var` partition. The registry argument becomes
`--target-imgref`, where the installed host pulls upgrades from, and trailing
arguments become kernel arguments.

### Raspberry Pi firmware

bootc installs the EFI bootloader but not the Broadcom firmware or U-Boot.
Those are overlaid once from `bootc/install/templates/files/rpi/`, gitignored
apart from `config.txt` and populated by hand, following the
[Fedora CoreOS Raspberry Pi 4 guide](https://docs.fedoraproject.org/en-US/fedora-coreos/provisioning-raspberry-pi4/#_installing_fcos_and_booting_via_u_boot).

Four packages supply the files: `uboot-images-armv8`, `bcm2711-firmware`,
`bcm283x-firmware` and `bcm283x-overlays`. The guide's `dnf download` omits
`bcm2711-firmware`, which holds `start4.elf` and `fixup4.dat`, without which
the Pi never reaches a kernel.

### Install-time overlay

The overlay supplies what cannot be baked in because it is machine-specific or
secret: the wifi PSKs, the admin SSH key, the hostname and the timezone.
`bootc/install/templates/config.toml` documents its own schema.

`systemd-sysusers.service` ships with `ConditionNeedsUpdate=|/etc`, which never
fires in image mode, so the admin user would never be created. A drop-in resets
the condition and `add-templates` touches the deployment's `/usr`.

## Networking

The host runs one of two uplink modes, named for how it reaches upstream.
`/etc/homelab/network-mode` selects it; `wired` is the default.

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

`ap-admin` sits in `/etc`, being identical in both modes, and holds
172.19.149.1: the address the cluster publishes on and DHCP hands out, so it
survives a mode change. `wlan-usb` changes role with the mode, so
172.19.150.0/24 exists under `wired` only.

Interfaces are named by driver in `systemd/network/*.link`, so names survive
probe order. `no-auto-default=*` makes every connection a declared keyfile and
leaves `cni0` and `flannel*` alone. Keyfiles must be mode `0600` or
NetworkManager ignores them. Both access points use `method=shared`, so each
runs its own dnsmasq and NAT; both uplinks set `ignore-auto-dns` so upstream
cannot displace the local resolver.

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

The cluster's own ports are absent because they never reach a zone. Traefik's
Service is a ClusterIP carrying an `externalIP`, so kube-proxy rewrites 80, 443
and 3922 in `prerouting` and the traffic is forwarded to a pod rather than
delivered to the host ([Services](services.md#traefik)).

### Pre-DNAT filter

`preroute-priority.service` loads two nftables tables hooked at `prerouting`
priorities -140 and -130. That is after `conntrack` at -200 and ahead of
`dstnat` at -100, the only window where the original destination is intact.

- `block_external` drops every new connection arriving on the uplink.
  `load-preroute` picks `preroute-wired.nft` or `preroute-wireless.nft`, which
  drop `eth-ext` and, under `wireless`, `wlan-usb` with it. This is all that
  stands between the uplink and the ports the host and the cluster answer on,
  since both bind every interface.
- `block_outside_cluster_access` drops traffic addressed to 10.42/16 or
  10.43/16 unless it came from there, so pod and service addresses are
  reachable from inside the cluster only. The rewrite above is unaffected,
  because at that point the destination is still the host's own address.

Both chains accept `established` and `related` first, so replies to the host's
own connections survive. `PartOf=firewalld.service` restarts the unit with
firewalld; a reload does not need it.

### DNS

```
client -> CoreDNS :53 -> 10.43.0.53, then 1.1.1.1, 8.8.8.8
```

CoreDNS binds every interface, so one resolver answers the access points, the
tailnet, the pods and the host. dnsmasq runs with `port=0` and serves DHCP
alone, handing out 172.19.149.1 as the resolver on both access points.

The host's own name is answered from `/etc/coredns/hosts` ahead of `forward`,
not from blocky, because ssh has to work when the cluster does not. That file
is written at install time, so the record tracks the configured hostname.

10.43.0.53 is blocky's Service ([Services](services.md#dns)), fixed because the
Corefile names it literally and unreachable from outside the cluster
([pre-DNAT filter](#pre-dnat-filter)). CoreDNS health-checks it, so a cluster
outage costs ad blocking and the internal records rather than DNS.

### Tailscale

Tailscale runs as a host service, so remote access survives a cluster outage.
Tailscale ACLs decide which devices may connect; the firewall zone decides only
what the host answers. Two settings live in the admin console: advertised
subnet routes and the exit node both need approval, and split DNS maps
`${DOMAIN}` to 172.19.149.1.

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
- `resolv-conf` names a file holding that same address ([DNS](#dns)).
- `kubelet-arg` sets a shutdown grace period, so an upgrade reboot drains.
- `kube-apiserver-arg` sets `admission-control-config-file`
  ([Cluster](cluster.md#admission-control)).

`k3s-killall` is included for stops that would otherwise leave containers and
mounts behind.
