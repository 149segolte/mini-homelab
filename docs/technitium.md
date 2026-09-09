# Technitium settings

Technitium reads `DNS_SERVER_*` only when `/etc/dns` has no config file — first
run. After that the container serves from its own store, on the PVC, and the
env block is inert.

So this page is the source of truth for everything below, and the recovery
procedure after a lost volume. Only the admin password stays declarative: it is
a credential, not a preference, and first-run-only is the right semantics for a
bootstrap password — without it a rebuild has an admin/admin window on a
namespace the home AP can reach.

Rebuild order: restore or recreate the PVC, reach the UI (below), apply §1,
then §2, then §3. §1 first is not stylistic — see the forwarders entry.

## First access

`dns.${DOMAIN}` has no public record and Technitium is what would resolve it,
so on a fresh store the name does not work yet. Port-forward instead — it needs
only the apiserver, which the admin AP allows:

```bash
kubectl -n technitium port-forward svc/technitium 5380:5380
# http://127.0.0.1:5380
```

To make the name work afterwards, add a **zone per hostname** — `dns.${DOMAIN}`
as its own primary zone with an A record at the apex pointing at 172.19.149.1,
and the same for `doh.${DOMAIN}` if you want DoH by name. Record these in §3.

Not a zone for `${DOMAIN}` itself: that makes Technitium authoritative for the
whole domain and shadows every public name under it, `whoami.` and `flux.`
included, until each is recreated by hand.

Reaching those names still depends on where you are. Traefik is only exposed to
the zones that allow http/s — `home` and `tailscale`, not `admin`
([networking](networking.md)). From the admin AP, port-forward stays the way
in. Expect a browser warning until the wildcard issues ([tls](tls.md)).

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

### SSO

OIDC against Authelia ([auth](auth.md)). Like everything `DNS_SERVER_*`, the
env block in the deployment applies on a **fresh store only** — the running
instance is configured in Settings, and these are the values it must hold:

| Setting | Value |
| --- | --- |
| Authority | `https://auth.${DOMAIN}` |
| Client ID / secret | from `authelia/oidc/clients/technitium/{id,secret}` |
| Scopes | `openid,profile,groups` — must match the client's granted scopes |
| Client auth | Technitium sends `client_secret_post`; the Authelia client is registered to match ([auth](auth.md)) |
| Allow signup | on |
| Only for mapped users | on |
| Group map | `admins:Administrators` |

Both signup flags are needed together: allow-signup is the gate, and
only-for-mapped-users narrows it to users carrying a mapped group. With the
gate off the narrowing never runs and login fails with "new user sign up is
disabled".

Technitium's own login stays in place; SSO is an additional button on it.
Groups arrive from userinfo, so no Authelia claims policy is involved.

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
| Query logging | as needed | Settings → Logging — `/var/log/technitium` is an `emptyDir`, so logs do not survive a restart |
| DNSSEC validation | on | Settings → Recursion |

Blocklists re-download on a lost volume, so expect a few minutes of unblocked
queries after a rebuild.

## §3 Zones

**Fill this in as zones are created, not afterwards.** Zones cannot be set by
env var at all, only through the UI or API, so this section is the only backup
they have.

### Never a zone for `${DOMAIN}` itself

It makes Technitium authoritative for everything under the domain, so any name
without a record returns NXDOMAIN instead of falling through to the forwarders.
That breaks every Cloudflare-hosted service, and it breaks
`_acme-challenge.${DOMAIN}` — so certificate renewal fails silently about sixty
days later.

### One primary zone per internal name

A zone named for the full hostname, one A record at its apex. Longest match
wins, so everything else under `${DOMAIN}` still forwards out. The failure mode
inverts usefully: a missing zone resolves publicly and takes the tunnel — slower,
but working.

Defaults apply unless stated: TTL 3600, no expiry. No reverse DNS anywhere —
every service shares 172.19.149.1, so a PTR could only name one of them.

| Zone | Type | Record | Value |
| --- | --- | --- | --- |
| `dns.${DOMAIN}` | primary | `@` A | 172.19.149.1 |
| `auth.${DOMAIN}` | primary | `@` A | 172.19.149.1 |
| `whoami.${DOMAIN}` | primary | `@` A | 172.19.149.1 |
| `flux.${DOMAIN}` | primary | `@` A | 172.19.149.1 |

Always 172.19.149.1. Home-AP clients reach it directly; tailnet clients reach
it over the advertised `/32` and resolve through split DNS pointed at the same
address ([networking](networking.md)). Admin-AP clients cannot reach http/s at
all, so they stay on port-forward.

Anything hosted at Cloudflare rather than on the Pi gets no zone here.

Add zones one at a time, after certificates work ([tls](tls.md)) — while they
do not exist, a browser failure is unambiguously a cert problem.

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
