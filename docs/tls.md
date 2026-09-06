# TLS

One wildcard for `${DOMAIN}` and `*.${DOMAIN}`, issued by cert-manager over
DNS-01, served as Traefik's **default certificate**.

Traefik reads `spec.tls[].secretName` from the Ingress's own namespace, so a
wildcard in `kube-system` reaches nothing else without replicating the secret
into every namespace. Setting it as the default instead means no Ingress needs
a `tls:` block at all — which is what keeps the workload manifests clean.

Only one `TLSStore` may be named `default` cluster-wide, so this is a one-shot
decision. A per-namespace certificate later needs an explicit `tls:` block on
that Ingress, which then wins over the default.

Traefik falls back to a generated self-signed certificate while `wildcard-tls`
is absent, so there is no hard ordering between it and cert-manager.

## Shape

|               |                                                          |
| ------------- | -------------------------------------------------------- |
| `controller/` | the Helm release, CRDs included                          |
| `issuers/`    | ClusterIssuer, the Cloudflare token, and the Certificate |

`issuers` waits on both cert-manager's CRDs and ESO's, with `timeout: 10m`
because `wait: true` blocks on the Certificate going Ready and a first DNS-01
issuance takes minutes.

The Certificate lives in `kube-system`, where the bundled Traefik and its
TLSStore are. It sits under `cert-manager/issuers/` rather than beside the
Traefik files only for ordering — the Traefik directory is applied by the
`infrastructure` Kustomization directly, with no CRD gate in front of it.

## DNS-01 and the local resolver

cert-manager self-checks propagation before asking Let's Encrypt to validate,
and by default resolves through the pod's resolver — here dnsmasq, host CoreDNS
with its 30s cache, then Technitium with its own. Negative caching of
`_acme-challenge` makes that check stall or flap, so the controller runs with:

```
--dns01-recursive-nameservers-only
--dns01-recursive-nameservers=1.1.1.1:53,8.8.8.8:53
```

That takes the whole local DNS stack out of the issuance path.

## Prerequisites

- A Cloudflare API token in Infisical as `CLOUDFLARE_API_TOKEN`, scoped to the
  one zone: **Zone → DNS → Edit** and **Zone → Zone → Read**.
- `ACME_EMAIL` in `clusters/rpi4/cluster-vars.yaml`. The ClusterIssuer uses
  `${ACME_EMAIL:?...}`, so reconciliation fails loudly rather than registering
  an ACME account with an empty address.

## Bringing it up

The ClusterIssuer ships pointing at Let's Encrypt **staging**. Production
allows 5 duplicate certificates per week and a DNS-01 misconfiguration burns
them fast.

1. Commit, then validate on staging in this order.

    ```bash
    # token synced and non-empty, without printing it
    kubectl -n cert-manager get externalsecret cloudflare-api-token
    kubectl -n cert-manager get secret cloudflare-api-token \
      -o jsonpath='{.data.token}' | base64 -d | wc -c

    # ACME account registered: email and account key, independent of DNS.
    # A literal ${ACME_EMAIL...} here is substitution failing silently.
    kubectl get clusterissuer letsencrypt -o jsonpath='{.spec.acme.email}{"\n"}'
    kubectl describe clusterissuer letsencrypt | tail -20

    # the challenge chain
    kubectl -n kube-system describe certificate wildcard
    kubectl -n kube-system get certificaterequest,order,challenge

    # the certificate itself
    kubectl -n kube-system get secret wildcard-tls -o jsonpath='{.data.tls\.crt}' \
      | base64 -d | openssl x509 -noout -issuer -dates -ext subjectAltName

    # and that Traefik serves it — Ready only means the Secret exists
    openssl s_client -connect 172.19.149.1:443 -servername whoami.${DOMAIN} \
      </dev/null 2>/dev/null | openssl x509 -noout -issuer
    ```

    `TRAEFIK DEFAULT CERT` on that last one means the wildcard is not being
    served and production would change nothing.

    ```bash
    kubectl -n kube-system describe challenge
    NS=$(dig +short NS ${DOMAIN} @1.1.1.1 | head -1)
    dig +short TXT _acme-challenge.${DOMAIN} @"$NS"
    ```

2. Swap `server` in `cluster-issuer.yaml` to
   `https://acme-v02.api.letsencrypt.org/directory`, then clear both secrets.
   The account key is per-ACME-server, and leaving the staging one behind
   causes confusing re-registration:

    ```bash
    kubectl -n kube-system delete secret wildcard-tls
    kubectl -n cert-manager delete secret letsencrypt-account-key
    ```

3. Verify from a home-AP or tailnet client — expect a Let's Encrypt chain
   rather than the self-signed fallback:

    ```bash
    curl -v https://whoami.${DOMAIN} 2>&1 | grep -i issuer
    ```

4. Only then add Technitium zones ([technitium](technitium.md)), one at a time.
   While they do not exist every name still resolves publicly, so a browser
   failure is a cert problem and nothing else. Once the zones are in, the two
   failure modes look identical.
