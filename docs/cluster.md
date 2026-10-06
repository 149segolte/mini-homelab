# Cluster

Flux reconciles this repository into the cluster. Removing a manifest removes
the object it created.

The cluster runs
[flux-operator](https://github.com/controlplaneio-fluxcd/flux-operator) rather
than a `flux bootstrap` checkout. `clusters/rpi4/flux-instance.yaml` declares
the distribution, the components and the sync source. The operator itself is a
`HelmRelease` under `infrastructure/flux-operator/`, so Flux upgrades Flux. The
image automation controllers are not installed, so image tags are pinned in git
and changed by commit.

## Tiers

`clusters/rpi4/` holds one Kustomization per tier, chained with `dependsOn` and
`wait: true`:

```
initialization --> infrastructure --> apps
```

`initialization` holds what the later tiers build on: Traefik's
`HelmChartConfig`, which declares the entrypoints Ingresses and IngressRoutes
attach to, and External Secrets Operator, whose CRDs and `ClusterSecretStore`
every `ExternalSecret` needs. Because the tier waits on its nested
Kustomizations, `infrastructure` starts only once the secret store is ready.

Admission control is not a tier. It lives in the apiserver and is in force
before Flux applies anything ([Admission control](#admission-control)).

Every tier prunes and substitutes variables from the `cluster-vars`
ConfigMap. Substitution reaches only the manifests a Kustomization renders
itself, so a nested Kustomization that uses a variable needs its own
`postBuild`. It does reach generated ConfigMap content, which is how cloudflared
and Glance receive the domain.

### cluster-vars

`cluster-vars` belongs to the cluster rather than to git, because
`EXTERNAL_IP` is expected to change on a live one. It carries that plus
`DOMAIN`, `ACME_EMAIL` and `LOCATION`. `initialization/bootstrap.yaml` holds
the reference copy and is deliberately absent from
`initialization/kustomization.yaml`, so Flux renders the directory without ever
adopting the file, and an edit in the cluster survives reconciliation.

`flux-instance.yaml` patches kustomize-controller with
`--watch-configs-label-selector=owner!=helm`, so editing it reconciles the
tiers instead of waiting out the interval. The selector skips Helm storage
Secrets.

A component without internal ordering is a plain directory of manifests that
the tier renders directly, so it inherits the tier's substitution and health
checks and needs no Flux Kustomization of its own. authelia, cloudflared and
flux-operator are laid out this way.

A component with internal ordering repeats the pattern one level down: a
directory of manifests, a Flux Kustomization pointing at it, and a
`sources.yaml` for whatever it pulls from. external-secrets and external-dns
both order `crds -> operator -> crs` this way. Tier order already places
`external-secrets-crs` ahead of every `infrastructure` component; technitium
and external-dns still declare `dependsOn: external-secrets-crs` as well, which
is redundant but harmless. Each component declares its own namespace as a manifest rather than
relying on `targetNamespace`, which keeps labels and deletion declarative.

## Bootstrapping

These steps run once, on a fresh install. k3s is enabled in the image, so the
node is already up on first boot.

```bash
# kubeconfig. k3s writes it root-only; the apiserver answers on the admin AP
ssh <admin>@172.19.149.1 sudo cat /etc/rancher/k3s/k3s.yaml \
  | sed 's#127.0.0.1#172.19.149.1#' > ~/.kube/mini-homelab
export KUBECONFIG=~/.kube/mini-homelab

# flux-operator by hand: the FluxInstance CRD must exist before the manifest
# using it. Flux adopts the release, as name and namespace match
helm install flux-operator oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator \
  --namespace flux-system --create-namespace --version 0.61.x

# pull secret. flux-instance.yaml sets provider: github, so App credentials
flux create secret githubapp flux-system \
  --app-id=<id> --app-installation-id=<id> --app-private-key=<key>.pem

# cluster-vars, before handover, or every tier fails variable substitution
kubectl apply -f initialization/bootstrap.yaml

# hand over. Flux then reconciles clusters/rpi4, including flux-instance.yaml
kubectl apply -f clusters/rpi4/flux-instance.yaml

# Infisical credentials, or no ExternalSecret ever becomes ready
kubectl create namespace external-secrets
kubectl create secret generic infisical-universal-auth --namespace external-secrets \
  --from-literal=clientId=<id> --from-literal=clientSecret=<secret>
```

The tiers then settle in order.

## Secrets

No secret is committed. Host secrets are overlaid at install time
([Host](host.md#install-time-overlay)). Cluster secrets come from Infisical
through External Secrets Operator, which installs in three ordered steps:

| Step       | Source                        | Notes                                                    |
| ---------- | ----------------------------- | -------------------------------------------------------- |
| `crds`     | Upstream git at tag `v2.11.0` | CRDs only; `ignore` rules fetch just `config/crds/bases` |
| `operator` | Helm chart `2.11.0`           | `installCRDs: false`                                     |
| `crs`      | This repository               | The `ClusterSecretStore`                                 |

Taking the CRDs from git with `wait: true` means the operator never starts
against a half-established API. The chart version and the CRD tag are pinned to
the same release and have to be bumped together.

`installCRDs` is the chart's real toggle. `crds.create: false` looks plausible,
is not rejected, and silently installs a second copy of the CRDs.

One `ClusterSecretStore` named `infisical` uses universal auth against the
project's `prod` environment. Workloads declare an `ExternalSecret` in their own
namespace with `creationPolicy: Owner`, so removing the manifest removes the
Secret. A rotation reaches the cluster within `refreshInterval`, but the
consuming pod still has to restart to pick it up.

### Config files built from secrets

When a component needs a configuration _file_ that contains a secret, the
ExternalSecret builds the file. `spec.target.template` with `engineVersion: v2`
keeps the structure in git and interpolates one single-line Infisical value per
secret. Authelia and the Flux UI both use this ([Services](services.md#oidc)).

Three constraints apply:

- Sprig is available, so a multi-line value such as a PEM goes into a block
  scalar with `nindent`. A quoted scalar folds the newlines into spaces, which
  parses cleanly and produces an unusable key.
- `template.data` replaces the Secret's data entirely, so pure passthrough keys
  have to be listed as well. A single bad reference drops every key.
- Flux runs envsubst over these manifests before ESO sees them, so `${...}`
  belongs to Flux and `{{ ... }}` to ESO. A `regexp` rewrite target needs
  `${1}`, which Flux then fails on as an unset variable. A `transform` rewrite
  contains no `$` and avoids the collision.

## Admission control

Pod security is enforced entirely inside the apiserver, through
`admission-control-config-file` ([Host](host.md#k3s)). Two in-tree plugins
share the file:

| Plugin                    | Role                                        |
| ------------------------- | ------------------------------------------- |
| `MutatingAdmissionPolicy` | Fills in the `restricted` boilerplate       |
| `PodSecurity`             | Enforces `restricted`, `kube-system` exempt |

Neither involves a webhook or the network, so neither can fail closed during an
outage, and no Flux tier has to come up before the workloads it governs. The
default is set at the apiserver rather than through namespace labels, because
it has to apply to namespaces nobody has labelled. No namespace carries PSA
labels of its own.

### Mutation

`pod-security-defaults.static.k8s.io` is a static policy, loaded from
`/etc/rancher/k3s/admission/mutating-policies/` rather than from the API. It
ships with the image, so it changes by image upgrade, not by Flux. On pod
`CREATE` outside `kube-system` it adds only what is missing:

- `seccompProfile: RuntimeDefault` at pod level, which covers every container.
- `allowPrivilegeEscalation: false` on each container and init container.
- `capabilities.drop: [ALL]` where a container declares no `capabilities` at
  all. The list is atomic, so a container that sets `capabilities` of its own
  is left alone and has to drop `ALL` itself.

Mutating admission runs before validating admission, so a workload that merely
omits the boilerplate is corrected rather than rejected. `runAsNonRoot` is not
defaulted; a workload has to declare it, which keeps the choice of a non-root
image with the workload.
