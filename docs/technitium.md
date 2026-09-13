# Technitium

Technitium reads its `DNS_SERVER_*` environment variables only when `/etc/dns`
holds no configuration file, which is the first run. After that it serves from
its own store on the PVC and the environment block has no effect.

This page records the settings that store holds, and is therefore the recovery
procedure after a lost volume. The admin password is the one exception that
stays declarative: it is a credential rather than a preference, and first-run
semantics are correct for a bootstrap password. Without it a rebuild leaves an
admin/admin window on a namespace the home AP can reach.

To rebuild: restore or recreate the PVC, reach the UI, then apply sections 1, 2
and 3 in that order. Section 1 comes first because of the forwarders entry.

## First access

`dns.${DOMAIN}` has no public record, and Technitium is what would resolve it,
so on a fresh store the name does not work yet. Port-forward instead, which
needs only the apiserver:

```bash
kubectl -n technitium port-forward svc/technitium 5380:5380
# http://127.0.0.1:5380
```

To make the name work afterwards, create a zone per hostname: `dns.${DOMAIN}`
as its own primary zone with an A record at the apex pointing at 172.19.149.1,
and the same for `doh.${DOMAIN}` if DoH by name is wanted. Record them in section 3.

Do not create a zone for `${DOMAIN}` itself. That makes Technitium
authoritative for the whole domain and shadows every public name under it,
including `whoami.` and `flux.`, until each is recreated by hand.

