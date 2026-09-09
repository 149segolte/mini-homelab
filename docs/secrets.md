# Secrets

Nothing is committed. Host secrets are overlaid at install time
([overlay](overlay.md)); cluster secrets come from Infisical via External
Secrets Operator.

`infrastructure/external-secrets/` installs in three ordered steps:

|            | Source                        |                                                          |
| ---------- | ----------------------------- | -------------------------------------------------------- |
| `crds`     | upstream git at tag `v2.10.0` | CRDs only; `ignore` rules fetch just `config/crds/bases` |
| `operator` | Helm chart `2.10.0`           | `installCRDs: false`                                     |
| `crs`      | this repo                     | the `ClusterSecretStore`                                 |

CRDs come from git and `wait: true` means the operator never starts against a
half-established API — ESO's own [Flux recipe](https://external-secrets.io/latest/examples/gitops-using-fluxcd/),
working around a race there rather than a pattern the rest of the repo follows. Chart version and CRD tag
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

## Composing config from secrets

Where a component wants a config _file_ that happens to contain a secret, the
ExternalSecret builds it: `spec.target.template` with `engineVersion: v2` keeps
the structure in git and interpolates one single-line Infisical value per
secret. Used by Authelia's `oidc.yml` and users database, and by the Flux UI's
`config.yaml` ([auth](auth.md)).

Sprig is available, so a multi-line value such as a PEM can be written into a
block scalar with `nindent`. It must not go into a quoted scalar — YAML folds
the newlines into spaces, which parses cleanly and yields something unusable.

`template.data` replaces the Secret's data wholesale, so keys that are pure
passthrough have to be listed there too, and a single bad reference drops every
key.

Flux runs envsubst over these manifests first, so `${...}` belongs to Flux and
`{{ ... }}` to ESO. A `regexp` rewrite target needs `${1}`, which Flux then
fails on as an unset variable — escapable as `$${1}`, but a `transform` rewrite
avoids the collision entirely and is preferred.
