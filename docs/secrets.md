# Secrets

Nothing is committed. Host secrets are overlaid at install time
([overlay](overlay.md)); cluster secrets come from Infisical via External
Secrets Operator.

`infrastructure/external-secrets/` installs in three ordered steps:

| | Source | |
| --- | --- | --- |
| `crds` | upstream git at tag `v2.10.0` | CRDs only; `ignore` rules fetch just `config/crds/bases` |
| `operator` | Helm chart `2.10.0` | `installCRDs: false` |
| `crs` | this repo | the `ClusterSecretStore` |

CRDs come from git so their lifecycle is explicit, and `wait: true` means the
operator never starts against a half-established API. Chart version and CRD tag
are pinned to the same release and must be bumped together.

`installCRDs` is the chart's real master toggle. `crds.create: false` looks
plausible and does nothing — the schema does not reject unknown keys, so a
wrong name silently installs a second copy of the CRDs.

## Infisical

One `ClusterSecretStore` named `infisical`, universal auth, scoped to the
project's `prod` environment, recursive from `/`.

Its credentials are `external-secrets/infisical-universal-auth`. That Secret is
the one cluster credential created out of band — it is what everything else is
fetched with.

Workloads declare an `ExternalSecret` in their own namespace with
`creationPolicy: Owner`, so deleting the manifest deletes the Secret. A
rotation reaches the cluster within `refreshInterval`; the consuming pod still
has to restart to pick it up.
