# Technitium

Technitium reads its `DNS_SERVER_*` environment variables only on the first
run, when `/etc/dns` holds no configuration file. After that it serves from its
own store on the PVC and the environment block has no effect. The settings
below live in that store.

After a lost volume: restore or recreate the PVC, reach the UI, then apply
sections 1, 2 and 3 in order. Section 1 comes first because of the forwarders
entry.

## First access

`dns.${DOMAIN}` has no public record, and Technitium is what would resolve it,
so on a fresh store the name does not work yet. Port-forward instead, which
needs only the apiserver:

```bash
kubectl -n technitium port-forward svc/technitium 5380:5380
# http://127.0.0.1:5380
```

Create the zone in section 3 and the TSIG key in section 1. external-dns writes
the records once both exist, and the UI becomes reachable by name. Expect a
browser warning until the wildcard certificate issues
([Services](services.md#tls)).

## 1. Hard constraints

An incorrect value here affects DNS for the whole machine, and several of these
fail silently.

- **Forwarders**: `https://1.1.1.1/dns-query` and `https://8.8.8.8/dns-query`.
  IP literals only. A hostname would have to be resolved first, and the pod
  resolves via 172.19.150.1 -> dnsmasq -> CoreDNS -> 10.43.0.53, which is
  Technitium itself. Both certificates carry IP SANs, so TLS still validates.
- **DNS service port**: `53`, which the Service targets. This is the least
  visible of these failures. DNS keeps working, because CoreDNS falls through
  to 1.1.1.1, and only ad blocking stops.
- **Web service port**: `5380`, which the Service and Ingress target.
- **Recursion**: `UseSpecifiedNetworkACL` with ACL `10.42.0.0/16`, not "private
  networks". The two look equivalent, but everything arriving through Traefik
  carries a pod source address.
- **Reverse proxy addresses**: `10.42.0.0/16`, for real client addresses in the
  logs.
- **DoT / DoQ**: off. Nothing routes to 853.
- **DoH**: plain HTTP on 80 in-pod. Traefik terminates TLS.

Two rules apply that are not settings. `dns.` and `doh.` get no public DNS
record: the recursion ACL is `10.42.0.0/16` and every request through Traefik
has a pod source address, so a reachable `doh.` host is an open resolver. And
the admin password is never changed in the UI. It stays declarative because it
is a credential rather than a preference, and the environment variable applies
on first run only, so a UI change wins permanently and the Infisical value goes
stale. Without it a rebuild leaves an admin/admin window on a namespace the
home AP can reach.

### TSIG

external-dns writes the zone over RFC 2136. The key and its grants live only in
this store, so without them a restored zone stays empty.

Settings > TSIG holds one key named `external-dns-tsig`, algorithm HMAC-SHA256,
whose secret matches `external-dns/tsig-secret` in Infisical. Zone Options on
`${DOMAIN}` then holds:

- **Dynamic Updates**: `Allow`, with a Security Policy row for the key, domain
  `*.${DOMAIN}`, record types `A, CNAME, TXT`.
- **Zone Transfer**: the key listed under Zone Transfer TSIG Key Names.
  `--rfc2136-axfr` needs it to read existing records back.

The domain must be the wildcard. external-dns writes names under the zone, not
at the apex.

### SSO

Technitium authenticates against Authelia over OIDC
([Services](services.md#oidc)). Like every `DNS_SERVER_*` setting, the
environment block applies to a fresh store only. The running instance is
configured in Settings and must hold these values:

| Setting               | Value                                                                   |
| --------------------- | ----------------------------------------------------------------------- |
| Authority             | `https://auth.${DOMAIN}`                                                |
| Client ID and secret  | `authelia/oidc/clients/technitium/{id,secret}`                          |
| Scopes                | `openid,profile,groups`, matching the client's granted scopes           |
| Client authentication | `client_secret_post`, which the Authelia client is registered to expect |
| Allow signup          | On                                                                      |
| Only for mapped users | On                                                                      |
| Group map             | `admins:Administrators`                                                 |

Both signup flags are needed together. Allow-signup is the gate, and
only-for-mapped-users narrows it to users carrying a mapped group. With the
gate off the narrowing never runs, and login fails with "new user sign up is
disabled".

Technitium's own login remains available. Groups arrive from the userinfo
endpoint, so no Authelia claims policy is involved.

## 2. Preferences

These are safe to change. A wrong value looks odd rather than breaking
resolution. Tab names track Technitium's UI and may have moved.

| Setting            | Value           | Where                         |
| ------------------ | --------------- | ----------------------------- |
| Server domain      | `dns.${DOMAIN}` | Settings > General            |
| Forwarder protocol | `Https`         | Settings > Proxy & Forwarders |
| Blocking           | Enabled         | Settings > Blocking           |
| Blocklist refresh  | Daily           | Settings > Blocking           |
| In-memory stats    | On              | Settings > Logging            |
| Query logging      | As needed       | Settings > Logging            |
| DNSSEC validation  | On              | Settings > Recursion          |

The blocklist is HaGeZi's Multi PRO list at
`https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/pro.txt`.
`/var/log/technitium` is an `emptyDir`, so query logs do not survive a restart.
Blocklists download again on a lost volume, so expect a few minutes of
unblocked queries after a rebuild.

## 3. Zones

Zones cannot be set by environment variable, only through the UI or the API.
The records inside them come from the cluster
([Services](services.md#hostnames)).

One zone, `${DOMAIN}`, of type **Conditional Forwarder**, with `this-server` as
the forwarder. Records in it resolve locally, and every other name under the
domain forwards out as it would without the zone. That keeps the
Cloudflare-hosted names working, and `_acme-challenge.${DOMAIN}` with them.

A **primary** zone for `${DOMAIN}` does the opposite. Technitium becomes
authoritative for the whole domain, any name without a record returns NXDOMAIN,
and certificate renewal fails silently about sixty days later.

external-dns writes one CNAME per service name pointing at `node.${DOMAIN}`,
the single A record for 172.19.149.1, plus a `zzz-external-dns-` TXT beside
each for ownership. A missing record resolves publicly and takes the tunnel,
which is slower but works. `files.` is the exception, because the tunnel
carries HTTP alone and SFTP on 3922 then has no route at all.

Home AP clients reach 172.19.149.1 directly, admin AP clients through the Pi,
and tailnet clients over the advertised `/32` with split DNS pointed at the
same address ([Host](host.md#tailscale)). There is no reverse DNS, because
every service shares the address.

## Verifying

```bash
dig @10.43.0.53 example.com +short            # Technitium directly
dig @127.0.0.1 example.com +short             # through CoreDNS
dig @127.0.0.1 doubleclick.net +short         # 0.0.0.0 once blocklists load
dig @127.0.0.1 whoami.${DOMAIN} +short        # CNAME to node., then its address
```

The third confirms that CoreDNS reaches Technitium rather than staying with the
public forwarders. Allow a few minutes for the blocklist. The fourth is empty
when the zone exists but the TSIG grants do not.
