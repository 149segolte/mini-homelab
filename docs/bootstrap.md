# Bootstrapping the cluster

Run once, after a fresh [install](install.md). k3s is enabled in the image, so
the node is up on first boot; these steps hand it over to Flux.

## 1. kubeconfig

k3s writes `/etc/rancher/k3s/k3s.yaml` mode `0600`, root only.

```bash
ssh <admin>@172.19.150.1 sudo cat /etc/rancher/k3s/k3s.yaml \
  | sed 's#127.0.0.1#172.19.150.1#' > ~/.kube/mini-homelab
export KUBECONFIG=~/.kube/mini-homelab
```

The apiserver is reachable from the admin AP and the tailnet only
([networking](networking.md)).

## 2. flux-operator

The `FluxInstance` CRD has to exist before the manifest that uses it, so the
operator is installed by hand once. Flux adopts the release afterwards, via the
`HelmRelease` in `infrastructure/flux-operator/` — matching release name and
namespace is what makes the handover work.

```bash
helm install flux-operator oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator \
  --namespace flux-system --create-namespace --version 0.59.x
```

## 3. Pull secret

`flux-instance.yaml` sets `provider: github`, so the Secret holds GitHub App
credentials rather than a token.

```bash
flux create secret githubapp flux-system \
  --app-id=<id> \
  --app-installation-id=<id> \
  --app-private-key=<key>.pem
```

Needs the `flux` CLI, and defaults to the `flux-system` namespace.

## 4. Hand over

```bash
kubectl apply -f clusters/rpi4/flux-instance.yaml
```

Flux now reconciles `clusters/rpi4`, which includes `flux-instance.yaml`
itself, and works through the tiers ([gitops](flux.md#tiers)).

## 5. Infisical credentials

The `ClusterSecretStore` stays unready until this exists, and every
`ExternalSecret` with it ([secrets](secrets.md)). The namespace arrives with
the infrastructure tier; creating it early is harmless, Flux adopts it.

```bash
kubectl create namespace external-secrets
kubectl create secret generic infisical-universal-auth --namespace external-secrets \
  --from-literal=clientId=<id> \
  --from-literal=clientSecret=<secret>
```

## Verify

```bash
flux get all -A
kubectl get externalsecrets -A
kubectl get clusterpolicy
```

Expect the tiers to settle in order: `policy`, then `infrastructure`, then
`apps`. Kyverno failing closed makes a stalled `policy` tier look like
unrelated pods refusing to schedule ([admission](admission.md#failurepolicy-fail)).
