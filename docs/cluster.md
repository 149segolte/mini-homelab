# Cluster

Flux reconciles this repository into the cluster. Removing a manifest removes
the object it created, in the same way that dropping a package from the
Containerfile removes it from the host.

The cluster runs
[flux-operator](https://github.com/controlplaneio-fluxcd/flux-operator) rather
than a `flux bootstrap` checkout. `clusters/rpi4/flux-instance.yaml` declares
the distribution, the components and the sync source. The operator itself is a
`HelmRelease` under `infrastructure/flux-operator/`, so Flux upgrades Flux. The
image automation controllers are not installed; image tags are pinned in git
and changed by commit.

## Tiers

`clusters/rpi4/` holds one Kustomization per tier, chained with `dependsOn` and
`wait: true`:

```
policy (./infrastructure/kyverno) --> infrastructure --> apps
```

Kyverno is absent from `infrastructure/kustomization.yaml`. Giving it a tier of
its own is what places admission control ahead of everything it governs.

All three tiers prune, and all three substitute variables from the
`cluster-vars` ConfigMap. Substitution reaches only the manifests a
Kustomization renders itself, so a nested Kustomization that uses a variable
needs its own `postBuild`. It does reach generated ConfigMap content, which is
how cloudflared and Glance receive the domain.

A component with internal ordering repeats the pattern one level down: a
directory of manifests, a Flux Kustomization pointing at it, and a
`sources.yaml` for whatever it pulls from. This orders `crds -> operator -> crs`
inside external-secrets without constraining anything else, and lets other
components declare `dependsOn: external-secrets-crs` across component
boundaries. Each component declares its own namespace as a manifest rather than
relying on `targetNamespace`, which keeps labels and deletion declarative.

## Bootstrapping

These steps run once, on a fresh install. k3s is enabled in the image, so the
node is already up on first boot.

Retrieve the kubeconfig. k3s writes it mode `0600`, root only, and the
apiserver is reachable from the admin AP and the tailnet.

```bash
ssh <admin>@172.19.150.1 sudo cat /etc/rancher/k3s/k3s.yaml \
  | sed 's#127.0.0.1#172.19.150.1#' > ~/.kube/mini-homelab
export KUBECONFIG=~/.kube/mini-homelab
```

Install flux-operator by hand. The `FluxInstance` CRD has to exist before the
manifest that uses it. Flux adopts the release afterwards, which works because
the release name and namespace match those in the repository.

```bash
helm install flux-operator oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator \
  --namespace flux-system --create-namespace --version 0.59.x
```

Create the pull secret. `flux-instance.yaml` sets `provider: github`, so the
secret holds GitHub App credentials rather than a token.

```bash
flux create secret githubapp flux-system \
  --app-id=<id> --app-installation-id=<id> --app-private-key=<key>.pem
```

Hand the cluster over. Flux then reconciles `clusters/rpi4`, which includes
`flux-instance.yaml` itself.

```bash
kubectl apply -f clusters/rpi4/flux-instance.yaml
```

Create the Infisical credentials. Without them the `ClusterSecretStore` never
becomes ready, and neither does any `ExternalSecret`. Creating the namespace
early is harmless; Flux adopts it.

```bash
kubectl create namespace external-secrets
kubectl create secret generic infisical-universal-auth --namespace external-secrets \
  --from-literal=clientId=<id> --from-literal=clientSecret=<secret>
```

The tiers then settle in order.

```bash
flux get all -A
kubectl get externalsecrets -A
kubectl get clusterpolicy
```

A stalled `policy` tier presents as unrelated pods refusing to schedule,
because Kyverno fails closed.

## Secrets

No secret is committed. Host secrets are overlaid at install time
([Host](host.md#install-time-overlay)). Cluster secrets come from Infisical
through External Secrets Operator, which installs in three ordered steps:

| Step | Source | Notes |
| --- | --- | --- |
| `crds` | Upstream git at tag `v2.10.0` | CRDs only; `ignore` rules fetch just `config/crds/bases` |
| `operator` | Helm chart `2.10.0` | `installCRDs: false` |
| `crs` | This repository | The `ClusterSecretStore` |

Taking the CRDs from git with `wait: true` means the operator never starts
against a half-established API. This follows ESO's own Flux recipe and works
around a race in it, rather than being a pattern the rest of the repository
repeats. The chart version and the CRD tag are pinned to the same release and
have to be bumped together.

`installCRDs` is the chart's real toggle. `crds.create: false` looks plausible,
is not rejected, and silently installs a second copy of the CRDs.

One `ClusterSecretStore` named `infisical` uses universal auth against the
project's `prod` environment. Workloads declare an `ExternalSecret` in their
own namespace with `creationPolicy: Owner`, so removing the manifest removes
the Secret. A rotation reaches the cluster within `refreshInterval`, but the
consuming pod still has to restart to pick it up.

### Config files built from secrets

When a component needs a configuration *file* that contains a secret, the
ExternalSecret builds the file. `spec.target.template` with `engineVersion: v2`
keeps the structure in git and interpolates one single-line Infisical value per
secret. Authelia and the Flux UI both use this
([Services](services.md#oidc)).

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

Two layers enforce pod security:

| Layer | Where | Enforces | Failure mode |
| --- | --- | --- | --- |
| Pod Security Admission | Apiserver, in-tree | `baseline` | Cannot fail; no webhook and no network |
| Kyverno | Admission webhook | `restricted`, plus mutation | Admission stops for governed namespaces |

PSA is the floor, configured through `admission-control-config-file`
([Host](host.md#k3s)) with `enforce: baseline`, `audit` and `warn` at
`restricted`, and `kube-system` exempt. It is set at the apiserver rather than
through namespace labels because a default has to apply to namespaces nobody
has labelled.

`technitium` is the only namespace that lifts the floor, labelled `privileged`.
Baseline disallows any non-zero `hostPort`, and PSA is all or nothing per
namespace. Kyverno does the enforcing there instead, which is the reason for
having two layers.

Kyverno runs as its own tier, so it is live before anything it governs.
`add-default-securitycontext` mutates pods outside the exempt namespaces and
adds only what is missing; the `+(field)` anchor never overwrites.
`validate-pod-security-restricted` then enforces `restricted`. Mutation runs
first, so a workload that merely omits the boilerplate is corrected rather than
rejected.

`technitium` needs two rules rather than one relaxation. The broad rule
excludes the namespace, and a second rule re-applies `restricted` there without
the `Host Ports` control. The exclusion is required: both rules would otherwise
evaluate and the stricter one would still block the pod. `Host Ports` is a
container-level control, so Kyverno rejects the policy at admission unless an
image pattern accompanies it. Both mistakes appear as a `dry-run failed` on the
Kustomization rather than at pod creation.

### failurePolicy: Fail

An unreachable webhook rejects a pod regardless of whether it would have
passed. The namespaces that must come up during an outage never consult it:
`kube-system` and `flux-system` are excluded by
`config.webhooks.namespaceSelector`, and `kyverno` by the chart's own default.

`flux-system` had to be added to that selector. The policies exclude it in
their own `exclude` blocks, but a policy-level exclusion still requires Kyverno
to be reachable before it can be consulted, which would place the recovery
mechanism behind the failure.

While Kyverno is down, governed namespaces cannot schedule pods, external
access included. Recovery paths remain open throughout: kubectl over the admin
AP, Flux reconciling a fix, or deleting the webhook configurations by hand.
Switching to `Ignore` leaves PSA's `baseline` underneath, which is what makes
the choice reversible.
