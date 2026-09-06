# GitOps

Flux reconciles this repo into the cluster. Deleting a manifest deletes the
object, the same way dropping a package from the Containerfile removes it from
the host.

The cluster runs [flux-operator](https://github.com/controlplaneio-fluxcd/flux-operator),
not a `flux bootstrap` checkout. `clusters/rpi4/flux-instance.yaml` declares
the distribution, components and sync source; the operator itself is a
`HelmRelease` under `infrastructure/flux-operator/`, so Flux upgrades Flux. The
image controllers are not run — images are pinned in git and bumped by commit.

## Tiers

`clusters/rpi4/` holds one Kustomization per tier, chained with `dependsOn` and
`wait: true`:

```
policy (./infrastructure/kyverno) ─▶ infrastructure ─▶ apps
```

Kyverno is deliberately absent from `infrastructure/kustomization.yaml`; being
its own tier is what puts admission control ahead of everything it governs
([admission](admission.md)).

All three prune and substitute from the `cluster-vars` ConfigMap, currently
`DOMAIN`. Substitution reaches only what a Kustomization itself renders, so a
nested one using a variable needs its own `postBuild`. Substitution reaches
generated ConfigMap content too, which is how cloudflared's `config.yaml` gets
the domain.

## Nesting

Components with internal ordering repeat the pattern one level down — a
directory of manifests, a Flux Kustomization pointing at it, and `sources.yaml`
for what it pulls from. That is what orders `crds → operator → crs` inside
external-secrets without ordering everything else, and lets `cloudflared` and
`technitium` declare `dependsOn: external-secrets-crs` across component
boundaries.

Every component declares its own namespace as a manifest rather than relying on
`targetNamespace`, so labels and deletion stay declarative.

## Bootstrap

Two Secrets exist in the cluster but not in this repo, both credentials Flux
needs before it can read anything: `flux-system/flux-system` (the pull secret)
and `external-secrets/infisical-universal-auth` ([secrets](secrets.md)). See
[bootstrap](bootstrap.md).
