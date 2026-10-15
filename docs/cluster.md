# Cluster

Flux reconciles this repository, installed by
[flux-operator](https://github.com/controlplaneio-fluxcd/flux-operator) from
`clusters/rpi4/flux-instance.yaml`. The operator is itself a `HelmRelease`, so
Flux upgrades Flux. Image tags are pinned in git and change by commit.

## Tiers

`clusters/rpi4/` chains three Kustomizations with `dependsOn` and `wait: true`:

```
initialization --> infrastructure --> apps
```

`initialization` holds what the others build on: Traefik's entrypoints, External
Secrets Operator and the external-partition volumes. A component that needs its
own ordering nests a Flux Kustomization per step, as external-secrets
(`crds -> operator -> crs`) and cert-manager (`controller -> issuers`) do.

### Variables

Every tier substitutes `${...}` from the `cluster-vars` ConfigMap: `DOMAIN`,
`EXTERNAL_IP`, `ACME_EMAIL`, `LOCATION` and `TIMEZONE`. An unset variable
becomes an empty string. Substitution covers generated ConfigMap content, but
not manifests rendered by a nested Kustomization, which needs its own
`postBuild`. A resource annotated `kustomize.toolkit.fluxcd.io/substitute:
disabled` is skipped.

`cluster-vars` lives in the cluster, not in git, because `EXTERNAL_IP` may
change. `initialization/bootstrap.yaml` is the reference copy and is left out
of the kustomization so Flux never adopts it. kustomize-controller watches it,
so an edit reconciles immediately.

## Bootstrapping

Once, on a fresh install:

```bash
# kubeconfig
ssh <admin>@172.19.149.1 sudo cat /etc/rancher/k3s/k3s.yaml \
  | sed 's#127.0.0.1#172.19.149.1#' > ~/.kube/mini-homelab
export KUBECONFIG=~/.kube/mini-homelab

# the FluxInstance CRD; Flux adopts the release later
helm install flux-operator oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator \
  --namespace flux-system --create-namespace --version 0.61.x

# GitHub App credentials for the sync
flux create secret githubapp flux-system \
  --app-id=<id> --app-installation-id=<id> --app-private-key=<key>.pem

# before handover, or substitution fails in every tier
kubectl apply -f initialization/bootstrap.yaml
kubectl apply -f clusters/rpi4/flux-instance.yaml

# Infisical credentials, or no ExternalSecret becomes ready
kubectl create namespace external-secrets
kubectl create secret generic infisical-universal-auth --namespace external-secrets \
  --from-literal=clientId=<id> --from-literal=clientSecret=<secret>
```

## Secrets

No secret is committed. Cluster secrets come from Infisical's `prod`
environment through the `infisical` ClusterSecretStore. A consuming pod restarts
to pick up a rotation, except where noted.

The ESO CRDs come from upstream git and the operator from its chart, so the
operator never starts against missing CRDs. Bump the git tag and chart version
together. The chart's toggle is `installCRDs: false`; `crds.create: false` is
accepted and silently installs a second copy.

Config files that contain secrets are built by the ExternalSecret's
`target.template` (`engineVersion: v2`):

- Indent multi-line values such as a PEM with `nindent` into a block scalar. A
  quoted scalar folds the newlines and yields an unusable key.
- `template.data` replaces the Secret's data, so passthrough keys are listed
  too, and one bad reference drops every key.
- `${...}` belongs to Flux and `{{ ... }}` to ESO. A `regexp` rewrite target
  such as `${1}` fails Flux substitution; use a `transform` rewrite instead.

## Admission control

Pod security lives in the apiserver's admission configuration, shipped in the
image, so it involves no webhook and cannot fail closed.

- `PodSecurity` enforces `restricted` on every namespace except `kube-system`,
  including unlabelled ones.
- A static `MutatingAdmissionPolicy` fills in what is missing on pod creation:
  `seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation: false`, and
  `capabilities.drop: [ALL]`. A container that sets any `capabilities` of its
  own is left alone and has to drop `ALL` itself.

`runAsNonRoot` is not defaulted. Each workload declares it, along with
`runAsUser` when the image has no `USER`.

## Storage

`local-path` is the default StorageClass and holds application state. It does
not enforce the requested size.

`external` is the partition at `/var/external`, without a provisioner.
`initialization/external-storage/<app>.yaml` declares a retained PV per folder,
and the app's claim names it in `volumeName`. A PV binds one claim, so two apps
sharing a folder need a PV each. A missing folder fails the pod; create it
first, owned by UID 1000.
