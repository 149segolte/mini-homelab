# k3s

Single-node server, running as a host service. The binary and `k3s-selinux` are
in the image ([host image](host-image.md)); the cluster's contents are Flux's
([gitops](flux.md)).

`/usr/lib/systemd/system/k3s.service` replaces the shipped unit:

- `After=time-sync.target` — no RTC, and k3s issues certificates at first
  start; without it the cluster comes up with epoch-dated certs.
- `RequiresMountsFor=/var/lib/rancher` — `/var` is mounted by a karg.

`k3s-killall.sh` is in the image for stops that leave containers and mounts
behind.

## Config

`/etc/rancher/k3s/config.yaml`:

| | |
| --- | --- |
| `node-ip: 172.19.150.1` | the admin AP address, hosted by the Pi itself. Upstream DHCP disappears with upstream, and a node advertising an unroutable address is worse than one on a link that is always up |
| `resolv-conf` | 172.19.150.1, not loopback ([networking](networking.md#dns)) |
| `kubelet.config` | 30s shutdown grace, 10s for critical pods, so an upgrade reboot drains |
| `psa.yaml` | in-tree PodSecurity plugin ([admission](admission.md)) |

Service parameters are otherwise left alone — the unit and config files are the
interface.

Traefik ships with k3s and is configured in place rather than replaced
([ingress](ingress.md)).
