# Host

The host is a [bootc](https://bootc-dev.github.io/bootc/) image built from
`bootc/` on `quay.io/fedora/fedora-bootc:44`. `bootc/files/` is copied to `/`.
Configuration lives under `/usr` where the software allows it, and `/etc` holds
the rest plus machine-local files written at install time.

## Building

`bootc.yml` builds on any change to `bootc/`, `build.py` or the workflow, and
pushes to ghcr.io and quay.io except from a pull request. `main` publishes
`latest`; other refs publish the short SHA. `cleanup-ghcr` keeps five tagged
images. The image timestamp follows the last commit, not the clock.

## Upgrades

`bootc-fetch-apply-updates` stages new images without `--apply`, so the reboot
stays manual. Two deployments are kept, so `bootc rollback` works offline.

## Installation

For a fresh disk only. Partition and mount the target first:

| #   | Mount   | Size          | Filesystem | Contents                          |
| --- | ------- | ------------- | ---------- | --------------------------------- |
| 1   | ESP     | 1 GiB         | FAT32      | Pi firmware and U-Boot            |
| 2   | `/boot` | 2 GiB         | ext4       | Kernel and initramfs              |
| 3   | `/`     | 8 GiB or more | ext4       | Deployments                       |
| 4   | `/var`  | Remainder     | ext4       | Container storage, k3s data, logs |

Mount the root at the target, `/boot` beneath it and the ESP at `boot/efi`.
Leave `/var` unmounted, or `bootc install to-filesystem` fails
([bootc#997](https://github.com/bootc-dev/bootc/issues/997)). Then, in this
order:

```bash
sudo ./build.py install /mnt "$ROOT_UUID" "$BOOT_UUID" quay.io/149segolte \
  "systemd.mount-extra=UUID=$VAR_UUID:/var:ext4"
sudo ./build.py add-templates /mnt ADMIN_AP_PSK=... HOME_AP_PSK=... \
  UPLINK_PSK=... ADMIN_SSH_KEY=...
sudo bootc/install/fix-var-mount.py /mnt /dev/sda4
```

The registry argument is where the host pulls upgrades from. Trailing arguments
become kernel arguments. `add-templates` writes the machine-specific and secret
files described by `bootc/install/templates/config.toml`, and
`fix-var-mount.py` copies what it wrote onto the real `/var`.

### Raspberry Pi firmware

bootc does not install the Broadcom firmware or U-Boot. Populate
`bootc/install/templates/files/rpi/` by hand from `uboot-images-armv8`,
`bcm2711-firmware`, `bcm283x-firmware` and `bcm283x-overlays`, following the
[Fedora CoreOS Raspberry Pi 4 guide](https://docs.fedoraproject.org/en-US/fedora-coreos/provisioning-raspberry-pi4/#_installing_fcos_and_booting_via_u_boot).
The guide omits `bcm2711-firmware`, which holds `start4.elf` and `fixup4.dat`;
without it the Pi never reaches a kernel.

`systemd-sysusers.service` is conditioned on `/etc` needing an update, which
never fires in image mode. A drop-in resets the condition so the admin user is
created.

## Networking

`/etc/homelab/network-mode` selects one of two uplink modes, `wired` by default.
Changing it takes a reboot.

| Connection        | Interface  | Mode     | Zone  | Address         |
| ----------------- | ---------- | -------- | ----- | --------------- |
| `ap-admin`        | `wlan-adm` | both     | admin | 172.19.149.1/24 |
| `ap-home`         | `wlan-usb` | wired    | home  | 172.19.150.1/24 |
| `uplink-wired`    | `eth-ext`  | wired    | ext   | Upstream DHCP   |
| `uplink-wireless` | `wlan-usb` | wireless | ext   | Upstream DHCP   |

Under `wired` the Pi routes for everything beneath it. Under `wireless` it
publishes nothing to the upstream network, so services are reachable over the
tunnel, the admin AP and the tailnet only.

`network-mode.service` stages the selected mode's profiles into
`/run/NetworkManager/system-connections`, so NetworkManager never sees the
other mode. 172.19.149.1 is the same in both modes, which is why the cluster
publishes on it. Keyfiles must be mode `0600` or NetworkManager ignores them.

### Firewall

firewalld governs traffic to the host. Every zone drops by default.

| Zone        | Allows                                                    |
| ----------- | --------------------------------------------------------- |
| `home`      | dhcp, dns                                                 |
| `admin`     | dhcp, dns, ssh, 6443                                      |
| `tailscale` | dns, ssh, 6443                                            |
| `ext`       | nothing                                                   |
| `k8s`       | everything, from pod addresses and `cni0` and `flannel.1` |

The `to-ext` policy masquerades every other zone towards `ext`. Nothing may
address `k8s` from outside.

The cluster's ports 80, 443 and 3922 never reach a zone: kube-proxy rewrites
them in `prerouting` to Traefik's pod ([Services](services.md#traefik)).

### Pre-DNAT filter

`preroute-priority.service` loads two nftables tables between conntrack (-200)
and DNAT (-100), the only point where the original destination is intact.

- `block_external` drops new connections arriving on the uplink. It is the only
  thing between the uplink and the ports the host and cluster answer on.
- `block_outside_cluster_access` drops traffic to 10.42/16 and 10.43/16 unless
  it came from there.

### DNS

```
client -> CoreDNS :53 -> 10.43.0.53 (blocky), then 1.1.1.1, 8.8.8.8
```

CoreDNS answers every interface, and dnsmasq serves DHCP only. The host's own
name comes from `/etc/coredns/hosts`, so ssh works without the cluster. If
blocky is down, CoreDNS falls back to the public resolvers and loses only ad
blocking and the internal records.

### Tailscale

Tailscale runs on the host so remote access survives a cluster outage. In the
admin console, approve the subnet routes and exit node, and add split DNS
mapping `${DOMAIN}` to 172.19.149.1.

## k3s

k3s runs as a single-node server. The cluster's contents belong to Flux
([Cluster](cluster.md)).

- The unit waits for `time-sync.target`. The Pi has no real-time clock, and
  k3s issues its certificates on first start.
- `node-ip` is 172.19.149.1, which stays up when the upstream link does not.
- ServiceLB is disabled; Traefik uses `externalIPs` instead.
- `kube-apiserver-arg` loads the admission configuration
  ([Cluster](cluster.md#admission-control)).
