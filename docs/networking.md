# Networking

Everything transits the Pi: upstream client, both APs, router, resolver.

| Zone | Interface | Network | Accepts | Forwards to |
| --- | --- | --- | --- | --- |
| `ext` | `eth-ext`, onboard | upstream DHCP | nothing | nothing |
| `admin` | `wlan-adm`, 2.4GHz | 172.19.150.0/24 | ssh, dns, dhcp, 6443 | `ext` |
| `home` | `wlan-usb`, 5GHz | 172.19.149.0/24 | dns, dhcp, http/s | `ext`, `k8s` |
| `tailscale` | `tailscale0` | tailnet | ssh, dns, http/s, 6443 | `ext`, `k8s` |
| `k8s` | matched by source | 10.42/16, 10.43/16 | — | — |

`ext` is `DROP` and egress only. `k8s` matches on source address, so it holds
whatever the CNI names its links. Forwarding is one firewalld policy file per
direction; unlisted pairs do not forward. `admin` has no path to `k8s` —
administration is kubectl over 6443, not direct pod access. Default zone is
`drop`.

Interfaces are named by driver in `systemd/network/*.link` so they survive
probe order. NetworkManager gets `no-auto-default=*` — every connection is a
declared keyfile — and leaves `cni0`/`flannel*` alone. Keyfiles must be `0600`
or they are ignored.

Both APs are `method=shared`, so each runs its own dnsmasq and NAT. `upstream`
sets `ignore-auto-dns` with `dns=127.0.0.1`, so the upstream router's resolver
never displaces the local one.

## DNS

```
client → dnsmasq (per-AP, no cache) → coredns :53 → 127.0.0.1:5335, then 1.1.1.1, 8.8.8.8
```

dnsmasq forwards only; CoreDNS is the single cache (resolved's is off). CoreDNS
binds loopback, so k3s is pointed at 172.19.150.1 instead — a pod's loopback is
its own.

127.0.0.1:5335 is Technitium, running as a cluster workload with a `hostPort`
bound to loopback ([technitium](technitium.md)). Health checks mean the host
keeps resolving through the public forwarders whenever that pod is down, so
losing the cluster costs ad blocking, not DNS.

That the resolver is downstream of the resolver chain is also why its own
forwarders must be IP literals — a hostname there resolves through this chain
back into itself.

## Tailscale

Host service, not a workload, so remote access survives the cluster being down.
Forwarding is enabled in `sysctl.d` for exit-node and subnet-router use. Which
devices may connect is a Tailscale ACL question; the zone only decides what the
host answers.