Which clients can reach those names depends on the zone they are in. Traefik is
exposed only to `home` and `tailscale`, not to `admin`
([Host](host.md#networking)), so port-forward remains the route from the admin
AP. Expect a browser warning until the wildcard certificate issues
([Services](services.md#tls)).

## 1. Hard constraints

An incorrect value here affects DNS for the whole machine, and several of these
fail silently.

| Setting | Value | Reason |
| --- | --- | --- |
| Forwarders | `https://1.1.1.1/dns-query`, `https://8.8.8.8/dns-query` | IP literals only. A hostname would have to be resolved first, and the pod resolves via 172.19.150.1 -> dnsmasq -> CoreDNS -> 127.0.0.1:5335, which is Technitium itself. Both certificates carry IP SANs, so TLS still validates. The `https://cloudflare-dns.com/dns-query (1.1.1.1)` form pins a bootstrap IP if a hostname is preferred |
| DNS service port | `53` | The `hostPort` mapping targets it |
| Web service port | `5380` | The Service and Ingress target it |
| Recursion | `UseSpecifiedNetworkACL`, ACL `10.42.0.0/16` | Not "private networks". The two look equivalent, but everything arriving through Traefik carries a pod source address |
| Reverse proxy addresses | `10.42.0.0/16` | Real client addresses in the logs |
| DoT / DoQ | Off | Nothing routes to 853 |
| DoH | Plain HTTP on 80 in-pod | Traefik terminates TLS |

Changing the DNS service port is the least visible of these failures. DNS keeps
working, because CoreDNS falls through to 1.1.1.1, and only ad blocking stops.

Two rules apply that are not settings:

- `dns.` and `doh.` get no public DNS record. The recursion ACL is
  `10.42.0.0/16` and every request through Traefik has a pod source address, so
  a reachable `doh.` host is an open resolver. Before exposing it, confirm
  whether reverse proxy addresses also feed the recursion ACL.
- The admin password is not changed in the UI. The environment variable applies
  on first run only, so a UI change wins permanently and the Infisical value
  goes stale.

### SSO

Technitium authenticates against Authelia over OIDC
([Services](services.md#oidc)). Like every `DNS_SERVER_*` setting, the
environment block applies to a fresh store only; the running instance is
configured in Settings and must hold these values:

| Setting | Value |
| --- | --- |
| Authority | `https://auth.${DOMAIN}` |
| Client ID and secret | `authelia/oidc/clients/technitium/{id,secret}` |
| Scopes | `openid,profile,groups`, matching the client's granted scopes |
| Client authentication | `client_secret_post`, which the Authelia client is registered to expect |
| Allow signup | On |
| Only for mapped users | On |
| Group map | `admins:Administrators` |

Both signup flags are needed together. Allow-signup is the gate and
only-for-mapped-users narrows it to users carrying a mapped group. With the
gate off the narrowing never runs, and login fails with "new user sign up is
disabled".

Technitium's own login remains available; SSO appears as an additional button.
Groups arrive from the userinfo endpoint, so no Authelia claims policy is
involved.

## 2. Preferences

These are safe to change. A wrong value looks odd rather than breaking
resolution. Tab names track Technitium's UI rather than this repository and may
have moved.

| Setting | Value | Where |
| --- | --- | --- |
| Server domain | `dns.${DOMAIN}` | Settings > General |
| Forwarder protocol | `Https` | Settings > Proxy & Forwarders |
| Blocking | Enabled | Settings > Blocking |
| Blocklist URLs | `https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts` | Settings > Blocking |
| Blocklist refresh | Daily | Settings > Blocking |
| In-memory stats | On | Settings > Logging |
| Query logging | As needed | Settings > Logging. `/var/log/technitium` is an `emptyDir`, so logs do not survive a restart |
| DNSSEC validation | On | Settings > Recursion |

Blocklists download again on a lost volume, so expect a few minutes of
unblocked queries after a rebuild.

## 3. Zones

Zones cannot be set by environment variable, only through the UI or the API, so
this section is their only backup. Record each zone as it is created.

Each internal name gets its own primary zone with a single A record at the
apex. Longest match wins, so every other name under `${DOMAIN}` still forwards
out. The failure mode inverts usefully: a missing zone resolves publicly and
takes the tunnel, which is slower but works.

Defaults apply unless stated: TTL 3600, no expiry. There is no reverse DNS,
because every service shares 172.19.149.1 and a PTR could name only one of
them.

| Zone | Type | Record | Value |
| --- | --- | --- | --- |
| `dns.${DOMAIN}` | Primary | `@` A | 172.19.149.1 |
| `auth.${DOMAIN}` | Primary | `@` A | 172.19.149.1 |
| `whoami.${DOMAIN}` | Primary | `@` A | 172.19.149.1 |
| `flux.${DOMAIN}` | Primary | `@` A | 172.19.149.1 |
| `traefik.${DOMAIN}` | Primary | `@` A | 172.19.149.1 |
| `mini.${DOMAIN}` | Primary | `@` A | 172.19.149.1 |

The address is always 172.19.149.1. Home AP clients reach it directly, and
tailnet clients reach it over the advertised `/32` and resolve through split
DNS pointed at the same address ([Host](host.md#tailscale)). Admin AP clients
cannot reach http/s at all and stay on port-forward.

Anything hosted at Cloudflare rather than on the Pi gets no zone here.

A zone for `${DOMAIN}` itself makes Technitium authoritative for everything
under the domain, so any name without a record returns NXDOMAIN instead of
falling through to the forwarders. That breaks every Cloudflare-hosted service,
and it breaks `_acme-challenge.${DOMAIN}`, so certificate renewal fails
silently about sixty days later.

Add zones one at a time, and only after certificates work
([Services](services.md#tls)). While a zone does not exist, a browser failure
is unambiguously a certificate problem.

## Verifying

```bash
sudo ss -lntup | grep 5335
```

The result should show `127.0.0.1` only. `0.0.0.0` means the CNI portmap plugin
ignored `hostIP` and Technitium is reachable from both access points; block
5335 in the `home` and `admin` zones ([Host](host.md#networking)) or return to
loopback only.

```bash
dig @127.0.0.1 -p 5335 example.com +short     # Technitium directly
dig @127.0.0.1 example.com +short             # through CoreDNS
dig @127.0.0.1 doubleclick.net +short         # 0.0.0.0 once blocklists load
```

The third command confirms that CoreDNS's health check promoted 5335 ahead of
1.1.1.1 rather than silently staying with the public forwarder. Allow a few
minutes for the blocklist.
