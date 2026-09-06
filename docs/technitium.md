# Technitium settings

Technitium reads `DNS_SERVER_*` only when `/etc/dns` has no config file — first
run. After that the container serves from its own store, on the PVC, and the
env block is inert.

So this page is the source of truth for everything below, and the recovery
procedure after a lost volume. Only the admin password stays declarative: it is
a credential, not a preference, and first-run-only is the right semantics for a
bootstrap password — without it a rebuild has an admin/admin window on a
namespace the home AP can reach.

Rebuild order: restore or recreate the PVC, log in at `dns.${DOMAIN}`, apply
§1, then §2, then §3. §1 first is not stylistic — see the forwarders entry.

## §1 Hard constraints

Get one of these wrong and DNS for the whole box is affected. Several fail
silently.

| Setting | Value | Why |
| --- | --- | --- |
| Forwarders | `https://1.1.1.1/dns-query`, `https://8.8.8.8/dns-query` | IP literals only. A hostname has to be resolved first, and the pod resolves via 172.19.150.1 → dnsmasq → host CoreDNS → 127.0.0.1:5335 → itself. Both certs carry IP SANs, so TLS still validates. The `https://cloudflare-dns.com/dns-query (1.1.1.1)` form pins a bootstrap IP if you prefer the hostname |
| DNS service port | `53` | the `hostPort` mapping targets it |
| Web service port | `5380` | the Service and Ingress target it |
| Recursion | `UseSpecifiedNetworkACL`, ACL `10.42.0.0/16` | not "private networks" — they look equivalent, but everything arriving through Traefik has a pod source address |
| Reverse proxy addresses | `10.42.0.0/16` | real client IPs in the logs |
| DoT / DoQ | off | nothing routes to 853 |
| DoH | plain HTTP on 80 in-pod | Traefik terminates TLS |

Changing the DNS port is the nastiest of these: DNS keeps working, because host
CoreDNS falls through to 1.1.1.1, and only ad blocking stops.

Two standing rules that are not settings:

- **No public DNS records for `dns.` or `doh.`.** The recursion ACL is
  `10.42.0.0/16` and every request through Traefik has a pod source address, so
  a reachable `doh.` host is an open resolver. Before ever exposing it, verify
  for yourself whether reverse-proxy addresses also feed the recursion ACL.
- **Do not change the admin password in the UI.** The env var is first-run
  only, so a UI change wins permanently and Infisical goes stale.

## §2 Preferences

Safe to change; wrong values look odd rather than break resolution. Tab names
are hints — they track Technitium's UI, not this repo, and may have moved.

| Setting | Value | Where |
| --- | --- | --- |
| Server domain | `dns.${DOMAIN}` | Settings → General |
| Forwarder protocol | `Https` | Settings → Proxy & Forwarders |
| Blocking | enabled | Settings → Blocking |
| Blocklist URLs | `https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts` | Settings → Blocking |
| Blocklist refresh | daily | Settings → Blocking |
| In-memory stats | on | Settings → Logging |
| Query logging | as needed | Settings → Logging |
| DNSSEC validation | on | Settings → Recursion |

Blocklists re-download on a lost volume, so expect a few minutes of unblocked
queries after a rebuild.

## §3 Zones

Deliberately empty — **fill this in as records are created, not afterwards.**
Zones cannot be set by env var at all, only through the UI or API, so this
section is the only backup they have.

Split-horizon rewrites and reverse DNS land here. Both need cert-manager and
the wildcard first.

## Verify

```bash
sudo ss -lntup | grep 5335
```

Expect `127.0.0.1` only. On `0.0.0.0` the CNI portmap plugin ignored `hostIP`
and Technitium is reachable from both APs — add `5335` blocks to the `home` and
`admin` zones ([networking](networking.md)) or drop back to loopback-only.

```bash
dig @127.0.0.1 -p 5335 example.com +short     # Technitium directly
dig @127.0.0.1 example.com +short             # via host CoreDNS
dig @127.0.0.1 doubleclick.net +short         # 0.0.0.0 once lists load
```

The third is the proof that CoreDNS's health check promoted 5335 to first place
rather than silently sticking with 1.1.1.1. Give the blocklist a few minutes.
